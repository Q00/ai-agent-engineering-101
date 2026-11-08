"""Round 3 seller: sells real units from the stock the kit keeps in $MARKET_DIR/inventory.json, over A2A v1.0.

    python seller_r3.py --port 9301

Week 7 showed that fast, certain deals win buyers, so every price here is set in code from the unit's grade,
list price and cost, and a reply never waits on a model. The restock agent (restock.py) is where the model works.

- Opening ask: the shop manager's share of list price for the category, times the grade factor and the stale
  markdown, times a multiplier chosen by Thompson sampling from our own deal outcomes (see ARMS).
- First message: find the unit the buyer asked for (SKU first, then name, then category) and answer with the
  facts the buyer's kit checks before paying (grade, certificate, hours left, warranty) and the ask, which is
  always the last number in the reply.
- Never below cost: the floor is cost plus FLOOR_MARGIN.
- An offer at or above the ask closes at the ask; an offer near it closes at the offer; anything lower gets a
  counter that gives up a shrinking share of the room down to the floor (STEPS).
- The receipt claims the unit's true grade, keeps the market's certificate on and carries the warranty we
  promised in the reply, so the ledger shows every claim as honest.
Before Round 3 there is no stock: the week-7 items sell at their reserve, as in Round 2.
"""
import argparse
import base64
import json
import math
import os
import random
import re
import time
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

NAME = os.environ.get("SHOP_NAME", "kimauve")
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2: the week-7 items

# the opening ask as a share of the list price for a grade-A unit: the shop manager (planner.py) sets it per
# category in .market/prices.json, else DEFAULT_SHARE. The buyer's value is the list price, so a lower ask is
# the buyer's surplus. Lower grades open lower, and stock held over a day is marked down; both stay in code.
DEFAULT_SHARE = 0.85
GRADE_FACTOR = {"A": 1.0, "B": 0.92, "C": 0.85}   # C 0.80 until Nov 4 14:00 (market pays C 0.67-0.70 of list vs A 0.78-0.80)
STALE_MARKDOWN = 0.05     # per day in stock, up to 3 days
_prices = {"t": 0, "rules": {}}
FLOOR_MARGIN = float(os.environ.get("SHOP_FLOOR_MARGIN", "0.05"))
CLOSE_GAP = 0.03          # an offer within 3% of the ask closes at the offer
MAX_DISCOUNT = 0.10       # haggling never takes the ask more than 10% below the opening ask
MAX_ROUNDS = 6
STEPS = (0.4, 0.25, 0.2, 0.15)   # each counter gives up this share of the room between opening ask and floor
# Learning the price from our own talks: Thompson sampling, a bandit (the simplest kind of reinforcement
# learning). Each category has ARMS, multipliers on the manager's share. Per arm we count talks that ended in a
# deal (success) or without one: the buyer took another shop's quote, walked after haggling, or we refused
# (failure). For each new buyer, every arm draws a deal chance from its Beta posterior and we open with the arm
# whose draw times its margin (price minus cost) is highest. Arms rarely tried draw widely, so they still get
# tried; arms that lose stop being picked. A category with few talks borrows from all categories' counts,
# weighted by POOL. Counts live in .market/price_bandit.json.
ARMS = (0.88, 0.94, 1.0, 1.06)
POOL = 0.2
# Exploring costs reviews too: a buyer who leaves rates us, and stars are a fifth of the shop's score. So the
# test starts from the manager's price (arm 1.0 begins as if it had closed PRIOR_DEALS deals; the others must earn
# their turn), and no arm opens above MAX_SHARE of list, so even a deal at our first ask stays well below list.
PRIOR_DEALS = {"1.0": 2}
MAX_SHARE = 0.95          # 0.92 until Nov 4 13:00: our sales averaged 0.62 of list, the top shops 0.79-0.86
# Big-ticket goods open at most BIG_MAX_SHARE (Nov 4 17:40): the leader sold 97 of the 2.32M sofa in 1.5 h at a
# median 0.80 of list while ours sat for up to 3.7 h at 0.79-0.88; reviews of ours at 87-89% of list gave 3 stars.
# Opening at 0.86 with our up-to-10% concession closes near 0.80.
BIG_TICKET, BIG_MAX_SHARE = 1_000_000, 0.86
# Cheap goods and food open at most CHEAP_MAX_SHARE (Nov 5 01:40): each review counts the same in our stars
# whatever the price, and ours fall with the price paid (1.77 h of ledger results: 0.70-0.75 of list 4.6 stars,
# 0.80 4.34, 0.85 4.07, 0.90 3.82, 0.95 2.72; 120 of our 261 reviews were at 0.85+), while a 20k sale at 0.95
# instead of 0.80 earns 3k. Stars are 0.2 of shop ALL and ours sat at the 0.32 percentile (4.58 vs 4.72 at 0.75).
CHEAP_TICKET, CHEAP_MAX_SHARE = 100_000, 0.80
# 0.86 fits SKUs the market buys at ~0.80, not cheaper ones (Nov 5 02:20): we bought 20 hotel bed sets (G03d0199f,
# list 1.61M, 19.4M) at 01:43 and opened them at 0.86 while the market paid a median 0.72 (ledger 1.48 h, 48.7 sold
# and 63.5 supplied per hour); 35 min later 1 sold, 18 held. A big-ticket SKU now opens at most its recent market
# median (restock's last 10 settles, at least 5) plus BIG_OVER_MARKET; the Jakomo sofa and Simmons bed (0.80) keep 0.86.
BIG_OVER_MARKET, BIG_MIN_SETTLES = 0.06, 5
# The same median rule now holds for mid-priced goods (list 100k-1M, cap stays MAX_SHARE) (Nov 5 04:31-06:17):
# our 19 reviews of them averaged 3.42 stars (0.85 of list 4.75 on 4, 0.90-1.0 3.21 on 14), and 20 goose duvets
# (G05277ffe, list 600k) sat up to 9.2 h opening at 0.85-0.91 while the market's median was 0.78.


# A big-ticket SKU that many shops sell opens at the market median itself, not above it (Nov 5 19:00-Nov 6 04:20
# log, our first ask per talk): Jakomo G8ec51935 (15 shops sold it in the last hour) closed 30 of 67 talks opened
# under 0.78 of list and 20 of 155 opened at 0.78+, and none of 11 talks after 02:50 (asks 0.80-0.86, market median
# 0.77); Simmons Gbec2b68f (8 shops) 9 of 31 vs 1 of 17. The LG styler G7978c65e that only we sell closed 8 of 11
# at 0.78+, so the premium stays where few shops sell the SKU (restock's sku_shops, sellers seen in the last hour).
CROWDED_SHOPS = 8


def crowded(sku):
    m = (_load("restock_state.json", {}).get("sku_shops") or {}).get(sku) or {}
    return sum(1 for t in m.values() if time.time() - t < 3600) >= CROWDED_SHOPS


# Grade C food opens at most CHEAP_C_SHARE (Nov 8): since Patch 3.4 stars are one mean per reviewing account
# (stars_rank 4.32, 28th percentile; p50 4.35, p90 4.43), and since Nov 7 23:00 our reviews by condition were
# food A 4.85, food B 4.73, food C 4.32 (196 reviews, 18 low), the only group under 4.6.
CHEAP_C_SHARE = 0.70


def max_share(u):
    if u["list_price"] < CHEAP_TICKET:
        return CHEAP_C_SHARE if u.get("grade") == "C" and u.get("kind") == "food" else CHEAP_MAX_SHARE
    cap = BIG_MAX_SHARE if u["list_price"] >= BIG_TICKET else MAX_SHARE
    px = sorted((_load("restock_state.json", {}).get("sku_px") or {}).get(u.get("sku")) or [])
    if len(px) >= BIG_MIN_SETTLES:
        over = 0.0 if u["list_price"] >= BIG_TICKET and crowded(u.get("sku")) else BIG_OVER_MARKET
        return min(cap, px[len(px) // 2] + over)
    return cap
TYPICAL_CLOSE = 0.95      # a deal closes at about this share of the opening ask, after haggling
SHARE_BOUNDS = (0.55, 0.97)
CLEARANCE_HOURS = 12      # food this close to its date opens 20% lower and may go below cost: unsold, it is a full loss
CLEARANCE_FLOOR = 0.6     # share of cost
WARRANTY = {"A": 6}       # goods grade -> warranty hours; B and C goods carry none
# A warranty refunds the whole price if the unit breaks within it, and goods break only in the 6 h after the sale,
# so a 6 h warranty costs (break rate) x price. The market does not publish the rate, but every break is a
# "broken" entry on the ledger: restock.py counts them per grade against goods settles (.market/break_rates.json).
# The warranty stays while the grade's rate (with a prior of 1 in 20) is under MAX_BREAK_FOR_WARRANTY.
MAX_BREAK_FOR_WARRANTY = 0.10
# Our buyer collects every competitor's first ask (.market/competitors.json); we open at most UNDERCUT of the
# lowest recent one for the same SKU, never under the floor.
UNDERCUT, COMP_FRESH = 0.98, 6 * 3600
# A rival ask under RIVAL_MIN of list is a misread, not a price: supply costs ~0.6 of list, and until 41fc22c our
# buyer read e.g. "Ed25519" as an ask of 25519 for 0.1-0.4M mattresses (competitors.json Nov 5 00:20: of 1,850
# asks, 93 under 0.15 of list and 17 more under 0.4); since Nov 4 11:00, 138 of our openings were cut toward cost.
RIVAL_MIN = 0.4
GRADE_RANK = {"A": 0, "B": 1, "C": 2}
SHORT_MARKET = 0.4        # no undercutting while this share of the market's orders ends without a deal
# Scarcity: when much of the market's demand goes unmet (pulse.json, the no-deal share of recent results that
# restock.py reads off the ledger) or no other shop held the category when our buyer last saw it, buyers have
# nowhere else to go, so we open higher. Both stay under MAX_SHARE.
SHORTAGE_PREMIUM = 0.10    # times (no-deal share - 0.3), so 0.6 unmet opens 3% higher
ALONE_PREMIUM = 0.06
# Cover = hours the market's stock of the category lasts at its sales rate (market_state.json, restock.py).
# Short categories open higher, glutted ones lower (Nov 4 13:40: most categories under one hour).
COVER_STEPS = ((0.33, 0.06), (1.0, 0.03), (10.0, 0.0))   # cover under h -> premium; above the last: GLUT_CUT
GLUT_CUT = -0.05
# Supply opens once every 20 minutes for 3 (agent.md): right after a window every shop is full, and as the cycle
# runs on, rivals sell out while orders keep coming. So the opening share rises with the time since the last
# window, by up to CYCLE_PREMIUM just before the next one: we sell more of our stock when buyers have fewest
# alternatives, and keep some for then instead of selling all of it cheap in the first minutes.
CYCLE_PREMIUM = 0.08
CYCLE = 20 * 60
# Unsold inventory contributes cost to Book. The deadline alone does not justify
# selling goods below cost; only genuinely expiring food has a clearance policy.
END_TS = 1794927540        # Tue Nov 17 23:59 KST
ENDGAME = ()
MIN_FOOD_HOURS = 2        # never ship food with less shelf life left than this
FOOD_PAYMENT_BUFFER = 1 / 60  # one minute for checkout; rechecked before signing
SOLD_HOLD = 120           # seconds a sold unit stays out of offers, until inventory.json drops it
# A talk the buyer leaves without a reply or a cancel stayed in self.open, so its unit was never offered again
# (Nov 6 10:24-14:17: of 1,439 talks 1,437 ended within 60 s, but 7 grade A Jakomo G8ec51935 units, cost
# 1.75-1.88M, were each offered a few times and then never again, unsold, so the opening ask climbed unit by unit
# from 1,838,575 to 2,065,671; the B unit (cost 1.11M) was last offered 11:56, the C unit 12:32). A talk holds its
# unit OPEN_HOLD seconds; a late reply whose unit has gone to another talk or sold gets no deal.
OPEN_HOLD = 300
# A food order is worth nothing to the buyer unless the food lasts its use_within_hours (agent.md), and our seller
# never sees the order. So: ship food that lasts what the buyer says it needs ("must last 30 hours"), else what
# orders usually need (.market/use_within.json, the use_within_hours our own buyer saw, 80th percentile).
NEED = re.compile(r"(?i)\b(?:last(?:s|ing)?\D{0,15}?|use(?:d)?\s+within\s+)(\d{1,4}(?:\.\d+)?)\s*h(?:ours?)?\b"
                  r"|\buse_within_hours\s*[:=]\s*(\d{1,4}(?:\.\d+)?)\b")
DEFAULT_NEED = 24
ACCEPT = re.compile(r"\b(ok|okay|yes|deal|accept|accepted|agree|agreed|take it|buy it|sold)\b", re.I)


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} [seller] {msg}", flush=True)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def inventory():
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hours_left(u):
    return None if not u.get("expires_at") else (u["expires_at"] - time.time()) / 3600


def need_hours(text):
    """Hours the buyer says the food must last, or None when it does not say."""
    m = NEED.search(text or "")
    return float(next(g for g in m.groups() if g is not None)) if m else None


# Orders ask food to last 0.5 or 0.75 of the SKU's catalog shelf_hours (G00c60e91 coffee, shelf 720: 360 or 540).
# Buyers that do not say so rate a shorter unit 2 stars ("Only 410 h of shelf life left; I needed 540 h").
NEED_SHARE = 0.75
_SHELF = {}


def default_need(sku):
    if not _SHELF:
        cat = _load("catalog.json", [])
        _SHELF.update({x["sku"]: x.get("shelf_hours") for x in (cat if isinstance(cat, list) else cat.values())})
        _SHELF.setdefault("", None)
    if _SHELF.get(sku):
        return NEED_SHARE * _SHELF[sku]
    seen = sorted(_load("use_within.json", []))
    return seen[int(len(seen) * 0.8)] if len(seen) >= 5 else DEFAULT_NEED


def find_unit(text, taken=()):
    """The in-stock unit for what the buyer asked (SKU, then name, then category). Among several units of the
    same product: goods ship the best grade, food the one that expires first but still has MIN_FOOD_HOURS."""
    want = text.lower()
    inv = inventory()
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": RESERVE[item], "cost": RESERVE[item],
                         "kind": "goods", "grade": "A", "unit": None, "expires_at": None, "cert": False}
    said = need_hours(text)
    need = lambda u: max(MIN_FOOD_HOURS, said if said is not None else default_need(u["sku"]))
    units = [u for u in inv.get("units", []) if u["grade"] != "expired" and u["unit"] not in taken
             and (u["kind"] != "food" or (hours_left(u) or 0) >= MIN_FOOD_HOURS)]
    # With no stated need, prefer the inferred usual need. An explicit requirement
    # is a hard constraint, including time for payment; never substitute a shorter unit.
    fresh = [u for u in units if u["kind"] != "food" or
             (hours_left(u) or 0) >= need(u) + FOOD_PAYMENT_BUFFER]
    for match in (lambda u: u["sku"].lower() in want,
                  lambda u: (u.get("name") or "").lower() in want,
                  lambda u: (u.get("category") or "").split(">")[-1].lower() in want):
        fit = [u for u in units if match(u)]
        if fit:
            if fit[0]["kind"] == "food":
                ok = [u for u in fresh if match(u)]
                if not ok and said is not None:
                    log(f"[quality] no matching food lasts the requested {said:g}h plus checkout buffer")
                    return None
                return (min(ok, key=lambda u: u["expires_at"] or math.inf) if ok
                        else max(fit, key=lambda u: u["expires_at"] or 0))
            # a unit whose floor (cost x 1.05) is over the most it may open at (max_share) can only sell above the
            # market, so it goes behind the others (Nov 5 10:27-12:17: 4 surge-cost grade A Zinus Q mattresses
            # G1515d951, cost 380-464k, opened at list 423810 vs a market median 0.825; 15 deals, 3 settled, while 11
            # B/C units of it, cost 161-353k, were never offered and sat 11-15 h).
            # Among such units the cheapest one goes first (Nov 6 09:10-10:17: 9 grade A Jakomo G8ec51935 at cost
            # 1.78-1.97M; the oldest, cost 1,967,305, opened every talk at its floor 2,065,671 and none sold, while
            # the market sold 57 grade A at a median 1,847,880, 24 of them at or over our cheapest unit's floor
            # 1,870,531 and 5 at or over 2,065,671)
            cap = {}
            for u in fit:
                if u["sku"] not in cap:
                    cap[u["sku"]] = u["list_price"] * max_share(u)
            over = lambda u: math.ceil(u["cost"] * (1 + FLOOR_MARGIN)) > cap[u["sku"]]
            return min(fit, key=lambda u: (over(u), u["grade"], u["cost"] if over(u) else u["supplied_at"]))
    return None


def _load(name, default):
    try:
        return json.loads((MARKET_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


BANDIT = _load("price_bandit.json", {"all": {}, "cats": {}})   # arm -> [deals, talks without one]


def base_share(category):
    if time.time() - _prices["t"] > 30:
        _prices["rules"] = _load("prices.json", {}).get("rules", _prices["rules"])
        _prices["t"] = time.time()
    return _prices["rules"].get(category, DEFAULT_SHARE)


def pick_arm(u, base, floor):
    """Thompson sampling: the arm whose sampled deal chance times margin is highest, and the opening ask it gives."""
    cat = BANDIT["cats"].get(u.get("category"), {})
    best = None
    for arm in ARMS:
        k = str(arm)
        s_, f_ = cat.get(k, [0, 0])
        gs, gf = BANDIT["all"].get(k, [0, 0])
        theta = random.betavariate(1 + PRIOR_DEALS.get(k, 0) + s_ + POOL * gs, 1 + f_ + POOL * gf)
        ask = max(floor, int(round(u["list_price"] * min(max(base * arm, SHARE_BOUNDS[0]), max_share(u)), -1)))
        score = theta * (ask * TYPICAL_CLOSE - u["cost"])
        if best is None or score > best[0]:
            best = (score, arm, ask, theta)
    return best[1], best[3]


def learn(category, arm, deal):
    """One finished talk opened with `arm`: a deal is a success, anything else a failure."""
    if not category or arm is None:
        return
    i = 0 if deal else 1
    for counts in (BANDIT["cats"].setdefault(category, {}), BANDIT["all"]):
        counts.setdefault(str(arm), [0, 0])[i] += 1
    tmp = MARKET_DIR / "price_bandit.tmp"
    tmp.write_text(json.dumps(BANDIT, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MARKET_DIR / "price_bandit.json")
    log(f"[bandit] {category.split('>')[-1]}: arm {arm} {'deal' if deal else 'no deal'}, "
        f"counts {BANDIT['cats'][category]}")


STALE_FLOOR = 0.88


def market_price(u):
    """Median price the market paid for this SKU lately (restock_state sku_px, shares of list), or None."""
    px = sorted((_load("restock_state.json", {}).get("sku_px") or {}).get(u.get("sku")) or [])
    return int(px[len(px) // 2] * u["list_price"]) if len(px) >= BIG_MIN_SETTLES else None


def terms(u):
    """(ask, floor, warranty hours, bandit arm or None) for one unit."""
    if u["unit"] is None:
        return u["cost"], u["cost"], 0, None
    floor = math.ceil(u["cost"] * (1 + FLOOR_MARGIN))
    days = min(3, int((time.time() - u.get("supplied_at", time.time())) // 86400))
    share = base_share(u.get("category")) * GRADE_FACTOR.get(u["grade"], 0.8) * (1 - STALE_MARKDOWN * days)
    arm = None
    share *= scarcity(u.get("category"))
    left = END_TS - time.time()
    end = next(((f, fl) for h, f, fl in ENDGAME if left < h), None)
    if end:                                   # sell-off: not a price test either
        share *= end[0]
        floor = max(1, math.ceil(u["cost"] * end[1]))
    elif u["kind"] != "food" and days >= 1 and (mk := market_price(u)) and floor > mk:
        # goods held a day or more and bought above what the market now pays (surge cost): sell at the market price,
        # down to STALE_FLOOR of cost. Nov 9 06:30: 128M of our 146M stock was three SKUs (Downing 21 units at 2.61M
        # vs market 2.55M, Jangsu bed 27 at 1.62M vs 1.44M, Jakomo 17 at 1.73M vs 1.71M), 37 of 65 units over 24 h,
        # held by the cost x 1.05 floor. Holding costs storage (1%/day, ~11.5M to Nov 17); selling at the market
        # costs ~6.5M and returns ~120M of revenue and cash to turn over.
        floor = max(math.ceil(u["cost"] * STALE_FLOOR), int(mk * 0.99))
    elif u["kind"] == "food" and (hours_left(u) or 0) < CLEARANCE_HOURS:
        share *= 0.8                          # clearance: not a price test, the bandit stays out
        floor = math.ceil(u["cost"] * CLEARANCE_FLOOR)
    else:
        arm, theta = pick_arm(u, share, floor)
        log(f"[bandit] {u['category'].split('>')[-1]}: opening with arm {arm} (sampled deal chance {theta:.2f})")
        share *= arm
    ask = max(floor, int(round(u["list_price"] * min(max(share, 0 if end else SHARE_BOUNDS[0]), max_share(u)), -1)))
    rival = rival_ask(u["sku"], u["list_price"], u.get("grade") if u["kind"] == "goods" else "C")
    short = (_load("pulse.json", {}).get("nodeal") or 0) >= SHORT_MARKET
    if rival and rival * UNDERCUT < ask and not short:     # in a short market a buyer takes what it finds
        log(f"[rival] {u['sku']}: lowest competitor ask {rival}, opening at {max(floor, int(rival * UNDERCUT))} not {ask}")
        ask = max(floor, int(rival * UNDERCUT))
    # never ask above list: the market settles at most list, so a surge-cost unit (cost x 1.05 > list) that opened at
    # 469545 on a 423810 mattress closed at 423810 and got 1 star for "Agreed 469545, receipt says 423810"
    ask = min(ask, u["list_price"])
    floor = min(floor, ask)
    warranty = warranty_for(u)
    return ask, floor, warranty, arm


def scarcity(category):
    pulse = _load("pulse.json", {})
    f = 1.0
    win = inventory().get("restock") or {}
    if win and not win.get("open") and win.get("opens_at"):
        phase = 1 - (win["opens_at"] - time.time()) / (CYCLE - 180)
        f += CYCLE_PREMIUM * min(1.0, max(0.0, phase))
    if pulse.get("nodeal") is not None and time.time() - pulse.get("ts", 0) < 600:
        f += SHORTAGE_PREMIUM * max(0.0, pulse["nodeal"] - 0.3)
    c = _load("competitors.json", {}).get("cats", {}).get(category)
    if c and c.get("shops", 9) == 0 and time.time() - c["ts"] < COMP_FRESH:
        f += ALONE_PREMIUM
    ms = _load("market_state.json", {})
    m = (ms.get("cats") or {}).get(category) if time.time() - ms.get("ts", 0) < 600 else None
    if m and m.get("cover") is not None:
        f += next((prem for h, prem in COVER_STEPS if m["cover"] < h), GLUT_CUT)
    return f


def rival_ask(sku, list_price=0, grade="C"):
    """Lowest fresh rival ask for the SKU among units of our unit's grade or better. Grade C asks set the opening of
    our grade A units until Nov 6 08:20: myeonggi's C at 1,484,267 (Nov 6 01:43) and Kiro729's C at 1,707,520
    cut 18 grade A and 5 grade B Jakomo openings by 3.67M in total (04:35-08:17). Asks recorded without a grade
    (before this change, or the shop named none) count as C."""
    mine = GRADE_RANK.get(grade, 2)
    asks = [e[2] for e in _load("competitors.json", {}).get("asks", {}).get(sku, [])
            if time.time() - e[0] < COMP_FRESH and e[1] != NAME and e[2] and e[2] >= RIVAL_MIN * list_price
            and GRADE_RANK.get(e[3] if len(e) > 3 else None, 2) <= mine]
    return min(asks) if asks else None


def warranty_for(u):
    hours = WARRANTY.get(u["grade"], 0) if u["kind"] == "goods" else 0
    if hours:
        broken, sold = _load("break_rates.json", {}).get(u["grade"], [0, 0])
        if (broken + 1) / (sold + 20) >= MAX_BREAK_FOR_WARRANTY:
            return 0
    return hours


def facts(u, warranty):
    """What the buyer's kit checks before paying, stated as it is."""
    if u["unit"] is None:
        return u["name"]
    bits = [f"{u['name']} ({u['sku']})", f"grade {u['grade']}",
            "market certificate included" if u.get("cert", True) else "no certificate"]
    if u["kind"] == "food":
        bits.append(f"{hours_left(u):.0f} hours of shelf life left")
    else:
        bits.append(f"{warranty}h full-refund warranty" if warranty else "no warranty")
    bits.append(f"list price {u['list_price']}")
    return ", ".join(bits)


def last_number(text):
    nums = re.findall(r"\d[\d,]*", text)
    return int(nums[-1].replace(",", "")) if nums else None


def receipt_item(text, unit):
    """Keep a category order's item name, while the explicit unit still fixes the
    shipped SKU. Round 3 accepts either the ordered category or an acceptable SKU.
    A named SKU/product always wins; do not silently substitute a different product."""
    want = text.lower()
    category = (unit.get("category") or "").split(">")[-1]
    named = unit["sku"].lower() in want or (unit.get("name") or "").lower() in want
    return category if category and category.lower() in want and not named else unit["sku"]


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}   # task id -> {"unit", "ask", "floor", "warranty", "rounds"}
        self.sold = {}   # unit -> when we closed it

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        tid = task.id[:8]

        def say(t):
            log(f"[{tid}] {text[:60]!r} -> {t[:240]}")      # one line per message: two weeks of logs must fit
            return up.new_agent_message(parts=[new_text_part(t)])

        deal = self.open.get(task.id)
        if deal is None:
            # units in an open talk, and units sold in the last SOLD_HOLD seconds: inventory.json can be 10 s old,
            # and on Nov 3 one unit went to two buyers a second apart, so the first buyer's checkout was refused
            now = time.time()
            self.sold = {k: t for k, t in self.sold.items() if now - t < SOLD_HOLD}
            self.open = {k: d for k, d in self.open.items() if now - d.get("ts", now) < 3600}
            taken = {d["unit"]["unit"] for d in self.open.values()
                     if d["unit"]["unit"] and now - d.get("ts", now) < OPEN_HOLD} | set(self.sold)
            u = find_unit(text, taken)
            if u is None:
                await up.reject(say("Sorry, none of that in stock."))
                return
            ask, floor, warranty, arm = terms(u)
            # profit is half the shop's score: concede at most MAX_DISCOUNT, and never below cost
            floor = max(floor, int(ask * (1 - MAX_DISCOUNT)))
            deal = self.open[task.id] = {"unit": u, "ask": ask, "opening": ask, "floor": floor,
                                         "warranty": warranty, "rounds": 0, "arm": arm, "ts": now,
                                         "food_need": need_hours(text), "receipt_item": receipt_item(text, u)}
            await up.requires_input(say(f"{facts(u, warranty)}. Send the ask or say ok to buy. "
                                        f"Our ask: {ask}"))
            return

        uid, now = deal["unit"]["unit"], time.time()
        if uid and now - deal.get("ts", now) >= OPEN_HOLD and (
                uid in self.sold or uid not in {u["unit"] for u in inventory().get("units", [])}
                or any(d["unit"]["unit"] == uid and d is not deal and now - d.get("ts", now) < OPEN_HOLD
                       for d in self.open.values())):
            del self.open[task.id]
            await up.reject(say("Sorry, that unit is no longer available. Ask again for a quote."))
            return
        deal["ts"] = now
        if deal["unit"]["kind"] == "food" and need_hours(text) is not None:
            deal["food_need"] = max(deal.get("food_need") or 0, need_hours(text))
        deal["rounds"] += 1
        ask, floor = deal["ask"], deal["floor"]
        offer = last_number(text)
        if offer is None and ACCEPT.search(text):
            offer = ask
        if offer is not None and offer >= ask:
            # a bare number at or over our ask is the buyer's agreed price: the receipt says that price (up to list).
            # Nov 5 00:00-01:40, buyer cheapest-in-town sent list price, we closed at our lower ask, and it rated
            # 21 of our deals 1.38 stars on average ("Agreed 17900, receipt says 16340") vs 3.0 for other shops
            bare = re.fullmatch(r"\s*[\d,]+(\.\d+)?\s*", text or "")
            await self._close(task, up, say, deal, int(min(offer, deal["unit"]["list_price"])) if bare else ask)
        elif offer is not None and offer >= max(floor, ask * (1 - CLOSE_GAP)):
            await self._close(task, up, say, deal, offer)
        elif deal["rounds"] >= MAX_ROUNDS:
            del self.open[task.id]
            if deal["unit"]["unit"]:
                learn(deal["unit"].get("category"), deal["arm"], False)
            await up.reject(say(f"No deal. Our last ask was {ask}."))
        else:
            if offer is not None:            # a third of the gap, but never more than this round's step
                step = STEPS[min(deal["rounds"], len(STEPS)) - 1] * (deal["opening"] - floor)
                deal["ask"] = max(floor, int(round(ask - min((ask - offer) / 3, step), -1)))
            await up.requires_input(say(f"Counteroffer: {deal['ask']}"))

    async def _close(self, task, up, say, deal, price):
        u = deal["unit"]
        if u["kind"] == "food":
            required = max(MIN_FOOD_HOURS, deal.get("food_need") or 0) + FOOD_PAYMENT_BUFFER
            if (hours_left(u) or 0) < required:
                self.open.pop(task.id, None)
                log(f"[quality] refusing {u['sku']}: shelf life no longer meets {required:g}h at signing")
                await up.reject(say("The remaining shelf life cannot meet your requirement through checkout. "
                                    "Please request another quote."))
                return
        receipt = {"shop": NAME, "item": deal.get("receipt_item", u["sku"]), "price": int(price), "task": task.id}
        if u["unit"]:
            # a food unit's grade drops while it waits and the claim is judged at checkout or settle, so a claim
            # read from a 10 s old inventory can turn false: food carries only the market's certificate
            receipt.update(unit=u["unit"], cert=True, warranty_hours=deal["warranty"])
            if u["kind"] != "food":
                receipt["claim"] = u["grade"]
        if u["unit"]:
            self.sold[u["unit"]] = time.time()
        await up.add_artifact(parts=[new_data_part(sign(receipt))], name="receipt")
        await up.complete(say(f"Deal: {u['name']} at {int(price)}."))
        if u["unit"]:
            learn(u.get("category"), deal["arm"], True)
        log(f"DEAL {u['sku']} {u['name'][:30]} grade {u['grade']} at {int(price)} "
            f"(cost {u['cost']}, list {u['list_price']}, warranty {deal['warranty']}h)")
        del self.open[task.id]

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal and deal["unit"]["unit"]:
            learn(deal["unit"].get("category"), deal["arm"], False)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=("Real stock, honest grades. Every reply states the unit's true grade, the market "
                     "certificate, shelf life left (food) and warranty (grade A goods: 6h full refund). "
                     "Prices below list. Answers in milliseconds; send the ask or say ok and the signed "
                     "receipt comes back at once."),
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Buy a unit from stock",
                           description="Ask for a SKU: 'I want to buy <name> (<sku>)'. The ask is the last number "
                                       "in each reply. Send it or say ok to buy.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 포기김치 10kg (G00001)", "ok"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    log(f"{NAME}: round 3 seller, stock from {MARKET_DIR}")
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
