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
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import httpx

import buyer
from buyer import HEADERS, MAX_OFFERS, TIME_LIMIT, decide, log, rpc, send, \
    state, status_text
from harness import ask
import buyer_memory

MEMORY = Path(os.environ.get("MARKET_BUYER_MEMORY", Path(__file__).parent / "memory.jsonl"))   # tests use another file
T = buyer_memory.theta()      # the tunable numbers; the meta-harness (meta_harness.py) writes them to theta.json
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
    text = re.sub(r"(?<=\d),(?=\d{3})", "", status_text(task))     # Round 3 prices in won: "8,550"
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
# Cheating is graded (buyer_memory.sanction): an inflated receipt blocks for good, a raised ask pauses the
# shop and blocks it only the second time. Before this, one raised ask blocked ggari-shop for good again.
NO_ANSWER_LAST, NO_ANSWER_MIN, NO_ANSWER_HOURS = 5, 3, 1   # 2 of 5 within 6 h paused 13+ big shops (11-04)


def blocked(prof, name, now=None):
    """Read from the shop's profile (buyer_memory.py), not from every line of memory.jsonl."""
    p = prof.get(name)
    if not p:
        return None
    level, why = buyer_memory.sanction(p, now)
    if level:
        return f"my memory ({level}): {why}"
    recent = p["recent"][-NO_ANSWER_LAST:]
    silent = [r for r in recent if r[1] == "no answer"]
    now = now or time.time()
    if len(silent) >= T.get("pause_no_answers", NO_ANSWER_MIN) and \
            now - (silent[-1][0] or 0) < T.get("pause_h", NO_ANSWER_HOURS) * 3600:
        return f"my memory: no answer in {len(silent)} of my last {len(recent)} trades, latest {int((now - silent[-1][0]) / 60)} min ago"
    return None


# --- reviews and choosing

def summary(i, shop, prof):
    revs = shop.get("reviews") or []
    if revs:
        avg = sum(r.get("stars", 0) for r in revs) / len(revs)
        bad = [r["review"] for r in revs if BAD_WORDS.search(r.get("review", ""))]
        rev = f"{len(revs)} reviews, avg {avg:.1f} stars, {len(bad)} complain about receipts or overcharging"
        rev += f'; newest: "{revs[0].get("review", "")[:80]}"'
    else:
        rev = "no reviews yet"
    p = prof.get(shop["name"])
    if p and p["trades"]:
        mine = f"my trades: {p['trades']}, deals {p['deals']}"
        if p["deals"]:
            mine += f", signed receipts {p['signed']}/{p['deals']}"
        if p["paid"][1]:
            mine += f", paid {p['paid'][0] / p['paid'][1]:.0%} of budget on average"
        r = buyer_memory.settle_ratio(p)
        if r is not None:
            mine += f", usually settles at {r:.0%} of its first ask"
        if p["problems"]:
            mine += f", problems: {'; '.join(p['problems'][-2:])}"
    else:
        mine = "never traded with it"
    return f"{i}. {shop['name']}: {rev}. {mine}."


def score(shop, prof):
    revs = shop.get("reviews") or []
    s = 3.0 if not revs else sum(r.get("stars", 0) for r in revs) / len(revs)
    s -= sum(1 for r in revs if BAD_WORDS.search(r.get("review", "")))
    p = prof.get(shop["name"]) or buyer_memory._empty()
    # recent defect rate, not offenses ever (Amazon's order defect rate): 10% defects costs 2 points
    # deals on a log scale: 0.2 x deals let the shop with the most past deals win every time the harness failed
    # (corner-store got 82% of Round 2 picks on 2026-11-01)
    return s + math.log1p(p["deals"]) + (1.0 if p["deals"] and p["signed"] == p["deals"] else -1.0 if p["deals"] else 0) \
        - 20 * buyer_memory.defect_rate(p)


def unsigned_only(prof, name):
    """Two or more deals with this shop and not one signed receipt: in Round 2 its deals do not count."""
    p = prof.get(name)
    return bool(p) and p["deals"] >= 2 and p["signed"] == 0


def choose(shops, item, budget, prof):
    for s in shops:
        why = blocked(prof, s["name"])
        if why:
            log(f"[filter] skip {s['name']}: {why}")
    allowed = [s for s in shops if not blocked(prof, s["name"])] or shops
    # Round 2 counts only signed receipts: drop shops that never signed for me, if others are left
    signing = [s for s in allowed if not unsigned_only(prof, s["name"])]
    if signing and len(signing) < len(allowed):
        log(f"[filter] skip unsigned-only shops: {', '.join(s['name'] for s in allowed if s not in signing)}")
        allowed = signing
    if len(allowed) == 1:
        return allowed[0], "the only shop left after my memory filter"
    lines = "\n".join(summary(i + 1, s, prof) for i, s in enumerate(allowed))
    log("[shops]\n" + lines)
    try:
        answer = ask(CHOOSE_PROMPT.format(item=item, budget=budget, lines=lines))
        pick = int(answer["pick"])
        if not 1 <= pick <= len(allowed):
            raise ValueError(f"pick {pick} out of range")
        return allowed[pick - 1], str(answer.get("reason", "")).strip() or "harness pick"
    except Exception as e:
        best = max(allowed, key=lambda s: score(s, prof))
        return best, f"harness failed ({e}); best score by code"


# --- trading

def code_decide(item, budget, history, ask_price):
    """Round 3 moves without a model call: always ask for the most, and let the guards below bring it down to a
    safe step (the market low first, then a fixed share of the gap, then the ask once enough low offers were
    made). The free models were rate-limited upstream on about 45% of calls (2026-10-31), and every failed call
    cost a turn; the guards were already deciding most moves."""
    if ask_price <= budget and history and re.search(r"\bfinal\b", history[-1], re.I):
        return "accept", ask_price          # a shop's last word within budget: more offers only risk the deal
    return ("offer", ask_price) if ask_price <= budget else ("offer", budget)


def negotiate(card_url, item, budget, started, ratio=None, low=None, opened=None, decide_fn=None):
    """Week-06 buyer loop with two more guards. Returns (task, asks, offers): every ask the shop made and
    every price we sent, oldest first. low is the lowest price I agreed for this item lately at any shop
    (the market floor): an ask at or below it is taken at once, and my first offer is that price, not a
    share of the ask. A shop whose ask stays above my budget and does not move is left after two asks.
    opened = (url, task, opening) continues a task the Round 3 quote round already opened."""
    if opened:
        url, task, opening = opened
    else:
        card = httpx.get(card_url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
        url = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")["url"]
        log(f"card: {card['name']} | JSONRPC {url} | buying {item} with budget {budget}")
        opening = f"I want to buy a {item}"
        task = send(url, opening)
    task_id = task["id"]
    history = [f"buyer: {opening}", f"seller: {status_text(task)}"]
    first_max = T["first_offer_max"]
    if ratio is not None:
        # my memory of this shop: where its deals with me usually ended, as a share of its first ask
        history.insert(0, f"(my memory: this shop usually settles at {ratio:.0%} of its first ask)")
        first_max = min(T["first_offer_max"], max(0.5, ratio - 0.1))
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
        if low and ask_price <= low and ask_price <= budget:
            log(f"   ask {ask_price} is at the market low {low}: taking it, no haggling")
            action, price = "accept", ask_price
        elif len(asks) >= 2 and ask_price > budget and ask_price >= asks[-2]:
            log(f"   ask {ask_price} is above budget {budget} and did not move: walking away")
            action, price = "walk_away", 0
        elif time.time() - started > TIME_LIMIT:
            action, price = ("accept", ask_price) if ask_price <= budget else ("walk_away", 0)
            log(f"   time limit: {action}")
        else:
            action, price = (decide_fn or decide)(item, budget, history, ask_price)
            log(f"   {'code' if decide_fn else 'harness'}: {json.dumps({'action': action, 'price': price})}"
                f" (ask {ask_price}, budget {budget})")

        # week-06 guards
        if action == "accept" and ask_price > budget:
            log(f"   GUARD accept at {ask_price} > budget {budget}: offering {budget} instead")
            action, price = "offer", budget
        anchor = low if low and low < ask_price and low <= budget else None   # a low above budget is no use
        if action == "walk_away" and last_offer is None and len(asks) == 1:
            # one offer before leaving, even when the harness is down (all free models 429 on 10/29)
            action, price = "offer", anchor or min(budget, int(first_max * ask_price))
            log(f"   GUARD no offer made yet: offering {price} before walking away")
        if action == "accept" and last_offer is None and anchor:
            action = "offer"             # never take a first ask above the market low without trying it
        if action == "offer":
            # week-07 guards: do not give the budget away early
            if last_offer is None and anchor:
                if price != anchor:
                    log(f"   GUARD first offer {price}: offering the market low {anchor} instead")
                price = anchor
            elif last_offer is None and price > first_max * ask_price:
                cap = int(first_max * ask_price)
                log(f"   GUARD first offer {price} > {first_max:.0%} of ask {ask_price}: lowered to {cap}")
                price = cap
            elif last_offer is not None and price > last_offer + max(5, int((ask_price - last_offer) * T["concede"])):
                cap = last_offer + max(5, int((ask_price - last_offer) * T["concede"]))
                log(f"   GUARD offer {price} closes more than {T['concede']:.0%} of the gap from {last_offer} to {ask_price}: {cap}")
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
            if low_offers > T["take_after"]:
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
        t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
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


def review_for(outcome, item, asks, offers, rec, problem, issues=(), worst=None, use_model=True):
    stars, facts = facts_and_stars(outcome, item, asks, offers, rec, problem, issues, worst)
    if not use_model:          # Round 3: review_r3.py writes the review that counts, from the settled facts
        return stars, facts
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

# --- Round 3: a category order, SKUs with list prices, shops with stock

def catalog():
    try:
        rows = json.loads((Path(".market") / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r["sku"]: r for r in rows} if isinstance(rows, list) else rows


def enuri_book():
    """The Enuri team's book (enuri.py, rebuilt by the shop's restock hook); {} when it is missing."""
    try:
        import enuri
        return enuri.book()
    except Exception as e:
        log(f"[enuri] no book: {type(e).__name__}: {e}")
        return {}


def candidates(shops, order, cat, prof, book=None):
    """Every (shop, SKU) that fits the order, best first, by code: what I expect to keep (list price minus the
    price I expect to pay at that shop) times the chance the shop is honest. One SKU per shop.
    Reach: an offer stays in while min_reach x list fits the budget. The old test (r3_first_offer x list) dropped
    every SKU listed above budget / 0.6, while leaders settle near half the list: 21 of 37 orders at 60-120k and
    12 of 24 at 250k+ had no candidate at all, and the 8 deals at 250k+ kept 2.38M (VPS, 2026-11-04).
    book: the Enuri team's shops to avoid are skipped, and its price forecast per (shop, SKU) replaces my own
    list-price ratio when it has one."""
    book = book or {}
    avoid = set(book.get("avoid") or [])
    budget, acceptable = order["budget"], set(order["acceptable"])
    best, why = {}, {"not acceptable": 0, "no stock": 0, "blocked shop": 0, "no list price": 0, "too expensive": 0}
    dear = []
    for s in shops:
        fits = [o for o in s.get("offers") or [] if o.get("sku") in acceptable]
        why["not acceptable"] += len(s.get("offers") or []) - len(fits)
        blk = blocked(prof, s["name"])
        if not blk and s["name"] in avoid:
            blk = "enuri: " + str(((book.get("quality") or {}).get(s["name"]) or {}).get("why") or "avoid")
            log(f"[enuri] skip {s['name']}: {blk[7:]}")
        elif blk:
            log(f"[filter] skip {s['name']}: {blk}")
        if blk:
            why["blocked shop"] += bool(fits)
            continue
        p = prof.get(s["name"])
        ratio = buyer_memory.list_ratio(p) or T["prior_price_ratio"]
        for o in fits:
            lp = (cat.get(o["sku"]) or {}).get("list_price")
            if not o.get("qty"):
                why["no stock"] += 1
            elif not lp:
                why["no list price"] += 1
            elif lp * T["min_reach"] > budget:
                why["too expensive"] += 1
                dear.append(lp)
            else:
                pred = _predict(book, s["name"], o["sku"], order.get("kind"))
                expect = min(pred or lp * ratio, budget)
                keep = (lp - expect) * (1 - buyer_memory.defect_rate(p))
                if s["name"] not in best or keep > best[s["name"]][0]:
                    best[s["name"]] = (keep, s, o, lp)
    if not best:
        log("[r3] no candidates: " + ", ".join(f"{n} {k}" for k, n in why.items())
            + (f" (list {min(dear)}-{max(dear)}, budget {budget}, reach {T['min_reach']})" if dear else ""))
    return sorted(best.values(), key=lambda c: -c[0])


def follow_enuri(order, shops, cat, prof, book):
    """Candidates in the order the Enuri team advises (enuri.advise), minus shops my own memory blocks.
    Returns (cands, advice, {(shop, sku): row}); ([], None, {}) without a book or advice."""
    if not book:
        return [], None, {}
    try:
        import enuri
        adv = enuri.advise(order, shops, book, T, cat)
    except Exception as e:
        log(f"[enuri] no advice: {type(e).__name__}: {e}")
        return [], None, {}
    cands, rows = [], {}
    for r in adv["ranked"]:
        why = blocked(prof, r["shop"])
        if why:
            log(f"[filter] skip {r['shop']}: {why}")
            continue
        rows[(r["shop"], r["sku"])] = r
        cands.append((r["keep"], {"name": r["shop"], "url": r["url"]},
                      {"sku": r["sku"], "name": r.get("name") or r["sku"], "qty": 1}, r["list_price"]))
    log(f"[enuri] advice: {len(adv['ranked'])} ranked, {len(cands)} after my filter; "
        + "; ".join(f"{r['shop']} {r['sku']} expect {r['expected_price']} open {r['target']} max {r['walk_away']}"
                    for r in adv["ranked"][:3]) + (f" | {'; '.join(adv['warnings'])}" if adv["warnings"] else ""))
    return cands, (adv if cands else None), rows


def _predict(book, shop, sku, kind):
    if not book:
        return None
    try:
        import enuri
        return enuri.predict(book, shop, sku, kind)
    except Exception:
        return None


def offer_of(task):
    """A shop's structured offer, if it sent one as a data part (e.g. {"type": "offer", "shelf_hours_left": ...})."""
    msg = (task.get("status") or {}).get("message") or {}
    for part in msg.get("parts") or []:
        d = part.get("data")
        if isinstance(d, dict) and d.get("type") == "offer":
            return d
    return None


NEED = {}          # set by round3: what this order needs, said to every shop up front
SHELF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*h(?:ours?)?\s*(?:of\s+)?shelf|shelf[ -]life[^0-9]{0,25}(\d+(?:\.\d+)?)\s*h", re.I)


def shelf_of(task, off=None):
    """Hours of shelf life the shop says its unit has: the structured offer first, else the reply text; None."""
    if off and off.get("shelf_hours_left") is not None:
        return float(off["shelf_hours_left"])
    msg = (task.get("status") or {}).get("message") or {}
    text = " ".join(p.get("text") or "" for p in msg.get("parts") or [])
    m = SHELF_RE.search(text)
    return float(m.group(1) or m.group(2)) if m else None


def quote(c):
    """Open a task with one shop for one SKU and read its first ask (the quote round runs these in parallel,
    like delegate_task: each one alone, only the result comes back). The opening says what the order needs
    (shelf hours, grades, certificate): a shop that can pick a unit for it will, and the kit will not refuse
    to pay later (the team found deals agreed, then refused: 145 h left, 541 h needed, 2026-11-03)."""
    keep, s, o, lp = c
    try:
        card = httpx.get(s["url"].rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
        url = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")["url"]
        opening = f"I want to buy {o['name']} ({o['sku']})" + (f" | need: {NEED['text']}" if NEED.get("text") else "")
        task = send(url, opening)
        ask = asking_price(task) if state(task) == "TASK_STATE_INPUT_REQUIRED" else None
        off = offer_of(task)
        left = shelf_of(task, off)
        if ask and NEED.get("hours") and left is not None and left < NEED["hours"]:
            rpc(url, "CancelTask", {"id": task["id"]})
            return {"c": c, "error": f"offered unit has {left} h left, need {NEED['hours']} h"}
        if ask and NEED.get("strict") and left is None:     # a long-life need: no stated shelf life, no deal
            rpc(url, "CancelTask", {"id": task["id"]})     # (144 of 153 refused payments were shelf life, 11-04)
            return {"c": c, "error": f"no shelf life stated; this order needs {NEED['hours']} h"}
        return {"c": c, "url": url, "task": task, "opening": opening, "ask": ask}
    except Exception as e:
        return {"c": c, "error": f"{type(e).__name__}: {e}"}


PHANTOMS = Path(__file__).parent / "private" / "enuri" / "phantoms.json"
NO_STOCK = re.compile(r"none of that in stock|no matching (?:unreserved )?stock|not in stock|out of stock|품절|재고(?:가)? 없", re.I)


def remember_phantoms(quotes):
    """A shop that answers 'none in stock' for a SKU the market listed: Enuri skips that (shop, sku) for 30 min."""
    try:
        ph = json.loads(PHANTOMS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ph = {}
    now, hit = time.time(), False
    for q in quotes:
        if q.get("ask") or not q.get("task"):
            continue
        msg = (q["task"].get("status") or {}).get("message") or {}
        text = " ".join(p.get("text") or "" for p in msg.get("parts") or [])
        if NO_STOCK.search(text):
            ph[f"{q['c'][1]['name']}|{q['c'][2]['sku']}"] = now
            hit = True
            try:                                 # the record behind the forum's stock-check posts (forum.py)
                with (PHANTOMS.parent / "phantom_log.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": round(now), "shop": q["c"][1]["name"], "sku": q["c"][2]["sku"]}) + "\n")
            except OSError:
                pass
    if hit:
        ph = {k: v for k, v in ph.items() if now - v < 1800}
        try:
            PHANTOMS.parent.mkdir(parents=True, exist_ok=True)
            PHANTOMS.write_text(json.dumps(ph), encoding="utf-8")
        except OSError:
            pass


AB_MIN = 60000            # HQ 11-04 A/B: orders of this budget and up are split by order id
LOWEST_RE = re.compile(r"(?:our |my )?lowest(?: price)?(?: is|:)?\s*([\d,]+)", re.I)


def ab_arm(order):
    """'B' (Enuri probes first, the buyer buys at what it learned) or 'A' (the buyer negotiates as before) for an
    order of AB_MIN and up; None for smaller orders. Stable per order id."""
    if (order.get("budget") or 0) < AB_MIN:
        return None
    h = int(hashlib.sha256(str(order.get("order") or order.get("item")).encode()).hexdigest()[:8], 16)
    return "B" if h % 2 else "A"


def probe(q):
    """Enuri's scout offer in a quote's open task: 45% of list, to learn the shop's counter or stated lowest. Returns
    (q with the updated task, price learned or None). A shop that takes the scout offer has made a deal: the caller
    keeps it (never left unpaid)."""
    lp = q["c"][3]
    try:
        t2 = send(q["url"], str(int(0.45 * lp)), q["task"]["id"])
    except Exception as e:
        log(f"[probe] {q['c'][1]['name']}: {type(e).__name__}")
        return q, None
    text = status_text(t2)
    m = LOWEST_RE.search(text)
    learned = int(m.group(1).replace(",", "")) if m else (asking_price(t2) if state(t2) == "TASK_STATE_INPUT_REQUIRED" else None)
    log(f"[probe] {q['c'][1]['name']}: offered {int(0.45 * lp)}, {state(t2)}, learned {learned}")
    return dict(q, task=t2, probe_offer=int(0.45 * lp), learned=learned), learned


def accept_policy(order):
    """What the checkout must show before the kit pays. Food that will not last use_within_hours is worth 0."""
    pol = {"cert": True}
    if order.get("kind") == "food":
        pol["min_hours_left"] = round((order.get("use_within_hours") or 0) + T["food_margin_h"], 1)
    else:
        pol["grades"] = list(T["goods_grades"])
    return pol


def round3(a, order, shops, mem, started):
    from concurrent.futures import ThreadPoolExecutor
    prof, cat, book = mem["profiles"], catalog(), enuri_book()
    cands, adv, rows = follow_enuri(order, shops, cat, prof, book)
    if not cands:                       # no book, or no advice: my own ranking (it logs why nothing fits)
        cands, adv = candidates(shops, order, cat, prof, book), None
    rfq = T["rfq_big"] if order["budget"] >= T["big_budget"] else T["rfq"]      # big orders: ask more shops
    try:
        import market_model          # the buyer is the shop's sensor: what this order showed goes in the shared model
        market_model.remember_seen(order, shops)
    except Exception as e:
        log(f"[r3] market model not updated: {type(e).__name__}: {e}")
    log(f"[r3] order {order.get('item')} ({order.get('kind')}), budget {order['budget']}, "
        f"{len(order['acceptable'])} SKUs fit, {len(cands)} shop offers")
    for keep, s, o, lp in cands[:rfq]:
        log(f"[r3]   {s['name']}: {o['name']} ({o['sku']}) list {lp}, expect to keep {keep:.0f}")
    if not cands:                       # remembered without a shop (profiles skip it), so Enuri can answer for it
        remember({"ts": round(time.time(), 1), "shop": "", "item": order.get("item"), "category": order.get("item"),
                  "kind": order.get("kind"), "budget": order["budget"], "acceptable": list(order["acceptable"])[:30],
                  "outcome": "no candidates"})
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}), flush=True)
        return
    pol = accept_policy(order)
    NEED.clear()
    shelf = min(((cat.get(s) or {}).get("shelf_hours") or 1e9) for s in order.get("acceptable") or [""])
    NEED.update(hours=pol.get("min_hours_left"),            # strict: only a fresh grade A unit can pass
                strict=bool(pol.get("min_hours_left") and pol["min_hours_left"] > T.get("shelf_strict", 0.45) * shelf), text=", ".join(
        x for x in (f"shelf life {pol['min_hours_left']} h or more" if pol.get("min_hours_left") else "",
                    f"grade {'/'.join(pol['grades'])}" if pol.get("grades") else "", "market certificate") if x))
    with ThreadPoolExecutor(rfq) as ex:
        quotes = list(ex.map(quote, cands[:rfq]))
    for q in quotes:
        log(f"[quote] {q['c'][1]['name']}: " + (q.get("error") or f"ask {q['ask']}"))
    remember_phantoms(quotes)
    good = [q for q in quotes if q.get("ask")]
    if not good and len(cands) > rfq:            # 11-04: 62% of quotes met "none in stock" (stock the market listed
        with ThreadPoolExecutor(rfq) as ex:      # but the shop no longer had); ask the next shops before giving up
            more = list(ex.map(quote, cands[rfq:2 * rfq]))
        for q in more:
            log(f"[quote] 2nd round {q['c'][1]['name']}: " + (q.get("error") or f"ask {q['ask']}"))
        remember_phantoms(more)
        quotes += more
        good = [q for q in quotes if q.get("ask")]
    if not good:
        q = quotes[0]
        shop = q["c"][1]
        stars, review = 1, f"No quote for {q['c'][2]['sku']}: {q.get('error') or 'no price in the reply'}."
        # a unit that will not last is the shop answering, not silence: it must not count toward the no-answer pause
        # (2 of 5 within 6 h blocks the shop, often the big one that heads the list)
        # only a failed connection is silence; a shop that answered (no unit that lasts, no stated shelf life, out
        # of stock, no price) did answer. Counting those as "no answer" paused 13+ big shops for 6 h (11-04 05:50)
        silent = bool(re.match(r"[A-Z]\w*(Error|Timeout|Exception)\b", q.get("error") or ""))
        remember({"ts": round(time.time(), 1), "shop": shop["name"], "item": q["c"][2]["sku"], "budget": order["budget"],
                  "outcome": "no answer" if silent else "no deal", "problem": "no answer (no quote)" if silent else None,
                  "enuri_first": adv["ranked"][0]["shop"] if adv else None})
        print(json.dumps({"shop": shop["url"], "receipt": None, "stars": stars, "review": review}, ensure_ascii=False),
              flush=True)
        return
    arm, probed, scout_deal = ab_arm(order), {}, None
    if arm == "B":                                # Enuri scouts first: each open quote gets a 45%-of-list offer
        for q in sorted(good, key=lambda q: q["ask"])[:3]:
            q2, learned = probe(q)
            good[good.index(q)] = q2
            probed[q2["c"][1]["name"]] = learned
            if state(q2["task"]) == "TASK_STATE_COMPLETED":
                scout_deal = q2                   # the shop took the scout offer: that is the deal
                break
        good = [q for q in good if state(q["task"]) in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_COMPLETED")] or good
        for q in good:                            # what the scouting learned is the price to beat
            if q.get("learned") and state(q["task"]) == "TASK_STATE_INPUT_REQUIRED":
                q["ask"] = min(q["ask"], q["learned"])
    # with Enuri's advice: the lowest ask (surplus is budget - paid), then its rank; without: list - ask
    pick = scout_deal or (min(good, key=lambda q: (q["ask"], cands.index(q["c"]))) if adv else
                          max(good, key=lambda q: q["c"][3] - q["ask"]))
    for q in good:
        if q is not pick:
            try:
                rpc(q["url"], "CancelTask", {"id": q["task"]["id"]})
            except Exception:
                pass
    keep, shop, offer, lp = pick["c"]
    sku = offer["sku"]
    cap = min(order["budget"], int(lp * T["max_pay_ratio"]))     # paying near the list price keeps nothing
    row = rows.get((shop["name"], offer["sku"])) if adv else None
    if row:
        cap = min(cap, row["walk_away"])
    # first offer: the anchor share of the list, but under the cap (an anchor above it would be dropped and the
    # buyer would open at first_offer_max x ask); 0.9 x cap so an ask at the anchor still leaves surplus
    low = int(lp * T["r3_first_offer"])
    book_low = ((book.get("prices") or {}).get(sku) or {}).get("low")
    if book_low:                        # the market sold it lower lately: open there, never under 0.45 x list
        low = int(max(0.45 * lp, min(low, book_low)))
    if row:
        low = row["target"]
    if arm == "B" and pick.get("learned"):        # buy at what the scouting learned
        low = min(pick["learned"], cap)
    low = min(low, int(0.9 * cap)) if arm != "B" else min(low, cap)
    log(f"[choose] {shop['name']} {offer['name']} ({sku}): ask {pick['ask']}, list {lp}, I pay at most {cap}, open {low}")

    task, asks, offers, texts, notes, rec, problem = None, [], [], [], [], None, None
    try:
        if scout_deal:
            task, asks, offers, texts, notes = pick["task"], [pick["ask"]], [pick["probe_offer"]], [], []
        else:
            task, asks, offers, texts, notes = negotiate(shop["url"], offer["name"], cap, started,
                                                         low=low,
                                                         opened=(pick["url"], pick["task"], pick["opening"]),
                                                         decide_fn=code_decide)
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
            # the kit takes the order's category or the SKU on a receipt; another SKU is another product
            problem = check_receipt(rec, task, rec.get("item") if rec.get("item") in (sku, order.get("item")) else sku,
                                    agreed)
            log(f"[receipt] {rec}  agreed {agreed}: {problem or 'matches the deal'}")
    issues, worst = misbehaviour(sku, asks, offers, texts, outcome)
    issues += notes
    if notes:
        worst = worst or "hallucination"
    stars, review = review_for(outcome, offer["name"], asks, offers, rec, problem, issues, worst, use_model=False)
    agreed = offers[-1] if outcome == "deal" and offers else None
    remember({"ts": round(time.time(), 1), "shop": shop["name"], "item": sku, "category": order.get("item"),
              "kind": order.get("kind"), "list_price": lp, "budget": order["budget"], "rfq": rfq,
              "enuri_first": adv["ranked"][0]["shop"] if adv else None, "enuri_target": row and row["target"],
              "enuri_walk": row and row["walk_away"], "ab": arm, "probed": probed or None,
              "scout_deal": bool(scout_deal) or None,
              "first_ask": asks[0] if asks else None, "last_ask": asks[-1] if asks else None,
              "last_offer": offers[-1] if offers else None, "agreed": agreed,
              "receipt_price": rec.get("price") if rec else None, "outcome": outcome,
              "signed": bool(rec and rec.get("sig_seller")), "stars": stars,
              "problem": problem or (f"misbehaved ({worst}): " + "; ".join(issues) if issues else None)})
    log(f"result: {outcome}" + (f", keep {lp - agreed} of list {lp}" if agreed else "") + f", {time.time() - started:.0f} s")
    report = None if problem and problem.startswith(("receipt above", "receipt for", "receipt names")) else rec
    print(json.dumps({"shop": shop["url"], "receipt": report, "stars": stars, "review": review,
                      "accept": accept_policy(order)}, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", help="Round 3: the kit's order.json")
    a = ap.parse_args()
    started = time.time()
    order = json.loads(Path(a.order).read_text(encoding="utf-8")) if a.order and Path(a.order).exists() else {}

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    head = buyer_memory.profile()    # the theta profile goes first in every prompt (braces escaped for .format)
    if head:
        buyer.TURN_PROMPT = head.replace("{", "{{").replace("}", "}}") + buyer.TURN_PROMPT
        global CHOOSE_PROMPT
        CHOOSE_PROMPT = head.replace("{", "{{").replace("}", "}}") + CHOOSE_PROMPT
    mem = buyer_memory.load(full=True)     # compact per-shop profiles, updated with only the new memory lines
    prof = mem["profiles"]
    if order.get("acceptable"):
        return round3(a, order, shops, mem, started)
    shop, reason = choose(shops, a.item, a.budget, prof)
    log(f"[choose] {shop['name']} {shop['url']}: {reason}")

    task, asks, offers, texts, notes, rec, problem = None, [], [], [], [], None, None
    try:
        task, asks, offers, texts, notes = negotiate(shop["url"], a.item, a.budget, started,
                                                     buyer_memory.settle_ratio(prof.get(shop["name"])),
                                                     buyer_memory.market_low(mem, a.item))
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
