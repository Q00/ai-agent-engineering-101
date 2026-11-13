"""Round 3 seller (ddolcom, from the kit's starter): sells from the stock the kit keeps in $MARKET_DIR/inventory.json,
behind A2A v1.0.

    python seller_r3.py --port 9301

The code decides every price (a concession schedule from the list price down to a floor), so a reply never waits
on the model. The harness only polishes the wording of a reply, and the code's own sentence is used when it is slow.
Every receipt names the unit, claims its true grade and keeps the market's certificate; goods of grade A and B carry
a break warranty and food says how many hours it has left. Food near its date may go below cost (NEAR_EXPIRY).
"""
import argparse
import asyncio
import base64
import json
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

from harness import CMD, ask

NAME = "ddolcom"                             # card and receipt name, the same as the market name (join --name)
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
# ddolcom: food close to its date may go below cost (a small loss beats throwing it away at full cost)
NEAR_EXPIRY = [(0.20, 0.80), (0.45, 0.90)]  # (shelf life left below this share, floor as a share of cost)
# ddolcom: goods break within 6 h of a sale, so 24 h covers every break. 87600 (10 years) failed the market's
# claim_and_warranty_well_formed check: 9 goods deals agreed on 2026-11-03 never settled; 24 h is the most any
# settle in the ledger carried, and a buyer policy asking for up to 24 h still pays
# 2026-11-06: 72 h, as most top shops now settle with. 533 breaks in 5.66 h of ledger all came within 5.97 h, so it costs nothing
WARRANTY_HOURS = 72
# ddolcom: every goods unit carries the warranty at no extra charge. The +20% premium with a 'no warranty' opt-out
# is gone (2026-11-03): no top-8 shop charges for a warranty, and they saw only 1-2 breaks each in Round 3
# no digits in this line: a buyer took the "20" of an earlier "+20%" for our first ask
WARRANTY_LINE = ("Break warranty included at no extra charge: goods can only break in the first hours after a sale, "
                 "and if it breaks the market refunds you in full from our balance.")
# ddolcom: grade C goods carry no warranty (trial from 2026-11-04). Over the first 20 h of Round 3 C broke 27.7% of the
# time in the market (A 2.5%, B 9.3%), which leaves about 2% of list price after refunds; here 13 of 29 C units broke,
# 1,330,855 refunded against 972,322 of margin. 45% of the market's C units sold without a warranty
NO_WARRANTY = {"C"}
NO_WARRANTY_LINE = "Refurbished unit sold as is, without a break warranty."
# ddolcom (2026-11-09 07:45, the user's instruction "c재고에 보증을 한시적으로 붙여서 정리하자"): grade C goods carry the same
# warranty until C_WARRANTY_UNTIL (24 h) to clear 125 C units (cost 47.6M). C costs about 0.35 of list and the market pays about
# 0.59 for C, so even at the old 27.7% break rate about 0.08 of list stays after refunds. Buyers that take only A and B still will not pay
C_WARRANTY_UNTIL = 1794265200          # 2026-11-10 08:00 KST
# ddolcom (2026-11-09 16:40, the user's instruction "C 보증을 종결하고, 대신 가격을 많이 할인해서 팔아줘"): ended early. From 08:26
# 105 C sold with the warranty, 35 broke (33%), 19.9M refunded against about 22.8M of margin; refunds beat C margin two checks running
C_WARRANTY_UNTIL = 1794210000          # 2026-11-09 16:40 KST: over
# ddolcom (strategy 3): prices come from code. ask = floor + (list price - floor) * STEP ** (buyer offers so far)
STEP = 0.6                                  # each buyer offer moves the ask 40% of the way down to the floor
ACCEPT_GAP = 0.92                           # from a buyer's second offer, one at the floor and this share of the ask or more is a deal
# ddolcom (2026-11-12 20:20, the user's "1번부터 5번까지 ... 동시에 진행해", logs/top-shops-1112-2002.txt): margin 9.1% of paid,
# the lowest of the top 20 shops (Nocha12 18.8%, yms979 12.5%); their grade A goods sell at 0.70-0.79 of list against my 0.693.
# Back to 0.92 if my deals an hour fall under 70 (97 over those 6 h)
ACCEPT_GAP = 0.95
# Same plan: grade C goods sold at 0.422 of list against 0.556-0.654 at the top shops (market C goods 0.565, cost 0.473), so
# their floor is never under this share of list (it stays at cost when the cost is higher)
C_GOODS_FLOOR = 0.50
# ddolcom: a buyer that leaves without cancelling kept its unit promised until a restart. On 2026-11-06 after 34 h of
# uptime 10 of 14 high-value SKUs answered "none in stock" with units on the shelf. A negotiation silent this long is
# over; 300 s is above the 180 s a harness call may take between two offers
OPEN_TTL = 300
# ddolcom (trial from 2026-11-06): since the 03:58 restart 7% of opens at 1,000,000 or more became deals, 9% under that
# down to 300,000 and 19% from 100,000; others open near 80% of list. Half of the opens at TRIAL_MIN or more start at
# TRIAL_SHARE of list, half at list, so the log can compare them. "arm" in a [price] line says which
TRIAL_MIN = 100_000
TRIAL_SHARE = 0.85
# ddolcom (2026-11-06 11:40): 85% did not move opens at 1,000,000 or more (low 1 deal of 45, list 2 of 51), where about 85% of
# the market's money is. Market deals there settle near 72% of list, so the low arm of those units now opens closer to that
TRIAL_DEAR_MIN = 1_000_000
TRIAL_DEAR_SHARE = 0.78
# ddolcom (trial from 2026-11-06 22:30): at 1,000,000 or more half of the opens start at 73% (arm low2), half at 78% (arm low). Most
# buyers leave before any offer (G8ec51935: 136 opens, 28 offers) and the market settles near 70%. Judge at 50 opens each by
# conversion x (avg paid - cost); 73% needs about 1.46 times the conversion if the paid price falls as much as the ask
# Ended 2026-11-07 01:23: 73% had 8 deals of 91 opens at 0.687, 78% had 12 of 106 at 0.694, so about 0.83% against 1.14% of list per
# open at a cost of 0.593. The sample is small, but lower asks did not sell more. TRIAL2_RATE 0 keeps every open at 78%
TRIAL2_SHARE = 0.73
# ddolcom (2026-11-07 19:23): goods of grade B and C open closer to what the market pays for that grade (1M+ on 11-06: A 0.726,
# B 0.671, C 0.622 of list), not at the 78% of grade A. G021aae64 had 11 opens and no deal in an hour with B and C units in stock
# Claude (2026-11-08 14:23, hourly check): C held 124 units at a cost of 46.8M, 20.8M of it unsold 12 h+. Over 6 h on 1M+ goods
# the market sold 138 C at 0.592 of list and I sold 3 at 0.66 (A: 69 of 939 at 0.704). C costs 0.25-0.45 of list and has no refunds
GRADE_OPEN = {"B": 0.72, "C": 0.60}
# ddolcom (2026-11-09 16:40, the same instruction): grade C goods sold as is now open well under the market's C price (about 0.59
# of list): 0.45, and at every price (not only the "low" arm of 100,000+). The floor stays the cost (C costs about 0.35 of list)
GRADE_OPEN["C"] = 0.45
# ddolcom (2026-11-12 10:30, the user's "C 등급의 호가를 0.6으로 올려서 시작해"): over 6 h my C goods sold at 0.396 of list while the
# class's top shops sold theirs at 0.46-0.66, mostly without a warranty (logs/top-shops-1112-1008.txt). The floor stays the cost.
# This replaces plan 2 step 5's 0.40 from 11-16
GRADE_OPEN["C"] = 0.60
# Claude (2026-11-11 02:22, hourly check, plan 2 step 2): stars against paid/list (star_price.py, logs/star-price-1111-0226.txt,
# 6 h): my grade C food paid 0.714 of list on average, stars 4.13 (184 results; 0.70-0.75: 106, 4.16, 28% 5*; 0.75+: 26, 3.12)
# against the market's C food at 0.543, 4.45. At 0.55-0.70 mine got 4.57-4.72. Grade C food now opens at most C_FOOD_OPEN of list
# Claude (2026-11-11 05:22, hourly check): reverted (1.0 = off). 02:29-05:22 (logs/star-price-1111-0523.txt) C food paid 0.521
# of list (was 0.714) and still got 4.04 stars, 5* 15% (was 4.13): price was not what the buyers rated, so the 0.19 of list was lost
C_FOOD_OPEN = 1.0
TRIAL2_RATE = 0.0
# ddolcom (2026-11-06, the user's instruction): one clearance only, at cost or a little above, to free the cash. These 6
# units had sat 13-40 h and are the ones the forum post 22459 names. New units never join by age: a rule on age alone also
# priced well-selling, cheaply drawn units near cost. Any further clearance is the user's decision after looking at this one
DEAD_UNITS = frozenset({"u-1bd898b1ae6e", "u-5ad60d23f58b", "u-eacff6b41009",
                        "u-ce3724b8836a", "u-c933d766e8aa", "u-aa2c398a88db"})
DEAD_MARKUP = 1.02
# ddolcom (2026-11-07 21:30, the user's instruction): from now on any goods unit of DEAD_MIN_LIST or more, unsold DEAD_AGE_HOURS
# after it arrived, whose cost is at or above what the market pays for its grade (DEAD_GRADE_SHARE of list, 11-06/07 ledger),
# opens at cost + 2%. The floor stays the cost: nothing goes below it
DEAD_AGE_HOURS = 12
# Claude (2026-11-09 03:23, hourly check): 0.65 reverted to always (inf). With C first, settles fell to about half of the
# night before (01 h 50 against 117, 02 h 51 against 128) and no deals per settle rose from 0.37 to 0.78: buyers walked at C
C_SWITCH = False         # offer the same SKU's grade C when an offer is under the A/B floor (on 02:47-03:44 11-12, see execute)
C_BEHIND_SHARE = float("inf")  # grade C goes after A and B only if an A or B unit costs at most this share of list (find_unit)
DEAD_FIRST_SHARE = 0.5   # share of asks on a named SKU that offer its old units first (see find_unit)
DEAD_MIN_LIST = 100_000
DEAD_GRADE_SHARE = {"A": 0.70, "B": 0.65, "C": 0.60}
# ddolcom (2026-11-06, the user's instruction): this one unit may go below its cost of 1,650,854. Its cost was above the market's
# recent grade A average of 1,603,868 and it had not sold in 14 h. Named units only: any other is the user's decision
BELOW_COST_FLOOR = {"u-aa2c398a88db": 1_600_000}
WORDING_TIMEOUT = 0                         # seconds the harness gets to word a reply; 0 = off (claude -p haiku
                                            # took 8.8 s on 2026-11-03, so the buyer only waited for nothing)
GRADE_MEANS = {"food": {"A": "plenty of shelf life", "B": "normal shelf life", "C": "close to its date"},
               "goods": {"A": "new", "B": "box damaged, item fine", "C": "refurbished"}}

WORDING = """You are the sales agent of an honest online shop. Rewrite the shop's reply below as one or two short,
friendly English sentences. Keep every number and every fact exactly as written; add no new claims or numbers.
Answer with the sentence only.

Reply: {reply}"""


def shelf_left(u):
    """Share of the unit's shelf life still left (1.0 fresh, 0.0 at its date); None for goods or unknown."""
    if not u.get("expires_at"):
        return None
    try:
        hours = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))[u["sku"]]["shelf_hours"]
    except (OSError, ValueError, KeyError, TypeError):
        hours = None
    if not hours:                                   # no catalog: grade C means under 45% left
        return 0.44 if u.get("grade") == "C" else 1.0
    return max(0.0, (u["expires_at"] - time.time()) / (hours * 3600))


# ddolcom (2026-11-13 11:50, the user's "오래된 재고를 원가와 비슷하게 팔다가 그래도 안팔리면 원가 이하로 내려서 팔아도 좋아"):
# goods unsold OLD_BELOW_HOURS (24 h) open at cost (+2%, see is_dead and dead_markup) and come down to the floor; still unsold
# OLD_DEEP_HOURS they may go to OLD_BELOW_SHARE (0.90) of cost whatever the market pays, grade C included (no C_GOODS_FLOOR).
# At 11:44: 24 h+ goods 144 units, of which A/B at cost 39 (1,004만), C above cost 75 (1,711만), already below cost 30 (1,658만)
OLD_DEEP_HOURS = 48


def goods_age_hours(u):
    return (time.time() - (u.get("supplied_at") or time.time())) / 3600 if is_goods(u) else 0.0


def floor_price(u):
    """Lowest price for this unit (see _floor), and for grade C goods never under C_GOODS_FLOOR of list, until OLD_DEEP_HOURS."""
    f = _floor(u)
    if u.get("unit") in BELOW_COST_FLOOR:
        return f
    if goods_age_hours(u) >= OLD_DEEP_HOURS:
        return min(f, int(OLD_BELOW_SHARE * u["cost"]))
    if is_goods(u) and u.get("grade") == "C":
        f = max(f, int(C_GOODS_FLOOR * (u.get("list_price") or 0)))
    return f


def _floor(u):
    """Lowest price for this unit: its cost, or less for food near its date (NEAR_EXPIRY) or a named unit."""
    if u.get("unit") in BELOW_COST_FLOOR:
        return BELOW_COST_FLOOR[u["unit"]]
    left = shelf_left(u)
    for below, share in NEAR_EXPIRY:
        if left is not None and left < below:
            return int(u["cost"] * share)
    if is_goods(u) and time.time() - (u.get("supplied_at") or time.time()) >= OLD_BELOW_HOURS * 3600:
        market = market_grade_price(u)
        if market and u.get("cost", 0) >= market:
            return int(min(u["cost"], max(OLD_BELOW_SHARE * u["cost"], AT_COST_SHARE * market)))
    return u["cost"]


# ddolcom (2026-11-10 23:55, plan 2 step 2 "24시간이 넘은 재고의 경우 원가 보다 할인된 가격으로 팔아도 됨."): goods unsold
# OLD_BELOW_HOURS whose cost is at or above the market price of their grade may go down to AT_COST_SHARE of that market price,
# never under OLD_BELOW_SHARE of cost. At 01:22 that was 21 units, cost 8,081,003, market 0.82-0.99 of their cost
OLD_BELOW_HOURS = 24
OLD_BELOW_SHARE = 0.90


def is_dead(u):
    """A named clearance unit, or old goods whose cost is at or above the market price of their grade."""
    if u.get("unit") in DEAD_UNITS or goods_age_hours(u) >= OLD_BELOW_HOURS:
        return True
    lp = u.get("list_price") or 0
    return (is_goods(u) and lp >= DEAD_MIN_LIST and u.get("grade") in DEAD_GRADE_SHARE
            and time.time() - (u.get("supplied_at") or time.time()) >= DEAD_AGE_HOURS * 3600
            and u.get("cost", 0) >= DEAD_GRADE_SHARE[u["grade"]] * lp)


# ddolcom (2026-11-10 18:22, the user's plan step 4 "4번은 18:22 에 적용합시다."): an old unit whose cost is above AT_COST_SHARE
# of what its grade sells for in the market opens at its cost, not cost + 2%: G03d0199f A at 0.737 and 0.747 of list sat 3 h
# against a market A of 0.751. Market grade A per SKU comes from restock_memory.json (customers.sku_a, A_MIN_DEALS deals);
# B and C use DEAD_GRADE_SHARE. Never below cost
AT_COST_SHARE = 0.97
_SKU_A = {"at": 0.0, "rows": {}}


def market_grade_price(u):
    """What the market pays for this unit's grade, in won, or None."""
    lp = u.get("list_price") or 0
    if u.get("grade") == "A":
        if time.time() - _SKU_A["at"] > 60:
            _SKU_A["at"] = time.time()
            try:
                _SKU_A["rows"] = json.loads(Path("restock_memory.json").read_text(encoding="utf-8"))["customers"].get("sku_a", {})
            except (OSError, ValueError, KeyError, TypeError):
                pass
        row = _SKU_A["rows"].get(u.get("sku"))
        if row and row[0] and row[1] >= 5:
            return row[0] * lp
    share = DEAD_GRADE_SHARE.get(u.get("grade"))
    return share * lp if share and lp else None


def dead_markup(u):
    """DEAD_MARKUP, or 1.0 (open at cost) when the cost is above AT_COST_SHARE of the market price of its grade."""
    market = market_grade_price(u)
    return 1.0 if market and u.get("cost", 0) > AT_COST_SHARE * market else DEAD_MARKUP


def trial_arm(u):
    """The trial ended 2026-11-06 14:33 and low won: at 1,000,000 or more 9 deals of 71 opens against 3 of 63, about the
    same margin per open and 2.3 times the revenue; under that 45 of 106 against 35 of 119, more margin and revenue."""
    if is_dead(u):
        return "dead"
    if u.get("list_price", 0) >= TRIAL_DEAR_MIN and random.random() < TRIAL2_RATE:
        return "low2"
    return "low" if u.get("list_price", 0) >= TRIAL_MIN else None


def ask_price(u, offers, arm=None):
    """The ask after `offers` buyer offers: the first ask, then STEP of the way to the floor each time."""
    floor = floor_price(u)
    if arm == "dead":
        top = max(u["cost"] * dead_markup(u), floor)     # opens at cost (+ markup) even when the floor is under cost
        return int(floor + (top - floor) * STEP ** offers)
    if arm == "low2":
        share = TRIAL2_SHARE
    else:
        share = 1 if arm != "low" else TRIAL_DEAR_SHARE if u["list_price"] >= TRIAL_DEAR_MIN else TRIAL_SHARE
    if (arm == "low" or (arm is None and u.get("grade") == "C")) and is_goods(u) and u.get("grade") in GRADE_OPEN:
        share = min(share, GRADE_OPEN[u["grade"]])
    if u.get("kind") == "food" and u.get("grade") == "C":
        share = min(share, C_FOOD_OPEN)
    top = max(u["list_price"] * share, floor)
    return int(floor + (top - floor) * STEP ** offers)


def is_goods(u):
    return bool(u.get("unit")) and u.get("kind") == "goods"


def has_warranty(u):
    return is_goods(u) and (u.get("grade") not in NO_WARRANTY or (u.get("grade") == "C" and time.time() < C_WARRANTY_UNTIL))


def facts(u):
    """What the buyer can check: the true grade with the certificate, hours left (food) or the warranty (goods)."""
    if not u.get("unit"):
        return ""
    kind = u.get("kind", "goods")
    out = f"Grade {u['grade']} ({GRADE_MEANS.get(kind, {}).get(u['grade'], '')}), market certificate included."
    if kind == "food" and u.get("expires_at"):
        out += f" About {max(0, int((u['expires_at'] - time.time()) / 3600))} hours of shelf life left."
    if is_goods(u):
        out += f" {WARRANTY_LINE if has_warranty(u) else NO_WARRANTY_LINE}"
    return out


async def worded(reply):
    """The harness's wording of the code's reply, or the reply itself when the harness is slow or drops a number."""
    if WORDING_TIMEOUT <= 0:
        return reply
    try:
        text = (await asyncio.to_thread(ask, WORDING.format(reply=reply), WORDING_TIMEOUT)).strip()
    except Exception as e:
        print(f"[wording] {type(e).__name__}, code sentence used", flush=True)
        return reply
    if not text or any(n not in text for n in re.findall(r"\d+", reply)):
        print(f"[wording] dropped a number, code sentence used: {text[:80]}", flush=True)
        return reply
    return text


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


# ddolcom (2026-11-12 20:20, the user's "판매코드가 요청을 읽고 정직하게 대응할 수 있도록 변경해줘"): buyers ask for shelf life
# ("It must have at least 84 hours of shelf life left") and the code did not read it: 17 of my low reviews in about 5 h were
# "Only 30.8 h of shelf life left; I needed 126.0 h." A stated need now picks a unit with that much left, or the shop says it has none
SHELF_KEY = r"(?:shelf|fresh|expir|best before|use within|유통|소비기한|남은)"
SHELF_NUM = r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|days?|d|시간|일)(?![a-z])"
SHELF_RES = [re.compile(SHELF_NUM + r"[^.\n]{0,50}?" + SHELF_KEY, re.I),
             re.compile(SHELF_KEY + r"[^.\n\d]{0,50}?" + SHELF_NUM, re.I)]


def shelf_need(text):
    """Hours of shelf life the buyer says it needs, or None when it states none."""
    for rx in SHELF_RES:
        m = rx.search(text or "")
        if m:
            return float(m.group(1)) * (24 if m.group(2).lower() in ("d", "day", "days", "일") else 1)
    return None


def hours_left(u):
    return (u["expires_at"] - time.time()) / 3600 if u.get("expires_at") else None


def fitting_units(text, taken=()):
    """In-stock units that fit what the buyer asked for (category, name or SKU), not held in another open negotiation."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    return inv, [u for u in inv.get("units", []) if u["grade"] != "expired" and u["unit"] not in taken and
                 ((u.get("category") or "").split(">")[-1].lower() in want or (u.get("name") or "").lower() in want
                  or u["sku"].lower() in want)]


def find_unit(text, taken=(), need_hours=None):
    """The in-stock unit that fits what the buyer asked for (category, name or SKU) and expires first,
    skipping units already promised in another open negotiation and food with less than need_hours of shelf life left."""
    want = text.lower()
    inv, fit = fitting_units(text, taken)
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": int(RESERVE[item] * 1.3), "cost": RESERVE[item],
                         "unit": None, "expires_at": None, "supplied_at": 0}
    if need_hours:
        fit = [u for u in fit if hours_left(u) is None or hours_left(u) >= need_hours]
    # Claude (2026-11-10 02:22, hourly check): a buyer naming the product, not its SKU code, fell to the oldest unit of the category,
    # grade C: G00327e80 (the market's top SKU, A at 0.726) opened its C at 1,011,060 21 times since 00:23 with 0 settles, its
    # two new A (cost 0.60 of list) once. A name match now counts as named, so A and B and the cheapest cost go first
    named = [u for u in fit if u["sku"].lower() in want or (u.get("name") or "").lower() in want]
    # ddolcom (2026-11-07): a named SKU's cheapest unit first, not its oldest, so a floor above the market's price does not block the
    # units that can sell: since 22:26 G8095ddd6 had 62 offers and 4 at or above the floor while its grade A at 0.73 of list stood first
    # and units at 0.38 and 0.57 waited. The units of the forum clearance stay first, as the post says. Food still goes by its date
    # Goods of grade C after A and B (02:28-03:23: 6 of 27 grade C goods receipts settled, 70 of 70 A and 21 of 21 B)
    # Claude (2026-11-08 20:23, hourly check): old units first on every ask blocked whole SKUs when their cost + 2% sat above the
    # market: G03d0199f (29 deals/h in the market) had 17 opens, 23 offers and no deal (offer 1,063,595, floor 1,134,642) while
    # its cheaper units waited. Old units now go first on half of the asks (DEAD_FIRST_SHARE); the other half get the cheapest unit
    dead_first = random.random() < DEAD_FIRST_SHARE
    # Claude (2026-11-09 00:23, hourly check): C waits behind A and B only while one of them can sell at the market's price.
    # Gf6b70da8 had 26 opens and no deal: its two A cost 0.72 and 0.74 of list (market 0.654, best offer 1,596,455 against a
    # floor of 1,680,818) while five C at 0.25-0.38 of list sat 21-33 h behind them
    c_last = any(not (is_goods(u) and u.get("grade") == "C") and u["cost"] <= C_BEHIND_SHARE * (u.get("list_price") or 0)
                 for u in named)
    key = ((lambda u: (u["expires_at"] or float("inf"), not (dead_first and is_dead(u)),
                       c_last and is_goods(u) and u.get("grade") == "C", u["cost"],
                       u["supplied_at"])) if named
           else (lambda u: (u["expires_at"] or float("inf"), is_goods(u) and u.get("grade") == "C", u["supplied_at"])))
    # Claude (2026-11-10 03:22, hourly check): category requests (no SKU, no name) took the oldest unit, grade C: after the name fix
    # G00327e80 still opened its 63 h old C 12 times, 9 deals agreed, 0 settled, while two A sat. Goods of grade C now go last here too
    fit = named or fit                                   # a SKU the buyer named comes first
    return min(fit, key=key, default=None)


def grade_c_unit(sku, taken=()):
    """The cheapest in-stock grade C unit of this SKU not held in another open negotiation, or None."""
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cs = [u for u in inv.get("units", []) if u["sku"] == sku and u.get("grade") == "C" and u["unit"] not in taken]
    return min(cs, key=lambda u: u["cost"], default=None)


def offer_in(text):
    """The buyer's offer: the first number in the message, thousands separators allowed (52,000 -> 52000).
    A number glued to letters (a SKU such as G00001) is not an offer."""
    m = re.search(r"(?<![A-Za-z\d])\d[\d,]*", text)
    return int(m.group().replace(",", "")) if m else None


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offers"}

    async def execute(self, context, event_queue):
        task = context.current_task
        fresh = task is None                             # a new negotiation, not a later message of an old one
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now = time.time()
        for k in [k for k, d in self.open.items() if now - d["seen"] > OPEN_TTL]:
            del self.open[k]                             # the buyer left without cancelling: its unit is free again
        deal = self.open.get(task.id)
        if deal:
            deal["seen"] = now

        if deal is None:                                 # the buyer names what it wants: open at the list price
            need = shelf_need(text)
            unit = find_unit(text, {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]}, need)
            if unit is None:
                # Claude (2026-11-11 13:20, the user's "고쳐줘" on the star study, logs/star-buyers-1111-1315.txt): olstjr gave
                # 1 star for "claimed to be out of stock but then completed the deal". "None in stock" was also the answer when
                # every unit was held in another open negotiation, or when this negotiation had timed out (OPEN_TTL)
                if not fresh:
                    await up.reject(say("This negotiation timed out on our side. Please ask for the item again."))
                elif find_unit(text, (), need):
                    await up.reject(say("Every unit of that is held in another open negotiation right now. "
                                        "Please ask again in a minute."))
                elif need and find_unit(text):
                    best = max((hours_left(u) or 0 for u in fitting_units(text)[1]), default=0)
                    print(f"[shelf] asked {need:g} h, freshest unit {best:.0f} h: declined", flush=True)
                    await up.reject(say(f"Sorry, we have none with at least {need:g} hours of shelf life left: "
                                        f"our freshest unit has about {int(best)} hours left."))
                else:
                    await up.reject(say("Sorry, we have none of that in stock."))
                return
            arm = trial_arm(unit)
            deal = self.open[task.id] = {"unit": unit, "ask": ask_price(unit, 0, arm), "offers": 0, "seen": now, "arm": arm}
            u = deal["unit"]
            # Claude (2026-11-11 13:20, same study): q00 asked for 과자, got G1831f2c0 (Pringles, category 과자) and gave
            # 1 star three times: "receipt names G1831f2c0 ... forges receipts". The SKU now shows before any price is agreed
            reply = f"{u['name']} (SKU {u['sku']}, category {(u.get('category') or '').split('>')[-1]}): asking {deal['ask']}. {facts(u)} Send your offer as a number.".replace("  ", " ")
            print(f"[price] open {u['sku']} at {deal['ask']} (list {u['list_price']}, floor {floor_price(u)}, arm {arm})", flush=True)
            await up.requires_input(say(await worded(reply)))
            return

        u = deal["unit"]
        offer = offer_in(text)
        if offer is None:
            await up.requires_input(say(f"Counteroffer: {deal['ask']}. Send your offer as a number."))
            return
        # Claude (2026-11-12 02:44, hourly check): G03d0199f (16 deals/h in the market) sold 0 in 6 h with 4 in stock: its A
        # (cost 1,207,605, 13 h old) stood first and got offers of 945,338-1,062,214, all under its floor, while three C at
        # 545,095-572,793 waited behind it (C_BEHIND_SHARE). An offer under an A or B floor now gets the same SKU's grade C
        # offered openly, as a new ask the buyer must answer, never a silent switch
        # Claude (2026-11-12 03:44, hourly check): off. 58 switches since 02:47 on 22 C units, 19 of them agreed, but the same units
        # came back up to 7 times and only 2 C goods settled in the hour: buyers agree to the C and then do not pay for it
        if C_SWITCH and is_goods(u) and u.get("grade") != "C" and offer < floor_price(u):
            c = grade_c_unit(u["sku"], {d["unit"]["unit"] for k, d in self.open.items() if k != task.id and d["unit"]["unit"]})
            if c and floor_price(c) <= offer:
                deal.update(unit=c, ask=ask_price(c, 0, deal["arm"]), offers=0)
                print(f"[price] {u['sku']} offer {offer} under grade {u['grade']} floor {floor_price(u)}: "
                      f"offered grade C {c['unit']} at {deal['ask']} (floor {floor_price(c)})", flush=True)
                await up.requires_input(say(f"We cannot go that low on grade {u['grade']}. The same item {c['name']} "
                                            f"(SKU {c['sku']}) in grade C: asking {deal['ask']}. {facts(c)} "
                                            f"Send your offer as a number.".replace("  ", " ")))
                return
        deal["offers"] += 1
        deal["ask"] = max(min(ask_price(u, deal["offers"], deal["arm"]), deal["ask"]), floor_price(u))   # an ask never goes up
        ask = deal["ask"]
        print(f"[price] {u['sku']} offer {offer}, ask {ask} (floor {floor_price(u)})", flush=True)
        # Claude (2026-11-12 08:44, hourly check): G021aae64 (40 deals/h in the market) sold 0 in 2 h. Buyers offered about 320,000,
        # then 360,142 against our 396,978 (floor 359,256) and walked, 19 times. In the hour 170 of 840 offers sat between the floor
        # and the ask. From the second offer, one at the floor and within ACCEPT_GAP of the ask is taken
        if offer < ask and deal["offers"] >= 2 and offer >= floor_price(u) and offer >= ACCEPT_GAP * ask:
            print(f"[price] {u['sku']} offer {offer} taken within {ACCEPT_GAP} of ask {ask}", flush=True)
            ask = offer
        if offer >= ask:
            print(f"[price] deal {u['sku']} at {offer} (list {u['list_price']}, arm {deal['arm']})", flush=True)
            receipt = {"shop": NAME, "item": u["sku"], "price": offer, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
                receipt["claim"] = u["grade"]            # the true grade; the ledger shows claim_honest afterwards
            if has_warranty(u):
                receipt["warranty_hours"] = WARRANTY_HOURS
            receipt = sign(receipt)
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {offer}"
                                  f"{' with the break warranty' if has_warranty(u) else ''}. Signed receipt attached."))
            del self.open[task.id]
            return
        await up.requires_input(say(f"Counteroffer: {deal['ask']}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="A small online shop with real stock and honest grades. Ask for an item by name or SKU. "
                    "Every receipt claims the unit's true grade and every unit ships with the market's certificate; "
                    "food comes with the hours of shelf life left. Grade A and B non-food items come with a break warranty "
                    "at no extra charge: if one breaks, the market refunds you in full from our balance. Grade C "
                    "(refurbished) items are sold without one. Prices are set in code, so replies are fast."
                    + (f" A coding harness ({CMD.split()[0]}) words the opening reply." if WORDING_TIMEOUT > 0 else ""),
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 알뜰맘 포기김치 10kg (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
