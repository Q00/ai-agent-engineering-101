"""Deterministic money rules for both sides. Pure functions over a unit/SKU and evidence from memory, so they
are easy to test and to explain in REPORT.md."""
import time

from . import config


# ------------------------------------------------------------------------------------------- shop
def unit_floor(u, now=None, hours_left_round=None):
    """Lowest price for one unit. Normally cost plus a margin. Lower when keeping the unit is worse than selling
    it cheap: food close to its date (thrown away at cost) and anything still in stock near the end of the round
    (unsold stock counts as a total loss)."""
    now = now or time.time()
    hl = config.hours_left_in_round(now) if hours_left_round is None else hours_left_round
    cost = u["cost"]
    floor = cost * (1 + config.MIN_MARGIN)
    if u.get("kind") == "food" and u.get("expires_at"):
        left = (u["expires_at"] - now) / 3600
        if left < 6:
            floor = min(floor, cost * 0.3)
        elif left < 12:
            floor = min(floor, cost * 0.8)
    if hl < config.CLEARANCE_HOURS:
        floor = min(floor, cost * max(0.3, hl / config.CLEARANCE_HOURS))
    return max(1, int(floor))


def unit_ask(u, market_ratio=None, adjust=0.0):
    """First ask. Starts from what the market has been paying for this SKU or category (median settled price /
    list price) plus a little room, else START_ASK_RATIO; then the grade factor and our own deal-rate adjustment."""
    base = (market_ratio + 0.05) if market_ratio else config.START_ASK_RATIO
    base = min(1.0, max(0.5, base + adjust))
    grade = u.get("grade") if u.get("grade") in ("A", "B", "C") else "B"
    ask = u["list_price"] * base * config.GRADE_FACTOR.get(u.get("kind"), config.GRADE_FACTOR["goods"])[grade]
    return int(max(ask, unit_floor(u) * 1.05))


def ask_adjustment(outcomes):
    """From our shop's last negotiations: lower asks when few close, raise them when nearly all close."""
    recent = outcomes[-30:]
    if len(recent) < 8:
        return 0.0
    rate = sum(1 for o in recent if o.get("outcome") == "deal") / len(recent)
    if rate < 0.35:
        return -0.08
    if rate < 0.5:
        return -0.04
    if rate > 0.85:
        return 0.04
    return 0.0


def seller_reply(ask, floor, offer, n_offers):
    """One step of the shop's concession. Returns ("accept", price) or ("counter", price)."""
    if offer is not None and offer >= ask * 0.97:
        return "accept", int(offer)
    if offer is not None and offer >= floor and (n_offers >= 4 or offer >= (ask + floor) / 2 and n_offers >= 2):
        return "accept", int(offer)
    anchor = max(offer or floor, floor)
    new_ask = max(floor, int(ask - (ask - anchor) * (0.25 if n_offers < 2 else 0.4)))
    return "counter", new_ask


def warranty_hours_for(u, price, p_break):
    """Offer a warranty when its expected cost (p_break * price refunded) stays under half the unit's margin."""
    if u.get("kind") != "goods":
        return 0
    margin = max(0, price - u["cost"])
    return config.WARRANTY_HOURS if p_break * price <= config.WARRANTY_MAX_EXPECTED_COST * margin else 0


# ------------------------------------------------------------------------------------------- buyer
def buyer_limits(list_price, budget, kind, p_break=0.1, warranty=False):
    """Most we will pay. Value to the order is the list price (0 if the goods break without a warranty), so
    the expected value of goods without a warranty is list_price * (1 - p_break) - p_break * price."""
    cap = list_price * (1 - config.MIN_SURPLUS_RATIO)
    if kind == "goods" and not warranty:
        # value - price >= MIN_SURPLUS * L  with value = L(1-p) - p*price  ->  price <= L(1 - p - m) / (1 + p)
        cap = list_price * (1 - p_break - config.MIN_SURPLUS_RATIO) / (1 + p_break)
    return int(max(0, min(budget, cap)))


def expected_price(list_price, shop_ratio=None, market_ratio=None):
    r = shop_ratio or market_ratio or 0.85
    return int(list_price * r)


def buyer_next_offer(list_price, max_pay, ask, last_offer, n):
    """Our next offer. Opens low (FIRST_OFFER_RATIO of list), then closes half the gap to the shop's ask each
    round, never above max_pay. Returns ("accept", ask) when the ask is already good enough."""
    if ask is not None and ask <= max_pay and (ask <= list_price * 0.72 or n >= 3):
        return "accept", int(ask)
    if last_offer is None:
        first = int(list_price * config.FIRST_OFFER_RATIO)
        return "offer", min(max_pay, first, ask or first)
    if ask is None:
        return "offer", min(max_pay, int(last_offer * 1.1))
    nxt = int(last_offer + (ask - last_offer) * 0.5)
    if nxt <= last_offer:
        nxt = last_offer + max(1, int(list_price * 0.02))
    return "offer", min(max_pay, nxt)
