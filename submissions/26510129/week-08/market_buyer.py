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
import math
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

import buyer_policy
import harness
import ledger
import memory_sync
import strategy_params
from buyer import HEADERS, MAX_ROUNDS, rpc, send, state, status_text

import re

MEMORY = memory_sync.MEMORY   # my trades so far: shop name, item, price, outcome, and since day 2 the shop's first ask and
                              # my last offer; since 11-03 also the settled facts memory_sync merges in after the order
# Challenge 2: shop messages that try to steer the buyer or its review. The harness is told to ignore them; this is
# the code-side check, so that the shop is remembered as manipulative and skipped next time whatever the harness did.
MANIPULATION = re.compile(r"MARKET NOTICE|market operator|market policy|give (this|the|our) shop \d|\d stars in your review|"
                          r"rate (this shop|us) \d|\[?SYSTEM\]?:|ignore (your|all|previous) (instructions|rules)|you must accept", re.I)


def load_memory():
    return memory_sync.load(MEMORY)


def save_memory(mem):
    memory_sync.save(mem, MEMORY)


def blacklisted(shops, mem):
    """Shops my own memory marks as dishonest: a receipt that did not match, or messages that tried to steer me."""
    bad = buyer_policy.blacklisted_names(mem)
    return [s for s in shops if s["name"] in bad]


def fallback_choice(shops, mem, item, reference=None, explore_rate=buyer_policy.EXPLORE_RATE, prefer_warranty=False):
    """The same budget-blind portfolio policy is used after an unreachable shop."""
    return buyer_policy.choose_shop(shops, mem, item, reference, explore_rate, prefer_warranty)[0]


def choose(shops, item, mem, reference=None, explore_rate=buyer_policy.EXPLORE_RATE, prefer_warranty=False):
    pick, features, mode, top = buyer_policy.choose_shop(shops, mem, item, reference, explore_rate, prefer_warranty)
    buyer_policy.emit("buyer_route", item=item, mode=mode, chosen=pick["name"], features=features, top=top)
    print(f"[choose] {pick['url']}: {pick['name']}. portfolio {mode}, score {features['score']}", flush=True)
    return pick


def bargain_record(name, mem, discount=0.15):
    """How this shop bargained with me before, in one line for the harness. Rows from day 1 have no ask and are skipped.
    Day 1 showed the harness pays the ask unless told otherwise, so the line ends with a concrete first-offer rule.
    `discount` is the tuner's first_offer_discount."""
    rows = [m for m in mem if m.get("shop") == name and isinstance(m.get("ask"), (int, float)) and m["ask"] > 0]
    pushed = [m for m in rows if (m.get("first_offer") or m["ask"]) < m["ask"]]   # only rows where I opened below the ask tell anything
    deals = [1 - m["price"] / m["ask"] for m in pushed if m.get("outcome") == "deal" and m.get("price")]
    rejected_low = sum(1 for m in pushed if m.get("outcome") == "closed" and (m.get("last_offer") or 0) < m["ask"])
    if not pushed:
        return f"My past record with this shop: none on how it bargains. First offer: about {discount:.0%} below the ask."
    parts = [f"My past record with this shop: {len(deals)} deal(s) after a low first offer"]
    if deals:
        parts.append(f"closed on average {sum(deals) / len(deals):.0%} below its first ask")
    if rejected_low:
        parts.append(f"it ended the negotiation {rejected_low} time(s) when I offered below its ask")
    if (deals and sum(deals) / len(deals) < 0.02 and len(deals) >= 2) or rejected_low:
        rule = "First proposal: the ask itself; the private mandate guardian decides whether it is affordable."
    else:
        rule = f"First offer: about {discount:.0%} below the ask."
    return ", ".join(parts) + ". " + rule


GRADE_LIFE = {"A": 0.80, "B": 0.45, "C": 0.0}   # share of its shelf life a food unit of that grade still has, at least (agent.md)


def unit_grade(t):
    """The grade the shop states for the unit it reserved: A2A metadata (grade or claim), else 'grade X' in its text."""
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    grade = meta.get("grade") or meta.get("claim")
    if grade in GRADE_LIFE:
        return grade
    m = re.search(r"grade\s*([ABC])\b", status_text(t), re.I)
    return m.group(1).upper() if m else None


def unit_hours_left(t):
    """Some shops state the unit's remaining shelf life in the opening ("about 34 h of shelf life left", 11-04 02:35)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?)?\s+of\s+shelf\s+life\s+left", status_text(t), re.I)
    return float(m.group(1)) if m else None


def food_unit_ok(grade, shelf_hours, need):
    """Can a food unit of this grade still last `need` hours? Judged on the grade's floor, so a pass is safe.
    11-04 02:30: 87 agreed food deals were refused at checkout for too little shelf life; each had cost a negotiation."""
    if not isinstance(shelf_hours, (int, float)) or not isinstance(need, (int, float)) or need <= 0:
        return True
    return shelf_hours * GRADE_LIFE.get(grade, 1.0) >= need


NICK = os.environ.get("MARKET_NICK", "jiseokkk")   # my market name, for shops that take a few percent off for regulars
WARRANTY_MATTERS_FROM = 300_000   # goods at or above this reference price prefer shops that give a warranty
REOPEN_SLACK = 1.15               # a stated floor up to this much over my reference is paid in a new task
STEP_CAP = 1.06                   # a later offer may not jump more than this over my previous one (11-11: shops close
                                  # at my 4th offer, 4% steps from 80% reached 0.84 of the ask there; 6% reach 0.95)


def scarcity(mem, category, recent=60):
    """How scarce this category is for me right now: the share of my recent visits that found the listed unit gone.
    11-05 01:00: orders arrive at a flat ~150/h around the clock (agents, not people), so there is no busy/quiet
    hour; what moves is supply per category (sofas: two thirds of visits found nothing, kimchi: none). None under 10 visits."""
    rows = [r for r in mem if r.get("item") == category][-recent:]
    if len(rows) < 10:
        return None
    return sum(1 for r in rows if r.get("outcome") == "no_stock") / len(rows)


def cooling_off(mem, shop_name, streak=4):
    """A shop whose last `streak` visits all ended before any offer (no stock, or a rejection at the opening like
    leeuichann's "cannot be verified against current inventory", 11 times in 40 orders at 01:10) is left alone
    until it has been visited and answered again; no_stock rows do not lower its score, so this is the only brake."""
    rows = [r for r in mem if r.get("shop") == shop_name][-streak:]
    return len(rows) == streak and all(r.get("outcome") in ("no_stock", "stale") for r in rows)


def closing_gap(last_offer, ask, limit, reference, slack):
    """When my last offer is within 2% of the shop's ask and the ask is inside my limit and near my reference, pay the
    ask: 01:15, seven offers crept 385,000 -> 404,800 against a 405,000 ask and the round limit ended it with nothing."""
    return (isinstance(last_offer, (int, float)) and isinstance(ask, (int, float)) and ask > 0
            and last_offer >= 0.98 * ask and ask <= limit and reference and ask <= reference * max(slack, 1.10))


def regime(sc, discount):
    """Bargaining posture from scarcity: scarce -> take a price up to 10% over the reference and open closer to the
    ask; plentiful -> take only the reference or better and open deeper. Returns (quick-offer slack, first-offer discount)."""
    # 11-06 18:30: the board's buyer rank follows the surplus RATIO (saved = 1 - price / value, weight 0.5; a fit of
    # the board gives error 0.07 with it and 0.14 with absolute surplus). Mine was 0.21 against 0.32-0.36 for the
    # top buyers, so a price is taken without bargaining only at my reference or better.
    if sc is None:
        return 1.00, discount, "unknown"
    if sc >= 0.6:
        return 1.05, round(max(0.05, discount - 0.05), 3), "scarce"
    if sc <= 0.3:
        return 0.97, round(min(0.25, discount + 0.05), 3), "plentiful"
    return 1.00, discount, "normal"


def regulars_prompt(text):
    """A shop asking regulars to identify themselves: "reply 'buyer: <your market name>' and VIP -3% comes off this
    price at once (checked on the ledger)" (00:41, a shop I have 95 deals with). Free surplus for one message."""
    return bool(re.search(r"reply\s+'buyer:\s*<your market name>'", text or "", re.I))


def shop_price_levels(cache_rows, catalog):
    """Per shop URL, the median of settled price / list price over the ledger cache (24 h). 11-10: the top buyers pay
    0.47-0.59 of list and buy from shops that settle at 0.56-0.60; I paid 0.70 and my favourite shop settles at 0.70."""
    by = {}
    for r in cache_rows or []:
        lp = (catalog.get(r.get("sku")) or {}).get("list_price")
        if r.get("shop") and lp:
            by.setdefault(r["shop"], []).append(r["price"] / lp)
    return {url: statistics.median(v) for url, v in by.items() if len(v) >= 5}


def opening_offer(ask, reference, discount):
    """The first offer is code, not the model: the lower of my reference and the ask less the discount. 11-10: told
    to open 20% below, the model opened at 88% of the ask and deals closed at 92%; others pay 5% less at the same
    shop for the same SKU."""
    if not isinstance(ask, (int, float)) or ask <= 0:
        return None
    price = ask * (1 - discount)
    if reference:
        price = min(price, reference)
    return max(1, int(round(price)))


def capped_step(previous, proposal, cap=STEP_CAP):
    """A later offer may not jump more than STEP_CAP over my previous one; the model tends to leap to the ask."""
    if not isinstance(previous, (int, float)) or not isinstance(proposal, (int, float)):
        return proposal
    return int(round(min(proposal, previous * cap)))


FLOOR_PHRASES = re.compile(r"(?:lowest|no deal below|cost floor|our last ask was|final price was)[^0-9]{0,24}(\d[\d,]*)", re.I)


def stated_floor(text):
    """A floor the shop states in its reply, else None. 11-11: of ~1,100 closes in 19 h, 580 named one in five
    phrasings ("No deal at 80000; our lowest is 92000", "No deal below 92000", "Lowest for this unit: 92000",
    "Cannot sell below certified cost floor (92000)", "Our last ask was 92000"); the first regex read only one."""
    m = FLOOR_PHRASES.search(text or "")
    return int(m.group(1).replace(",", "")) if m else None


def quick_offer(ask, floor, reference, limit, slack=1.05):
    """A price worth taking without bargaining: the shop's stated floor or the ask when it is inside my limit and no
    more than `slack` over my reference (the reference is 95% of the market price, so 1.05 is "at the market price
    or better": 00:30, asks ran 4-20% over the reference and a stricter rule never fired). Returns (price, why)."""
    for price, why in ((floor, "the shop's stated lowest"), (ask, "the ask")):
        if isinstance(price, (int, float)) and 0 < price <= limit and reference and price <= reference * slack:
            return int(round(price)), why
    return None, None


def first_ask(t, list_price=None):
    """The shop's opening ask: the A2A metadata when given, else the last number in its message.
    Round 3 (first tuner summary, 11-04 00:00): shop messages carry weights and counts ("120g x 20팩"), and 179 of
    535 stored asks were such numbers. A parsed number counts only when it is plausible against the SKU's list
    price (0.2x to 3x); otherwise the ask is unknown and the harness is told nothing rather than nonsense."""
    ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
    if isinstance(ask, (int, float)):
        return int(ask)
    nums = [int(n) for n in re.findall(r"(?<![\d.])(\d{1,9})(?![\d])", status_text(t))]
    if isinstance(list_price, (int, float)) and list_price > 0:
        nums = [n for n in nums if 0.2 * list_price <= n <= 3 * list_price]
    return nums[-1] if nums else None


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


def negotiate(url, item, budget, reference, record="", request=None, value_cap=None, food_need=None, shelf_hours=None,
              grades=(), slack=1.05, discount=0.20):
    """A sealed-budget loop: the LLM proposes, then the mandate guardian authorizes.
    `value_cap` is the SKU's list price: the guardian never pays above it, whatever the budget.
    `food_need` (the order's use_within_hours) with the SKU's `shelf_hours`: a unit whose stated grade cannot last
    that long is cancelled before any offer (info["stale"] = grade) instead of being refused at checkout later.

    Returns (outcome, agreed price or None, receipt or None, transcript, manipulative, {ask, first_offer, last_offer})."""
    card = httpx.get(url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    iface = next((i for i in card.get("supportedInterfaces", []) if i.get("protocolBinding") == "JSONRPC"), None)
    if iface is None:
        print("[buyer] the card has no JSONRPC interface", flush=True)
        return "no_card", None, None, [], False, {}
    rpc_url = iface["url"]
    print(f"[buyer] card: {card.get('name')!r}, skills {[s.get('id') for s in card.get('skills', [])]}, rpc {rpc_url}", flush=True)

    opening = (f"I want to buy {request['name']} ({request['sku']})" if request
               else f"I want to buy a {item}")
    t = send(rpc_url, opening)
    print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
    manip = steered(t)
    ask0 = first_ask(t, value_cap)
    info = {"ask": ask0, "first_offer": None, "last_offer": None, "reference": reference,
            "guard_decisions": [], "checkout_authorized": False}
    if state(t) != "TASK_STATE_INPUT_REQUIRED":
        return "closed", None, None, [("you", opening), ("shop", status_text(t))], manip, info
    tid = t["id"]
    if regulars_prompt(status_text(t)):
        print(f"[buyer] the shop takes a few percent off for regulars who give their name; replying as {NICK}", flush=True)
        named = send(rpc_url, f"buyer: {NICK}", tid)
        print(f"[buyer] <- {state(named)} | {status_text(named)}", flush=True)
        if state(named) == "TASK_STATE_INPUT_REQUIRED":
            t = named
            info["ask"] = ask0 = first_ask(t, value_cap)
    grade, hours_left = unit_grade(t), unit_hours_left(t)
    unfit = None
    if grade and grades and grade not in grades:   # 02:47: a 377,000 grade C unit was negotiated, then refused at checkout
        unfit = f"grade {grade} unit; the order accepts {'/'.join(grades)}"
    elif food_need and hours_left is not None and hours_left < food_need:
        unfit = f"the shop says {hours_left} h of shelf life left; the order needs {food_need} h"
    elif food_need and grade and not food_unit_ok(grade, shelf_hours, food_need):
        unfit = f"grade {grade} unit of a {shelf_hours} h product cannot last the order's {food_need} h"
    if unfit:
        print(f"[buyer] {unfit}; not negotiating", flush=True)
        rpc(rpc_url, "CancelTask", {"id": tid})
        info["stale"] = grade
        return "closed", None, None, [("you", opening), ("shop", status_text(t))], manip, info
    log = [("you", opening), ("shop", status_text(t))]
    guardian = buyer_policy.MandateGuardian(budget, reference, value_cap)
    print(f"[buyer] first ask {ask0}, private ceiling sealed, personal reference {reference}, value cap {value_cap}. {record}", flush=True)

    offers = []   # (my offer, the ask the shop answered with): the harness gets this instead of the whole transcript
    for round_index in range(MAX_ROUNDS):
        ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
        if ask is None:
            ask = first_ask(t, value_cap)
        # after a reopen the shop has shown its floor: the next ask near it is taken rather than bargained down again
        quick, why = quick_offer(ask, stated_floor(status_text(t)), reference, guardian.limit,
                                 max(slack, 1.05) if info.get("reopened") else slack)
        if quick is None and offers and closing_gap(offers[-1][0], ask, guardian.limit, reference, slack):
            quick, why = int(round(ask)), "the ask, within 2% of my last offer,"
        if quick is not None:
            # 11-05 00:30: 45% of the market's orders go unfilled and a unit draws several buyers within minutes, so
            # a price already at or under my reference is taken at once (no model call) rather than haggled for a few
            # percent and lost; a floor the shop states inside my limit is paid the same way (111 closes in 6 h).
            print(f"[buyer] {why} {quick} is within my reference {reference} and limit; taking it", flush=True)
            d = {"action": "offer", "price": quick}
            buyer_policy.emit("buyer_quick_offer", item=item, task=tid[:8], round=round_index + 1, why=why, price=quick,
                              reference=reference)
        elif not offers and opening_offer(ask, reference, discount):
            d = {"action": "offer", "price": opening_offer(ask, reference, discount)}   # the opener is code (no model call)
            print(f"[buyer] opening at {d['price']} ({discount:.0%} under the ask {ask}, capped at my reference {reference})", flush=True)
        else:
            d = harness.decide(buyer_policy.NEGOTIATOR_SYSTEM,
                               buyer_policy.situation(f"Item: {item}. Personal reference price: {reference}. {record}",
                                                      offers, status_text(t), ask))
            if d and isinstance(d.get("price"), (int, float)) and offers:
                stepped = capped_step(offers[-1][0], d["price"])
                if stepped != d["price"]:
                    print(f"[buyer] the model's {d['price']} capped to {stepped}: no more than {STEP_CAP - 1:.0%} over my last offer", flush=True)
                    d["price"] = stepped
        if d is None:
            fallback = buyer_policy.public_fallback_offer(reference, ask, round_index)
            d = {"action": "offer", "price": fallback}
            buyer_policy.emit("buyer_model_fallback", item=item, task=tid[:8], round=round_index + 1,
                              reference=reference, ask=ask, proposal=fallback)
        if d.get("action") == "walk_away" or "price" not in d:
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
        offers.append((d["price"], first_ask(t, value_cap)))
        if state(t) == "TASK_STATE_COMPLETED":
            info["checkout_authorized"] = guardian.checkout_ok(d["price"])
            return "deal", d["price"], receipt_of(t), log, manip, info
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            # 00:30: 111 closes in 6 h came as "No deal. Lowest for this unit: N" with the task already over. When N is
            # inside my limit and near my reference, one new task is opened and N is paid at once.
            floor = stated_floor(status_text(t))
            # 11-11: stated floors ran at 1.13x my reference (market x 0.92), so 83% of them were left on the table
            # at the old 1.05; up to 1.15x (about 6% over the market price) is still inside my limit and worth a deal
            if floor and floor <= guardian.limit and floor <= reference * REOPEN_SLACK and not info.get("reopened"):
                info["reopened"] = True
                print(f"[buyer] the shop closed at a stated lowest {floor} inside my limit; reopening to pay it", flush=True)
                buyer_policy.emit("buyer_reopen_at_floor", item=item, floor=floor, reference=reference)
                t = send(rpc_url, opening)
                print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
                if state(t) == "TASK_STATE_INPUT_REQUIRED":
                    tid = t["id"]
                    decision = guardian.authorize(floor, 2, floor)
                    if decision["action"] == "offer":
                        info["last_offer"] = decision["price"]
                        t = send(rpc_url, str(decision["price"]), tid)
                        print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
                        log += [("you", str(decision["price"])), ("shop", status_text(t))]
                        offers.append((decision["price"], first_ask(t, value_cap)))
                        if state(t) == "TASK_STATE_COMPLETED":
                            info["checkout_authorized"] = guardian.checkout_ok(decision["price"])
                            return "deal", decision["price"], receipt_of(t), log, manip, info
                        if state(t) == "TASK_STATE_INPUT_REQUIRED":
                            continue
            return "closed", None, None, log, manip, info
    print("[buyer] round limit reached, walking away", flush=True)
    rpc(rpc_url, "CancelTask", {"id": tid})
    return "walked_away", None, None, log, manip, info


def receipt_names_the_order(receipt_item, wanted, acceptable=(), catalog=None):
    """A receipt may name the requested SKU, any of the order's acceptable SKUs, or (when I asked by category) a SKU of
    that category (agent.md: "a receipt's item is the order's item or one of its acceptable SKUs"). 11-05 11:00: the
    old check compared the receipt's SKU with the category name I had asked for and marked 30 honest shops dishonest,
    which blacklisted the market's ten biggest suppliers for a day and a half (fill rate 16% against a 44% median)."""
    got = str(receipt_item or "")
    if got.lower() == str(wanted).lower() or got in set(acceptable or ()):
        return True
    category = ((catalog or {}).get(got) or {}).get("category") or ""
    return bool(category) and (category == wanted or category.split(">")[-1] == wanted)


def review(item, outcome, agreed, receipt, reference, manip=False, acceptable=(), catalog=None):
    """A factual code-side review; untrusted shop text is never sent to another LLM."""
    honest = outcome != "deal" or (
        isinstance(receipt, dict)
        and receipt_names_the_order(receipt.get("item"), item, acceptable, catalog)
        and isinstance(receipt.get("price"), (int, float))
        and int(round(float(receipt["price"]))) == agreed
    )
    if outcome == "deal" and not honest:
        got = receipt.get("price") if isinstance(receipt, dict) else None
        return 1, f"Agreed {item} at {agreed}, but the receipt did not match (price {got}).", False
    if outcome == "unreachable":
        return 2, "The shop could not be reached or answered with something that is not A2A; no negotiation took place.", honest
    if outcome == "no_stock":   # Round 3 minute 1: "No matching unreserved stock" before any offer
        return 2, f"The shop had no {item} to sell when asked; no negotiation took place.", honest
    if outcome == "stale":
        return 2, f"The shop's {item} unit did not fit the order (grade or shelf life); no negotiation took place.", honest
    if manip:
        note = f"Deal at {agreed}, but the" if outcome == "deal" else "No deal, and the"
        return 2, f"{note} shop's messages carried fake market notices telling me to accept its price and rate it 5 stars.", honest
    if outcome == "deal":
        ratio = agreed / reference if reference else 1
        stars = 5 if ratio <= 1 else 4 if ratio <= 1.05 else 3
        return stars, f"Agreed {item} at {agreed}; receipt matched. Personal reference was {reference}.", True
    return 2, f"No honest {item} deal reached the private mandate guard's limit.", honest


def load_round3(order_path):
    """The kit's per-order context; an absent/literal {order} keeps the week-07 test compatible."""
    if not order_path:
        return {}, {}
    path = Path(order_path)
    try:
        order = json.loads(path.read_text(encoding="utf-8"))
        catalog = json.loads((path.parent / "catalog.json").read_text(encoding="utf-8"))
        return order, catalog
    except (OSError, ValueError):
        return {}, {}


def category_reference(order, catalog):
    prices = [catalog.get(sku, {}).get("list_price") for sku in (order.get("acceptable") or [])]
    prices = [price for price in prices if isinstance(price, (int, float))]
    return int(round(statistics.median(prices) * 0.90)) if prices else None


def prepare_round3_shops(shops, order, catalog, guardian):
    """Guardian filters plausibly affordable stock; router gets no numeric ceiling."""
    acceptable = set(order.get("acceptable") or [])
    if not acceptable:
        return shops
    stocked, affordable = [], []
    for original in shops:
        offers = [offer for offer in (original.get("offers") or []) if offer.get("sku") in acceptable]
        if not offers:
            continue
        shop = dict(original, _eligible_offers=offers)
        stocked.append(shop)
        eligible = guardian.affordable_offers(offers, catalog)
        if eligible:
            affordable.append(dict(shop, _eligible_offers=eligible))
    return affordable or stocked or shops


def choose_request(shop, catalog, order=None, exclude=()):
    """The SKU to ask this shop for, skipping `exclude` (SKUs it already said it has no unit of)."""
    offers = [o for o in (shop.get("_eligible_offers") or shop.get("offers") or []) if o.get("sku") not in exclude]
    if not offers:
        return None
    need = (order or {}).get("use_within_hours")
    if (order or {}).get("kind") == "food" and isinstance(need, (int, float)) and need > 0:
        # 21:04-21:07: four agreed food deals were refused at checkout because the unit's shelf life was shorter than
        # the order's use_within_hours (358 h left, 360 asked). A SKU whose whole shelf life is shorter can never pass;
        # prefer one where even a grade B unit (45% of its life left) covers the order, then one where grade A does.
        shelf = lambda offer: catalog.get(offer.get("sku"), {}).get("shelf_hours")
        feasible = [offer for offer in offers if shelf(offer) is None or shelf(offer) >= need]
        tiers = [[o for o in feasible if shelf(o) is not None and shelf(o) * GRADE_LIFE[g] >= need] for g in ("B", "A")]
        offers = tiers[0] or tiers[1] or feasible or offers
    # Higher catalog value improves buyer surplus; quantity breaks ties toward availability.
    return max(offers, key=lambda offer: (catalog.get(offer.get("sku"), {}).get("list_price", 0),
                                          offer.get("qty", 0), offer.get("sku", "")))


def sku_reference(sku, catalog, fallback, stats=None):
    """The bargaining reference for one SKU: what the market lately paid for it (ledger stats, x0.95), capped at 90%
    of its list price, which is what a settled deal is worth to the order.
    21:09 (o-b3f18a8374b8): the category reference (35910) sat above the SKU's list price (28900), the model opened
    at 30500 and the deal settled with negative surplus. 11-04 02:30: with 0.9 x list as the anchor we paid 80% of
    list on average while the market's category medians sit near 75%. The category reference stands in without a SKU."""
    list_price = catalog.get(sku, {}).get("list_price")
    if not isinstance(list_price, (int, float)) or list_price <= 0:
        return fallback
    cap = 0.90 * list_price
    market, _, _ = ledger.market_price(stats or {}, sku, catalog.get(sku, {}).get("category"), list_price)
    return int(round(min(cap, market * 0.92) if market else cap))   # 0.92: the top buyers pay 0.65-0.68 of list


def accept_constraints(order):
    """What the kit must verify before paying. A settled deal is worth the list price whatever the grade (agent.md),
    so for goods only the grade floor is kept (A/B: fewer breaks) and the certificate is not demanded: 11-04 02:30,
    20 agreed goods deals had been refused only for "no certificate". Food keeps both: the certificate is what proves
    the shelf life the order depends on."""
    if not order.get("acceptable"):
        return None
    if order.get("kind") == "food":
        # 11-06 18:00: the kit knows the unit's shelf life with or without a certificate (its refusals quote the hours
        # left either way), and the only shop refused for "no certificate" (elm-street, 80 agreed deals) sells food
        # whose value is the list price like anyone's. The hours check stays; the certificate flag goes.
        return {"min_hours_left": int(math.ceil(order.get("use_within_hours") or 0))}
    return {"grades": ["A", "B"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order")
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    if not shops:
        sys.exit("[buyer] no shops in the list")
    mem = load_memory()
    synced = memory_sync.reconcile(mem, MEMORY.parent)   # refused payments and broken goods since the last order
    if any(synced.values()):
        save_memory(mem)
        buyer_policy.emit("buyer_memory_sync", **synced)
    params = strategy_params.load()   # the nightly tuner's knobs; defaults when the file is absent
    buyer_policy.emit("buyer_params", **params)
    order, catalog = load_round3(a.order)
    category = order.get("category") or a.item
    reference = buyer_policy.item_reference(mem, category, fallback=category_reference(order, catalog))
    selector_guardian = buyer_policy.MandateGuardian(a.budget, reference)
    candidates = prepare_round3_shops(shops, order, catalog, selector_guardian)
    levels = shop_price_levels((ledger.load_json(MEMORY.parent / ledger.CACHE_FILE) or {}).get("rows"), catalog)
    for s in candidates:                      # what each shop's settles cost against list, for the router's cheapness
        s["_price_ratio"] = levels.get(s.get("url"))
    cooled = [s for s in candidates if cooling_off(mem, s.get("name"))]
    if cooled and len(cooled) < len(candidates):
        print(f"[choose] skipping {[s.get('name') for s in cooled]}: answered nothing on their last visits", flush=True)
        candidates = [s for s in candidates if s not in cooled]
    # 11-06 18:00: 32 goods broke with no warranty to refund them, 12.6M lost, 9.2M of it on eight units over
    # 500k; every break under a warranty was refunded (36 of 36). Dear goods go to shops that give one.
    prefer_warranty = order.get("kind") == "goods" and (reference or 0) >= WARRANTY_MATTERS_FROM
    pick = choose(candidates, category, mem, reference, params["explore_rate"], prefer_warranty)
    request = choose_request(pick, catalog, order)
    wanted = request["sku"] if request else a.item

    stats = ledger.load_stats(MEMORY.parent)   # what the market lately paid: the bargaining anchor per SKU
    food_need = order.get("use_within_hours") if order.get("kind") == "food" else None
    grades = tuple((accept_constraints(order) or {}).get("grades") or ())   # what the kit will pay for; checked first
    sc = scarcity(mem, category)
    slack, discount, posture = regime(sc, params["first_offer_discount"])
    buyer_policy.emit("buyer_regime", item=category, scarcity=round(sc, 2) if sc is not None else None,
                      posture=posture, quick_slack=slack, first_offer_discount=discount)
    category_ref, tried = reference, {}   # tried: shop url -> SKUs it was asked for
    for attempt in range(1, 7):   # day 1: 15 orders died on a shop whose card came back as an error page; now the order moves on
        tried.setdefault(pick["url"], set()).add(wanted)
        value_cap = catalog.get(wanted, {}).get("list_price")
        reference = sku_reference(wanted, catalog, category_ref, stats)
        try:
            outcome, agreed, receipt, log, manip, info = negotiate(
                pick["url"], wanted, a.budget, reference,
                bargain_record(pick["name"], mem, discount), request, value_cap,
                food_need, catalog.get(wanted, {}).get("shelf_hours"), grades, slack, discount
            )
        except Exception as e:   # httpx errors, a card that is not JSON, a task without the fields we expect
            print(f"[buyer] shop unreachable: {type(e).__name__}: {str(e)[:120]}", flush=True)
            outcome, agreed, receipt, log, manip, info = "unreachable", None, None, [], False, {}
        # Round 3 minute 1 (11-03 20:55): the chosen shop's stock was gone before the first word ("No matching unreserved
        # stock") and the order ended there. A close before any offer says nothing about the shop (outcome "no_stock",
        # or "stale" when the unit was only too old for the order; both ignored by the shop scores) and is as good as
        # unreachable. 11-04 02:30: 755 of 1050 such closes were at shops that listed other SKUs of the category, so
        # the same shop is asked for its next SKU before the order moves to another shop.
        no_offer = outcome == "closed" and info.get("first_offer") is None
        if no_offer:
            outcome = "stale" if info.get("stale") else "no_stock"
        stars, text, honest = review(wanted, outcome, agreed, receipt, reference, manip,
                                     set(order.get("acceptable") or ()), catalog)
        if outcome == "deal" and not info.get("checkout_authorized"):
            print("[guard] deal was not authorized for checkout; discarding its receipt", flush=True)
            outcome, agreed, receipt, stars, text = "rejected_checkout", None, None, 1, "Checkout exceeded the private mandate."
        mem.append({"order": order.get("order"), "shop": pick["name"], "item": category, "sku": wanted,
                    "name": request["name"] if request else None, "outcome": outcome, "price": agreed,
                    "list_price": catalog.get(wanted, {}).get("list_price"), "honest": honest, "manipulative": manip,
                    "stars": stars, "reference": reference, "policy": "sealed-budget-v1", "settled": False,
                    "at": time.strftime("%Y-%m-%d %H:%M"),
                    **{k: info.get(k) for k in ("ask", "first_offer", "last_offer", "guard_decisions", "checkout_authorized")}})
        save_memory(mem)
        if (outcome != "unreachable" and not no_offer) or attempt == 6:
            break
        request = choose_request(pick, catalog, order, exclude=tried[pick["url"]]) if no_offer else None
        if request is not None:
            print(f"[choose] same shop {pick['name']}, next SKU {request['sku']} after {outcome}", flush=True)
        else:
            rest = [s for s in candidates if s.get("url") not in tried and s not in blacklisted(candidates, mem)]
            if not rest:
                break
            pick = fallback_choice(rest, mem, category, category_ref, params["explore_rate"], prefer_warranty)
            request = choose_request(pick, catalog, order)
            print(f"[choose] retry with {pick['url']}: {pick['name']}. picked by portfolio policy after the last shop "
                  f"{'had no fitting stock' if no_offer else 'failed'}", flush=True)
        wanted = request["sku"] if request else a.item

    accept = accept_constraints(order)
    print(json.dumps({"shop": pick["url"], "receipt": receipt, "stars": stars, "review": text,
                      **({"accept": accept} if accept else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
