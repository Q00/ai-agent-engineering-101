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
import objective
import profiles
import rulebook
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


def blacklisted(shops, mem, shop_profiles=None):
    """Shops my own memory marks as dishonest: a receipt that did not match, or messages that tried to steer me."""
    bad = buyer_policy.blacklisted_names(mem, shop_profiles)
    return [s for s in shops if s["name"] in bad]


def fallback_choice(shops, mem, item, reference=None, explore_rate=buyer_policy.EXPLORE_RATE, prefer_warranty=False,
                    shop_profiles=None):
    """The same budget-blind portfolio policy is used after an unreachable shop."""
    return buyer_policy.choose_shop(shops, mem, item, reference, explore_rate, prefer_warranty, shop_profiles)[0]


def choose(shops, item, mem, reference=None, explore_rate=buyer_policy.EXPLORE_RATE, prefer_warranty=False,
           shop_profiles=None):
    pick, features, mode, top = buyer_policy.choose_shop(shops, mem, item, reference, explore_rate, prefer_warranty,
                                                         shop_profiles)
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


GRADE_LIFE = {"A": 0.80, "B": 0.45, "C": 0.22}   # share of its shelf life a food unit of that grade still has: A and B at least
# (agent.md), C a typical value (under 45%; 11-11 17:50: every C food unit was refused before a word, while the top buyers
# buy the cheap B/C units - 724 of my visits today ended "stale", asks at a median 0.63 of list; the kit's checkout check
# of the real hours left is the backstop, a refusal there costs one visit)


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


NO_WARRANTY = re.compile(r"warranty\s*:?\s*0\s*h|no warranty|without (?:a )?warranty", re.I)   # "warranty 0 h", "no warranty"
NICK = os.environ.get("MARKET_NICK", "jiseokkk")   # my market name, for shops that take a few percent off for regulars
WARRANTY_MATTERS_FROM = 0         # 11-11 17:30: a break scores -1 whatever the price (agent.md: "a sofa and a snack at 30% off
                                  # score the same"; 62 of my 157 breaks had no warranty), so every goods order prefers warranty shops
DEFAULT_SCORE = 0.70              # my running score before any deal is remembered (profiles.py gives the real one)
# 11-17: the two list-price shares that stood here (pay cap 0.70, quick take 0.64) were the patch 3.4 score written
# into the code. The limits are now derived per unit, after the shop names its grade, from my own running score
# under the rule in force: pay while the deal scores my average - buyer_kept_slack or better, take an ask at once
# at my average + buyer_quick_gain. On 11-17 (average 0.73, patch 3.6) that is 0.75 of list for a grade A unit,
# 0.64 for B, 0.54 for C; the flat 0.70 had bought C units scoring 0.63 and refused A units scoring 0.9.
SHOPS_PER_ORDER = 3               # a close or walk-away moves the order to the next shop: 11-11, 47% of my orders ended at the
                                  # first shop's refusal (fill 18%); the top buyers fill 60-70% of theirs
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


COOL_OFF_MINUTES = 30


def cooling_off(mem, shop_name, streak=4, now=None):
    """A shop whose last `streak` visits all ended before any offer (no stock, or a rejection at the opening like
    leeuichann's "cannot be verified against current inventory", 11 times in 40 orders at 01:10) is left alone for
    COOL_OFF_MINUTES after its last visit; no_stock rows do not lower its score, so this is the only brake.
    11-11 18:10: without the expiry a cooled shop was never visited again, so it stayed cooled for good: 48 shops,
    among them the market's biggest and cheapest (nocha12 10,756 settles a day, psyoung82 8,053, yms979, lee-sungsoo,
    inextro, olstjr...), 14 of the 23 shops of one order. Most of their "stale" rows came from the food grade rule
    relaxed at 17:42."""
    rows = [r for r in mem if r.get("shop") == shop_name][-streak:]
    if len(rows) < streak or not all(r.get("outcome") in ("no_stock", "stale") for r in rows):
        return False
    last = rows[-1].get("at")
    if not last:
        return True
    try:
        last_ts = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M"))
    except ValueError:
        return True
    return (now if now is not None else time.time()) - last_ts < COOL_OFF_MINUTES * 60


def shop_moved(previous_ask, ask, at_least=0.03):
    """Did the shop concede at least `at_least` from its previous ask? 18:10: 129 model offers in 25 min drew 127
    counters and 2 deals; shops here concede 1-2% a round, and the deals came from quick-takes, openers and my final
    ceiling. The model is asked only when the shop is actually bargaining."""
    return (isinstance(previous_ask, (int, float)) and isinstance(ask, (int, float)) and previous_ask > 0
            and (previous_ask - ask) / previous_ask >= at_least)


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


def shop_price_levels(cache_rows, catalog, url2name=None):
    """Per shop (its market name when `url2name` knows the URL, else the URL), the median of settled price / list
    price over the ledger cache (24 h). 11-10: the top buyers pay 0.47-0.59 of list and buy from shops that settle at
    0.56-0.60; I paid 0.70 and my favourite shop settles at 0.70. 11-11 17:30: a shop that rejoins gets a new tunnel
    and its settles sit under a URL the order's shop list no longer carries (17 of the 60 biggest shops, including
    the cheapest), so the levels are keyed by name wherever the name is known."""
    by = {}
    for r in cache_rows or []:
        lp = (catalog.get(r.get("sku")) or {}).get("list_price")
        url = (r.get("shop") or "").rstrip("/")
        if url and lp:
            by.setdefault((url2name or {}).get(url, url), []).append(r["price"] / lp)
    return {key: statistics.median(v) for key, v in by.items() if len(v) >= 5}


NAMES_FILE = "shop_names.json"


def shop_names(shops, mem, folder):
    """URL -> market name for every shop URL seen in an order's shop list or on a settled deal, kept across orders
    in .market/shop_names.json (the ledger names shops only by URL)."""
    path = Path(folder) / NAMES_FILE
    names = ledger.load_json(path) or {}
    before = len(names)
    for s in shops or []:
        if s.get("url") and s.get("name"):
            names[s["url"].rstrip("/")] = s["name"]
    for row in mem:
        if row.get("shop_url") and row.get("shop"):
            names.setdefault(row["shop_url"].rstrip("/"), row["shop"])
    if len(names) != before:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(names), encoding="utf-8")
        os.replace(tmp, path)
    return names


def order_goes_on(outcome, no_offer, bargained, attempt, shops_per_order=SHOPS_PER_ORDER):
    """Whether the order tries another shop (or SKU) after this visit: always after a visit that produced no offer
    (no stock, a unit that did not fit, an unreachable shop), and after a close or walk-away until `shops_per_order`
    shops have refused. A deal, a refused checkout or the sixth attempt ends it. Until 11-11 17:30 the first close
    ended the order: 639 + 289 of 1,958 orders that day."""
    if attempt >= 6 or outcome in ("deal", "rejected_checkout"):
        return False
    if no_offer or outcome in ("unreachable", "overpriced"):   # overpriced: a fixed-price shop left without an offer
        return True
    return outcome in ("closed", "walked_away") and bargained < shops_per_order


def opening_offer(ask, reference, discount, at_least=None):
    """The first offer is code, not the model: the lower of my reference and the ask less the discount, but no lower
    than `at_least` (the shop's own median settle on the ledger x list, 11-11 17:50: a shop that settles at 0.55 of
    list answered my 0.51 opener with a 0.69 counter, which I then paid; the top buyers pay it 0.54). 11-10: told to
    open 20% below, the model opened at 88% of the ask and deals closed at 92%."""
    if not isinstance(ask, (int, float)) or ask <= 0:
        return None
    price = ask * (1 - discount)
    if reference:
        price = min(price, reference)
    if isinstance(at_least, (int, float)) and at_least > 0:
        price = max(price, at_least)
    return max(1, int(round(min(price, ask))))


def capped_step(previous, proposal, cap=STEP_CAP):
    """A later offer may not jump more than STEP_CAP over my previous one; the model tends to leap to the ask."""
    if not isinstance(previous, (int, float)) or not isinstance(proposal, (int, float)):
        return proposal
    return int(round(min(proposal, previous * cap)))


FLOOR_PHRASES = re.compile(r"(?:lowest|no deal below|cost floor|our last ask was|final price was)[^0-9]{0,24}(\d[\d,]*)", re.I)
FLOOR_BEFORE = re.compile(r"(\d[\d,]*)\s+is\s+(?:our|the|my)\s+(?:lowest|floor|final)", re.I)   # "13438 is our lowest: ..."


def stated_floor(text):
    """A floor the shop states in its reply, else None. 11-11: of ~1,100 closes in 19 h, 580 named one in five
    phrasings ("No deal at 80000; our lowest is 92000", "No deal below 92000", "Lowest for this unit: 92000",
    "Cannot sell below certified cost floor (92000)", "Our last ask was 92000"); the first regex read only one.
    17:30: "13438 is our lowest: it is top grade (A) ..." (the number before the word) was read as nothing and cost
    three offers and two model calls before the shop rejected."""
    m = FLOOR_BEFORE.search(text or "") or FLOOR_PHRASES.search(text or "")
    return int(m.group(1).replace(",", "")) if m else None


def quick_offer(ask, floor, reference, limit, slack=1.05, quick_take=None):
    """A price worth taking without bargaining: the shop's stated floor or the ask when it is inside my limit and no
    more than `slack` over my reference (the reference is 95% of the market price, so 1.05 is "at the market price
    or better": 00:30, asks ran 4-20% over the reference and a stricter rule never fired), or at or under
    `quick_take` (QUICK_TAKE_RATIO of the list price, 11-11 17:30). Returns (price, why)."""
    for price, why in ((floor, "the shop's stated lowest"), (ask, "the ask")):
        if isinstance(price, (int, float)) and 0 < price <= limit and (
                (reference and price <= reference * slack) or (quick_take and price <= quick_take)):
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
    # 17:30: "Asking 12,530 credits" was read as 12 and 530, both implausible, so the ask was unknown
    nums = [int(n.replace(",", "")) for n in re.findall(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{1,9})(?![\d,])", status_text(t))]
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


def price_limits(list_price, grade, targets):
    """(pay cap, quick take, estimated cost) for one unit: the prices at which a deal scores `targets["pay"]` and
    `targets["quick"]` under `targets["formula"]`, with the unit's cost estimated from its grade (objective.py)."""
    if not targets or not isinstance(list_price, (int, float)) or list_price <= 0:
        return None, None, None
    cost = objective.unit_cost(list_price, grade, targets.get("kind"))
    return (objective.price_for(targets["pay"], list_price, cost, targets["formula"]),
            objective.price_for(targets["quick"], list_price, cost, targets["formula"]), int(cost))


def negotiate(url, item, budget, reference, record="", request=None, value_cap=None, food_need=None, shelf_hours=None,
              grades=(), slack=1.05, discount=0.20, targets=None, haggle=None, open_at_least=None):
    """A sealed-budget loop: the LLM proposes, then the mandate guardian authorizes.
    `value_cap` is the SKU's list price (what a parsed ask is checked against). `targets` ({formula, pay, quick,
    kind}) gives the scores a deal must reach; once the shop has named the unit's grade they become the pay cap the
    guardian never exceeds, whatever the budget, and the ask that is paid at once. `haggle` is what the shop's
    profile remembers of its bargaining: {"theta": the share of its ask where it starts to accept, "fixed": it
    does not come down}; a fixed-price shop is paid its ask or left without an offer (outcome "overpriced").
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
    info["grade"] = grade
    unfit = None
    if grade and grades and grade not in grades:   # 02:47: a 377,000 grade C unit was negotiated, then refused at checkout
        unfit = f"grade {grade} unit; the order accepts {'/'.join(grades)}"
    elif grade == "C" and grades and NO_WARRANTY.search(status_text(t)):
        # 19:50: a C unit is taken only under a warranty (the kit demands it at checkout); a shop that says "warranty
        # 0 h" in its opening (winun1127: its A/B units carry one, its C units do not) is left before any offer
        unfit = "grade C unit offered without a warranty"
    elif food_need and hours_left is not None and hours_left < food_need:
        unfit = f"the shop says {hours_left} h of shelf life left; the order needs {food_need} h"
    elif food_need and hours_left is None and grade and not food_unit_ok(grade, shelf_hours, food_need):
        # 17:50: hours the shop states settle it; the grade estimate is only for a shop that names none (a stated
        # 155 h on a grade C unit used to be refused by the grade rule after passing the hours rule)
        unfit = f"grade {grade} unit of a {shelf_hours} h product cannot last the order's {food_need} h"
    if unfit:
        print(f"[buyer] {unfit}; not negotiating", flush=True)
        rpc(rpc_url, "CancelTask", {"id": tid})
        info["stale"] = grade
        return "closed", None, None, [("you", opening), ("shop", status_text(t))], manip, info
    log = [("you", opening), ("shop", status_text(t))]
    pay_cap, quick_take, cost = price_limits(value_cap, grade, targets)
    info.update({"pay_cap": pay_cap, "cost_est": cost})
    haggle = haggle or {}
    guardian = buyer_policy.MandateGuardian(budget, reference, pay_cap or value_cap)
    print(f"[buyer] first ask {ask0}, private ceiling sealed, personal reference {reference}, grade {grade or 'not stated'}, "
          f"estimated cost {cost}, pay cap {pay_cap or value_cap}, quick take {quick_take}, shop accepts at "
          f"{haggle.get('theta') or 'unknown'} of its ask. {record}", flush=True)
    buyer_policy.emit("buyer_objective", item=item, formula=(targets or {}).get("formula"), grade=grade, cost_est=cost,
                      pay_target=(targets or {}).get("pay"), quick_target=(targets or {}).get("quick"),
                      pay_cap=pay_cap, quick_take=quick_take, ask=ask0, theta=haggle.get("theta"), fixed=haggle.get("fixed"))

    offers = []   # (my offer, the ask the shop answered with): the harness gets this instead of the whole transcript
    for round_index in range(MAX_ROUNDS):
        ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
        if ask is None:
            ask = first_ask(t, value_cap)
        floor = stated_floor(status_text(t))
        if floor and isinstance(value_cap, (int, float)) and not 0.2 * value_cap <= floor <= 3 * value_cap:
            floor = None   # a number read out of other text, not a price
        if floor and floor > guardian.limit:
            # 17:30: a shop that names a lowest over my pay cap will not come down to it; three offers and two model
            # calls went into "13438 is our lowest" (0.75 of list, cap 0.70) before it rejected
            print(f"[buyer] the shop's stated lowest {floor} is over my pay cap {guardian.limit}; leaving", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            return "walked_away", None, None, log, manip, info
        if isinstance(ask, (int, float)) and ask > guardian.limit and (round_index >= 3 or guardian.ceiling_used):
            print(f"[buyer] the ask {ask} is still over my pay cap {guardian.limit} after my last word; leaving", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            return "walked_away", None, None, log, manip, info
        # after a reopen the shop has shown its floor: the next ask near it is taken rather than bargained down again
        quick, why = quick_offer(ask, floor, reference, guardian.limit,
                                 max(slack, 1.05) if info.get("reopened") else slack, quick_take)
        if quick is None and offers and closing_gap(offers[-1][0], ask, guardian.limit, reference, slack):
            quick, why = int(round(ask)), "the ask, within 2% of my last offer,"
        if quick is None and offers and isinstance(ask, (int, float)) and 0 < ask <= guardian.limit:
            # 11-11 17:30: shops concede 1-2% a round and close at my third or fourth offer (639 orders ended that way
            # today), so a counter already inside my pay cap is paid rather than bargained a few percent more and lost
            quick, why = int(round(ask)), "the counter, inside my pay cap,"
        if quick is None and not offers and haggle.get("fixed") and isinstance(ask, (int, float)) and ask > 0:
            # 11-17: a shop whose profile says it accepts at its ask and nothing lower (YAHO1212, nocha12, bjeon01,
            # 25620027: 1.00 over hundreds of offers) is not bargained with: the ask is paid when it is inside my
            # cap, and otherwise the order moves on without an offer (22,883 visits in five days: an opener under
            # such a shop's ask was accepted 2% of the time and cost a round each)
            if ask <= guardian.limit:
                quick, why = int(round(ask)), "the ask of a shop that does not come down,"
            else:
                print(f"[buyer] the ask {ask} is over my cap {guardian.limit} and this shop does not come down "
                      f"(accepts at {haggle.get('theta')} of its ask); leaving without an offer", flush=True)
                rpc(rpc_url, "CancelTask", {"id": tid})
                return "overpriced", None, None, log, manip, info
        if quick is not None:
            # 11-05 00:30: 45% of the market's orders go unfilled and a unit draws several buyers within minutes, so
            # a price already at or under my reference is taken at once (no model call) rather than haggled for a few
            # percent and lost; a floor the shop states inside my limit is paid the same way (111 closes in 6 h).
            print(f"[buyer] {why} {quick} is within my reference {reference} and limit; taking it", flush=True)
            d = {"action": "offer", "price": quick}
            buyer_policy.emit("buyer_quick_offer", item=item, task=tid[:8], round=round_index + 1, why=why, price=quick,
                              reference=reference)
        elif not offers and haggle.get("theta") and isinstance(ask, (int, float)) and ask > 0:
            # the shop's remembered threshold (profiles.threshold): open where it usually starts to accept. When that
            # is at or over my cap, the cap is my one offer (the guardian's final ceiling), then the order moves on
            open_at = max(1, min(int(round(ask * haggle["theta"])), int(ask)))
            if open_at >= guardian.limit:
                d = {"action": "offer", "price": guardian.limit}
                round_index = max(round_index, 2)
                print(f"[buyer] this shop accepts at about {open_at} ({haggle['theta']} of its ask), at or over my cap "
                      f"{guardian.limit}; one offer at the cap", flush=True)
            else:
                d = {"action": "offer", "price": open_at}
                print(f"[buyer] opening at {open_at}: this shop has accepted from {haggle['theta']} of its ask ({ask})", flush=True)
        elif not offers and opening_offer(ask, reference, discount, open_at_least):
            d = {"action": "offer", "price": opening_offer(ask, reference, discount, open_at_least)}   # the opener is code (no model call)
            print(f"[buyer] opening at {d['price']} ({discount:.0%} under the ask {ask}, capped at my reference {reference}, "
                  f"at least the shop's usual {open_at_least})", flush=True)
        elif offers and not shop_moved(offers[-2][1] if len(offers) > 1 else ask0, ask):
            # the shop is not bargaining (under 3% off its last ask): my last word is the pay cap, offered as the
            # guardian's final ceiling (round 2 semantics) without a model round
            d = {"action": "offer", "price": guardian.limit}
            round_index = max(round_index, 2)
            print(f"[buyer] the shop holds near {ask}; my last word is the pay cap {guardian.limit}", flush=True)
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
            if targets and cost:
                info["kept_est"] = round(objective.score(d["price"], value_cap, cost, targets["formula"]), 3)
            return "deal", d["price"], receipt_of(t), log, manip, info
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            # 00:30: 111 closes in 6 h came as "No deal. Lowest for this unit: N" with the task already over. When N is
            # inside my limit and near my reference, one new task is opened and N is paid at once.
            floor = stated_floor(status_text(t))
            # 11-11: stated floors ran at 1.13x my reference (market x 0.92), so 83% of them were left on the table
            # at the old 1.05; 17:30: the limit is now the pay cap (0.70 of list), so any floor inside it is paid
            if floor and floor <= guardian.limit and not info.get("reopened"):
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
                            if targets and cost:
                                info["kept_est"] = round(objective.score(decision["price"], value_cap, cost, targets["formula"]), 3)
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
    if outcome == "overpriced":   # a fixed-price shop whose ask was over what I pay for that grade: nothing was offered
        return 3, f"The shop's ask for {item} was above what I pay for a unit of that grade; no offer was made.", honest
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


WARRANTY_SHOP_SHARE = 0.8   # a shop whose settled goods came with a warranty this often is trusted with a grade C unit


def accept_constraints(order, shop=None, mem=None, grade=None, shop_profiles=None):
    """What the kit must verify before paying. A settled deal is worth the list price whatever the grade (agent.md),
    so for goods only the grade floor is kept (A/B: fewer breaks) and the certificate is not demanded: 11-04 02:30,
    20 agreed goods deals had been refused only for "no certificate". Food keeps both: the certificate is what proves
    the shelf life the order depends on. 11-11 18:50: at a shop whose goods usually carry a warranty (`shop`, `mem`)
    a grade C unit is accepted too, with the warranty required: C goods cost 25-45% of list (YAHO1212 settles C at
    0.52, A at 0.70) and a break under warranty is refunded (scores 0, not -1); 14 such units were refused in 30 min.
    19:20: the warranty is demanded only when the unit negotiated is grade C (`grade`): demanded for every goods deal
    at such shops it refused 4 A/B deals at checkout ("warranty 0 h") in 30 min against 2 C deals won."""
    if not order.get("acceptable"):
        return None
    if order.get("kind") == "food":
        # 11-06 18:00: the kit knows the unit's shelf life with or without a certificate (its refusals quote the hours
        # left either way), and the only shop refused for "no certificate" (elm-street, 80 agreed deals) sells food
        # whose value is the list price like anyone's. The hours check stays; the certificate flag goes.
        return {"min_hours_left": int(math.ceil(order.get("use_within_hours") or 0))}
    share = buyer_policy.warranty_share(mem, shop.get("name"), shop_profiles) if shop and mem is not None else None
    if grade == "C" and share is not None and share >= WARRANTY_SHOP_SHARE:
        return {"grades": ["A", "B", "C"], "warranty_hours": 1}
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
    # 11-17: the three memories an order reads. The rulebook says which score is in force; consolidate() moves rows
    # older than 48 h into the archive and returns the per-shop profiles and my running score under that rule.
    book = rulebook.load(MEMORY.parent)
    t0 = time.time()
    prof, archived = profiles.consolidate(mem, MEMORY.parent, book["formula"])
    if archived:
        save_memory(mem)
    shop_profiles = prof["shops"]
    base = prof.get("saved") if prof.get("saved") is not None else DEFAULT_SCORE
    order, catalog = load_round3(a.order)
    targets = {"formula": book["formula"], "kind": order.get("kind"),
               "pay": round(max(0.0, base - params["buyer_kept_slack"]), 3),
               "quick": round(base + params["buyer_quick_gain"], 3)}
    buyer_policy.emit("buyer_memory", formula=book["formula"], rules_changed_at=book.get("changed_at"),
                      named_formula=book.get("named_formula"), hot_rows=len(mem), archived_now=archived,
                      archived_rows=prof.get("archived_rows"), shops=len(shop_profiles), saved=prof.get("saved"),
                      pay_target=targets["pay"], quick_target=targets["quick"], ms=int((time.time() - t0) * 1000))
    category = order.get("category") or a.item
    reference = buyer_policy.item_reference(mem, category, fallback=category_reference(order, catalog))
    selector_guardian = buyer_policy.MandateGuardian(a.budget, reference)
    candidates = prepare_round3_shops(shops, order, catalog, selector_guardian)
    names = shop_names(shops, mem, MEMORY.parent)
    levels = shop_price_levels((ledger.load_json(MEMORY.parent / ledger.CACHE_FILE) or {}).get("rows"), catalog, names)
    for s in candidates:                      # what each shop's settles cost against list, for the router's cheapness
        s["_price_ratio"] = levels.get(s.get("name")) or levels.get((s.get("url") or "").rstrip("/"))
        s["_kept"] = (shop_profiles.get(s.get("name")) or {}).get("kept")   # what a deal there scores for me, and
        s["_kept_base"] = base                                              # my average to measure it against
    cooled = [s for s in candidates if cooling_off(mem, s.get("name"))]
    if cooled and len(cooled) < len(candidates):
        print(f"[choose] skipping {[s.get('name') for s in cooled]}: answered nothing on their last visits", flush=True)
        candidates = [s for s in candidates if s not in cooled]
    # 11-06 18:00: 32 goods broke with no warranty to refund them, 12.6M lost, 9.2M of it on eight units over
    # 500k; every break under a warranty was refunded (36 of 36). Dear goods go to shops that give one.
    prefer_warranty = order.get("kind") == "goods" and (reference or 0) >= WARRANTY_MATTERS_FROM
    pick = choose(candidates, category, mem, reference, params["explore_rate"], prefer_warranty, shop_profiles)
    request = choose_request(pick, catalog, order)
    wanted = request["sku"] if request else a.item

    stats = ledger.load_stats(MEMORY.parent)   # what the market lately paid: the bargaining anchor per SKU
    food_need = order.get("use_within_hours") if order.get("kind") == "food" else None
    grades = tuple((accept_constraints(order, pick, mem, "C", shop_profiles) or {}).get("grades") or ())   # units worth negotiating at this shop
    sc = scarcity(mem, category)
    slack, discount, posture = regime(sc, params["first_offer_discount"])
    buyer_policy.emit("buyer_regime", item=category, scarcity=round(sc, 2) if sc is not None else None,
                      posture=posture, quick_slack=slack, first_offer_discount=discount)
    category_ref, tried, bargained = reference, {}, 0   # tried: shop url -> SKUs it was asked for; bargained: shops that refused
    for attempt in range(1, 7):   # day 1: 15 orders died on a shop whose card came back as an error page; now the order moves on
        tried.setdefault(pick["url"], set()).add(wanted)
        value_cap = catalog.get(wanted, {}).get("list_price")
        priced = isinstance(value_cap, (int, float)) and value_cap > 0
        level = pick.get("_price_ratio")   # what this shop's settles cost against list on the ledger (24 h)
        open_at_least = int(level * value_cap) if priced and isinstance(level, (int, float)) and level > 0 else None
        known = shop_profiles.get(pick["name"]) or {}
        haggle = {"theta": known.get("theta"), "fixed": known.get("fixed")}
        reference = sku_reference(wanted, catalog, category_ref, stats)
        try:
            outcome, agreed, receipt, log, manip, info = negotiate(
                pick["url"], wanted, a.budget, reference,
                bargain_record(pick["name"], mem, discount), request, value_cap,
                food_need, catalog.get(wanted, {}).get("shelf_hours"), grades, slack, discount,
                targets if priced else None, haggle, open_at_least
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
        elif outcome == "walked_away" and info.get("first_offer") is None and info.get("pay_cap"):
            # 11-17 19:39 (usagi, a grade C unit asked at 0.67 of list against a cap of 0.54): even my opener was over
            # the cap, the guardian refused it and no offer was made. That is an ask too high, not a bargain lost
            outcome = "overpriced"
        stars, text, honest = review(wanted, outcome, agreed, receipt, reference, manip,
                                     set(order.get("acceptable") or ()), catalog)
        if outcome == "deal" and not info.get("checkout_authorized"):
            print("[guard] deal was not authorized for checkout; discarding its receipt", flush=True)
            outcome, agreed, receipt, stars, text = "rejected_checkout", None, None, 1, "Checkout exceeded the private mandate."
        mem.append({"order": order.get("order"), "shop": pick["name"], "item": category, "sku": wanted,
                    "name": request["name"] if request else None, "outcome": outcome, "price": agreed,
                    "list_price": catalog.get(wanted, {}).get("list_price"), "honest": honest, "manipulative": manip,
                    "stars": stars, "reference": reference, "policy": "memory-v2", "settled": False,
                    "at": time.strftime("%Y-%m-%d %H:%M"), "kind": order.get("kind"),
                    # 11-17: what the shop said its unit was and what I was prepared to pay for it are remembered
                    # for every visit, not only for the deals the market later certifies
                    "unit_grade": info.get("grade"), "pay_cap": info.get("pay_cap"), "kept_est": info.get("kept_est"),
                    **{k: info.get(k) for k in ("ask", "first_offer", "last_offer", "guard_decisions", "checkout_authorized")}})
        save_memory(mem)
        bargained += outcome in ("closed", "walked_away") and not no_offer
        if not order_goes_on(outcome, no_offer, bargained, attempt):
            break
        request = choose_request(pick, catalog, order, exclude=tried[pick["url"]]) if no_offer else None
        if request is not None:
            print(f"[choose] same shop {pick['name']}, next SKU {request['sku']} after {outcome}", flush=True)
        else:
            rest = [s for s in candidates if s.get("url") not in tried and s not in blacklisted(candidates, mem, shop_profiles)]
            if not rest:
                break
            pick = fallback_choice(rest, mem, category, category_ref, params["explore_rate"], prefer_warranty, shop_profiles)
            request = choose_request(pick, catalog, order)
            print(f"[choose] retry with {pick['url']}: {pick['name']}. picked by portfolio policy after the last shop "
                  f"{'had no fitting stock' if no_offer else 'asked too much' if outcome == 'overpriced' else 'failed'}", flush=True)
        wanted = request["sku"] if request else a.item
        grades = tuple((accept_constraints(order, pick, mem, "C", shop_profiles) or {}).get("grades") or ())   # per shop: C only with a warranty

    accept = accept_constraints(order, pick, mem, info.get("grade"), shop_profiles)   # the warranty is demanded only for a grade C unit
    print(json.dumps({"shop": pick["url"], "receipt": receipt, "stars": stars, "review": text,
                      **({"accept": accept} if accept else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
