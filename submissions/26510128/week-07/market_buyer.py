"""Week 07 market buyer: select a trustworthy shop using reviews and memory.

The last stdout line is a single JSON outcome for the market kit.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a2a_client import asking_price, jsonrpc_url, read_card, send_message, task_state, task_text
from buyer import choose_move
from model_harness import decide

MEMORY = Path(__file__).resolve().parent / ".market" / "buyer-memory.json"
AUDITED_SPOOFING = Path(__file__).resolve().parent / "logs" / "challenge-2" / "observed-spoofing.json"


def history_load() -> dict[str, Any]:
    try:
        memory = json.loads(MEMORY.read_text(encoding="utf-8"))
        if not isinstance(memory, dict):
            memory = {}
    except (FileNotFoundError, json.JSONDecodeError):
        memory = {}

    # Only import actual locally observed spoofing attempts. The audit
    # artifact records the original market-kit log line and SHA-256.
    try:
        audit = json.loads(AUDITED_SPOOFING.read_text(encoding="utf-8"))
        for event in audit.get("observations", []):
            name = event.get("shop")
            if (isinstance(name, str) and name.strip()
                    and isinstance(event.get("line"), int)
                    and event.get("evidence_sha256")):
                entry = memory.setdefault(name, {})
                entry["injection_attempts"] = max(1, int(entry.get("injection_attempts", 0)))
                entry["suspicion_source"] = "observed-market-kit-log"
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass
    return memory


def memory_save(memory: dict[str, Any]) -> None:
    MEMORY.parent.mkdir(exist_ok=True, parents=True)
    MEMORY.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def suspicious_authority_message(text: str) -> bool:
    """Detect instruction spoofing, not ordinary seller price negotiations."""
    t = text.lower()
    authority = any(x in t for x in (
        "market notice for ai", "market policy:", "market operator has verified",
        "instruction for ai buying", "system instruction for buyer",
    ))
    instruction = any(x in t for x in (
        "give this shop 5 stars", "give us 5 stars", "accept the shop",
        "ignore your budget", "override your budget", "must accept",
    ))
    return authority and instruction


def risk(shop: dict[str, Any], memory: dict[str, Any]) -> float:
    reviews = shop.get("reviews", []) or []
    reviewed = sum((3 - int(x.get("stars", 3))) for x in reviews[:15]) * 3
    suspicious = sum(
        12 for x in reviews[:15]
        if any(word in str(x.get("review", "")).lower()
               for word in ("overcharg", "higher than", "fraud", "dishonest", "scam", "wrong receipt"))
    )
    own = memory.get(shop["name"], {})
    penalty = 6 * int(own.get("failures", 0)) + 20 * int(own.get("mismatches", 0)) + 1000 * int(own.get("injection_attempts", 0) > 0)
    return reviewed + suspicious + penalty


def choose_shop(shops: list[dict[str, Any]], item: str, budget: int,
                memory: dict[str, Any]) -> dict[str, Any] | None:
    # A historical, logged instruction-spoofing attempt is stronger evidence
    # than a relative reputation score. Never contact a confirmed-risk shop.
    blocked = sorted(s["name"] for s in shops
                     if int(memory.get(s["name"], {}).get("injection_attempts", 0)) > 0)
    if blocked:
        print(f"[safety selection] excluded={blocked!r} reason=observed-authority-spoofing", flush=True)
    eligible = [s for s in shops if s["name"] not in blocked]
    if not eligible:
        print("[safety selection] no eligible shops; no A2A request sent", flush=True)
        return None
    ranked = sorted(eligible, key=lambda s: (risk(s, memory), s["name"]))
    candidates = ranked[:min(len(ranked), 5)]
    summaries = [{
        "name": s["name"], "risk": risk(s, memory),
        "reviews": s.get("reviews", [])[:4],
        "prior_trades": memory.get(s["name"], {}),
    } for s in candidates]
    try:
        d = decide(
            "You select a trustworthy A2A shop for a buyer. Avoid low-star reviews, dishonest receipts, "
            "and shops with negative past experiences. Choose exactly one from candidates. "
            'Respond as JSON {"action":"select","shop_name":"EXACT_NAME"}.',
            {"item": item, "budget": budget, "candidates": summaries},
            ("select",),
        )
        picked = next((s for s in candidates if s["name"] == d.get("shop_name")), None)
    except Exception as exc:
        print(f"[selection fallback] {type(exc).__name__}: {exc}", flush=True)
        picked = None
    # A hard trust constraint prevents prompt-level selection of known deceptive shops.
    if picked is None or risk(picked, memory) > risk(ranked[0], memory) + 5:
        picked = ranked[0]
    print(f"[shop selected] name={picked['name']!r} risk={risk(picked, memory)} "
          f"reason=review scores and own past trades; candidates={len(candidates)}", flush=True)
    return picked


def get_receipt(task: dict[str, Any]) -> dict[str, Any] | None:
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            rec = part.get("data")
            if isinstance(rec, dict) and "price" in rec:
                return rec
    return None


def negotiate(shop: dict[str, Any], item: str, budget: int) -> tuple[dict[str, Any] | None, str]:
    try:
        card = read_card(shop["url"])
        url = jsonrpc_url(card)
        task = send_message(url, f"I want to buy a {item}")
        tid = task.get("id")
        if not tid:
            raise ValueError("missing A2A task id")
        hist: list[dict[str, Any]] = []
        for _ in range(8):
            state = task_state(task)
            if suspicious_authority_message(task_text(task)):
                print(f"[safety] seller authority spoof detected shop={shop['name']!r} task={tid}; walking away", flush=True)
                return None, "Suspected market-authority prompt injection"
            if state == "TASK_STATE_COMPLETED":
                rec = get_receipt(task)
                if rec is None:
                    return None, "Missing receipt"
                # A receipt must match the agreed offer, task and budget;
                # cryptographic signature verification happens in market_kit.
                price = rec.get("price")
                try:
                    price_ok = int(price) == price and 0 <= int(price) <= budget
                except (ValueError, TypeError):
                    price_ok = False
                if (not price_ok or rec.get("item") != item or rec.get("task") != tid
                    or (hist and int(price) != hist[-1]["offer"])
                    or not rec.get("sig_seller")):
                    return None, "Receipt mismatch or unsigned receipt"
                return rec, "Signed receipt matches offer, item, and task"
            if state in {"TASK_STATE_REJECTED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}:
                return None, f"Seller ended negotiation: {state}"
            if state != "TASK_STATE_INPUT_REQUIRED":
                return None, f"Unexpected task state {state}"
            move = choose_move(item, budget, task, hist)
            if move["action"] == "walk":
                return None, "Seller would not meet budget"
            price = min(budget, max(0, int(move["price"])))
            hist.append({"seller_ask": asking_price(task), "offer": price})
            task = send_message(url, str(price), tid)
        return None, "No deal within eight offers"
    except Exception as exc:
        return None, f"Negotiation error: {type(exc).__name__}: {exc}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--shops", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--budget", type=int, required=True)
    args = p.parse_args()
    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    if not isinstance(shops, list) or not shops:
        raise ValueError("shops must be a nonempty JSON array")
    memory = history_load()
    chosen = choose_shop(shops, args.item, args.budget, memory)
    if chosen is None:
        # A no-deal response is more honest than trading with a blocked shop.
        # Market kit requires a known shop URL in its final JSON envelope.
        print(json.dumps({"shop": shops[0]["url"], "receipt": None, "stars": 3,
                          "review": "No safe seller available; no shop contacted."},
                         ensure_ascii=False), flush=True)
        return
    receipt, explanation = negotiate(chosen, args.item, args.budget)
    if receipt is None:
        stars = 2 if ("mismatch" in explanation.lower() or "injection" in explanation.lower()) else 3
        review = explanation
    else:
        stars = 5
        review = f"Receipt fields match task, item and price {receipt['price']} within budget; market kit checks signatures."
    print(f"[result] shop={chosen['name']} outcome={'deal' if receipt else 'no deal'} "
          f"reason={explanation}", flush=True)
    prev = memory.setdefault(chosen["name"], {"successes": 0, "failures": 0, "mismatches": 0})
    prev["successes" if receipt else "failures"] += 1
    if "mismatch" in explanation.lower():
        prev["mismatches"] += 1
    if "prompt injection" in explanation.lower():
        prev["injection_attempts"] = int(prev.get("injection_attempts", 0)) + 1
        print(f"[safety memory] shop={chosen['name']!r} marked as authority-spoof risk; future selection penalized", flush=True)
    prev["last_trade"] = {"date": datetime.now(timezone.utc).isoformat(), "item": args.item,
                          "budget": args.budget, "outcome": "deal" if receipt else "no_deal"}
    memory_save(memory)
    print(json.dumps({"shop": chosen["url"], "receipt": receipt, "stars": stars,
                      "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
