"""2026-11-04 pricing changes: first ask room 0.09, and the premium pilot (800k+ goods on a separate small budget)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, market, pricing
from r3lib.memory import Memory
from test_r3_core import CAT, settle

SOFA = "가구/침구>가구>소파/의자/스툴"
PCAT = dict(CAT, P1={"name": "가죽 소파", "category": SOFA, "list_price": 1_200_000, "kind": "goods"},
            P2={"name": "리클라이너", "category": SOFA, "list_price": 1_000_000, "kind": "goods"},
            P3={"name": "모듈 소파", "category": SOFA, "list_price": 1_500_000, "kind": "goods"})
DEMAND = [{"category": SOFA, "item": "의자", "kind": "goods", "skus": 4, "median_price": 100000}]


def pmem(tmp_path, monkeypatch, premium_sales=15):
    monkeypatch.setattr(market, "_CATALOG", PCAT)
    m = Memory(tmp_path / "m.sqlite")
    now, rows, seq = time.time(), [], 0
    for sku, price, n in (("G1", 85000, 60), ("P1", 980_000, premium_sales), ("P2", 820_000, premium_sales),
                          ("P3", 1_200_000, 3)):
        for _ in range(n):
            seq += 1
            rows.append(settle(seq, sku, price, now - seq * 60))
    m._ingest(rows)
    return m, now


def test_first_ask_uses_the_wider_room():
    u = {"kind": "goods", "grade": "A", "cost": 50000, "list_price": 100000}
    assert pricing.unit_ask(u, market_ratio=0.70) == int(100000 * (0.70 + config.ASK_ROOM))
    assert config.ASK_ROOM == 0.07


def test_premium_pilot_buys_one_unit_each_within_budget(tmp_path, monkeypatch):
    import restock_r3
    monkeypatch.setattr(config, "PREMIUM_PER_SKU", 1)                  # the rule until 2026-11-09
    m, now = pmem(tmp_path, monkeypatch)
    lines, why, state = restock_r3.plan({"balance": 20_000_000, "units": []}, DEMAND, m, now=now)
    prem = [ln for ln in lines if ln["sku"].startswith("P")]
    # P1 then P2 by market sales; P3 has too few sales; 0.75 x (1.2M + 1.0M) = 1.65M fits 2.5M, a third would not
    assert prem == [{"sku": "P1", "qty": 1}, {"sku": "P2", "qty": 1}]
    assert state["premium_units"] == 2
    assert all(ln["sku"] == "G1" for ln in lines if ln not in prem)          # normal plan never picks a premium SKU


def test_premium_pilot_respects_held_units_and_cap(tmp_path, monkeypatch):
    import restock_r3
    monkeypatch.setattr(config, "PREMIUM_PER_SKU", 1)                  # the rule until 2026-11-09
    m, now = pmem(tmp_path, monkeypatch)
    held = [{"unit": "x1", "sku": "P1", "category": SOFA, "kind": "goods", "grade": "A", "cost": 800_000,
             "list_price": 1_200_000}]
    lines, why, _ = restock_r3.plan({"balance": 20_000_000, "units": held}, DEMAND, m, now=now)
    assert [ln["sku"] for ln in lines if ln["sku"].startswith("P")] == ["P2"]   # P1 already held
    monkeypatch.setattr(config, "PREMIUM_UNIT_COST", 10**12)        # adaptive budget: room for 1 unit
    lines, why, _ = restock_r3.plan({"balance": 20_000_000, "units": held}, DEMAND, m, now=now)
    assert not [ln for ln in lines if ln["sku"].startswith("P")]


def test_premium_stock_does_not_eat_the_normal_budget(tmp_path, monkeypatch):
    import restock_r3
    m, now = pmem(tmp_path, monkeypatch)
    held = [{"unit": f"x{i}", "sku": "P1", "category": SOFA, "kind": "goods", "grade": "A", "cost": 900_000,
             "list_price": 1_200_000} for i in range(3)]
    lines, _, state = restock_r3.plan({"balance": 8_000_000, "units": held}, DEMAND, m, now=now)
    assert state["stock_cost"] == 0 and any(ln["sku"] == "G1" for ln in lines)


def test_marketing_pricing_reports_close_rate_and_premium(tmp_path, monkeypatch):
    from r3lib.org import departments
    m, now = pmem(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    m.record_negotiation("shop", "t1", None, "G1", 80000, 76000, [70000], "deal")
    m.record_negotiation("shop", "t2", None, "G1", 80000, None, [50000], "cancel")
    out = departments.run("marketing", "pricing", {}, m, None)
    assert out["negotiations"] == 2 and out["close_rate"] == 0.5 and out["first_ask_ratio"] == 0.8
    assert out["ask_room"] == config.ASK_ROOM and out["premium"]["held"] == 0


def test_premium_demand_and_stockouts_do_not_size_normal_stock(tmp_path, monkeypatch):
    """2026-11-09: 86% of sofa-category sales were 800k+ SKUs and 10/h premium stockouts (one held unit busy in a
    negotiation) were added as 'missed' demand, so the normal plan aimed at 178 sofa units and bought 124 of one
    296k office chair that sells 0.4/h. The normal plan never buys premium, so premium demand must not size it."""
    import restock_r3
    plain, now = pmem(tmp_path / "a", monkeypatch, premium_sales=0)
    tmp_path.joinpath("b").mkdir()
    busy, _ = pmem(tmp_path / "b", monkeypatch, premium_sales=200)
    for _ in range(60):
        busy.record_stockout(SOFA, "P1")
    inv = {"balance": 500_000_000, "units": []}
    need = lambda m: [w for w in restock_r3.plan(inv, DEMAND, m, now=now, dry_run=True)[1]
                      if w.get("sku") == "G1" and w.get("reason") == "demand"][0]["rule_need"]
    assert need(busy) == need(plain)                                   # was 577 vs 82
    plain.record_stockout(SOFA, "G1")                                  # a normal SKU turned away still counts
    assert busy.stockout_rates(premium_min=config.PREMIUM_MIN_LIST) == {}
    assert plain.stockout_rates(premium_min=config.PREMIUM_MIN_LIST)[SOFA] > 0
    assert busy.stockout_rates()[SOFA] > 0                             # the cash cap still sees every stockout


def test_premium_buys_up_to_per_sku_limit_and_below_old_ratio(tmp_path, monkeypatch):
    """2026-11-09: market pays 0.67-0.69 of list on most premium SKUs, we sell them at 0.70 against 0.60 cost."""
    import restock_r3
    m, now = pmem(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "price_ratio", lambda **kw: (0.68, 50))
    held = [{"unit": "x1", "sku": "P1", "category": SOFA, "kind": "goods", "grade": "A", "cost": 800_000,
             "list_price": 1_200_000}]
    lines, why, state = restock_r3.plan({"balance": 200_000_000, "units": held}, DEMAND, m, now=now)
    prem = {ln["sku"]: ln["qty"] for ln in lines if ln["sku"].startswith("P")}
    n = config.PREMIUM_PER_SKU                             # these SKUs are not hot: the floor decides
    assert prem == {"P1": n - 1, "P2": n}                  # P1 tops up to n, P2 gets n
    monkeypatch.setattr(m, "price_ratio", lambda **kw: (0.64, 50))
    lines, why, _ = restock_r3.plan({"balance": 200_000_000, "units": held}, DEMAND, m, now=now)
    assert not [ln for ln in lines if ln["sku"].startswith("P")]


def test_normal_lines_leave_room_for_premium(tmp_path, monkeypatch):
    """42 of 144 ticks filled all 10 lines with normal stock before premium ran."""
    import restock_r3
    m, now = pmem(tmp_path, monkeypatch)
    cats = [f"가구/침구>가구>C{i}" for i in range(12)]
    pcat = dict(PCAT, **{f"N{i}": {"name": f"n{i}", "category": c, "list_price": 50000, "kind": "goods"}
                         for i, c in enumerate(cats)})
    monkeypatch.setattr(market, "_CATALOG", pcat)
    demand = [{"category": c, "item": "x", "kind": "goods", "skus": 1, "median_price": 50000} for c in cats]
    lines, why, _ = restock_r3.plan({"balance": 200_000_000, "units": []}, demand, m, now=now)
    assert len(lines) <= 10, why
    assert len([ln for ln in lines if ln["sku"].startswith("P")]) == 2, (lines, why)   # both candidates fit in the 3 kept lines
