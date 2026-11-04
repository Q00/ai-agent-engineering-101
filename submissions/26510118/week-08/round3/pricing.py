"""Round 3 seller prices: S3 (negotiation), S4 (warranty) and S5 (markdowns) in one place.

Everything that turns a unit into an asking price and a floor lives here, as plain
functions of the unit, the clock and the strategy, so the seller and the tests read
the same rules. These are the exploration-phase numbers; the focus phase and the
analysis memos may move them (strategy.json's price_adjust).

    first ask   by grade: A 75%, B 70%, C 60% of the list price (refurbished goods and
                food close to its date start lower than new and fresh)
    floor       the unit's cost + 5%. Never below cost, except when the unit would
                otherwise end at nothing:
                  food with under 24 h left       ask 45%, floor 25% of list
                  unsold for over 7 days          floor 25% of list
                  the last 24 h of the round      ask 15% of list lower, floor 25% of list
                  the last 6 h                    floor 10% of list
    markdown    after 3 days unsold the ask drops 5% of list a day
    warranty    goods only, on request, 6 h (goods break within 6 h of the sale, so it
                covers every breakdown): ask and floor + 5% of list for A, + 10% for B,
                + 20% for C. A warranty must cost more than the refunds it is expected to
                pay (breakdown share x price): about 20% for refurbished goods sold near
                60% of list if a quarter of them break. With one, every grade starts at 80%.
    the market  (focus phase, 11/4) the market's median paid share for the unit's category and
                grade, with a warranty or without (market_summary.json by_grade, 20 settles a
                day or more), plus 2%: when the rule's ask is more than 3% of list away from it,
                the ask moves toward it by at most 10% of list. On 11/4 the market paid for
                furniture and bedding A 0.77-0.80, B 0.72-0.74, C 0.69-0.70 of list, against our
                A 75%, refurbished 60% (too low) and 80% for any grade with a warranty (too
                high for B and C); for kimchi A 0.75, our 75%, it changes nothing.
    late        in the last 10 minutes before the next supply window the ask is 2% of list
                higher: other shops have sold out by then and the market pays more (11/4:
                kimchi 0.686 in the first 5 minutes of the cycle, 0.711 in the last 5;
                sofas over 1.2M 0.790 with 13 shops selling, 0.805 with 7).
"""
import math
import time

import common

START = {"A": 0.75, "B": 0.70, "C": 0.60}
ORDER = "ABC"                                  # best first
MARGIN = 0.05
FOOD_CUTS = (0.8, 0.45)                        # share of shelf life left for food grades A and B (the market's)
WARRANTY_HOURS = 6
WARRANTY_EXTRA = {"A": 0.05, "B": 0.10, "C": 0.20}
MARKDOWN_AFTER, MARKDOWN_STEP = 3, 0.05        # days unsold, share of list per further day
DUD_DAYS, DUD_FLOOR = 7, 0.25
NEAR_HOURS, NEAR_ASK, NEAR_FLOOR = 24, 0.45, 0.25
LAST_DAY, LAST_DAY_CUT, LAST_DAY_FLOOR = 24, 0.15, 0.25
LAST_HOURS, LAST_FLOOR = 6, 0.10
MAX_ROUNDS = 4           # offers before the shop closes: take one at or above the floor, else no deal
QUICK = 0.95             # an offer this close to the ask is taken at once
CLAIM_MARGIN = 300       # seconds a grade claim must stay true (deal to settle: 99% within 4 minutes)
LIFT = 0.02              # a market-referenced ask: the market's median for the grade plus this
DEADBAND = 0.03          # ...taken only when the rule's ask is further than this from it
REACH = 0.10             # ...moving the rule's ask by at most this
MARKET_MIN = 20          # settles of the category and grade (with a warranty, or without) in a day before its median counts
# The SKU's own market (11/4 night, the person's call; market_summary by_sku others_6h: other shops' settles of the
# SKU in the same grade, with a warranty or without, over 6 h, 10 or more). Our talks of 6 h against it, like for
# like: grade A first asked 2-5% of list under it closed 76% (margin per talk 0.058), at it 54% (0.039), 1-4% over
# 44% (0.040), more over 23% (0.016); grade C closed 91-100% from 5% under to 4% over (0.17-0.22 a talk) but only
# 0.092 a talk asked more than 5% under; grade B was flat (0.11-0.13) and the premium line did best at the market.
SKU_A_OVER, SKU_A_TARGET = 0.01, -0.02   # grade A asked more than SKU_A_OVER above it: down to SKU_A_TARGET from it
SKU_C_UNDER, SKU_C_TARGET = 0.05, -0.02  # grade C asked more than SKU_C_UNDER under it: up to SKU_C_TARGET from it
SKU_MARKET_MIN = 10
# Uncontested (11/5, the person's call: "raise it, a little over what we would have set"): a line product other shops
# sold fewer than SOLO_OTHERS times in 6 h in any grade. The first two beds our buyer found in no shop sold within
# minutes at 0.63-0.64 of list, near our floor (cost + 5% + the warranty share), after the buyer's 4 offers; its
# buyers' budgets were 1.8x list and other shops sold dear beds at 0.74-0.80. So: asked at SOLO_ASK and held at
# SOLO_FLOOR of list, each plus half the warranty share (refurbished with a warranty: 0.80 and 0.70, not the full
# share's 0.90), never under the cost floor.
SOLO_OTHERS = 10
SOLO_ASK = {"A": 0.85, "B": 0.80, "C": 0.70}
SOLO_FLOOR = {"A": 0.75, "B": 0.70, "C": 0.60}
LATE_LIFT, LATE_WINDOW = 0.02, 600   # the ask in the last LATE_WINDOW seconds before the next supply window
WARRANTY_DEFAULT_MIN = 100_000       # goods listed at this or more are quoted with the warranty unless asked otherwise:
                                     # 86-97% of the market's bed and sofa deals carry one, and our goods sold without
                                     # one went unpaid 15% (A, B) to 32% (C) of the time against 0-3% with one (11/4)


def default_warranty(unit: dict) -> bool:
    return unit.get("kind") == "goods" and (unit.get("list_price") or 0) >= WARRANTY_DEFAULT_MIN


def market_ref(by_grade: dict | None, g: str, warranty: bool):
    """The market's median paid share of the list price for grade g, with a warranty or without, or None."""
    d = (by_grade or {}).get(g) or {}
    key = "w" if warranty else "nw"
    return d.get(f"{key}_median") if (d.get(f"{key}_n") or 0) >= MARKET_MIN else None


def sku_ref(cells: dict | None, g: str, warranty: bool):
    """The SKU's own market (others_6h) for grade g, with a warranty or without, or None."""
    d = (cells or {}).get(g + ("_w" if warranty else "")) or {}
    return d.get("median") if (d.get("n") or 0) >= SKU_MARKET_MIN and isinstance(d.get("median"), (int, float)) else None


def late_in_cycle(window: dict | None, now: float) -> bool:
    """inventory.json's restock {open, opens_at, closes_at}: the last LATE_WINDOW seconds before a window opens."""
    w = window or {}
    return bool(w) and not w.get("open") and 0 < (w.get("opens_at") or 0) - now <= LATE_WINDOW


def hours_left(unit: dict, now: float):
    return (unit["expires_at"] - now) / 3600 if unit.get("expires_at") else None


def grade(unit: dict, now: float, catalog: dict | None = None) -> str:
    """Food: from the share of shelf life left now (it drops while the unit waits).
    Goods, or food whose shelf life is unknown: the grade the market gave it."""
    h = hours_left(unit, now)
    shelf = ((catalog or {}).get(unit.get("sku")) or {}).get("shelf_hours")
    if h is None or not shelf:
        return unit.get("grade") if unit.get("grade") in START else "C"
    left = h / shelf
    return "A" if left >= FOOD_CUTS[0] else "B" if left >= FOOD_CUTS[1] else "C"


def claim(unit: dict, now: float, catalog: dict | None = None) -> str:
    """The grade to state on the receipt: true now, and still true CLAIM_MARGIN later. The market
    judges the claim at settle (claim_honest = claim equals the certified grade), a median 1.5 s
    after the deal and within 4 minutes for 99 in 100 (ledger, 11/4). A one-hour margin claimed
    food a grade low while it still settled at the higher one; 14 such deals in 2.5 h averaged
    2.1 stars against 4.9 for the rest."""
    return max(grade(unit, now, catalog), grade(unit, now + CLAIM_MARGIN, catalog), key=ORDER.index)


def near_expiry(unit: dict, now: float) -> bool:
    h = hours_left(unit, now)
    return h is not None and h < NEAR_HOURS


def respond(ask: int, floor: int, offer: int, rounds: int) -> tuple[str, int]:
    """S3, the shop's answer to the buyer's offer number `rounds`: ("sell", price), ("reject", 0) or
    ("counter", the new ask, halfway to the offer and never below the floor). The seller answers with
    it, and the replay tool (tools.py) runs the same rule over past negotiations."""
    last = rounds >= MAX_ROUNDS
    if offer >= ask:
        return "sell", ask
    if offer >= floor and (offer >= QUICK * ask or last):
        return "sell", offer
    if last:
        return "reject", 0
    return "counter", max(floor, ask - (ask - max(offer, floor)) // 2)


def plan(unit: dict, now: float | None = None, warranty: bool = False, adjust: float = 0.0,
         catalog: dict | None = None, extra: dict | None = None, market: dict | None = None,
         late: bool = False, dear_default: bool = False, sku_market: dict | None = None,
         in_line: bool = False, solo: bool = False) -> dict:
    """{ask, floor, grade, hours_left, age_days, at_cost, warranty_hours, ref, late, sku_ref, nudge} for one unit.
    sku_market: the SKU's others_6h; a grade A ask well above it comes down, a grade C ask well under it goes up
    (not for the premium line, in_line, nor on clearance); nudge: by how much (share of list), or None. A premium
    line product is never asked under its own market (11/5: a bed sold at 0.63 where others got 0.80), and solo
    (uncontested, SOLO_OTHERS) is asked at SOLO_ASK and held at SOLO_FLOOR.
    at_cost: the floor is cost + 5% (no clearance), which is what the seller may say.
    adjust and extra come from the rules in force (price_adjust, warranty_extra); market is the
    category's by_grade from market_summary.json; late: the end of the supply cycle (late_in_cycle);
    dear_default: dear goods (default_warranty) get the warranty unasked, except on clearance (unsold a
    week, or the round's last day), where it would lift the clearance floor by its extra.
    ref: the market median the ask moved toward, or None."""
    extra = extra or WARRANTY_EXTRA
    now = now or time.time()
    lp, cost = unit["list_price"], unit["cost"]
    g = grade(unit, now, catalog)
    age = (now - unit.get("supplied_at", now)) / 86400
    to_end = (common.ROUND_END - now) / 3600
    clearing = age > DUD_DAYS or near_expiry(unit, now) or to_end < LAST_DAY
    w = (bool(warranty) or (dear_default and default_warranty(unit) and not clearing)) and unit.get("kind") == "goods"
    rule = START[g] + (extra[g] if w else 0)
    ref = market_ref(market, g, w)
    gap = round(ref + LIFT - rule, 6) if ref is not None else 0.0      # rounded: 0.72 - 0.75 is -0.030000000000000027
    if abs(gap) > DEADBAND:
        rule += max(-REACH, min(REACH, gap))
    else:
        ref = None
    start = rule + adjust + (LATE_LIFT if late else 0)
    sref, nudge = sku_ref(sku_market, g, w), None
    if sref is not None and not clearing and not in_line:
        if g == "A" and start > sref + SKU_A_OVER:
            nudge = round(sref + SKU_A_TARGET - start, 4)
        elif g == "C" and start < sref - SKU_C_UNDER:
            nudge = round(sref + SKU_C_TARGET - start, 4)
        start += nudge or 0
    if in_line and not clearing:
        if sref is not None and sref > start:
            nudge, start = round(sref - start, 4), sref           # the line: at least its own market
        if solo:
            want = SOLO_ASK[g] + (extra[g] / 2 if w else 0)
            if want > start:
                nudge, start = round((nudge or 0) + want - start, 4), want
    floor = math.ceil(round(cost * (1 + MARGIN), 6))            # 12000 * 1.05 is 12600.000000000002 in floats
    clearance = None
    if age > MARKDOWN_AFTER:
        start -= MARKDOWN_STEP * math.ceil(age - MARKDOWN_AFTER)
    if age > DUD_DAYS:
        clearance = DUD_FLOOR
    if near_expiry(unit, now):
        start, clearance = NEAR_ASK, NEAR_FLOOR
    if to_end < LAST_DAY:
        start -= LAST_DAY_CUT
        clearance = min(clearance if clearance is not None else 1.0, LAST_DAY_FLOOR)
    if to_end < LAST_HOURS:
        clearance = LAST_FLOOR
    if clearance is not None:
        floor = min(floor, round(lp * clearance))
    if w:
        floor += round(lp * extra[g])
    if in_line and solo and clearance is None:
        floor = max(floor, round(lp * (SOLO_FLOOR[g] + (extra[g] / 2 if w else 0))))
    ask = max(floor, round(lp * start))
    return {"ask": ask, "floor": floor, "grade": g, "hours_left": hours_left(unit, now), "age_days": age,
            "at_cost": clearance is None, "warranty_hours": WARRANTY_HOURS if w else None, "ref": ref, "late": late,
            "sku_ref": sref, "nudge": nudge, "solo": bool(in_line and solo)}
