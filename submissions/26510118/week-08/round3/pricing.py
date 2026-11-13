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
                  food unsold for over 7 days     floor 25% of list
                  goods unsold for over 7 days    floor down to cost x (1 - 1% x days left): no
                                                  lower than what holding it to the end costs
                (11/5 15:40: the board ranks shops by book, cash + what the unsold units cost,
                and goods in stock pay 1% of their cost a day. Until then the last 24 h of the
                round cut the ask 15% of list with a floor of 25%, the last 6 h a floor of 10%,
                and goods unsold a week a floor of 25%: each sale under cost took the
                difference out of the book, where the unit held would have counted in full.)
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
MARGIN = 0.02           # over cost on the floor (0.05 until 11/6 12:40, Kiro 2.0: the buyer AIs now score on the share of
                        # list they keep, and 70% of our dear talks left without an offer while the margin per sale held)
FOOD_CUTS = (0.8, 0.45)                        # share of shelf life left for food grades A and B (the market's)
WARRANTY_HOURS = 6
WARRANTY_EXTRA = {"A": 0.03, "B": 0.11, "C": 0.33}    # 11/13 11:50 (the person's call; .025/.08/.20 since 11/6 12:40,
                                                       # .05/.10/.20 before): our 500k+ goods over 24.6 h earned 29.8M over
                                                       # cost and refunded 20.2M (C 13 of 34 broke, 10.2M; B 13.9%, A 3.3%).
                                                       # A warranty breaks even at break x price / (1 - break): A ~0.024,
                                                       # B ~0.10, C ~0.34 of list. The extra lifts the floor (the anchored
                                                       # ask is the SKU's market), so it binds where the market asks less
BREAK = {"A": 0.031, "B": 0.110, "C": 0.362}          # the market's 24 h breakage by grade (the share refunded), 11/13
                                                       # (0.026 / 0.107 / 0.30 on 11/6)
# Kiro 2.0 (11/6 12:40): every unit whose SKU has a market of its own grade and warranty (others_6h, 10+ settles) is
# first asked at that market + ASK_OVER (the A/C nudges, the line's "never under its own market" and the uncontested
# SOLO asks are off). Before: 53% of our dear grade A talks had a floor above the SKU's market + 0.01 and closed 9-10%
# (78-81% left without an offer); with the floor at or under it they closed 23-29% at three times the margin a talk.
ANCHOR, ASK_OVER = True, 0.02
FOOD_FLOOR_GAP = 0.02   # the "high" food arm's floor under the SKU's market (0.05 for the "market" arm, 11/9-11/12)
FOOD_HIGH_LIFT = 0.04   # and its first ask over the anchored ask (11/12, the person's call: food is short of stock;
                        # see experiments.FOOD_ARMS)
FOOD_MAX_GAP, FOOD_MAX_LIFT = 0.0, 0.08   # the "max" arm (11/12, the person's call, bolder still): the floor at the
                                          # market itself, the first ask 8% of list over the anchored ask
# 11/9 23:30 (the person's call), an experiment (experiments.food_arm): our food sold 0.06-0.10 of list under other
# shops' price for the same product and grade (24 h, 2,770 sales: A 0.640 against 0.715, costing 0.676; B 0.551 against
# 0.637; C 0.452 against 0.580), haggled down toward a floor at cost + MARGIN (22% of food deals within 2% of it). In the
# "market" arm a food unit anchored to its SKU's market (and not on clearance) is held at that market less
# FOOD_FLOOR_GAP of list when that is over its cost floor; the seller then says so instead of "only 2% over cost".
SOLO_ON = False          # the uncontested asks and floors (SOLO_ASK, SOLO_FLOOR, learn_solo): off since 11/6 12:40
C_WARRANTY = True        # refurbished goods are offered with the warranty only: without one, 49-80% of our agreed C deals
                         # went unpaid (buyers' kits accept A and B only without a warranty)
STUCK_HOURS, MAX_LOSS = 6, 0.05   # 11/7 12:50: still over its market at cost after this long: at the market, never
                                  # more than MAX_LOSS under cost (27 dear units, 23.1M at cost, sat over their market)
DEAD_HOURS = 2           # 3 until 11/6 15:40 (96 of 238 goods units held 3 h+); a goods unit held this long whose floor sits over its market + 0.01: floor at its cost (plus the
                         # expected refund under a warranty), asked at the market; stock counts at cost in the book, so
                         # selling at cost loses nothing and frees the line (11/6: 50 such units, 40M at cost)
MARKDOWN_AFTER, MARKDOWN_STEP = 3, 0.05        # days unsold, share of list per further day
DUD_DAYS, DUD_FLOOR = 7, 0.25
NEAR_HOURS, NEAR_ASK, NEAR_FLOOR = 24, 0.45, 0.25
LAST_DAY, LAST_DAY_CUT, LAST_DAY_FLOOR = 0, 0.15, 0.25        # off (24 h until 11/5 15:40): see the floor above
LAST_HOURS, LAST_FLOOR = 0, 0.10                               # off (6 h until 11/5 15:40)
STORAGE = 0.01           # goods in stock pay 1% of their cost a day (/agent.md)
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
SOLO_ASK = {"A": 0.82, "B": 0.77, "C": 0.67}   # 0.85 / 0.80 / 0.70 until 11/5 03:45: of 16 uncontested talks, 3 closed
                                                # at 0.78-0.83 of list and 11 walked, most at the first ask (0.875 with a
                                                # warranty) without an offer, which the floor cannot learn from
SOLO_FLOOR = {"A": 0.75, "B": 0.70, "C": 0.60}
# ...and the floor learns (11/5 02:40, the person's call): the first two uncontested talks both walked, one at 0.697
# against our floor of 0.75 (the old floor, 0.684, would have sold it). A buyer who walks with a last offer within
# SOLO_GIVE of list under the floor sets that product's floor to the offer + SOLO_STEP for SOLO_LEARN; the ask still
# opens high, and the floor never goes under the one the rules without SOLO give.
SOLO_GIVE, SOLO_STEP, SOLO_LEARN = 0.06, 0.01, 12 * 3600
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


def learn_solo(book: dict, sku: str, plan: dict, offers: list, list_price: float, now: float) -> bool:
    """A talk on an uncontested product that ended without a deal: when the buyer's last offer came within SOLO_GIVE
    of list under our floor, the product's floor becomes that offer + SOLO_STEP (lower only). True when it changed."""
    if not plan.get("solo") or not offers or not list_price:
        return False
    last, floor = offers[-1] / list_price, plan["floor"] / list_price
    if not 0 <= floor - last <= SOLO_GIVE:
        return False
    new = round(last + SOLO_STEP, 4)
    if solo_floor_of(book, sku, now) is not None and book[sku]["floor"] <= new:
        return False
    book[sku] = {"floor": new, "at": int(now)}
    return True


def solo_floor_of(book: dict | None, sku: str, now: float):
    """The floor learn_solo set for the product (a share of list), while it is younger than SOLO_LEARN, else None."""
    e = (book or {}).get(sku) or {}
    return e.get("floor") if isinstance(e.get("floor"), (int, float)) and now - (e.get("at") or 0) < SOLO_LEARN else None


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


def crossing(unit: dict, now: float, catalog: dict | None = None) -> bool:
    """A food unit whose grade changes within CLAIM_MARGIN: whatever it claims may not be the grade the market
    certifies at settle (11/6: 7 of our 1,113 food settles in a day were claimed a grade low this way)."""
    return grade(unit, now, catalog) != grade(unit, now + CLAIM_MARGIN, catalog)


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
         in_line: bool = False, solo: bool = False, solo_floor: float | None = None, ask_over: float | None = None,
         comp_ask: float | None = None, food_floor: str | None = None) -> dict:
    """{ask, floor, grade, hours_left, age_days, at_cost, warranty_hours, ref, late, sku_ref, nudge} for one unit.
    sku_market: the SKU's others_6h; a grade A ask well above it comes down, a grade C ask well under it goes up
    (not for the premium line, in_line, nor on clearance); nudge: by how much (share of list), or None. A premium
    line product is never asked under its own market (11/5: a bed sold at 0.63 where others got 0.80), and solo
    (uncontested, SOLO_OTHERS) is asked at SOLO_ASK and held at SOLO_FLOOR, or at solo_floor (share of list, from
    learn_solo) when that is lower.
    at_cost: the floor is cost + 5% (no clearance), which is what the seller may say.
    adjust and extra come from the rules in force (price_adjust, warranty_extra); market is the
    category's by_grade from market_summary.json; late: the end of the supply cycle (late_in_cycle);
    dear_default: dear goods (default_warranty) get the warranty unasked, except on clearance (unsold a
    week, or the round's last day), where it would lift the clearance floor by its extra.
    ref: the market median the ask moved toward, or None. food_floor: the talk's arm of the food-floor experiment
    ("high": FOOD_HIGH_LIFT on the ask, the floor at the market less FOOD_FLOOR_GAP; market_floor in the plan says it
    raised the floor)."""
    extra = extra or WARRANTY_EXTRA
    now = now or time.time()
    lp, cost = unit["list_price"], unit["cost"]
    g = grade(unit, now, catalog)
    age = (now - unit.get("supplied_at", now)) / 86400
    to_end = (common.ROUND_END - now) / 3600
    clearing = age > DUD_DAYS or near_expiry(unit, now) or to_end < LAST_DAY
    w = (bool(warranty) or (dear_default and default_warranty(unit) and not clearing)
         or (C_WARRANTY and g == "C" and not clearing)) and unit.get("kind") == "goods"
    rule = START[g] + (extra[g] if w else 0)
    ref = market_ref(market, g, w)
    gap = round(ref + LIFT - rule, 6) if ref is not None else 0.0      # rounded: 0.72 - 0.75 is -0.030000000000000027
    if abs(gap) > DEADBAND:
        rule += max(-REACH, min(REACH, gap))
    else:
        ref = None
    start = rule + adjust + (LATE_LIFT if late else 0)
    sref, nudge = sku_ref(sku_market, g, w), None
    anchored = ANCHOR and sref is not None and not clearing
    if anchored:                                                # Kiro 2.0: the SKU's own market + ASK_OVER
        want = sref + (ASK_OVER if ask_over is None else ask_over) + adjust
        if food_floor in ("high", "max") and unit.get("kind") != "goods":  # 11/12: the bolder food arms ask more
            want += FOOD_HIGH_LIFT if food_floor == "high" else FOOD_MAX_LIFT
        if comp_ask is not None and g == "A":                   # not over what other shops first ask (shop_model)
            want = min(want, comp_ask - 0.005)
        nudge, start = round(want - start, 4), want
    elif sref is not None and not clearing and not in_line:
        if g == "A" and start > sref + SKU_A_OVER:
            nudge = round(sref + SKU_A_TARGET - start, 4)
        elif g == "C" and start < sref - SKU_C_UNDER:
            nudge = round(sref + SKU_C_TARGET - start, 4)
        start += nudge or 0
    if in_line and not clearing and not anchored:
        if sref is not None and sref > start:
            nudge, start = round(sref - start, 4), sref           # the line: at least its own market
        if solo and SOLO_ON:
            want = SOLO_ASK[g] + (extra[g] / 2 if w else 0)
            if want > start:
                nudge, start = round((nudge or 0) + want - start, 4), want
    floor = math.ceil(round(cost * (1 + MARGIN), 6))            # 12000 * 1.05 is 12600.000000000002 in floats
    clearance = None
    if age > MARKDOWN_AFTER:
        start -= MARKDOWN_STEP * math.ceil(age - MARKDOWN_AFTER)
    goods = unit.get("kind") == "goods"
    if age > DUD_DAYS and not goods:
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
    held_down = goods and age > DUD_DAYS and clearance is None
    if held_down:                                               # what holding it to the end would cost, no more
        floor = min(floor, math.ceil(cost * (1 - STORAGE * max(0.0, to_end) / 24)))
    if w:
        floor += round(lp * extra[g])
    market_floor, market_gap = False, None
    if food_floor in ("high", "max") and not goods and anchored and clearance is None:   # 11/12: the food price experiment
        market_gap = FOOD_FLOOR_GAP if food_floor == "high" else FOOD_MAX_GAP
        held = round(lp * (sref - market_gap))
        if held > floor:
            floor, market_floor = held, True
    dead = (anchored and unit.get("kind") == "goods" and clearance is None and not held_down
            and (now - unit.get("supplied_at", now)) >= DEAD_HOURS * 3600 and floor / lp > sref + 0.01)
    if dead:                                                    # at cost (with its expected refund), asked at the market
        floor = min(floor, math.ceil(cost / (1 - BREAK[g])) if w else math.ceil(cost))
        start = sref
    stuck = (dead and (now - unit.get("supplied_at", now)) >= STUCK_HOURS * 3600 and floor / lp > sref + 0.005)
    if stuck:                                                   # 11/7: still over its market at cost: at the market,
        floor = max(round(lp * sref), math.ceil(cost * (1 - MAX_LOSS)))   # at most MAX_LOSS under its cost
        start = sref
    if in_line and solo and SOLO_ON and clearance is None and not held_down and not dead:
        hold = SOLO_FLOOR[g] + (extra[g] / 2 if w else 0)
        floor = max(floor, round(lp * (min(hold, solo_floor) if solo_floor is not None else hold)))
    ask = max(floor, round(lp * start))
    return {"ask": ask, "floor": floor, "grade": g, "hours_left": hours_left(unit, now), "age_days": age,
            "at_cost": clearance is None and not held_down and not dead, "dead": dead, "stuck": stuck, "anchored": anchored,
            "market_floor": market_floor, "market_gap": market_gap,
            "warranty_hours": WARRANTY_HOURS if w else None, "ref": ref, "late": late,
            "sku_ref": sref, "nudge": nudge, "solo": bool(in_line and solo and SOLO_ON),
            "solo_floor": solo_floor if in_line and solo and SOLO_ON else None}
