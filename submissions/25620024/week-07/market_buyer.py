"""Week 07 — market_buyer.py: picks a shop from the market's list, trades over A2A, checks the receipt,
remembers the trade, and writes the review.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The kit runs this for every order from Round 1 on. {shops} is a JSON list of
{"name", "url", "reviews": [{"stars", "review", "round"}]}, newest review first.

  memory   memory.jsonl next to this file, one line per trade:
           {"ts", "shop", "item", "budget", "first_ask", "agreed", "receipt_price", "outcome", "signed",
            "stars", "problem"}. Shops are remembered by name (their URL changes when their kit restarts).
  filter   code drops shops my own memory marks as bad: a receipt above the agreed price, a receipt
           for another item, or two failed connections. If that leaves nothing, every shop stays in.
  choose   the harness reads one summary line per remaining shop (its reviews, compressed by code,
           and my own history with it) and answers with a shop number and a reason. If the harness
           fails, code takes the shop with the best score.
  trade    the week-06 negotiation (buyer.py helpers), with two more guards in code: the first offer
           is at most 70% of the opening ask, and each later offer closes at most half the gap to the ask.
           No offer above the budget, ever.
  receipt  code compares the receipt with the deal: the price we agreed (the last number we sent),
           the item, the A2A task id, and whether it carries sig_seller.
  review   stars and text are written by code from those checks, so the last line is always valid JSON.
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

from buyer import HEADERS, LOW_OFFERS_BEFORE_TAKING, MAX_OFFERS, TIME_LIMIT, asking_price, decide, log, rpc, send, \
    state, status_text
from harness import ask

MEMORY = Path(os.environ.get("MARKET_BUYER_MEMORY", Path(__file__).parent / "memory.jsonl"))   # tests use another file
FIRST_OFFER_MAX = 0.70        # first offer at most 70% of the opening ask
BAD_WORDS = re.compile(r"receipt (said|says)|overcharg|higher than (the price )?(we )?agreed|did not answer|scam|fake",
                       re.I)

CHOOSE_PROMPT = """You are a buyer agent choosing a shop for one purchase: a {item}, budget {budget}.
Some shops in this market are dishonest: they may write a higher price on the receipt than the price
agreed, or have fake reviews. Each line below summarizes one shop: its recent reviews and my own past
trades with it.
{lines}
Pick the shop most likely to sell at a fair price and issue an honest receipt.
Reply with one JSON object only: {{"pick": <shop number>, "reason": "<one short sentence>"}}"""


# --- memory

def load_memory():
    if not MEMORY.exists():
        return []
    out = []
    for line in MEMORY.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def remember(entry):
    with MEMORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def my_history(memory, name):
    rows = [m for m in memory if m.get("shop") == name]
    flags = [m["problem"] for m in rows if m.get("problem")]
    deals = [m for m in rows if m.get("outcome") == "deal"]
    return rows, deals, flags


def blocked(memory, name):
    _, _, flags = my_history(memory, name)
    cheats = [f for f in flags if f.startswith(("receipt above", "receipt for"))]
    unreachable = [f for f in flags if f.startswith("no answer")]
    if cheats:
        return f"my memory: {cheats[0]}"
    if len(unreachable) >= 2:
        return f"my memory: no answer {len(unreachable)} times"
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
        paid = [d["agreed"] / d["budget"] for d in deals if d.get("budget")]
        mine = f"my trades: {len(rows)}, deals {len(deals)}"
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
    return s + 0.2 * len(deals) - len(flags)


def choose(shops, item, budget, memory):
    for s in shops:
        why = blocked(memory, s["name"])
        if why:
            log(f"[filter] skip {s['name']}: {why}")
    allowed = [s for s in shops if not blocked(memory, s["name"])] or shops
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
    """Week-06 buyer loop with two more guards. Returns (task, first_ask, agreed, counters)."""
    card = httpx.get(card_url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    url = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")["url"]
    log(f"card: {card['name']} | JSONRPC {url} | buying {item} with budget {budget}")
    task = send(url, f"I want to buy a {item}")
    task_id = task["id"]
    history = [f"buyer: I want to buy a {item}", f"seller: {status_text(task)}"]
    first_ask, last_offer, sent, low_offers, counters = asking_price(task), None, None, 0, 0

    for _ in range(MAX_OFFERS):
        if state(task) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask_price = asking_price(task)
        if ask_price is None:
            log("   no price in the seller's message; leaving")
            break
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
        last_offer = sent = price
        history.append(f"buyer: {price}")
        task = send(url, str(price), task_id)
        history.append(f"seller: {status_text(task)}")
        if state(task) == "TASK_STATE_INPUT_REQUIRED":
            counters += 1

    if state(task) == "TASK_STATE_INPUT_REQUIRED":
        task = rpc(url, "CancelTask", {"id": task_id})
        log(f"-> CancelTask (out of offers) <- {state(task)}")
    return task, first_ask, sent, counters


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


def review_for(outcome, item, budget, first_ask, agreed, rec, problem, counters):
    if outcome == "no answer":
        return 1, "The shop did not answer; no trade."
    if problem:
        return 1, problem[0].upper() + problem[1:] + "."
    if outcome != "deal":
        if first_ask is not None and first_ask > budget:
            return 3, f"No deal: asked {first_ask} for the {item}, would not come down to my budget."
        return 3, f"No deal for the {item}."
    paid = rec.get("price")
    stars = 5 if paid <= 0.9 * budget else 4
    note = "signed receipt matched" if rec.get("sig_seller") else "receipt matched but unsigned"
    if not rec.get("sig_seller"):
        stars -= 1
    return stars, f"Paid {paid:g} for the {item} (asked {first_ask}) after {counters} counters; {note}."


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

    task, first_ask, agreed, counters, rec, problem = None, None, None, 0, None, None
    try:
        task, first_ask, agreed, counters = negotiate(shop["url"], a.item, a.budget, started)
        outcome = "deal" if state(task) == "TASK_STATE_COMPLETED" else "no deal"
    except Exception as e:
        log(f"buyer error: {type(e).__name__}: {e}")
        outcome, problem = "no answer", f"no answer ({type(e).__name__})"

    if outcome == "deal":
        rec = receipt_of(task)
        if rec is None:
            outcome, problem = "no deal", "completed without a receipt"
        else:
            problem = check_receipt(rec, task, a.item, agreed)
            log(f"[receipt] {rec}  agreed {agreed}: {problem or 'matches the deal'}")
    stars, review = review_for(outcome, a.item, a.budget, first_ask, agreed, rec, problem, counters)

    remember({"ts": round(time.time(), 1), "shop": shop["name"], "item": a.item, "budget": a.budget,
              "first_ask": first_ask, "agreed": agreed, "receipt_price": rec.get("price") if rec else None,
              "outcome": outcome, "signed": bool(rec and rec.get("sig_seller")), "stars": stars,
              "problem": problem})
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
