"""v1.18 supply windows: restock plans only around an open window, and no more lines than fit before it closes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy as P  # noqa: E402

NOW = 1793708000.0  # 2026-11-03 21:13:20 KST, inside Round 3


def _plan(restock):
    catalog, demand = {}, []
    for i in range(14):
        cat = f"테스트>분류{i:02d}"
        catalog[f"S{i:02d}"] = {"sku": f"S{i:02d}", "name": f"상품{i}", "category": cat, "kind": "goods",
                                "list_price": 10000}
        demand.append({"category": cat, "kind": "goods", "median_price": 10000})
    inv = {"balance": 4_500_000, "units": [], "storage_per_day": 0.01}
    if restock is not None:
        inv["restock"] = restock
    return P.restock_plan(inv, demand, catalog, P.load(), None, activity={}, now=NOW)


def test_closed_window_far_away_plans_nothing():
    buy, why = _plan({"open": False, "opens_at": NOW + 600, "closes_at": NOW + 780})
    assert buy == [] and why["why"] == "window_closed" and why["opens_in_s"] == 600


def test_window_opening_within_lead_plans_a_full_run():
    buy, why = _plan({"open": False, "opens_at": NOW + 30, "closes_at": NOW + 210})
    assert why["lines_fit"] == 18 and 1 <= len(buy) <= 10


def test_open_window_caps_lines_to_the_time_left():
    buy, why = _plan({"open": True, "opens_at": NOW - 150, "closes_at": NOW + 35})
    assert why["lines_fit"] == 3 and 1 <= len(buy) <= 3


def test_window_closing_plans_nothing():
    buy, why = _plan({"open": True, "opens_at": NOW - 175, "closes_at": NOW + 5})
    assert buy == [] and why["why"] == "window_closing"


def test_no_window_info_keeps_the_old_behaviour():
    buy, why = _plan(None)
    assert 1 <= len(buy) <= 10 and "lines_fit" not in why
