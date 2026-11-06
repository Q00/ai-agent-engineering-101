"""Deterministic money rules for both sides. Pure functions over a unit/SKU and evidence from memory, so they
are easy to test and to explain in REPORT.md."""
import math
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
    list price) plus ASK_ROOM, else START_ASK_RATIO; then the grade factor and our own deal-rate adjustment."""
    base = (market_ratio + config.ASK_ROOM) if market_ratio else config.START_ASK_RATIO
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


def seller_reply(ask, floor, offer, n_offers, hold=None):
    """One step of the shop's concession. Returns ("accept", price) or ("counter", price).
    hold: from memory, roughly what buyers with this opening style have paid us; counters do not drop below it
    during the first 3 offers (acceptance rules are unchanged, so the deal is still taken above the floor)."""
    if offer is not None and offer >= ask * 0.97:
        return "accept", int(offer)
    if offer is not None and offer >= floor and (n_offers >= 4 or offer >= (ask + floor) / 2 and n_offers >= 2):
        return "accept", int(offer)
    anchor = max(offer or floor, floor)
    new_ask = max(floor, int(ask - (ask - anchor) * (0.25 if n_offers < 2 else 0.4)))
    if hold and n_offers < 4:
        new_ask = max(new_ask, min(int(hold), ask))
    return "counter", new_ask


def warranty_hours_for(u, price, p_break):
    """Offer a warranty when its expected cost (p_break * price refunded) stays under half the unit's margin."""
    if u.get("kind") != "goods" or u.get("grade") not in config.WARRANTY_GRADES:
        return 0
    margin = max(0, price - u["cost"])
    return config.WARRANTY_HOURS if p_break * price <= config.WARRANTY_MAX_EXPECTED_COST * margin else 0


def warranty_ask(ask, u, warranty_hours, p_break):
    """With a warranty the buyer is insured, so the ask carries the expected refund (never above list price)."""
    if not warranty_hours or not config.WARRANTY_PRICE_IN:
        return ask
    return int(min(u["list_price"], ask * (1 + p_break)))


def cap_ask(ask, list_price, market_ratio, floor):
    """Keep the first ask within ASK_MAX_OVER_MARKET of what the market pays for this SKU at this grade, after the warranty
    price-in and scarcity premium have been added. Never below the floor; unchanged without market evidence."""
    if not market_ratio or not list_price:
        return ask
    return int(max(floor, min(ask, list_price * (market_ratio + config.ASK_MAX_OVER_MARKET))))


def warranty_floor(floor, u, price, warranty_hours, p_break):
    """With a warranty the shop refunds the full price if the unit breaks, so the floor also carries that expected
    refund (p_break * price). Without this a 190k mattress sold at a 7% margin carried a 190k refund risk."""
    if not warranty_hours or u.get("kind") != "goods":
        return floor
    return max(floor, int(u["cost"] * (1 + config.MIN_MARGIN) + p_break * price))


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


def memory_opening(list_price, max_pay, profile):
    """Buyer's first offer when we have closed with this shop before: just under the lowest share of list it has
    accepted from us, instead of FIRST_OFFER_RATIO, so we skip rounds it never moves in. None without evidence."""
    if not profile or (profile.get("deals") or 0) < 2 or not profile.get("min_close_ratio"):
        return None
    return int(min(max_pay, list_price * profile["min_close_ratio"] * 0.97))


def buyer_next_offer(list_price, max_pay, ask, last_offer, n, opening=None):
    """Our next offer. Opens low (FIRST_OFFER_RATIO of list), then closes half the gap to the shop's ask each
    round, never above max_pay. Returns ("accept", ask) when the ask is already good enough."""
    if ask is not None and ask <= max_pay and (ask <= list_price * 0.72 or n >= 3):
        return "accept", int(ask)
    if last_offer is None:
        first = max(int(list_price * config.FIRST_OFFER_RATIO), opening or 0)
        return "offer", min(max_pay, first, ask or first)
    if ask is None:
        return "offer", min(max_pay, int(last_offer * 1.1))
    nxt = int(last_offer + (ask - last_offer) * 0.5)
    if nxt <= last_offer:
        nxt = last_offer + max(1, int(list_price * 0.02))
    return "offer", min(max_pay, nxt)


# ------------------------------------------------------------------------------------------- restock (EV)
def poisson_tail(mu, j):
    """P(N >= j) for N ~ Poisson(mu): the chance the j-th unit we hold sells within the horizon."""
    if j <= 0:
        return 1.0
    term = math.exp(-mu)
    cdf = term
    for k in range(1, j):
        term *= mu / k
        cdf += term
    return max(0.0, 1.0 - cdf)


def hold_loss(kind, cost, shelf_hours=None, horizon_h=None):
    """Expected loss of a unit that does not sell within the horizon."""
    h = horizon_h or config.HORIZON_H
    if kind == "food":
        if shelf_hours and shelf_hours < 2 * h:
            return cost                                   # it will expire before the next chance: total loss
        return config.FOOD_LEFTOVER_RISK * cost
    return cost * (0.01 * h / 24 + config.GOODS_END_RISK)  # storage 1%/day plus clearance and round-end risk


def ev_quantity(have, mu, price, cost, loss, cap=20):
    """How many more units have positive expected profit. The (have + j)-th unit sells with P(N >= have + j);
    EV_j = P * (price - cost) - (1 - P) * loss. Units are bought while EV_j > 0. Returns (qty, [EV_j ...])."""
    evs = []
    for j in range(1, cap + 1):
        p = poisson_tail(mu, have + j)
        ev = p * (price - cost) - (1 - p) * loss
        if ev <= 0:
            break
        evs.append(round(ev))
    return len(evs), evs
