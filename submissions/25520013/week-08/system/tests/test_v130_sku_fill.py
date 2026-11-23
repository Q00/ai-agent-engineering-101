"""v1.30 (2026-11-05): the restock plan fills a category's top-k SKUs up to one level of units on hand (level_fill)
instead of an even split of each purchase. Live case at 05:50: labpc held 10 sofa G5e3784e6 (median 138 min from
supply to sale) and no G8ec51935 or G00327e80 (both sold within about 6-13 min), so the sofa need of 6 went 2/2/2
and the slow SKU kept piling up. Values below are the live labpc policy of 05:50 (sofa 16/8/2/3, mattress
20/16/4/3, max_unit_cost 200만, max_spend_frac 0.375)."""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy as P  # noqa: E402

NOW = 1793830000.0
SOFA = "가구/침구>가구>소파/의자/스툴"
BED = "가구/침구>가구>침대/매트리스"
# rank order by settles_all as logged by the live hook at 05:45 (sofa) and 05:45 (mattress)
SOFA_SKUS = [
    ("G8ec51935", 2324000, 1347),
    ("G00327e80", 2250000, 553),
    ("G5e3784e6", 570000, 490),
]
BED_SKUS = [
    ("G1515d951", 423810, 4899),
    ("G07ec17d5", 279000, 2977),
    ("G03d0199f", 1610000, 733),
]


class Stats:
    def __init__(self, sku, cat):
        self._sku, self._cat = sku, cat

    def sku(self, k):
        return self._sku.get(k)

    def category(self, k):
        return self._cat.get(k)


def _world():
    catalog, sku = {}, {}
    for cat, rows in ((SOFA, SOFA_SKUS), (BED, BED_SKUS)):
        for s, lp, n in rows:
            catalog[s] = {"sku": s, "category": cat, "kind": "goods", "list_price": lp}
            sku[s] = {"settles_all": n}
    demand = [
        {"category": SOFA, "kind": "goods", "median_price": 2000000},
        {"category": BED, "kind": "goods", "median_price": 600000},
    ]
    return (
        catalog,
        demand,
        Stats(sku, {SOFA: {"velocity_per_h": 80.0}, BED: {"velocity_per_h": 120.0}}),
    )


def _pol():
    pol = P.load()
    sp = pol["shop"]
    sp.update(
        max_unit_cost=2_000_000,
        max_spend_frac=0.375,
        max_inventory_cost_frac=1.33,
        balance_floor=3_000_000,
        skip_below_market=False,
        restock_priority=[SOFA, BED],
    )
    pol.setdefault("categories", {})
    pol["categories"][SOFA] = {
        "stock_target": 16,
        "qty_per_buy": 8,
        "breadth_qty": 2,
        "skus_per_category": 3,
    }
    pol["categories"][BED] = {
        "stock_target": 20,
        "qty_per_buy": 16,
        "breadth_qty": 4,
        "skus_per_category": 3,
    }
    return pol


def _unit(sku, cat, lp):
    return {
        "sku": sku,
        "category": cat,
        "kind": "goods",
        "cost": int(lp * 0.6),
        "list_price": lp,
        "grade": "A",
    }


def _inv(units, balance=200_000_000):
    return {
        "balance": balance,
        "units": units,
        "storage_per_day": 0.01,
        "restock": {"open": True, "opens_at": NOW - 10, "closes_at": NOW + 170},
    }


def _plan(units, balance=200_000_000):
    catalog, demand, stats = _world()
    buy, why = P.restock_plan(
        _inv(units, balance), demand, catalog, _pol(), stats, activity={}, now=NOW
    )
    return {b["sku"]: b["qty"] for b in buy}, why


def _old_split(qty, k):
    return [max(1, qty // k + (1 if i < qty % k else 0)) for i in range(k)]


def test_level_fill_with_nothing_on_hand_is_the_old_even_split():
    for k in range(1, 6):
        for qty in range(k, 21):
            assert P.level_fill(qty, [0] * k) == _old_split(qty, k)


def test_level_fill_levels_the_units_on_hand():
    rng = random.Random(130)  # seed fixed for the record
    for _ in range(2000):
        k = rng.randint(1, 5)
        held = [rng.randint(0, 12) for _ in range(k)]
        qty = rng.randint(1, 20)
        out = P.level_fill(qty, held)
        assert sum(out) == qty and all(q >= 0 for q in out)
        lvl = [h + q for h, q in zip(held, out)]
        for i, q in enumerate(
            out
        ):  # a SKU that got units ends at most one above the lowest level
            assert q == 0 or lvl[i] - 1 <= min(lvl)
        if qty <= max(held) * k - sum(
            held
        ):  # enough room below the top: the highest SKU gets nothing
            assert out[held.index(max(held))] == 0 or held.count(max(held)) < k


def test_a_slow_sku_over_its_level_gets_no_units():
    # 05:50 labpc: 10 sofa G5e3784e6 on hand, need 16 - 10 = 6 (v1.30); v1.31 leaves out the 4 units above the
    # level ceil(16 / 3) = 6, so the need is 10 and qty_per_buy 8 buys 4 + 4
    units = [_unit("G5e3784e6", SOFA, 570000) for _ in range(10)]
    q, why = _plan(units)
    assert (q.get("G8ec51935"), q.get("G00327e80"), q.get("G5e3784e6")) == (4, 4, None)
    assert _old_split(6, 3) == [
        2,
        2,
        2,
    ]  # what the old split bought, the slow SKU included


def test_mattress_units_go_where_the_shelf_is_empty():
    # 05:50 labpc: G1515d951 6, G07ec17d5 5, G03d0199f 0 on hand, need 20 - 11 = 9
    units = [_unit("G1515d951", BED, 423810) for _ in range(6)] + [
        _unit("G07ec17d5", BED, 279000) for _ in range(5)
    ]
    q, why = _plan(units)
    assert (q.get("G1515d951"), q.get("G07ec17d5"), q.get("G03d0199f")) == (1, 2, 6)
    assert (
        sum(q.get(s, 0) for s, _, _ in BED_SKUS) == 9
    )  # the category need is unchanged


def test_an_empty_shop_buys_the_old_even_split():
    q, why = _plan([])
    assert [q.get(s) for s, _, _ in SOFA_SKUS] == _old_split(8, 3)
    assert [q.get(s) for s, _, _ in BED_SKUS] == _old_split(16, 3)


def test_units_without_a_sku_count_for_the_category_only():
    units = [
        {
            "category": SOFA,
            "kind": "goods",
            "cost": 300000,
            "list_price": 570000,
            "grade": "A",
        }
        for _ in range(10)
    ]
    q, why = _plan(units)
    assert [q.get(s) for s, _, _ in SOFA_SKUS] == _old_split(6, 3)


def test_expired_units_are_not_on_hand():
    units = [dict(_unit("G5e3784e6", SOFA, 570000), grade="expired") for _ in range(10)]
    q, why = _plan(units)
    assert [q.get(s) for s, _, _ in SOFA_SKUS] == _old_split(8, 3)


@pytest.mark.parametrize(
    "held, qty, want",
    [
        ([0, 0, 10], 6, [3, 3, 0]),
        ([6, 5, 0], 9, [1, 2, 6]),
        ([2, 2, 2], 3, [1, 1, 1]),
        ([9, 0], 1, [0, 1]),
        ([0], 4, [4]),
    ],
)
def test_level_fill_cases(held, qty, want):
    assert P.level_fill(qty, held) == want
