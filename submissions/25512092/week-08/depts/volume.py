"""Volume pricing for every category (person's decision 11/07 ~02:00, '박리다매'), switched by volume_rules.json
(read on every call, no restart needed):

  first ask   the 48 h market median of the SKU (other shops' sales; depts/sofa.market), at most the list price
  floor       max(cost x 1.05, median x 0.92)
  counters    the seller never counters above the first ask (seller_r3.py)

Not in the endgame case. Without >= MIN_N market sales of the SKU in 48 h the old rules stay (rule != "volume").
Two old markdowns stay as lower options, so stock that would otherwise rot can still go: food clearance near expiry
and the 6-hourly aging markdown of non-food over 24 h on the shelf (pricing in depts/sales.py).
Sofas (category 소파/의자/스툴, non-food, cost >= 50,000) keep their own rules (depts/sofa.py), the same formula.

Safety (alerts.volume_guard): 6 h after "since", profit in [since, since + 6 h) under 70% of [since - 6 h, since)
-> "enabled": false (price rules back to the old ones) + urgent Telegram alert.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "volume_rules.json")
FLOOR_COST = 1.05
FLOOR_MEDIAN = 0.92
MIN_N = 5


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(r):
    with open(RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)


def on():
    return bool(rules().get("enabled"))


def floor(u, now=None):
    """(floor, "volume") or None (rules off, or too few market sales)."""
    if not (u and u.get("sku") and on()):
        return None
    from depts.sofa import market
    med, n, _, _ = market(u["sku"], now)
    if med is None or n < MIN_N:
        return None
    return max(int(u["cost"] * FLOOR_COST), int(med * FLOOR_MEDIAN)), "volume"


def first_ask(u, reserve, now=None):
    from depts.sofa import market
    med, _, _, _ = market(u["sku"], now)
    return max(reserve, int(min(u.get("list_price") or med, med)))
