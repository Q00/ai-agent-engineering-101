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
    """The grade to state on the receipt: true now, and still true in an hour (the claim is
    judged at checkout or at settle), so food about to drop a grade is claimed at the lower one."""
    return max(grade(unit, now, catalog), grade(unit, now + 3600, catalog), key=ORDER.index)


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
         catalog: dict | None = None, extra: dict | None = None) -> dict:
    """{ask, floor, grade, hours_left, age_days, at_cost, warranty_hours} for one unit.
    at_cost: the floor is cost + 5% (no clearance), which is what the seller may say.
    adjust and extra come from the rules in force (price_adjust, warranty_extra)."""
    extra = extra or WARRANTY_EXTRA
    now = now or time.time()
    lp, cost = unit["list_price"], unit["cost"]
    g = grade(unit, now, catalog)
    start = START[g] + adjust
    floor = math.ceil(round(cost * (1 + MARGIN), 6))            # 12000 * 1.05 is 12600.000000000002 in floats
    clearance = None
    age = (now - unit.get("supplied_at", now)) / 86400
    if age > MARKDOWN_AFTER:
        start -= MARKDOWN_STEP * math.ceil(age - MARKDOWN_AFTER)
    if age > DUD_DAYS:
        clearance = DUD_FLOOR
    if near_expiry(unit, now):
        start, clearance = NEAR_ASK, NEAR_FLOOR
    to_end = (common.ROUND_END - now) / 3600
    if to_end < LAST_DAY:
        start -= LAST_DAY_CUT
        clearance = min(clearance if clearance is not None else 1.0, LAST_DAY_FLOOR)
    if to_end < LAST_HOURS:
        clearance = LAST_FLOOR
    if clearance is not None:
        floor = min(floor, round(lp * clearance))
    w = bool(warranty) and unit.get("kind") == "goods"
    if w:
        start += extra[g]
        floor += round(lp * extra[g])
    ask = max(floor, round(lp * start))
    return {"ask": ask, "floor": floor, "grade": g, "hours_left": hours_left(unit, now), "age_days": age,
            "at_cost": clearance is None, "warranty_hours": WARRANTY_HOURS if w else None}
