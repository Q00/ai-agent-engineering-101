"""What the board scores one purchase by, in one place, chosen by the rule the market has in force.

Round 3 changed the buyer's score three times: surplus in credits, then the share saved off the list price (patch
3.4, 11-06), then the share of the shop's margin the buyer kept (patch 3.6, 11-12):

    list_discount   (list - price) / list
    margin_share    (list - price) / (list - unit cost), at most 1

Until 11-17 the first formula was written into this agent in three places (a pay cap of 0.70 x list, a quick-take
at 0.64 x list, the tuner's prompt). Patch 3.6 arrived on 11-12 and the agent went on buying for the old score for
five days: grade C units at 0.565 of list looked like its best deals and score 0.63 under the rule in force, grade A
units at 0.665 looked dear and score 0.91. Now every price decision goes through score() and price_for(), and the
formula name comes from rulebook.py, which re-reads the market's rules page.

A unit's cost is not public. agent.md gives its range by grade (the market draws it at supply); the midpoint is the
estimate. Checked against the board on 11-17: 16,341 settled deals scored this way give Saved 0.7304, the board
says 0.7346. A cost a shop states in its message is seller prose and is not used.
"""

COST_SHARE = {"A": (0.55, 0.75), "B": (0.40, 0.60), "C": (0.25, 0.45)}   # agent.md "Supply": cost / list by grade
KIND_COST = {"food": 0.52, "goods": 0.61}   # week-11 patch note 3.6: average cost share by kind, for an unstated grade
DEFAULT_COST = 0.55
FORMULAS = ("margin_share", "list_discount")
DEFAULT_FORMULA = "margin_share"   # the rule in force when this file was written (patch 3.6)


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def cost_share(grade=None, kind=None):
    """Estimated cost as a share of the list price: the grade's midpoint, else the kind's average, else 0.55."""
    if grade in COST_SHARE:
        low, high = COST_SHARE[grade]
        return (low + high) / 2
    return KIND_COST.get(kind, DEFAULT_COST)


def unit_cost(list_price, grade=None, kind=None):
    return list_price * cost_share(grade, kind)


def score(price, list_price, cost=None, formula=DEFAULT_FORMULA):
    """The board's score for a unit that served the order, bought at `price`. None when it cannot be computed."""
    if not _number(price) or not _number(list_price) or list_price <= 0:
        return None
    if formula == "list_discount":
        return (list_price - price) / list_price
    cost = cost if _number(cost) else list_price * DEFAULT_COST
    margin = list_price - cost
    if margin <= 0:
        return 1.0 if price <= cost else 0.0
    return min(1.0, (list_price - price) / margin)


def price_for(target, list_price, cost=None, formula=DEFAULT_FORMULA):
    """The highest whole price whose score is still at least `target`."""
    if not _number(target) or not _number(list_price) or list_price <= 0:
        return None
    if formula == "list_discount":
        return int(list_price * (1 - target) + 1e-6)
    cost = cost if _number(cost) else list_price * DEFAULT_COST
    return int(list_price - target * (list_price - cost) + 1e-6)


def outcome_score(row, formula=DEFAULT_FORMULA):
    """The board's score for one remembered order: -1 when the unit served nothing (broke, food too old), 0 when that
    was refunded, else score(). None for a row that is not a settled deal."""
    if row.get("outcome") != "deal" or not row.get("settled"):
        return None
    price, list_price = row.get("price"), row.get("list_price")
    if not _number(price) or not _number(list_price) or list_price <= 0:
        return None
    if row.get("broken") or row.get("value") == 0:
        return 0.0 if row.get("refunded") else -1.0
    return score(price, list_price, unit_cost(list_price, row.get("grade"), kind_of(row)), formula)


def kind_of(row):
    """food or goods for a remembered row: the stored kind, else read off the category (rows before 11-17 have none)."""
    kind = row.get("kind")
    if kind in KIND_COST:
        return kind
    return "food" if str(row.get("item") or "").startswith("식품") else "goods"
