"""Week 07 — market_buyer.py: picks a shop from the market's list, trades over A2A, checks the receipt,
remembers the trade, and writes the review.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The kit runs this for every order from Round 1 on. {shops} is a JSON list of
{"name", "url", "reviews": [{"stars", "review", "round"}]}, newest review first.

  memory   memory.jsonl next to this file, one line per trade:
           {"ts", "shop", "item", "budget", "first_ask", "agreed", "receipt_price", "outcome", "signed",
            "stars", "problem"}. Shops are remembered by name (their URL changes when their kit restarts).
  filter   code drops shops my own memory marks as bad: a receipt above the agreed price, a receipt
           for another item (for good), or no answer in 2 of my last 5 trades within 6 hours (for now).
           If that leaves nothing, every shop stays in.
  signed   Round 2 counts only receipts signed by the shop. Shops that sold to me two or more times and
           never signed are dropped when others are left, and each summary line shows how many of my
           receipts from that shop were signed.
  choose   the harness reads one summary line per remaining shop (its reviews, compressed by code,
           and my own history with it) and answers with a shop number and a reason. If the harness
           fails, code takes the shop with the best score.
  trade    the week-06 negotiation (buyer.py helpers), with two more guards in code: the first offer
           is at most 70% of the opening ask, and each later offer closes at most half the gap to the ask.
           No offer above the budget, ever.
  receipt  code compares the receipt with the deal: the price we agreed (the last number we sent),
           the item, the A2A task id, and whether it carries sig_seller.
  honesty  code also flags a shop that asks an absurd price (more than 2x the item's floor), raises its
           ask mid-negotiation, names another item or price in its deal message, or replies without a
           price; its review is clearly negative (1 star, 2 if an absurd opening still ended honestly)
           and my memory filter avoids it from then on.
  review   stars come from code and rate what the shop did (an honest receipt, conceding step by step),
           not whether my budget was enough. The text is the harness's wording of facts code collected
           (the ask trail, what I paid, the receipt check); a text with a number that is not in the facts
           is thrown away and the facts themselves are used, so the review never invents anything.
           A receipt that does not match the deal is reported as null (with a 1-star review), so the
           kit never adds our signature to an overcharge.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

from buyer import HEADERS, LOW_OFFERS_BEFORE_TAKING, MAX_OFFERS, TIME_LIMIT, decide, log, rpc, send, \
    state, status_text
from harness import ask

MEMORY = Path(os.environ.get("MARKET_BUYER_MEMORY", Path(__file__).parent / "memory.jsonl"))   # tests use another file
FIRST_OFFER_MAX = 0.70        # first offer at most 70% of the opening ask
FLOOR = {"lamp": 100, "chair": 180, "bookshelf": 260}   # every shop's reserve (week 06); an ask above
ABSURD = 2.0                                            # ABSURD x floor is not a real price
BAD_WORDS = re.compile(r"receipt (said|says)|overcharg|higher than (the price )?(we )?agreed|did not answer|scam|fake",
                       re.I)

CHOOSE_PROMPT = """You are a buyer agent choosing a shop for one purchase: a {item}, budget {budget}.
Some shops in this market are dishonest: they may write a higher price on the receipt than the price
agreed, or have fake reviews. Each line below summarizes one shop: its recent reviews and my own past
trades with it.
{lines}
Only deals with a receipt signed by the shop count in the market, so prefer shops whose receipts
I have seen signed. Pick the shop most likely to sell at a fair price and issue an honest, signed receipt.
Reply with one JSON object only: {{"pick": <shop number>, "reason": "<one short sentence>"}}"""


# --- reading the shop's ask

ASK_WORDS = re.compile(r"(?:asking(?: price)?|now asking|counteroffer|counter-offer|current ask|ask is|price is|is now)"
                       r"\W{0,3}(\d+)", re.I)


def asking_price(task):
    """The shop's ask. The week-06 rule "last number in the message" misread a shop that wrote
    'asking 108 ... we sell at our floor price 100' as an ask of 100 (and then called its 104 counter
    a raised ask). Order now: the A2A metadata {"ask": N}, then the number after 'asking' / 'counteroffer'
    / ..., and only then the last number."""
    msg = task.get("status", {}).get("message", {}) or {}
    meta = (msg.get("metadata") or {}).get("ask")
    if isinstance(meta, (int, float)):
        return int(meta)
    text = status_text(task)
    named = ASK_WORDS.findall(text)
    if named:
        return int(named[-1])
    nums = re.findall(r"\d+", text)
    return int(nums[-1]) if nums else None


# --- memory

def load_memory():
    """Every line of memory.jsonl, minus lines a later {"retracts": <ts>} line withdrew (our own mistakes)."""
    if not MEMORY.exists():
        return []
    out = []
    for line in MEMORY.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    withdrawn = {m["retracts"] for m in out if "retracts" in m}
    return [m for m in out if "retracts" not in m and m.get("ts") not in withdrawn]


def remember(entry):
    with MEMORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def my_history(memory, name):
    rows = [m for m in memory if m.get("shop") == name]
    flags = [m["problem"] for m in rows if m.get("problem")]
    deals = [m for m in rows if m.get("outcome") == "deal"]
    return rows, deals, flags


# A shop that did not answer is skipped only while that is recent: at least NO_ANSWER_MIN of my last
# NO_ANSWER_LAST trades with it got no answer, the latest one within NO_ANSWER_HOURS. Counting every
# no-answer ever (the first version) blocked ggari-shop for good after two timeouts in 1,866 deals.
# Cheating (an inflated receipt, a raised ask, an absurd price) still blocks for good.
NO_ANSWER_LAST, NO_ANSWER_MIN, NO_ANSWER_HOURS = 5, 2, 6


def blocked(memory, name, now=None):
    rows, _, flags = my_history(memory, name)
    cheats = [f for f in flags if f.startswith(("receipt above", "receipt for", "misbehaved"))]
    if cheats:
        return f"my memory: {cheats[0]}"
    recent = sorted(rows, key=lambda r: r.get("ts") or 0)[-NO_ANSWER_LAST:]
    silent = [r for r in recent if r.get("outcome") == "no answer"]
    now = now or time.time()
    if len(silent) >= NO_ANSWER_MIN and now - (silent[-1].get("ts") or 0) < NO_ANSWER_HOURS * 3600:
        return f"my memory: no answer in {len(silent)} of my last {len(recent)} trades, latest {int((now - silent[-1]['ts']) / 60)} min ago"
    return None


# --- reviews and choosing

def summary(i, shop, memory):
    revs = shop.get("reviews") or []
    if revs:
        avg = sum(r.get("stars", 0) for r in revs) / len(revs)
        bad = [r["review"] for r in revs if BAD_WORDS.search(r.get("review", ""))]
        rev = f"{len(revs)} reviews, avg {avg:.1f} stars, {len(bad)} complain about receipts or overcharging"
        rev += f'; newest: "{revs[0].get("review", "")[:80]}"'
    else:
        rev = "no reviews yet"
    rows, deals, flags = my_history(memory, shop["name"])
    if rows:
        paid = [d["agreed"] / d["budget"] for d in deals if d.get("budget") and d.get("agreed")]
        mine = f"my trades: {len(rows)}, deals {len(deals)}"
        if deals:
            mine += f", signed receipts {sum(bool(d.get('signed')) for d in deals)}/{len(deals)}"
        if paid:
            mine += f", paid {sum(paid) / len(paid):.0%} of budget on average"
        if flags:
            mine += f", problems: {'; '.join(flags[-2:])}"
    else:
        mine = "never traded with it"
    return f"{i}. {shop['name']}: {rev}. {mine}."


def score(shop, memory):
    revs = shop.get("reviews") or []
    s = 3.0 if not revs else sum(r.get("stars", 0) for r in revs) / len(revs)
    s -= sum(1 for r in revs if BAD_WORDS.search(r.get("review", "")))
    _, deals, flags = my_history(memory, shop["name"])
    signed = sum(bool(d.get("signed")) for d in deals)
    return s + 0.2 * len(deals) + 0.5 * signed - 0.5 * (len(deals) - signed) - len(flags)


def unsigned_only(memory, name):
    """Two or more deals with this shop and not one signed receipt: in Round 2 its deals do not count."""
    _, deals, _ = my_history(memory, name)
    return len(deals) >= 2 and not any(d.get("signed") for d in deals)


def choose(shops, item, budget, memory):
    for s in shops:
        why = blocked(memory, s["name"])
        if why:
            log(f"[filter] skip {s['name']}: {why}")
    allowed = [s for s in shops if not blocked(memory, s["name"])] or shops
    # Round 2 counts only signed receipts: drop shops that never signed for me, if others are left
    signing = [s for s in allowed if not unsigned_only(memory, s["name"])]
    if signing and len(signing) < len(allowed):
        log(f"[filter] skip unsigned-only shops: {', '.join(s['name'] for s in allowed if s not in signing)}")
        allowed = signing
    if len(allowed) == 1:
        return allowed[0], "the only shop left after my memory filter"
    lines = "\n".join(summary(i + 1, s, memory) for i, s in enumerate(allowed))
    log("[shops]\n" + lines)
    try:
        answer = ask(CHOOSE_PROMPT.format(item=item, budget=budget, lines=lines))
        pick = int(answer["pick"])
        if not 1 <= pick <= len(allowed):
            raise ValueError(f"pick {pick} out of range")
        return allowed[pick - 1], str(answer.get("reason", "")).strip() or "harness pick"
    except Exception as e:
        best = max(allowed, key=lambda s: score(s, memory))
        return best, f"harness failed ({e}); best score by code"


# --- trading

def negotiate(card_url, item, budget, started):
    """Week-06 buyer loop with two more guards. Returns (task, asks, offers): every ask the shop made and
    every price we sent, oldest first."""
    card = httpx.get(card_url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    url = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")["url"]
    log(f"card: {card['name']} | JSONRPC {url} | buying {item} with budget {budget}")
    task = send(url, f"I want to buy a {item}")
    task_id = task["id"]
    history = [f"buyer: I want to buy a {item}", f"seller: {status_text(task)}"]
    asks, offers, notes, last_offer, low_offers = [], [], [], None, 0

    for _ in range(MAX_OFFERS):
        if state(task) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask_price = asking_price(task)
        if ask_price is None:
            log("   no price in the seller's message; leaving")
            notes.append(f'replied without a price: "{status_text(task)[:60]}"')
            break
        asks.append(ask_price)
        if time.time() - started > TIME_LIMIT:
            action, price = ("accept", ask_price) if ask_price <= budget else ("walk_away", 0)
            log(f"   time limit: {action}")
        else:
            action, price = decide(item, budget, history, ask_price)
            log(f"   harness: {json.dumps({'action': action, 'price': price})} (ask {ask_price}, budget {budget})")

        # week-06 guards
        if action == "accept" and ask_price > budget:
            log(f"   GUARD accept at {ask_price} > budget {budget}: offering {budget} instead")
            action, price = "offer", budget
        if action == "offer":
            # week-07 guards: do not give the budget away early
            if last_offer is None and price > FIRST_OFFER_MAX * ask_price:
                cap = int(FIRST_OFFER_MAX * ask_price)
                log(f"   GUARD first offer {price} > {FIRST_OFFER_MAX:.0%} of ask {ask_price}: lowered to {cap}")
                price = cap
            elif last_offer is not None and price > last_offer + max(5, (ask_price - last_offer) // 2):
                cap = last_offer + max(5, (ask_price - last_offer) // 2)
                log(f"   GUARD offer {price} jumps more than half the gap from {last_offer} to {ask_price}: {cap}")
                price = cap
            if price > budget:
                log(f"   GUARD offer {price} > budget {budget}: lowered to {budget}")
                price = budget
            if price >= ask_price:
                log(f"   GUARD offer {price} >= ask {ask_price}: offering the ask itself")
                action, price = "accept", ask_price
            elif price <= 0:
                log(f"   GUARD offer {price} is not a price: walking away")
                action = "walk_away"
        if action == "offer" and ask_price <= budget:
            low_offers += 1
            if low_offers > LOW_OFFERS_BEFORE_TAKING:
                log(f"   GUARD {low_offers - 1} low offers already while ask {ask_price} is affordable: taking it")
                action, price = "accept", ask_price
        if action not in ("accept", "offer", "walk_away"):
            log(f"   GUARD unknown action {action!r}: walking away")
            action = "walk_away"

        if action == "walk_away":
            task = rpc(url, "CancelTask", {"id": task_id})
            log(f"-> CancelTask (walked away) <- {state(task)}")
            break
        if action == "accept":
            price = ask_price
        last_offer = price
        offers.append(price)
        history.append(f"buyer: {price}")
        task = send(url, str(price), task_id)
        history.append(f"seller: {status_text(task)}")

    if state(task) == "TASK_STATE_INPUT_REQUIRED":
        task = rpc(url, "CancelTask", {"id": task_id})
        log(f"-> CancelTask (out of offers) <- {state(task)}")
    texts = [h[len("seller: "):] for h in history if h.startswith("seller: ")]
    return task, asks, offers, texts, notes


def misbehaviour(item, asks, offers, texts, outcome):
    """Things an honest shop does not do: an absurd ask, an ask that goes up, a deal message that
    names another item or another price than the one agreed. Returns (issues, worst) where worst is
    'hallucination' (said something that did not happen), 'absurd', or None."""
    issues, worst = [], None
    floor = FLOOR.get(item)
    if floor and asks and max(asks) > ABSURD * floor:
        issues.append(f"absurd price: asked {max(asks)} for a {item}")
        worst = "absurd"
    for x, y in zip(asks, asks[1:]):
        if y > x:
            issues.append(f"raised its ask from {x} to {y} mid-negotiation")
            worst = "hallucination"
            break
    for t in texts:
        m = re.search(r"deal\W+(?:\w+\W+){0,3}?(\w+) at (\d+)", t, re.I)
        if m and outcome == "deal":
            said_item, said_price = m.group(1).lower(), int(m.group(2))
            if said_item in FLOOR and said_item != item:
                issues.append(f'its deal message names a {said_item}, I ordered a {item}')
                worst = "hallucination"
            if offers and said_price != offers[-1]:
                issues.append(f"its deal message says {said_price}, we agreed on {offers[-1]}")
                worst = "hallucination"
    return issues, worst


def receipt_of(task):
    for a in task.get("artifacts") or []:
        for p in a.get("parts") or []:
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def check_receipt(rec, task, item, agreed):
    """What is wrong with the receipt, or None. 'agreed' is the last price we sent."""
    price = rec.get("price")
    if rec.get("item") not in (None, item):
        return f"receipt for {rec.get('item')}, ordered {item}"
    if agreed is not None and isinstance(price, (int, float)) and price > agreed:
        return f"receipt above agreed price: agreed {agreed}, receipt says {price:g}"
    if rec.get("task") not in (None, task.get("id")):
        return "receipt names another task"
    return None


REVIEW_PROMPT = """Write a short, honest customer review (one or two sentences, under 200 characters)
of a shop, from a buyer agent's own experience. Use only these facts; do not invent anything,
Keep the prices from the facts (they are what makes a review useful). Do not mention the buyer's budget,
and do not use any number that is not in the facts.
Facts: {facts}
Reply with one JSON object only: {{"review": "<text>"}}"""


def facts_and_stars(outcome, item, asks, offers, rec, problem, issues=(), worst=None):
    """Stars rate what the shop did, not whether my budget was enough. Returns (stars, facts)."""
    first, last = (asks[0], asks[-1]) if asks else (None, None)
    trail = " -> ".join(map(str, asks))
    if outcome == "no answer":
        return 1, f"The shop's card or endpoint did not answer ({problem}). No trade happened."
    if issues and not (problem and problem.startswith("receipt")):
        # absurd prices and made-up statements are clearly negative: 1 star, or 2 when an absurd
        # opening still ended in an honest deal with a matching receipt
        paid = f" In the end I paid {rec.get('price'):g} and the receipt matched." if outcome == "deal" and rec else ""
        stars = 2 if worst == "absurd" and outcome == "deal" and len(issues) == 1 else 1
        return stars, f"Item {item}. Avoid: {'; '.join(issues)}. Its asks: {trail}.{paid}"
    if problem:   # a receipt that does not match the deal
        return 1, (f"Item {item}. We agreed on {offers[-1] if offers else '?'}, but the receipt says "
                   f"{rec.get('price'):g}" + (" (receipt for " + str(rec.get('item')) + ")" if rec.get('item') != item else "")
                   + ". The receipt does not match the deal.")
    if outcome == "deal":
        paid = rec.get("price")
        signed = bool(rec.get("sig_seller"))
        moved = f"its ask went {trail}" if len(asks) > 1 else f"it asked {first}"
        return (5 if signed else 4), (f"Item {item}. {moved}; I paid {paid:g}. The receipt shows exactly the agreed "
                                      f"price" + (" and is signed by the shop." if signed else ", but it is not signed."))
    # no deal: the shop is fair if it came down step by step; a budget below its floor is not its fault
    if len(asks) > 1 and last < first:
        return 4, (f"Item {item}. No deal: its ask came down {trail}, still above what I could pay. "
                   f"It negotiated in good faith.")
    if asks:
        return 3, f"Item {item}. No deal: it asked {first} and did not come down ({trail})."
    return 3, f"Item {item}. No deal; the shop ended the talk."


def review_for(outcome, item, asks, offers, rec, problem, issues=(), worst=None):
    stars, facts = facts_and_stars(outcome, item, asks, offers, rec, problem, issues, worst)
    allowed = set(re.findall(r"\d+", facts))
    try:
        text = str(ask(REVIEW_PROMPT.format(facts=facts)).get("review", "")).strip()
        made_up = [n for n in re.findall(r"\d+", text) if n not in allowed]
        if not text or len(text) > 240 or made_up:
            raise ValueError(f"rejected review {text!r} (numbers not in the facts: {made_up})")
    except Exception as e:
        log(f"[review] harness text not used ({e}); writing it from the facts")
        text = facts
    return stars, text


# --- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    started = time.time()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    shop, reason = choose(shops, a.item, a.budget, memory)
    log(f"[choose] {shop['name']} {shop['url']}: {reason}")

    task, asks, offers, texts, notes, rec, problem = None, [], [], [], [], None, None
    try:
        task, asks, offers, texts, notes = negotiate(shop["url"], a.item, a.budget, started)
        outcome = "deal" if state(task) == "TASK_STATE_COMPLETED" else "no deal"
    except Exception as e:
        log(f"buyer error: {type(e).__name__}: {e}")
        outcome, problem = "no answer", f"no answer ({type(e).__name__})"

    if outcome == "deal":
        rec = receipt_of(task)
        if rec is None:
            outcome, problem = "no deal", "completed without a receipt"
        else:
            agreed = offers[-1] if offers else None
            problem = check_receipt(rec, task, a.item, agreed)
            log(f"[receipt] {rec}  agreed {agreed}: {problem or 'matches the deal'}")
    issues, worst = misbehaviour(a.item, asks, offers, texts, outcome)
    issues += notes
    if notes:
        worst = worst or "hallucination"
    if issues:
        log(f"[misbehaviour] {worst}: {'; '.join(issues)}")
    stars, review = review_for(outcome, a.item, asks, offers, rec, problem, issues, worst)
    log(f"[review] {stars} stars: {review}")

    remember({"ts": round(time.time(), 1), "shop": shop["name"], "item": a.item, "budget": a.budget,
              "first_ask": asks[0] if asks else None, "last_ask": asks[-1] if asks else None,
              "last_offer": offers[-1] if offers else None,
              "agreed": offers[-1] if outcome == "deal" and offers else None,
              "receipt_price": rec.get("price") if rec else None,
              "outcome": outcome, "signed": bool(rec and rec.get("sig_seller")), "stars": stars,
              "problem": problem or (f"misbehaved ({worst}): " + "; ".join(issues) if issues else None)})
    log(f"result: {outcome}, {time.time() - started:.0f} s")
    # a receipt that does not match the deal is not handed on: the kit would add sig_buyer to it and the
    # market would count the shop's overcharge as a fair trade. The 1-star review says what happened.
    report = None if problem and problem.startswith(("receipt above", "receipt for", "receipt names")) else rec
    if rec is not None and report is None:
        log("[receipt] not reported to the market: it does not match the deal")
    print(json.dumps({"shop": shop["url"], "receipt": report, "stars": stars, "review": review}, ensure_ascii=False),
          flush=True)


if __name__ == "__main__":
    main()
