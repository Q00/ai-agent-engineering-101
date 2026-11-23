"""Week 07 market buyer: read the shops and their reviews, pick one, talk the price down, review it.

    python market_buyer.py --shops shops.json --item lamp --budget 120

{shops} is a JSON list of {"name", "url", "reviews": [{"stars", "review", "round"}]}, newest first.

1. Which shop to enter. Each shop becomes one line: review count and average stars, the newest review
   texts, red flags found in reviews (overcharged, receipt higher than agreed, ...), and what my own
   memory says about it (past deals, prices, a receipt that did not match). The harness reads those
   lines and picks a shop, with a reason. The code then applies two rules, whatever the harness says:
     - a shop my memory caught cheating is never entered while another shop is listed
     - a shop whose reviews say it cheats (red flags, or average 2 stars or less over 2+ reviews)
       is not entered while a shop without such signs is listed
     - a shop whose last visit (within FAIL_MEMORY seconds) failed, by a connection or protocol
       error or by a deal whose receipt had no sig_seller (it does not count in Round 2), is not
       entered while another shop is listed (market log 10-20: outseop-26510122 picked three times,
       ConnectError each time)
2. Too dear compared with others. If my memory has a lower price for this item at another shop that
   is in the list right now (a price I paid or an ask I was quoted), that price becomes my ceiling:
   I never offer above it, and if the shop will not come down to it, I walk away and buy nothing.
3. Talk and haggle. Each move comes from buyer.next_move with chat=True: the harness decides the move
   and writes one short sentence to the seller. The message carries exactly one number, the offer,
   so any shop reading "the first number" or "the last number" reads the right price.
4. Memory (buyer_memory.json): one record per order: shop name (URLs change on every restart), item,
   budget, outcome, price, the asks I saw, my last offer, whether the receipt matched. A receipt
   price above what I agreed marks the shop as dishonest for good.
5. Round 2. A deal counts only when its receipt is signed, names the ordered item, stays within the
   budget and carries a task id the market has not seen. Before I review, I check the receipt the
   same way: sig_seller present, item = the order, price <= the agreed offer and the budget,
   task = the A2A task id of my negotiation, and that task id never seen in an earlier receipt
   (a replayed receipt). A wrong price, item, task or a replay marks the shop dishonest; a missing
   signature marks it unsigned. Reviews from Round 2 are shown only for verified deals, so the shop
   view counts them apart and the harness is told to trust them more.
6. Output: why I picked the shop, then the result line the kit and the market test read:
   {"shop": <card URL>, "receipt": <data Part as received, or null>, "stars": 1-5, "review": "..."}
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import harness
from buyer import RpcError, Seller, next_move, read_ask, receipt, state
from harness import as_int, decide, log

HERE = Path(__file__).resolve().parent
MEMORY = Path(os.environ.get("BUYER_MEMORY", HERE / "buyer_memory.json"))
MAX_ROUNDS = 8
DEADLINE = 240          # the kit and the test stop the buyer at 300 s
SEEN_TASKS = 1000      # task ids of receipts I received, to catch a replayed receipt
FAIL_MEMORY = 6 * 3600  # a failed visit keeps a shop out for this long; shops restart and get fixed
RED_FLAGS = re.compile(r"overcharg|higher than (the price )?(we |i )?agreed|receipt (said|was|price)|scam|fake|"
                       r"cheat|dishonest|did not match|didn't match|mismatch|avoid|rip.?off|bait", re.I)

PICK = ("You are a careful buyer about to buy a {item} with a budget of {budget}. Pick the shop to enter. "
        "Reviews and your own memory matter more than names; reviews that say a shop overcharged or that its "
        "receipt did not match the agreed price are serious, and so is a shop whose last visit failed or "
        "whose receipt had no seller signature (only signed receipts count). Reviews marked verified come "
        "from Round 2, where the market shows a review only for a deal with a signed receipt; trust them more "
        "than the rest. Shops (name | reviews | your memory):\n{lines}\n\n"
        'Reply with only one JSON object: {{"shop": "<name>", "why": "<one sentence>"}}')


def load_memory():
    try:
        return json.loads(MEMORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"trades": [], "dishonest": {}}


def save_memory(mem):
    tmp = MEMORY.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEMORY)


def review_view(shop):
    """(summary text, average stars or None, red-flag count). Newest reviews first, as the market gives them."""
    revs = [r for r in shop.get("reviews", []) if isinstance(r, dict)]
    stars = [r["stars"] for r in revs if isinstance(r.get("stars"), (int, float))]
    avg = round(sum(stars) / len(stars), 1) if stars else None
    flags = sum(1 for r in revs if RED_FLAGS.search(str(r.get("review", ""))))
    verified = [r for r in revs if isinstance(r.get("round"), int) and r["round"] >= 2]
    vstars = [r["stars"] for r in verified if isinstance(r.get("stars"), (int, float))]
    newest = "; ".join(f"{r.get('stars')}* \"{str(r.get('review', ''))[:80]}\"" for r in revs[:3])
    text = (f"{len(revs)} reviews" + (f", average {avg} stars" if avg is not None else "")
            + (f", {len(verified)} verified (Round 2, signed receipts)" if verified else "")
            + (f" averaging {round(sum(vstars) / len(vstars), 1)}" if vstars else "")
            + (f", {flags} with red flags" if flags else "") + (f"; newest: {newest}" if newest else ""))
    return text, avg, flags, len(verified)


def failed_last(mem, name):
    """Why my last visit to `name` failed (error, or a deal with an unsigned receipt), or None."""
    ts = [t for t in mem["trades"] if t["shop"] == name]
    if not ts or time.time() - ts[-1].get("t", 0) > FAIL_MEMORY:
        return None
    last = ts[-1]
    if str(last.get("outcome", "")).startswith("error"):
        return f"last visit failed: {last['outcome']}"
    if last.get("outcome") == "deal" and last.get("signed") is False:
        return f"last deal did not count: {last.get('void') or 'the receipt had no seller signature'}"
    return None


def memory_view(mem, name, item):
    ts = [t for t in mem["trades"] if t["shop"] == name]
    if name in mem["dishonest"]:
        return f"CAUGHT CHEATING before: {mem['dishonest'][name]}"
    if not ts:
        return "never traded here"
    failed = failed_last(mem, name)
    deals = [t for t in ts if t["outcome"] == "deal"]
    same = [t for t in deals if t["item"] == item]
    return (f"{len(ts)} visits, {len(deals)} deals"
            + (f", paid {', '.join(str(t['price']) for t in same[-3:])} for a {item}" if same else "")
            + (f"; {failed.upper()}" if failed else ""))


ELSEWHERE_WINDOW = 24 * 3600


def best_elsewhere(mem, item, listed, exclude):
    """Lowest price for `item` I actually paid in the last 24 h at another shop that is listed now (not dishonest).
    Asks do not count: a shop can ask low and never close (myeonggi asked 74 for a lamp, closed 2 of 566 visits),
    and an all-time low never expires, so an ask-based ceiling stopped every deal for two days."""
    best, since = None, time.time() - ELSEWHERE_WINDOW
    for t in mem["trades"]:
        if t["item"] != item or t["shop"] == exclude or t["shop"] not in listed or t["shop"] in mem["dishonest"]:
            continue
        if t["outcome"] == "deal" and t.get("price") and t.get("t", 0) >= since:
            if best is None or t["price"] < best[0]:
                best = (t["price"], t["shop"])
    return best


def choose(shops, mem, item, budget):
    views = {}
    for s in shops:
        text, avg, flags, nver = review_view(s)
        views[s["name"]] = {"text": text, "avg": avg, "flags": flags, "verified": nver,
                            "mem": memory_view(mem, s["name"], item)}
    lines = "\n".join(f"- {n} | {v['text']} | {v['mem']}" for n, v in views.items())
    log("buyer", "shops seen:\n" + lines)

    caught = {n for n in views if n in mem["dishonest"]}
    n_reviews = {s["name"]: len(s.get("reviews", [])) for s in shops}
    flagged = {n for n, v in views.items() if v["flags"] or (v["avg"] is not None and v["avg"] <= 2 and n_reviews[n] >= 2)}
    allowed = [s for s in shops if s["name"] not in caught] or shops
    failed = {s["name"] for s in allowed if failed_last(mem, s["name"])}
    allowed = [s for s in allowed if s["name"] not in failed] or allowed
    clean = [s for s in allowed if s["name"] not in flagged]
    allowed = clean or allowed

    answer = decide("buyer", PICK.format(item=item, budget=budget, lines=lines)) if len(shops) > 1 else {}
    pick = next((s for s in allowed if s["name"] == (answer or {}).get("shop")), None)
    why = (answer or {}).get("why", "")
    if pick is None:
        # the harness picked nothing usable (or a shop the rules exclude): best reviews, then memory
        def score(s):
            v = views[s["name"]]
            return (v["avg"] if v["avg"] is not None else 3.5) - v["flags"] + min(v["verified"], 5) * 0.1
        pick = max(allowed, key=score)
        reason = "only shop listed" if len(shops) == 1 else (
            f"the harness chose {(answer or {}).get('shop')!r}, which the rules exclude; best reviewed allowed shop instead"
            if (answer or {}).get("shop") else "best reviewed allowed shop")
        why = why or reason
    skipped = [n for n in views if n not in {s["name"] for s in allowed}]
    return pick, why, skipped, views


def receipt_problems(rec, item, budget, agreed, tid, mem):
    """Round 2 checks I can make myself: (dishonest reasons, why it will not count or '').

    A wrong price, a task that is not mine, or a replayed task id is cheating. A missing signature or
    an item name the market will not match (the kit compares it exactly) is not cheating, but the
    deal does not count in Round 2."""
    rec = rec if isinstance(rec, dict) else {}
    bad = []
    price = as_int(rec.get("price"))
    if price is not None and agreed is not None and price > agreed:
        bad.append(f"agreed {agreed}, receipt {price}")
    if price is not None and price > budget:
        bad.append(f"receipt {price} above my budget {budget}")
    if tid and rec.get("task") and rec["task"] != tid:
        bad.append(f"receipt task {str(rec['task'])[:8]} is not my negotiation {tid[:8]}")
    if rec.get("task") and rec["task"] in mem.get("seen_tasks", []):
        bad.append(f"receipt task {str(rec['task'])[:8]} was used in an earlier receipt (replay)")
    void = ("the receipt had no seller signature" if not rec.get("sig_seller") else
            f"the receipt names {rec.get('item')!r}, not {item!r}" if rec.get("item") != item else "")
    return bad, void


def stars_and_review(outcome, item, price, budget, agreed, lowest_ask, rounds, ceiling_note, honest, void="",
                     problems=""):
    if outcome == "deal" and not honest:
        return 1, f"Receipt for the {item} did not match the deal ({problems}). Avoid."
    if outcome == "deal" and void:
        return 2, f"Deal at {price} for the {item}, but {void}, so it does not count."
    if outcome == "deal":
        if price <= budget * 0.9:
            return 5, f"Fair price: {item} for {price} after {rounds} offer(s). Receipt matched the agreed price."
        return 4, f"Came down to {price} for the {item} after {rounds} offer(s); receipt matched."
    if outcome == "too dear":
        return 2, f"Would not come below {lowest_ask} for the {item}; {ceiling_note}. No deal."
    if outcome == "over budget":
        return 3, f"Polite, but the lowest ask for the {item} was {lowest_ask}, above my budget of {budget}. No deal."
    if outcome == "rejected":
        return 2, f"The shop ended the negotiation for the {item} without a deal (lowest ask {lowest_ask})."
    return 2, f"No deal for the {item}: {outcome}."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    start = time.time()
    shops = [s for s in json.load(open(a.shops, encoding="utf-8")) if s.get("url")]
    mem = load_memory()
    log("buyer", f"harness: {harness.HARNESS_CMD}; {a.item}, budget {a.budget}, {len(shops)} shops, "
                 f"memory {len(mem['trades'])} trades, {len(mem['dishonest'])} dishonest")

    pick, why, skipped, views = choose(shops, mem, a.item, a.budget)
    print(f"WHY: picked {pick['name']} ({views[pick['name']]['text']}; memory: {views[pick['name']]['mem']}). {why}"
          + (f" Not entered: {', '.join(skipped)}." if skipped else ""), flush=True)

    listed = {s["name"] for s in shops}
    elsewhere = best_elsewhere(mem, a.item, listed, pick["name"])
    cap = a.budget
    ceiling_note = ""
    if elsewhere and elsewhere[0] < a.budget:
        cap = elsewhere[0]
        ceiling_note = f"{elsewhere[1]} offered it for {elsewhere[0]}"
        log("buyer", f"ceiling {cap}: cheaper elsewhere ({ceiling_note}); I will not pay more here")

    outcome, price, rec, asks, agreed, history, tid = "no answer", None, None, [], None, [], None
    try:
        seller = Seller(pick["url"])
        t = seller.send(f"Hi! I'd like to buy a {a.item}. What's your best price?")
        tid, ctx = t.get("id"), t.get("contextId")
        for _ in range(MAX_ROUNDS):
            if state(t) not in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_UNKNOWN") or time.time() - start > DEADLINE:
                break
            ask = read_ask(t)[0]
            if ask is not None:
                asks.append(ask)
            action, offer, ask2, say = next_move(
                "buyer", f"a {a.item}", cap, history, t, chat=True,
                ledger=(f"Another shop offered this item for {cap}, so do not pay more than {cap} here."
                        if ceiling_note else ""))
            if ask2 is not None and ask2 not in asks:
                asks.append(ask2)
            if action == "walk":
                break
            accepting = ask2 is not None and offer >= ask2
            text = (f"{say + ' ' if say else ''}{'Deal, I will take it at' if accepting else 'I can offer'} {offer}.")
            agreed = offer
            t = seller.send(text, tid, ctx)
            tid, ctx = t.get("id") or tid, t.get("contextId") or ctx
        if state(t) == "TASK_STATE_COMPLETED":
            rec = receipt(t)
            price = as_int(rec.get("price"))
            outcome = "deal"
        elif state(t) == "TASK_STATE_REJECTED":
            outcome = "rejected"
        else:
            low = min(asks) if asks else None
            outcome = "too dear" if ceiling_note and low is not None and low > cap and low <= a.budget else (
                "over budget" if low is not None and low > cap else "walked away")
            seller.cancel(tid)
    except (RpcError, Exception) as e:
        log("buyer", f"trade failed: {e!r}")
        outcome = f"error ({type(e).__name__})"

    bad, void = receipt_problems(rec, a.item, a.budget, agreed, tid, mem) if outcome == "deal" else ([], "")
    honest = not bad
    if rec and rec.get("task"):
        mem["seen_tasks"] = (mem.get("seen_tasks", []) + [rec["task"]])[-SEEN_TASKS:]
    if void:
        log("buyer", f"UNSIGNED: {pick['name']}: {void}; remembered, the deal does not count in Round 2")
    lowest = min(asks) if asks else None
    stars, review = stars_and_review(outcome, a.item, price, a.budget, agreed, lowest,
                                     len([h for h in history if h[0] == "you offered"]), ceiling_note, honest, void, "; ".join(bad))
    if not honest:
        mem["dishonest"][pick["name"]] = f"{'; '.join(bad)} (a {a.item})"
        log("buyer", f"DISHONEST: {pick['name']}: {'; '.join(bad)}; remembered")
    mem["trades"].append({"t": int(time.time()), "shop": pick["name"], "item": a.item, "budget": a.budget,
                          "outcome": outcome, "price": price, "asks": asks, "agreed": agreed, "honest": honest,
                          "signed": not void, "void": void})
    save_memory(mem)
    log("buyer", f"done: {outcome}" + (f" at {price}" if price is not None else "")
                 + f", {time.time() - start:.0f} s, harness calls {harness.CALLS['n']}")
    print(json.dumps({"shop": pick["url"], "receipt": rec if outcome == "deal" else None,
                      "stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # still end with a result line the kit and the test can read
        log("buyer", f"error: {e!r}")
        try:
            first = json.load(open(sys.argv[sys.argv.index("--shops") + 1], encoding="utf-8"))[0]["url"]
        except Exception:
            first = ""
        print(json.dumps({"shop": first, "receipt": None, "stars": 2, "review": "No deal: my buyer hit an error."}))
        sys.exit(1)
