"""Round end: food stops being bought STOP_BUYING_HOURS before the end (it expires as a loss), goods keep being bought
until GOODS_STOP_BUYING_HOURS (an unsold goods unit stays in book at its cost; only storage, 1%/day, is lost)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config
from r3lib.org import client
from test_org import _market, org  # noqa: F401  (fixture)
from test_r3_core import CAT, mem, settle


def _demand():
    return [{"category": CAT["F1"]["category"], "item": "생수", "kind": "food", "skus": 1, "median_price": 10000},
            {"category": CAT["G1"]["category"], "item": "의자", "kind": "goods", "skus": 1, "median_price": 100000}]


def _plan(tmp_path, monkeypatch, hours_left):
    import restock_r3
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: hours_left)
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    m._ingest([settle(i, "F1", 8500, now - i * 300) for i in range(1, 13)] +
              [settle(100 + i, "G1", 85000, now - i * 120) for i in range(1, 40)])
    return restock_r3.plan({"balance": 50_000_000, "units": []}, _demand(), m, now=now)


def test_both_kinds_are_bought_before_the_food_stop(tmp_path, monkeypatch):
    lines, _, _ = _plan(tmp_path, monkeypatch, config.STOP_BUYING_HOURS + 24)
    assert {ln["sku"] for ln in lines} >= {"F1", "G1"}


def test_only_goods_are_bought_inside_the_food_stop(tmp_path, monkeypatch):
    lines, why, _ = _plan(tmp_path, monkeypatch, config.STOP_BUYING_HOURS - 24)
    assert [ln["sku"] for ln in lines] == ["G1"]
    assert any(w.get("round_end") for w in why)


def test_ceo_tick_still_buys_goods_inside_the_food_stop(org, monkeypatch):  # noqa: F811
    m = _market(org, monkeypatch, {"open": True})
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: config.STOP_BUYING_HOURS - 24)
    out = client.call("hook", "ceo", "tick", {"state": str(config.MARKET_DIR)}, mem=m)
    assert out["buy"] and out["buy"][0]["sku"] == "G1"


def test_ceo_tick_stops_everything_at_the_goods_stop(org, monkeypatch):  # noqa: F811
    m = _market(org, monkeypatch, {"open": True})
    monkeypatch.setattr(config, "hours_left_in_round", lambda now=None: config.GOODS_STOP_BUYING_HOURS - 1)
    out = client.call("hook", "ceo", "tick", {"state": str(config.MARKET_DIR)}, mem=m)
    assert out["buy"] == [] and "last hours" in out["wait"]


import pytest  # noqa: E402
from r3lib import pricing  # noqa: E402


@pytest.mark.parametrize("hl", [71, 35, 11, 1])
def test_goods_floor_never_below_cost_near_end(hl):
    """2026-11-15 rehearsal of the last 72 h: book counts unsold goods at cost, so no goods floor goes under it
    (a slow SKU held 48 h+ is the one exception, cost minus the storage left: tests/test_slow_floor.py)."""
    u = {"kind": "goods", "cost": 500_000, "list_price": 800_000, "supplied_at": 0}
    assert pricing.unit_floor(u, now=1, hours_left_round=hl) >= 500_000


def test_food_floor_falls_toward_salvage_inside_clearance():
    u = {"kind": "food", "cost": 10_000, "list_price": 20_000, "expires_at": 10 ** 9, "shelf_hours": 10 ** 6}
    assert pricing.unit_floor(u, now=1, hours_left_round=config.CLEARANCE_HOURS + 1) >= 10_000
    assert pricing.unit_floor(u, now=1, hours_left_round=config.CLEARANCE_HOURS / 2) < 10_000
