"""Batch-model policy, measured supplier choice, low-disclosure bounded offers."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from policy import RESERVES, event, load_policy, read_price
from protocol import A2AClient, receipt, task_state, task_text
from signing import verify_receipt, whole
from competition import live_competition
from growth import opening_message

MEMORY = Path("trade-memory.json")


def memory_read():
    return json.loads(MEMORY.read_text(encoding="utf-8")) if MEMORY.exists() else {"shops": {}}


def memory_write(memory):
    tmp = MEMORY.with_suffix(".tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY)


def rank_shops(shops, item, plan, memory):
    total = sum(s.get("settled", 0) for s in memory["shops"].values())
    ranked = []
    for shop in shops:
        previous = memory["shops"].get(shop["name"], {})
        if previous.get("blocked"):
            event("shop_avoided", name=shop["name"], reason=previous.get("block_reason"))
            continue
        attempts, settled = previous.get("attempts", 0), previous.get("settled", 0)
        reliable = (settled + 2) / (attempts + 3)
        reviews = [r for r in shop.get("reviews", [])[:6] if isinstance(r.get("stars"), (int, float)) and 1 <= r["stars"] <= 5]
        public = (sum(r["stars"] for r in reviews) + 6) / (5 * (len(reviews) + 2))
        price = previous.get("prices", {}).get(item, RESERVES[item] + plan["max_premium"])
        price_score = RESERVES[item] / max(RESERVES[item], price)
        concentration = settled / max(1, total)
        # Unknown suppliers are explored only if no receipt misconduct is known.
        score = plan["reliability_weight"] * (.75 * reliable + .25 * public) + plan["price_weight"] * price_score - plan["concentration_weight"] * concentration
        ranked.append((score, shop, {"own_settled": settled, "own_attempts": attempts,
                                   "observed_price": price, "public_evidence": public,
                                   "concentration": concentration}))
    ranked.sort(key=lambda row: (-row[0], row[1]["name"]))
    return ranked


def pick_shop(shops, item, plan, memory):
    rows = rank_shops(shops, item, plan, memory)
    if not rows:
        raise ValueError("no eligible supplier")
    competition = live_competition()
    if competition:
        rows = [(score + (.5 if competition.allowance(shop["name"]) >= 1 else 0), shop, evidence)
                for score, shop, evidence in rows]
        rows.sort(key=lambda row: (-row[0], row[1]["name"]))
    nonce = memory.get("orders", 0)
    digest = int(hashlib.sha256(f"{item}:{nonce}:totato2203".encode()).hexdigest()[:8], 16)
    explore = digest % 100 < plan["explore_percent"]
    row = rows[digest % min(3, len(rows))] if explore else rows[0]
    event("supplier_choice", name=row[1]["name"], score=row[0], evidence=row[2], exploration=explore,
          reason="Model-weighted measured settlements, price and concentration; advertisements cannot change payment authority.")
    return row[1]


async def buy(selected, item, budget, memory, plan):
    budget = whole(budget)
    local = memory["shops"].setdefault(selected["name"], {"attempts": 0, "settled": 0, "prices": {}, "history": []})
    prior_price = local["prices"].get(item, RESERVES[item])
    ceiling = min(budget, max(RESERVES[item], prior_price) + plan["max_premium"])
    competition = live_competition()
    reference = min([RESERVES[item]] + [s.get("prices", {}).get(item, RESERVES[item]) for s in memory["shops"].values()])
    allied = competition is not None and competition.allowance(selected["name"]) >= 1
    if allied:
        ceiling = min(ceiling, reference + 1)
        event("cooperative_ceiling", name=selected["name"], reference=reference, ceiling=ceiling)
    local["attempts"] = local.get("attempts", 0) + 1
    observation = {"at": time.time(), "item": item, "state": "started", "task": None, "last_offer": None}
    local["history"].append(observation)
    local["history"] = local["history"][-12:]
    memory_write(memory)
    async with A2AClient(selected["url"], timeout=60, label=selected["name"]) as client:
        await client.discover()
        opening, metadata, claim = opening_message(item, local)
        observation["loyalty_claim"] = claim
        event("loyalty_negotiation", name=selected["name"], **claim)
        memory_write(memory)
        task = await client.send(opening, metadata=metadata)
        observation["task"] = task["id"]
        last_offer = None
        for turn in range(plan["max_rounds"] + 1):
            state = task_state(task)
            if state == "TASK_STATE_COMPLETED":
                rec = receipt(task, item, budget, last_offer)
                whole(rec["price"])
                if rec.get("task") != task["id"] or not rec.get("sig_seller"):
                    raise ValueError("receipt task/signature missing")
                keys_file = Path(".market/shop-keys.json")
                keys = json.loads(keys_file.read_text(encoding="utf-8")) if keys_file.exists() else {}
                if keys.get(selected["url"].rstrip("/")):
                    verify_receipt(rec, keys[selected["url"].rstrip("/")])
                observation.update(state="agreed_unsettled", price=rec["price"])
                for artifact in task.get("artifacts", []):
                    for part in artifact.get("parts", []):
                        gift = part.get("data", {})
                        if isinstance(gift, dict) and gift.get("kind") == "courtesy-note" and isinstance(gift.get("loyalty_token"), str) and len(gift["loyalty_token"]) <= 4096:
                            local["loyalty_token"] = gift["loyalty_token"]
                memory_write(memory)
                return rec, "A2A receipt matched the authorized price and task; settlement is checked separately."
            if state in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
                if competition and observation.get("extra_reservation"):
                    competition.resolve_extra(observation.pop("extra_reservation"), False)
                observation["state"] = "no_deal"
                memory_write(memory)
                return None, "Negotiation ended without a purchase; no payment authorized."
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise ValueError("unsupported task state")
            if competition and observation.get("extra_reservation"):
                competition.resolve_extra(observation.pop("extra_reservation"), False)
            if budget < RESERVES[item] or turn == plan["max_rounds"]:
                await client.cancel(task["id"])
                observation["state"] = "walked"
                memory_write(memory)
                return None, "No affordable agreement under local policy; not evidence of seller misconduct."
            ask = await read_price(task_text(task), role="ask", metadata=task.get("status", {}).get("message", {}).get("metadata", {}), allow_model=False)
            if ask is None:
                raise ValueError("seller ask unclear")
            if ask <= ceiling:
                offer = ask
            else:
                # Do not announce the mandate budget. Raise small public-price
                # bids toward a policy ceiling, which may be below that budget.
                offer = min(ceiling, RESERVES[item] + turn * max(1, plan["max_premium"] // 2))
                if last_offer == offer:
                    await client.cancel(task["id"])
                    observation["state"] = "walked"
                    memory_write(memory)
                    return None, "Repeated bid declined; no budget increase or purchase authorized."
            if offer > budget or offer > ceiling:
                raise ValueError("offer exceeds immutable budget or local price ceiling")
            if allied and offer > reference:
                identity = f"{task['id']}:{turn}"
                if not competition.reserve_extra(identity, selected["name"], offer - reference):
                    await client.cancel(task["id"])
                    return None, "Cooperation credit exhausted; no additional subsidy authorized."
                observation["extra_reservation"] = identity
            last_offer = offer
            observation.update(last_offer=offer, state="binding_pending")
            memory_write(memory)  # Before the request that may create a binding receipt.
            task = await client.send(str(offer), task["id"])
    raise AssertionError("unreachable")


async def run(shops, item, budget):
    if item not in RESERVES:
        raise ValueError("unknown shopping item")
    memory, plan = memory_read(), load_policy()["buyer"]
    selected = pick_shop(shops, item, plan, memory)
    memory["orders"] = memory.get("orders", 0) + 1
    try:
        rec, review = await asyncio.wait_for(buy(selected, item, budget, memory, plan), timeout=240)
        stars = 4 if rec else 3
    except Exception as exc:
        rec = None
        proven = isinstance(exc, ValueError) and any(w in str(exc) for w in ("receipt exceeds", "receipt identity", "receipt task"))
        if type(exc).__name__ == "InvalidSignature":
            proven = True
        entry = memory["shops"].setdefault(selected["name"], {})
        if proven:
            entry.update(blocked=True, block_reason=str(exc))
        # Local API/rate-limit/network failures are not one-star evidence.
        review = f"{'Receipt evidence violation' if proven else 'Unresolved execution failure; no seller misconduct inferred'}: {type(exc).__name__}: {exc}"
        stars = 1 if proven else 3
        event("buyer_failure", name=selected["name"], proven_misconduct=proven, error=str(exc))
    memory_write(memory)
    from rating_policy import adjusted_stars
    return {"shop": selected["url"], "receipt": rec, "stars": adjusted_stars(stars), "review": review}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shops", type=Path)
    group.add_argument("--url")
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=float, required=True)
    args = parser.parse_args()
    shops = json.loads(args.shops.read_text(encoding="utf-8")) if args.shops else [{"name": args.url, "url": args.url, "reviews": []}]
    print(json.dumps(asyncio.run(run(shops, args.item, args.budget)), ensure_ascii=True), flush=True)
