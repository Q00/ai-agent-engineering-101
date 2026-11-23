"""v1.18 market pricing: the first ask follows the SKU's median settle, floors sit 5% over cost, and restock skips
SKUs that the market settles under our expected unit cost + margin."""
import copy
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy as P  # noqa: E402

NOW = 1793708000.0  # 2026-11-03 21:13:20 KST, inside Round 3


class Stats:
    """aggregates stand-in: sku() / category() rows by key."""

    def __init__(self, sku=None, category=None):
        self._s, self._c = sku or {}, category or {}

    def sku(self, k):
        return self._s.get(k)

    def category(self, k):
        return self._c.get(k)


def _unit(kind="goods", cost=60000, **kw):
    u = {"sku": "S1", "category": "T>A", "kind": kind, "list_price": 100000, "cost": cost, "grade": "A"}
    if kind == "food":
        u["expires_at"] = NOW + 1000 * 3600
        u["shelf_hours"] = 1200
    return dict(u, **kw)


def _uncapped():
    """v2.1: the repo policy caps a grade-A goods ask at 0.775 x list (test_v210_stars); these pin the median rule."""
    pol = copy.deepcopy(P.load())
    pol["shop"].pop("star_price_cap", None)
    return pol


def test_ask_follows_the_skus_median():
    st = Stats(sku={"S1": {"n": 10, "p50": 80000, "p75": 90000}})
    assert P.ask_price(_unit(), _uncapped(), st, now=NOW) == 80000
    pol = _uncapped()
    pol["shop"]["ask_quantile"] = "p75"
    assert P.ask_price(_unit(), pol, st, now=NOW) == 90000


def test_category_fallback_stays_p75():
    st = Stats(category={"T>A": {"n": 10, "p50": 50000, "p75": 70000}})
    assert P.ask_price(_unit(), P.load(), st, now=NOW) == 70000


def test_few_settles_keep_the_list_based_ask():
    st = Stats(sku={"S1": {"n": 4, "p50": 70000, "p75": 75000}})
    assert P.ask_price(_unit(), _uncapped(), st, now=NOW) == 90000       # list 100000 x price_mult 0.9


def test_ask_never_below_a_floor_five_percent_over_cost():
    st = Stats(sku={"S1": {"n": 10, "p50": 50000, "p75": 55000}})
    assert P.floor_price(_unit(), P.load(), now=NOW) == math.ceil(60000 * 1.05)
    assert P.floor_price(_unit("food"), P.load(), now=NOW) == math.ceil(60000 * 1.05)
    assert P.ask_price(_unit(), P.load(), st, now=NOW) == math.ceil(60000 * 1.05)


def _plan(pol, units_cost_frac=0.6):
    catalog = {s: {"sku": s, "name": s, "category": c, "kind": "goods", "list_price": 100000}
               for s, c in (("A1", "T>A"), ("A2", "T>A"), ("B1", "T>B"))}
    demand = [{"category": c, "kind": "goods", "median_price": 100000} for c in ("T>A", "T>B")]
    units = [{"unit": f"u{i}", "sku": "Z1", "category": "T>Z", "kind": "goods", "list_price": 10000,
              "cost": int(10000 * units_cost_frac), "grade": "A"} for i in range(4)]
    st = Stats(sku={"A1": {"n": 10, "p50": 60000, "settles_all": 10},      # under 100000 x 0.6 x 1.05 = 63000
                    "A2": {"n": 10, "p50": 70000, "settles_all": 5},
                    "B1": {"n": 10, "p50": 62000, "settles_all": 10}})
    inv = {"balance": 4_500_000, "units": units, "storage_per_day": 0.01}
    return P.restock_plan(inv, demand, catalog, pol, st, activity={}, now=NOW)


def test_restock_skips_skus_the_market_settles_under_cost():
    buy, why = _plan(P.load())
    skus = {b["sku"] for b in buy}
    assert skus == {"A2"}, (buy, why)
    assert any("skip ['A1']" in n for n in why["notes"]) and any("skip ['B1']" in n for n in why["notes"])


def test_cheaper_supply_history_lets_the_sku_back_in():
    buy, _ = _plan(P.load(), units_cost_frac=0.5)                         # 100000 x 0.5 x 1.05 = 52500 < 60000
    assert {b["sku"] for b in buy} == {"A1", "B1"}


def test_switch_off_keeps_the_old_choice():
    pol = copy.deepcopy(P.load())
    pol["shop"]["skip_below_market"] = False
    buy, _ = _plan(pol)
    assert {b["sku"] for b in buy} == {"A1", "B1"}
