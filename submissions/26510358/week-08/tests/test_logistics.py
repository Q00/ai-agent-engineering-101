"""Logistics quick wins: warranty by grade and category, supply-window timing, stock-outs as our own demand."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, pricing
from test_r3_core import CAT, mem, settle


def test_no_warranty_on_grade_c_and_priced_in_otherwise():
    a = {"kind": "goods", "grade": "A", "cost": 50000, "list_price": 100000}
    c = dict(a, grade="C", cost=30000)
    assert pricing.warranty_hours_for(c, 60000, 0.01) == 0
    assert pricing.warranty_hours_for(a, 80000, 0.03) == config.WARRANTY_HOURS
    assert pricing.warranty_ask(80000, a, 6, 0.05) == 84000
    assert pricing.warranty_ask(98000, a, 6, 0.05) == 100000                      # never above list price
    assert pricing.warranty_ask(80000, a, 0, 0.05) == 80000


def test_break_rate_uses_category_when_it_has_data(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "BREAK_MIN_N", 20)
    now = time.time()
    m._ingest([settle(i, "G1", 80000, now - i, grade="A") for i in range(1, 31)])
    m._ingest([{"seq": 1000 + i, "ts": now, "kind": "broken", "round": 3, "data": {"order": f"o{i}", "shop": "https://s1",
                "item": "G1"}} for i in range(1, 7)])                              # 6 of 30 chairs broke
    assert abs(m.break_rate_for("A", CAT["G1"]["category"]) - 7 / 40) < 1e-9
    assert m.break_rate_for("A", "nowhere") == m.break_rate("A")


def test_window_timing():
    import restock_r3
    now = 1000.0
    assert restock_r3.window_near({"restock": {"open": True}}, now)
    assert restock_r3.window_near({"restock": {"open": False, "opens_at": now + 30, "closes_at": now + 210}}, now)
    assert not restock_r3.window_near({"restock": {"open": False, "opens_at": now + 600, "closes_at": now + 780}}, now)
    assert not restock_r3.window_near({"restock": {"open": False, "opens_at": now - 300, "closes_at": now - 120}}, now)
    assert restock_r3.window_near({}, now)


def test_window_uses_the_market_clock():
    import restock_r3
    inv = {"restock": {"open": False, "opens_at": 1000, "closes_at": 1180}}
    # our clock says 47 s to go, the market says 148 s: the market wins
    assert not restock_r3.window_near(inv, live={"now": 852, "open": False, "opens_at": 1000, "closes_at": 1180})
    assert restock_r3.window_near(inv, live={"now": 960, "open": False, "opens_at": 1000, "closes_at": 1180})


def test_stockouts_become_our_demand(tmp_path, monkeypatch):
    import restock_r3
    import shop_r3
    m = mem(tmp_path, monkeypatch)
    assert shop_r3.requested_category("I want to buy a 소파/의자/스툴")[0] == CAT["G1"]["category"]
    for _ in range(12):
        m.record_stockout(CAT["G1"]["category"], "G1")
    assert m.stockout_rates()[CAT["G1"]["category"]] == 12 / config.STOCKOUT_HOURS
    demand = [{"category": CAT["G1"]["category"], "item": "의자", "kind": "goods", "skus": 1, "median_price": 100000}]
    lines, why, _ = restock_r3.plan({"balance": 5_000_000, "units": []}, demand, m)   # no market settles at all
    assert lines and lines[0]["sku"] == "G1"
    assert any(w.get("stockouts_per_h") for w in why)


def test_warranty_is_the_markets_maximum_since_breaks_come_within_6_h():
    """A break is possible only within 6 h of the sale, so any warranty of 6 h or more costs the same; offer 72 h."""
    a = {"kind": "goods", "grade": "A", "cost": 50000, "list_price": 100000}
    assert pricing.warranty_hours_for(a, 80000, 0.03) == 72
