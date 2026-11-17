"""Deterministic money rules for both sides. Pure functions over a unit/SKU and evidence from memory, so they
are easy to test and to explain in REPORT.md."""
import math
import time

from . import config


# ------------------------------------------------------------------------------------------- shop
def unit_floor(u, now=None, hours_left_round=None, slow=False):
    """Lowest price for one unit. Normally cost plus a margin. Lower when keeping the unit is worse than selling
    it cheap: food close to its date or near the end of the round (food past its date leaves book at its cost).
    Goods near the end of the round go down to cost, never below: book counts unsold goods at cost.
    slow: the unit's SKU is slow (is_slow_sku). Goods held SLOW_FLOOR_AGE_H or more then go down to cost minus the
    storage they would still pay until the round ends: above that, selling now leaves more book than holding."""
    now = now or time.time()
    hl = config.hours_left_in_round(now) if hours_left_round is None else hours_left_round
    cost = u["cost"]
    floor = cost * (1 + config.MIN_MARGIN)
    if slow and u.get("kind") == "goods" and now - (u.get("supplied_at") or now) >= config.SLOW_FLOOR_AGE_H * 3600:
        return max(1, int(cost * (1 - config.STORAGE_PER_DAY * max(0.0, hl) / 24)))
    if u.get("kind") == "food" and u.get("expires_at"):
        left = (u["expires_at"] - now) / 3600
        shelf = u.get("shelf_hours") or 0
        share = left / shelf if 0 < shelf < config.MIN_FOOD_SHELF_FOR_EXPLORE else 1.0   # long-life: hours only
        # Orders need 0.25/0.5/0.75 of the shelf life, so a unit under ~0.3 of it is about to become unsellable.
        # Until 2026-11-10 only absolute hours (6/12 h) counted: a 24 h abalone hit 6 h at 0.25 of shelf, already
        # past every order; 183 units / 2.42M expired in 72 h, mostly 24-48 h-shelf SKUs.
        if left < 6 or share < config.FOOD_DUMP_SHARE:
            floor = min(floor, cost * 0.3)
        elif left < 12 or share < config.FOOD_MARKDOWN_SHARE:
            floor = min(floor, cost * 0.8)
    if hl < config.CLEARANCE_HOURS:
        if u.get("kind") == "food":
            floor = min(floor, cost * max(0.3, hl / config.CLEARANCE_HOURS))
        else:
            # Book counts unsold goods at cost (/agent.md), so below cost a sale lowers book; at cost it keeps
            # book, adds revenue and ends the storage fee. Until 2026-11-06 goods slid to 30% of cost too.
            floor = min(floor, cost)
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


def cap_ask(ask, list_price, market_ratio, floor, over=None):
    """Keep the first ask within ASK_MAX_OVER_MARKET of what the market pays for this SKU at this grade, after the warranty
    price-in and scarcity premium have been added. Never below the floor; unchanged without market evidence.
    over: another distance from the market ratio, e.g. -SLOW_UNDERCUT for a slow overstocked SKU."""
    if not market_ratio or not list_price:
        return ask
    over = config.ASK_MAX_OVER_MARKET if over is None else over
    return int(max(floor, min(ask, list_price * (market_ratio + over))))


def needs_slow_floor(u, slow, grade_ratio):
    """A unit of a slow SKU gets the lower floor only when cost + margin sits above the price it is meant to sell at
    (same-grade market minus SLOW_UNDERCUT): B and C units with room keep their normal floor."""
    if not slow or not grade_ratio:
        return False
    return u["cost"] * (1 + config.MIN_MARGIN) > u["list_price"] * (grade_ratio - config.SLOW_UNDERCUT)


def is_slow_sku(units_on_hand, sold_24h):
    """An overstocked goods SKU whose units on hand outlast SLOW_SUPPLY_DAYS at our own sales of the last 24 h."""
    return units_on_hand > config.OVERSTOCK_SKU_UNITS and units_on_hand > config.SLOW_SUPPLY_DAYS * (sold_24h or 0)


def overstock_order(fit, grade_ratio):
    """Which unit of an overstocked goods SKU to offer: the one with the widest margin over its same-grade market
    price (grade_ratio(sku, grade) -> (ratio, n)), oldest first among equals. Grade C goes last: it ships without a
    warranty and breaks ~30%, which costs stars. A grade without market evidence goes after the known ones."""
    ratios = {}

    def key(u):
        g = u.get("grade") or "C"
        if g not in ratios:
            ratios[g] = (grade_ratio(u["sku"], g) or (None, 0))[0]
        margin = ratios[g] * u["list_price"] - u["cost"] if ratios[g] else float("-inf")
        return (g == "C", -margin, u.get("supplied_at") or 0)
    return min(fit, key=key)


def warranty_floor(floor, u, price, warranty_hours, p_break, slow=False):
    """With a warranty the shop refunds the full price if the unit breaks, so the floor also carries that expected
    refund (p_break * price). Without this a 190k mattress sold at a 7% margin carried a 190k refund risk."""
    if not warranty_hours or u.get("kind") != "goods":
        return floor
    base = floor if slow else u["cost"] * (1 + config.MIN_MARGIN)   # a slow unit's floor sits under cost + margin
    return max(floor, int(base + p_break * price))


# ------------------------------------------------------------------------------------------- buyer
def buyer_threshold(model_value=None):
    """The expected margin share (patch 3.6) a deal must reach: the score model's break-even for one more deal
    (score_model.saved_threshold, near our current saved) plus BUYER_THETA_MARGIN, kept within BUYER_THETA_BOUNDS;
    MIN_SAVED_SHARE without a board.
    Until 2026-11-13 this was a share of list clamped to 0.24-0.36: after patch 3.6 moved saved to ~0.72 the model
    value sat over the band and the threshold was pinned at 0.36 of list for every grade."""
    if not isinstance(model_value, (int, float)):
        return config.MIN_SAVED_SHARE
    lo, hi = config.BUYER_THETA_BOUNDS
    return min(hi, max(lo, model_value + config.BUYER_THETA_MARGIN))


def cost_ratio(kind, grade=None):
    """Expected unit cost / list price a shop paid (market supply ledger, 72 h to 2026-11-13): food one value, goods
    by grade, the A/B/C supply mix when the grade is not named yet."""
    if kind != "goods":
        return config.COST_RATIO["food"]
    return config.COST_RATIO.get(grade) or config.COST_RATIO["goods"]


def margin_share(price, list_price, kind, grade=None):
    """What one deal adds to saved (patch 3.6): (list - price) / (list - cost), at most 1 (below cost = the whole
    margin), at least -1."""
    m = 1 - cost_ratio(kind, grade)
    if not list_price or m <= 0:
        return 0.0
    return max(-1.0, min(1.0, (1 - price / list_price) / m))


def buyer_limits(list_price, budget, kind, p_break=0.1, warranty=False, min_surplus=None, grade=None):
    """Most we will pay, from how a deal scores (/agent.md, patch 3.6): s = (L - price) / (L - cost) when the unit
    serves (at most 1), 0 when goods break under a warranty and -1 when they break without one. Expected value
    theta or more, with m = 1 - cost / L:
      (1 - p) s - (0 or p) >= theta   ->   price <= L (1 - m (theta + (0 or p)) / (1 - p))
    When that needs s > 1 no price reaches theta and the limit is 0.
    Until 2026-11-13 s was the share of list (pre-3.6) and theta 0.36 of list for every grade: grade A under a
    warranty was capped near 0.63 of list where 0.70-0.72 already beats our saved."""
    theta = config.MIN_SAVED_SHARE if min_surplus is None else min_surplus
    p = min(0.9, max(0.0, p_break or 0.0)) if kind == "goods" else 0.0
    need = (theta + (0 if warranty or kind != "goods" else p)) / (1 - p)
    if need > 1:
        return 0
    cap = list_price * (1 - (1 - cost_ratio(kind, grade)) * need)
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


def buyer_next_offer(list_price, max_pay, ask, last_offer, n, opening=None, final=False, open_floor=None,
                     counter_below=None, accept_after=3):
    """Our next offer. Opens low (FIRST_OFFER_RATIO of list), then closes half the gap to the shop's ask each
    round, never above max_pay. Returns ("accept", ask) when the ask is already good enough.
    final: the shop called its price final. Of 2,591 such prices in the logs 2,539 (98%) never moved, yet we went on
    for 4,123 more turns: take it now when it is within max_pay, else ("walk", None).
    open_floor: the first offer is at least this share of list (the estimated cost: patch 3.6 caps saved at 1 below
    cost, so opening lower buys no score).
    counter_below / accept_after (haggle canary, 2026-11-15): a first ask above counter_below is countered once even
    when it is within max_pay; after accept_after offers any ask within max_pay is taken."""
    if final and ask is not None:
        return ("accept", int(ask)) if ask <= max_pay else ("walk", None)
    counter_first = counter_below is not None and last_offer is None and ask is not None and ask > counter_below
    if ask is not None and ask <= max_pay and not counter_first and (ask <= list_price * 0.72 or n >= accept_after):
        return "accept", int(ask)
    if last_offer is None:
        first = max(int(list_price * config.FIRST_OFFER_RATIO), int(list_price * (open_floor or 0)), opening or 0)
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
