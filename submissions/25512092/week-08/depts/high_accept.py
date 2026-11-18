"""high_accept_105 (person's decision 11/09 ~17:40; on at the 20:27 soft_neg check only if soft_neg went to 100%):
orders whose SKU lists at >= MIN_LIST (200,000) accept up to FACTOR (1.05) x the market average instead of 1.00 -
the soft_neg accept bound, the accept rule's upper bound and the negotiation cap (still within the order's budget).
Other orders unchanged. Switched by high_accept.json (read per order); tools/saved_guard_watch.py switches it off when
my saved < the 2nd best saved + 0.025 (depts/saved_guard.py). Deals closed above the average: tag high_accept_105.
11/09 ~21:20 (person, "more aggressive"): x 1.05 -> x 1.10 and one more haggle (soft_neg 2 -> 3 offers) for these
orders (high_accept.json factor / extra_haggles); tag high_accept_110; the staged guard (2nd + 0.025) unchanged.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "high_accept.json")
TAG = "high_accept_110"                    # 11/09 ~21:20: was high_accept_105 (x 1.05, no extra haggle)
MIN_LIST, FACTOR, EXTRA_HAGGLES = 200_000, 1.10, 1


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(r=None, now=None):
    r = rules() if r is None else r
    return bool(r.get("enabled")) and (r.get("since") or 0) <= (now or time.time())


def factor(list_price, r=None):
    """The accept factor for this SKU's list price, or None (rule off, no list price or under MIN_LIST)."""
    r = rules() if r is None else r
    if not on(r) or not list_price or list_price < r.get("min_list", MIN_LIST):
        return None
    return float(r.get("factor", FACTOR))


def extra_haggles(r=None):
    """Offers added to the soft_neg haggles for these orders (11/09 ~21:20: 1)."""
    r = rules() if r is None else r
    return int(r.get("extra_haggles", 0)) if on(r) else 0
