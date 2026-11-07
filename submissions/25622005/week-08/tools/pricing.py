"""Seller price and terms rules (D3, D4, D5, D14, L5). Pure functions: no files, no clock, no network.

unit  an inventory.json unit (unit, sku, name, category, list_price, kind, cost, grade, supplied_at,
      expires_at, cert), plus shelf_hours from catalog.json for food (the seller adds it).
ctx   {"uwh": {"n", "median"} of the category's use_within_hours, "warranted_6h": sum of the list prices
      of warranted sales in the last 6 h}
pol   the policy dict from policy.load()
now   unix seconds, passed in so tests can move the clock
"""
import math

FOOD_GRADE_CUTS = (0.8, 0.45)          # share of shelf life left for food grade A, B (the market's, kit L83)
                                       # a market patch of the cuts needs a hand update here (D4, L5 follow it)


def hours_left(unit, now):
    """Food: hours until expiry. Goods: None."""
    exp = unit.get("expires_at")
    return None if not exp else (exp - now) / 3600


def grade_now(unit, now):
    """The grade the market will see now: food recomputed from shelf life left, goods as supplied."""
    h, shelf = hours_left(unit, now), unit.get("shelf_hours")
    if h is None or not shelf:
        return unit.get("grade")
    if h <= 0:
        return "expired"
    left = h / shelf
    return "A" if left >= FOOD_GRADE_CUTS[0] else "B" if left >= FOOD_GRADE_CUTS[1] else "C"


def hours_to_end(pol, now):
    return (pol["stock"]["end_ts"] - now) / 3600


def food_clearing(unit, ctx, pol, now):
    """D4 food: sell below cost when hours left < factor x observed use_within median and the unit is grade C
    (shelf life left below FOOD_GRADE_CUTS[1]; no shelf_hours: the median alone); fewer than min_obs
    observations: when the shelf life left is below food_clear_left_share."""
    if unit.get("kind") != "food":
        return False
    h = hours_left(unit, now)
    if h is None:
        return False
    s = pol["seller"]
    uwh = (ctx or {}).get("uwh") or {}
    shelf = unit.get("shelf_hours")
    if (uwh.get("n") or 0) >= s["food_clear_min_obs"] and uwh.get("median"):
        # near expiry only: a use-within median of half the shelf life would otherwise clear from intake
        return h < s["food_clear_uwh_factor"] * uwh["median"] and (not shelf or h / shelf < FOOD_GRADE_CUTS[1])
    return bool(shelf) and h / shelf < s["food_clear_left_share"]


def overpriced(unit, pol):
    """Goods whose cost floor (cost x floor_cost_mult) is above the list price, e.g. bought during a supply
    surge. They sell rarely, not never: quoted last, and restock does not count them as stock."""
    lst, cost = unit.get("list_price"), unit.get("cost")
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)    # restock must not crash on a bad row
    return (unit.get("kind") == "goods" and num(lst) and num(cost) and lst > 0
            and cost * pol["seller"]["floor_cost_mult"] > lst)


def goods_stale(unit, pol, now):
    """D4 goods: in stock for goods_stale_days or more."""
    sup = unit.get("supplied_at")
    return unit.get("kind") == "goods" and bool(sup) and now - sup >= pol["seller"]["goods_stale_days"] * 86400


def floor_detail(unit, ctx, pol, now):
    """(lowest price we accept, rules applied).
    D3 max(cost x floor_cost_mult, list x floor_list); D4 food near its end -> 1, stale goods -> cost x
    seller.stale_floor_cost_mult (1.0 = cost; below 1 clears goods that cost more to hold than they earn);
    D14 from T-clear_cost_h cost, from T-clear_70_h cost x 0.7, from T-clear_any_h 1 (the lower one wins)."""
    s, st = pol["seller"], pol["stock"]
    cost, lst = unit.get("cost") or 0, unit.get("list_price") or 0
    price, rules = max(cost * s["floor_cost_mult"], lst * s["floor_list"]), ["D3"]
    if food_clearing(unit, ctx, pol, now):
        price, rules = 1, rules + ["D4-food"]
    elif goods_stale(unit, pol, now):
        price, rules = cost * s["stale_floor_cost_mult"], rules + ["D4-goods"]
    h = hours_to_end(pol, now)
    end = 1 if h <= st["clear_any_h"] else cost * 0.7 if h <= st["clear_70_h"] else cost if h <= st["clear_cost_h"] else None
    if end is not None and end < price:
        price, rules = end, rules + ["D14"]
    return max(1, math.ceil(price)), rules


def floor(unit, ctx, pol, now):
    return floor_detail(unit, ctx, pol, now)[0]


def intro_ask(unit, ctx, pol):
    """L4 extension point (new-customer price). Not built: P2, conditional on day-1 data."""
    return None


def first_ask(unit, ctx, pol, now):
    """D3: max(list x first_ask_list, floor); big-ticket goods (list above stock.big_ticket_list) use
    first_ask_big_list, since the market clears them nearer to list x 0.7."""
    fl = floor(unit, ctx, pol, now)
    lst = unit.get("list_price") or 0
    big = unit.get("kind") == "goods" and lst > pol["stock"]["big_ticket_list"]
    ask = round(lst * pol["seller"]["first_ask_big_list" if big else "first_ask_list"])
    if pol["seller"]["intro_price_enabled"]:
        intro = intro_ask(unit, ctx, pol)
        if intro is not None:
            ask = min(ask, intro)
    return max(ask, fl)


def midpoint(state):
    return math.ceil((state["floor"] + state["first_ask"]) / 2)


def respond(state, offer, pol):
    """D3 concession. state: {floor, first_ask, ask, countered}. Returns (action, price):
    accept at or above the midpoint (never above our current ask), counter once at the midpoint between floor
    and midpoint (then accept anything at or above the floor), below the floor offer the floor as final."""
    ask = state["ask"]
    if offer is None:
        return "counter", ask
    if offer >= ask:
        return "accept", ask
    if offer >= midpoint(state):
        return "accept", offer
    if offer >= state["floor"]:
        return ("accept", offer) if state.get("countered") else ("counter", midpoint(state))
    return "final", state["floor"]


def warranty_terms(unit, requested_h, ctx, pol, now):
    """D5: {"hours", "price_mult"} or None. Only when asked (warranty_on_request), goods of warranty_grades,
    not in the last no_warranty_h hours, and while warranted sales of the last 6 h stay within the cap.
    The premium is the unit's measured breakage (ctx["break_q"]["upper"], the market's SKU x grade rate) x
    seller.warranty_q_mult, at least seller.warranty_premium_floor and at most seller.warranty_premium, which
    alone applies without data."""
    s, st = pol["seller"], pol["stock"]
    if requested_h is False or (not requested_h and s["warranty_on_request"]):
        return None
    if unit.get("kind") != "goods" or unit.get("grade") not in s["warranty_grades"]:
        return None
    if hours_to_end(pol, now) <= st["no_warranty_h"]:
        return None
    if ((ctx or {}).get("warranted_6h") or 0) + (unit.get("list_price") or 0) > s["warranty_cap_6h"]:
        return None
    hours = s["warranty_hours"]
    if type(requested_h) in (int, float) and requested_h > 0:
        hours = min(requested_h, hours)
    if hours <= 0:
        return None
    premium, bq = s["warranty_premium"], (ctx or {}).get("break_q")
    if isinstance(bq, dict) and isinstance(bq.get("upper"), (int, float)):    # memory/query.break_q of the unit
        premium = min(premium, max(s["warranty_premium_floor"], bq["upper"] * s["warranty_q_mult"]))
    return {"hours": int(hours) if float(hours).is_integer() else hours, "price_mult": round(1 + premium, 4)}


def claim(unit, now, pol):
    """L5: the grade to claim in the receipt, or None. Goods: the supplied grade. Food: the grade now, only
    with claim_food_margin_h hours to spare before the next grade cut (or expiry for C)."""
    if unit.get("kind") != "food":
        return unit.get("grade") if unit.get("grade") in ("A", "B", "C") else None
    h, shelf = hours_left(unit, now), unit.get("shelf_hours")
    if h is None or not shelf or h <= 0:
        return None
    g = grade_now(unit, now)
    cut = FOOD_GRADE_CUTS[0] if g == "A" else FOOD_GRADE_CUTS[1] if g == "B" else 0
    return g if h - cut * shelf >= pol["seller"]["claim_food_margin_h"] else None


def fact_line(unit, ask, terms):
    """L5 first reply. terms: {grade, hours_left, warranty_hours, cert}. The ask is the last number in the
    line, because some buyers read the last number of the text."""
    lst = unit.get("list_price") or 0
    bits = [f"SKU {unit['sku']}", f"unit {unit.get('unit')}", unit.get("name") or unit["sku"], f"list {lst}"]
    g = terms.get("grade")
    if g:
        bits.append(f"grade {g} now" + (", market certificate in the data part" if terms.get("cert") else ""))
    if terms.get("hours_left") is not None:
        bits.append(f"{terms['hours_left']:.1f} h shelf life left")
    w = terms.get("warranty_hours")
    bits.append(f"warranty {w} h included" if w else "no warranty")
    if lst:
        bits.append(f"{round(100 * ask / lst)}% of list")
    return " | ".join(bits) + f" | ask {ask}"
