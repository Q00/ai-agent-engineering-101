"""v2.0a (2026-11-05, Patch 3.2 and 3.3): the board's shop 'profit' term reads the 'book' column (cash plus unsold
stock at cost); restock costs follow the inventory.json surge factors (cost cap, below-market check, worst spend);
goods SKUs rank by market margin per hour (velocity x (p50 - 0.59 x factor x list)) before settles."""

import math

import pytest
from icrl import board as B

from tests import test_v130_sku_fill as T

P, SOFA, BED = T.P, T.SOFA, T.BED
LOGIN = "me-login"
BED_SET = {s for s, _, _ in T.BED_SKUS}


def _shop(name, profit, book=None, owner=None):
    r = {
        "name": name,
        "house": False,
        "deals": 5,
        "profit": profit,
        "revenue": 1.0,
        "stars_rank": 0.5,
        "all": 0.5,
        "owner": {"login": owner} if owner else {},
    }
    if book is not None:
        r["book"] = book
    return r


# --- board: profit term = book


def test_profit_term_reads_book_when_the_row_has_it():
    assert B._num(_shop("a", 100.0, book=900.0), "profit") == 900.0
    assert (
        B._num(_shop("a", 100.0), "profit") == 100.0
    )  # older snapshots without book: cash profit
    assert B._num(_shop("a", 100.0, book=True), "profit") == 100.0
    assert B._num({"revenue": 5, "book": 9}, "revenue") == 5.0  # other terms unchanged


def test_summary_ranks_the_profit_term_by_book():
    # we lead on cash but hold the least book
    shops = [
        _shop("me", 500.0, book=100.0, owner=LOGIN),
        _shop("b", 200.0, book=300.0),
        _shop("c", 100.0, book=400.0),
    ]
    s = B.summary({"shops": shops, "buyers": []}, LOGIN)
    assert s["profit"]["ours"] == 100.0
    assert s["profit"]["rank"] == 3 and s["profit"]["pct"] == 0.0
    assert s["profit"]["gap_up"] == 200.0


# --- surge factors and margin rate


def test_surge_factors_parse_the_inventory_list():
    inv = {
        "surge": [
            {"sku": "A", "units": 160, "factor": 1.22},
            {"sku": "B", "factor": 1.0},
            {"sku": "C", "factor": 1.9},
            {"sku": "D", "factor": "1.3"},
            "bad",
            {"factor": 1.2},
            {"sku": "E", "factor": True},
        ]
    }
    assert P.surge_factors(inv) == {"A": 1.22, "C": 1.5}
    assert P.surge_factors({}) == {} and P.surge_factors({"surge": None}) == {}


class _S:
    def __init__(self, rows):
        self.rows = rows

    def sku(self, k):
        return self.rows.get(k)


def test_margin_rate_is_velocity_times_margin_over_expected_cost():
    x = {"list_price": 1_000_000}
    st = _S({"A": {"n": 10, "p50": 800_000, "velocity_per_h": 2.0}})
    assert P.margin_rate("A", x, st) == pytest.approx(2.0 * (800_000 - 590_000))
    assert P.margin_rate("A", x, st, factor=1.2) == pytest.approx(
        2.0 * (800_000 - 708_000)
    )
    assert (
        P.margin_rate("A", x, st, factor=1.5) == 0.0
    )  # p50 under the surged cost: no margin
    for row in (
        {"n": 2, "p50": 800_000, "velocity_per_h": 2.0},
        {"n": 10, "velocity_per_h": 2.0},
        {"n": 10, "p50": 800_000},
        None,
    ):
        assert P.margin_rate("A", x, _S({"A": row})) == 0.0


# --- restock plan (world and live 05:50 policy of test_v130_sku_fill, one SKU per category unless set)


def _plan(stats_rows, surge=(), bed_k=1, sofa_k=1, bed_margin=0.05, **sp):
    catalog, demand, stats = T._world()
    for s, row in stats_rows.items():
        stats._sku[s] = dict(stats._sku.get(s) or {}, **row)
    pol = T._pol()
    pol["categories"][BED].update(skus_per_category=bed_k, min_margin=bed_margin)
    pol["categories"][SOFA]["skus_per_category"] = sofa_k
    pol["shop"].update(sp)
    inv = T._inv([])
    inv["surge"] = [{"sku": s, "units": 100, "factor": f} for s, f in surge]
    buy, why = P.restock_plan(inv, demand, catalog, pol, stats, activity={}, now=T.NOW)
    return {b["sku"]: b["qty"] for b in buy}, why


def test_goods_rank_by_margin_per_hour_before_settles():
    # G1515d951 has the most settles (4899) but a thin margin; G03d0199f (1.61M list) earns more per hour
    rows = {
        "G1515d951": {
            "n": 50,
            "p50": 300_000,
            "velocity_per_h": 20.0,
        },  # 20 x (300k - 250k) = 1.0M/h
        "G07ec17d5": {
            "n": 50,
            "p50": 200_000,
            "velocity_per_h": 10.0,
        },  # 10 x (200k - 165k) = 0.35M/h
        "G03d0199f": {"n": 30, "p50": 1_400_000, "velocity_per_h": 3.0},
    }  # 3 x (1.4M - 950k) = 1.35M/h
    q, why = _plan(rows)
    assert set(q) & BED_SET == {"G03d0199f"}
    assert any("by margin/h" in n for n in why["notes"])
    q, why = _plan({})  # no p50 or velocity: the settles order as before
    assert set(q) & BED_SET == {"G1515d951"}


def test_surge_lowers_a_hot_skus_margin_rank():
    rows = {
        "G1515d951": {
            "n": 50,
            "p50": 300_000,
            "velocity_per_h": 20.0,
        },  # 1.0M/h, at 1.18 0.10M/h
        "G07ec17d5": {"n": 50, "p50": 220_000, "velocity_per_h": 10.0},
    }  # 0.55M/h
    q, _ = _plan(rows)
    assert set(q) & BED_SET == {"G1515d951"}
    q, _ = _plan(rows, surge=[("G1515d951", 1.18)])
    assert set(q) & BED_SET == {"G07ec17d5"}


def test_surge_counts_in_the_unit_cost_cap():
    # sofa G8ec51935: 2.324M x 0.75 = 1.743M under the 2.0M cap; at surge 1.2 it is 2.09M, over it
    q, _ = _plan({})
    assert "G8ec51935" in q
    q, _ = _plan({}, surge=[("G8ec51935", 1.2)])
    assert "G8ec51935" not in q and "G00327e80" in q


def test_surge_counts_in_the_worst_spend():
    q0, why0 = _plan({})
    q1, why1 = _plan({}, surge=[("G8ec51935", 1.1)])
    assert q0 == q1 and q0["G8ec51935"] > 0
    unit0 = int(math.ceil(2_324_000 * 0.75))
    unit1 = int(math.ceil(2_324_000 * 0.75 * 1.1))
    assert why1["worst_spend"] - why0["worst_spend"] == q0["G8ec51935"] * (
        unit1 - unit0
    )


def test_surge_counts_in_the_below_market_check():
    # G03d0199f p50 1.0M, list 1.61M, min_margin 0.05: over 0.59 x 1.05 x list (997k) at factor 1, under it at 1.2
    rows = {"G03d0199f": {"n": 30, "p50": 1_000_000, "velocity_per_h": 3.0}}
    kw = dict(skip_below_market=True, est_cost_frac=0.59)
    q, why = _plan(rows, **kw)
    assert set(q) & BED_SET == {"G03d0199f"}
    q, why = _plan(rows, surge=[("G03d0199f", 1.2)], **kw)
    assert "G03d0199f" not in q
    assert any("market p50 under cost" in n for n in why["notes"])


# --- action space inside the policy validator (09:58 KST: stock_target 36 failed bounds [0, 20] and was rolled back)


def test_action_space_ranges_pass_the_policy_validator():
    from icrl import space as SP

    pol = P.load()
    bounds = pol["bounds"]
    for e in SP.load():
        key = e["path"].rsplit(".", 1)[-1]
        if e.get("type") in ("int", "float") and key in bounds:
            lo, hi = bounds[key]
            assert lo <= e["min"] and e["max"] <= hi, (e["path"], e["min"], e["max"], bounds[key])
        if key == "skus_per_category":
            assert 1 <= e["min"] and e["max"] <= 5, e
    pol["categories"]["가구/침구>가구>침대/매트리스"] = {"stock_target": 60, "qty_per_buy": 20, "skus_per_category": 5}
    P.validate(pol)


# --- v2.0b: our units' cost_frac already carries past surges, so the below-market check does not multiply it again


def test_surge_cost_frac_does_not_count_the_surge_twice():
    assert P.surge_cost_frac(0.70, "goods", 1.18) == pytest.approx(0.70)  # 0.59 x 1.18 = 0.696 < 0.70
    assert P.surge_cost_frac(0.60, "goods", 1.3) == pytest.approx(0.767)
    assert P.surge_cost_frac(0.60, "food", 1.3) == pytest.approx(0.65)
    assert P.surge_cost_frac(0.62, "goods", 1.0) == 0.62


def test_a_surged_bestseller_is_not_skipped_by_our_own_surged_cost():
    # 10:05 labpc: G1515d951 list 423,810, p50 340,000 (0.80 of list), surge 1.18, our cost_frac 0.70
    rows = {"G1515d951": {"n": 50, "p50": 340_000, "velocity_per_h": 20.0}}
    units = [T._unit("G07ec17d5", BED, 279_000) for _ in range(4)]
    for u in units:
        u["cost"] = int(0.70 * 279_000)
    catalog, demand, stats = T._world()
    stats._sku["G1515d951"].update(rows["G1515d951"])
    pol = T._pol()
    pol["shop"].update(skip_below_market=True)
    pol["categories"][BED].update(skus_per_category=1, min_margin=0.05, stock_target=30)
    inv = T._inv(units)
    inv["surge"] = [{"sku": "G1515d951", "units": 140, "factor": 1.18}]
    buy, why = P.restock_plan(inv, demand, catalog, pol, stats, activity={}, now=T.NOW)
    assert "G1515d951" in {b["sku"] for b in buy}, why["notes"]
