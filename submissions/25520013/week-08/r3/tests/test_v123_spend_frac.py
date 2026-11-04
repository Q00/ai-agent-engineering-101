"""v1.23: shop.max_spend_frac caps one restock run at a share of the balance (성수님 2026-11-05: per-run spend
follows cash). max_spend_per_run stays the absolute ceiling; without the key the v1.22 budget is unchanged."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy as P  # noqa: E402

NOW = 1793708000.0
M = "가구/침구>가구>침대/매트리스"


class Stats:
    def sku(self, k):
        return {"G1": {"settles_all": 100}}.get(k)

    def category(self, k):
        return {M: {"velocity_per_h": 100.0}}.get(k)


CATALOG = {"G1": {"sku": "G1", "category": M, "kind": "goods", "list_price": 400000}}
DEMAND = [{"category": M, "kind": "goods", "median_price": 400000}]


def _pol(frac=None, per_run=9_000_000):
    pol = copy.deepcopy(P.load())
    sp = pol["shop"]
    sp.update(
        max_spend_per_run=per_run,
        max_inventory_cost_frac=None,
        balance_floor=1_000_000,
        max_unit_cost=2_000_000,
        max_lines_per_run=10,
        skip_below_market=False,
        restock_priority=[],
    )
    sp.pop("max_spend_frac", None)
    if frac is not None:
        sp["max_spend_frac"] = frac
    pol.setdefault("categories", {})[M] = {
        "stock_target": 20,
        "qty_per_buy": 20,
        "skus_per_category": 1,
    }
    return pol


def _budget(pol, balance):
    inv = {
        "balance": balance,
        "units": [],
        "storage_per_day": 0,
        "restock": {"open": True, "opens_at": NOW - 10, "closes_at": NOW + 170},
    }
    buy, why = P.restock_plan(inv, DEMAND, CATALOG, pol, Stats(), activity={}, now=NOW)
    return why["budget"], sum(b["qty"] for b in buy)


def test_frac_caps_the_run_at_a_share_of_the_balance():
    budget, qty = _budget(_pol(frac=0.25), 20_000_000)
    assert budget == 5_000_000
    assert qty == 5_000_000 // 300_000  # worst cost 0.75 x list = 300,000 per unit


def test_frac_grows_with_cash_up_to_the_absolute_ceiling():
    assert _budget(_pol(frac=0.25), 30_000_000)[0] == 7_500_000
    assert (
        _budget(_pol(frac=0.25), 40_000_000)[0] == 9_000_000
    )  # max_spend_per_run still binds


def test_no_frac_keeps_the_old_budget():
    assert _budget(_pol(), 20_000_000)[0] == 9_000_000


@pytest.mark.parametrize("bad", [0, -0.1, 1.5, True, "0.25"])
def test_validate_rejects_bad_frac(bad):
    pol = _pol()
    pol["shop"]["max_spend_frac"] = bad
    with pytest.raises(P.PolicyError):
        P.validate(pol)


@pytest.mark.parametrize("ok", [0.25, 1, 0.05])
def test_validate_accepts_frac(ok):
    pol = _pol()
    pol["shop"]["max_spend_frac"] = ok
    P.validate(pol)
