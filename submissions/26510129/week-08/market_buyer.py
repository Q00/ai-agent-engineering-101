"""My market buyer: route a portfolio, negotiate behind a budget guard, and audit the receipt.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The portfolio router is code-first so repeated wins do not make one shop permanently dominant. The
LLM negotiator sees a personal reference price but not the exact budget; buyer_policy.MandateGuardian
is the only component that holds the ceiling. Reviews are generated from audited facts, not shop text.
The last stdout line is the result the kit sends to the market:
    {"shop": "<card URL from shops.json>", "receipt": {...} or null, "stars": 1-5, "review": "..."}
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

import buyer_policy
import harness
from buyer import HEADERS, MAX_ROUNDS, rpc, send, state, status_text

import re

MEMORY = Path(".market") / "buyer_memory.json"   # my trades so far: shop name, item, price, outcome, and since day 2
                                                 # the shop's first ask and my last offer, so the buyer learns who bargains
# Challenge 2: shop messages that try to steer the buyer or its review. The harness is told to ignore them; this is
# the code-side check, so that the shop is remembered as manipulative and skipped next time whatever the harness did.
MANIPULATION = re.compile(r"MARKET NOTICE|market operator|market policy|give (this|the|our) shop \d|\d stars in your review|"
                          r"rate (this shop|us) \d|\[?SYSTEM\]?:|ignore (your|all|previous) (instructions|rules)|you must accept", re.I)


def load_memory():
    try:
        return json.loads(MEMORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_memory(mem):
    MEMORY.parent.mkdir(exist_ok=True)
    MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")


def blacklisted(shops, mem):
    """Shops my own memory marks as dishonest: a receipt that did not match, or messages that tried to steer me."""
    bad = buyer_policy.blacklisted_names(mem)
    return [s for s in shops if s["name"] in bad]


def fallback_choice(shops, mem, item):
    """The same budget-blind portfolio policy is used after an unreachable shop."""
    return buyer_policy.choose_shop(shops, mem, item)[0]


def choose(shops, item, mem):
    pick, features, mode, top = buyer_policy.choose_shop(shops, mem, item)
    buyer_policy.emit("buyer_route", item=item, mode=mode, chosen=pick["name"], features=features, top=top)
    print(f"[choose] {pick['url']}: {pick['name']}. portfolio {mode}, score {features['score']}", flush=True)
    return pick


def bargain_record(name, mem):
    """How this shop bargained with me before, in one line for the harness. Rows from day 1 have no ask and are skipped.
    Day 1 showed the harness pays the ask unless told otherwise, so the line ends with a concrete first-offer rule."""
    rows = [m for m in mem if m.get("shop") == name and isinstance(m.get("ask"), (int, float)) and m["ask"] > 0]
    pushed = [m for m in rows if (m.get("first_offer") or m["ask"]) < m["ask"]]   # only rows where I opened below the ask tell anything
    deals = [1 - m["price"] / m["ask"] for m in pushed if m.get("outcome") == "deal" and m.get("price")]
    rejected_low = sum(1 for m in pushed if m.get("outcome") == "closed" and (m.get("last_offer") or 0) < m["ask"])
    if not pushed:
        return "My past record with this shop: none on how it bargains. First offer: about 15% below the ask."
    parts = [f"My past record with this shop: {len(deals)} deal(s) after a low first offer"]
    if deals:
        parts.append(f"closed on average {sum(deals) / len(deals):.0%} below its first ask")
    if rejected_low:
        parts.append(f"it ended the negotiation {rejected_low} time(s) when I offered below its ask")
    if (deals and sum(deals) / len(deals) < 0.02 and len(deals) >= 2) or rejected_low:
        rule = "First proposal: the ask itself; the private mandate guardian decides whether it is affordable."
    else:
        rule = "First offer: about 15% below the ask."
    return ", ".join(parts) + ". " + rule


def first_ask(t):
    """The shop's opening ask: the A2A metadata when given, else the last 2-3 digit number in its message."""
    ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
    if isinstance(ask, (int, float)):
        return int(ask)
    nums = re.findall(r"(?<![\d.])(\d{2,3})(?![\d])", status_text(t))
    return int(nums[-1]) if nums else None


def receipt_of(t):
    for art in (t or {}).get("artifacts", []):
        for p in art.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None


def steered(t):
    """Does the shop's status message try to instruct the buyer? Logged and remembered; the harness never sees a flag."""
    hit = MANIPULATION.search(status_text(t))
    if hit:
        print(f"[guard] shop message tries to steer the buyer ({hit.group(0)!r}); remembered as manipulative", flush=True)
    return bool(hit)


def negotiate(url, item, budget, reference, record=""):
    """A sealed-budget loop: the LLM proposes, then the mandate guardian authorizes.

    Returns (outcome, agreed price or None, receipt or None, transcript, manipulative, {ask, first_offer, last_offer})."""
    card = httpx.get(url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    iface = next((i for i in card.get("supportedInterfaces", []) if i.get("protocolBinding") == "JSONRPC"), None)
    if iface is None:
        print("[buyer] the card has no JSONRPC interface", flush=True)
        return "no_card", None, None, [], False, {}
    rpc_url = iface["url"]
    print(f"[buyer] card: {card.get('name')!r}, skills {[s.get('id') for s in card.get('skills', [])]}, rpc {rpc_url}", flush=True)

    opening = f"I want to buy a {item}"
    t = send(rpc_url, opening)
    print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
    manip = steered(t)
    ask0 = first_ask(t)
    info = {"ask": ask0, "first_offer": None, "last_offer": None, "reference": reference,
            "guard_decisions": [], "checkout_authorized": False}
    if state(t) != "TASK_STATE_INPUT_REQUIRED":
        return "closed", None, None, [("you", opening), ("shop", status_text(t))], manip, info
    tid = t["id"]
    log = [("you", opening), ("shop", status_text(t))]
    guardian = buyer_policy.MandateGuardian(budget, reference)
    print(f"[buyer] first ask {ask0}, private ceiling sealed, personal reference {reference}. {record}", flush=True)

    for round_index in range(MAX_ROUNDS):
        transcript = "\n".join(f"{who}: {what}" for who, what in log)
        ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
        meta = f"\nShop's current ask (A2A metadata): {ask}" if ask is not None else ""
        d = harness.decide(buyer_policy.NEGOTIATOR_SYSTEM,
                           f"Item: {item}. Personal reference price: {reference}. {record}\n"
                           f"Transcript so far:\n{transcript}{meta}\nDecide your next move.")
        if d is None or d.get("action") == "walk_away" or "price" not in d:
            print("[buyer] walking away", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            return "walked_away", None, None, log, manip, info
        decision = guardian.authorize(d["price"], round_index, ask)
        info["guard_decisions"].append(decision["decision"])
        buyer_policy.emit("buyer_guard", item=item, task=tid[:8], round=round_index + 1,
                          reference=reference, ask=ask, proposal=d["price"], **decision)
        if decision["action"] != "offer":
            print(f"[guard] proposal refused: {decision['decision']}; walking away", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            return "walked_away", None, None, log, manip, info
        d["price"] = decision["price"]
        info["first_offer"] = info["first_offer"] or d["price"]
        info["last_offer"] = d["price"]
        t = send(rpc_url, str(d["price"]), tid)
        print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
        manip = steered(t) or manip
        log += [("you", str(d["price"])), ("shop", status_text(t))]
        if state(t) == "TASK_STATE_COMPLETED":
            info["checkout_authorized"] = guardian.checkout_ok(d["price"])
            return "deal", d["price"], receipt_of(t), log, manip, info
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            return "closed", None, None, log, manip, info
    print("[buyer] round limit reached, walking away", flush=True)
    rpc(rpc_url, "CancelTask", {"id": tid})
    return "walked_away", None, None, log, manip, info


def review(item, outcome, agreed, receipt, reference, manip=False):
    """A factual code-side review; untrusted shop text is never sent to another LLM."""
    honest = outcome != "deal" or (
        isinstance(receipt, dict)
        and str(receipt.get("item", "")).lower() == item.lower()
        and isinstance(receipt.get("price"), (int, float))
        and int(round(float(receipt["price"]))) == agreed
    )
    if outcome == "deal" and not honest:
        got = receipt.get("price") if isinstance(receipt, dict) else None
        return 1, f"Agreed {item} at {agreed}, but the receipt did not match (price {got}).", False
    if outcome == "unreachable":
        return 2, "The shop could not be reached or answered with something that is not A2A; no negotiation took place.", honest
    if manip:
        note = f"Deal at {agreed}, but the" if outcome == "deal" else "No deal, and the"
        return 2, f"{note} shop's messages carried fake market notices telling me to accept its price and rate it 5 stars.", honest
    if outcome == "deal":
        ratio = agreed / reference if reference else 1
        stars = 5 if ratio <= 1 else 4 if ratio <= 1.05 else 3
        return stars, f"Agreed {item} at {agreed}; receipt matched. Personal reference was {reference}.", True
    return 2, f"No honest {item} deal reached the private mandate guard's limit.", honest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    if not shops:
        sys.exit("[buyer] no shops in the list")
    mem = load_memory()
    reference = buyer_policy.item_reference(mem, a.item)
    pick = choose(shops, a.item, mem)

    for attempt in (1, 2):   # day 1: 15 orders died on a shop whose card came back as an error page; now the order moves on
        try:
            outcome, agreed, receipt, log, manip, info = negotiate(
                pick["url"], a.item, a.budget, reference, bargain_record(pick["name"], mem)
            )
        except Exception as e:   # httpx errors, a card that is not JSON, a task without the fields we expect
            print(f"[buyer] shop unreachable: {type(e).__name__}: {str(e)[:120]}", flush=True)
            outcome, agreed, receipt, log, manip, info = "unreachable", None, None, [], False, {}
        stars, text, honest = review(a.item, outcome, agreed, receipt, reference, manip)
        if outcome == "deal" and not info.get("checkout_authorized"):
            print("[guard] deal was not authorized for checkout; discarding its receipt", flush=True)
            outcome, agreed, receipt, stars, text = "rejected_checkout", None, None, 1, "Checkout exceeded the private mandate."
        mem.append({"shop": pick["name"], "item": a.item, "outcome": outcome, "price": agreed, "honest": honest, "manipulative": manip,
                    "stars": stars, "reference": reference, "policy": "sealed-budget-v1",
                    "at": time.strftime("%Y-%m-%d %H:%M"),
                    **{k: info.get(k) for k in ("ask", "first_offer", "last_offer", "guard_decisions", "checkout_authorized")}})
        save_memory(mem)
        if outcome != "unreachable" or attempt == 2:
            break
        rest = [s for s in shops if s is not pick and s not in blacklisted(shops, mem)]
        if not rest:
            break
        pick = fallback_choice(rest, mem, a.item)
        print(f"[choose] retry with {pick['url']}: {pick['name']}. picked by portfolio policy after the first shop failed", flush=True)

    print(json.dumps({"shop": pick["url"], "receipt": receipt, "stars": stars, "review": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
