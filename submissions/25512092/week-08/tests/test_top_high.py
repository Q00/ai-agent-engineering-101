"""Over 1.5M high-price units (person 11/11 ~20:10), through the operating paths:
  purchasing.make_plan (what restock.main calls, also for the 2 x MAX_LINES window plan): HIGH_MAX 3.0M lets a 2.4M SKU
    in; at most 2 a SKU even when deep_high allows 5; at most 10 over-1.5M units held + planned; under-1.5M lines untouched
  strategy.warranty_hours (what the seller calls per negotiation): over 1.5M only A grade in a category with market break
    <= 6% (n >= min_n); B / C none (before the B grade and sofa rules); under 1.5M unchanged
  top_high.cat_break (what market_db.refresh writes into market_stats.json)
Live files are not touched: temp rules / policy, the market and the db faked.
    python tests/test_top_high.py"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import strategy  # noqa: E402
from depts import b_warranty, deep_high, purchasing as PU, sofa, top_high  # noqa: E402

RULES = {"enabled": True, "min_cost": 1_500_000, "max_units": 10, "max_per_sku": 2,
         "warranty": {"grades": ["A"], "max_break": 0.06, "min_n": 30}}
CAT = "가전>TV"


def catalog():
    c = {f"T{i}": {"name": f"top {i}", "list_price": 4_000_000, "kind": "goods", "category": CAT} for i in range(8)}
    c["MID"] = {"name": "mid", "list_price": 1_000_000, "kind": "goods", "category": CAT}     # est. cost 600k
    c["BIG"] = {"name": "big", "list_price": 6_000_000, "kind": "goods", "category": CAT}     # 3.6M > HIGH_MAX 3.0M
    return c


def stats(cat):
    sk = {k: {"name": k, "avg": int(v["list_price"] * 0.6 * 1.3), "n": 50, "sold_5m": 2, "sold_30m": 5}
          for k, v in cat.items()}
    return {"skus": sk, "hot": list(sk), "wanted": [], "wanted_cats": [], "sku_break": {}, "sku_n": {}}


def plan_with(units, rules=RULES, high_max=3_000_000, deep_limit=5, actual=None):
    cat = catalog()
    st = stats(cat)
    P = mock.patch.object
    noop = lambda *a, **k: None
    with contextlib.ExitStack() as es:
        for obj, name, val in (
                (top_high, "RULES", tmp_json(rules, "top_high")), (PU, "HIGH_MAX", high_max),
                (top_high, "last_costs", lambda *a, **k: dict(actual or {})),
                (PU, "load_stats", lambda: st), (PU, "price_of", lambda k, s=None: (s or st)["skus"][k]["avg"] and
                                                 ((s or st)["skus"][k]["avg"], 50)),
                (PU, "busy_categories", lambda c, top=5: ["TV"]), (PU, "c_rules", lambda: {}),
                (PU, "excluded", lambda: {}), (PU, "guard_check", lambda *a: None), (PU, "sofa_trial", lambda *a: None),
                (PU, "note_band", noop), (PU.sales_report, "policy", lambda: {"high_share": 0.9, "high_max": 100}),
                (deep_high, "current", lambda **k: {}), (deep_high, "limit", lambda sku, deep: deep_limit),
                (PU.decisions, "record", noop), (PU.strategy, "current", lambda: ("normal", None))):
            es.enter_context(P(obj, name, val))
        es.enter_context(mock.patch("depts.overcost.mark", lambda units, f, log=None: set()))
        es.enter_context(mock.patch("depts.request_buy.candidates", lambda *a, **k: []))
        es.enter_context(mock.patch("depts.hot_fast.shares", lambda hot, share, log=None: {}))
        es.enter_context(mock.patch("depts.high_cost_exclude.current", lambda **k: {}))
        es.enter_context(mock.patch("depts.line_rank.on", lambda *a: False))
        es.enter_context(mock.patch("depts.high_ask.buy_ok", lambda *a: (True, 0, 0)))
        logs = []
        plan = PU.make_plan({"balance": 10 ** 9, "units": units}, units, cat, 10 ** 9, logs.append)
    return plan, logs


def unit(sku, cost, n=1):
    return [{"unit": f"u{sku}{i}", "sku": sku, "cost": cost, "grade": "A"} for i in range(n)]


def test_plan_caps():
    old = PU.MAX_LINES
    PU.MAX_LINES = 20                                     # as restock.main for a window's first plan (both halves)
    try:
        plan, logs = plan_with([])
        q = {p["sku"]: p["qty"] for p in plan}
        assert "BIG" not in q                             # 3.6M > HIGH_MAX 3.0M
        assert all(q[f"T{i}"] <= 2 for i in range(8) if f"T{i}" in q)   # deep 5 cut to 2
        assert sum(v for k, v in q.items() if k.startswith("T")) == 10  # 10 over 1.5M in all
        assert q.get("MID") == 5                          # under 1.5M: deep 5 as before
        assert any(m.startswith("[top high]") for m in logs)
        plan, _ = plan_with(unit("T0", 2_400_000, 2) + unit("T1", 2_400_000, 7))   # 9 held: room 1, T0/T1 full
        q = {p["sku"]: p["qty"] for p in plan}
        assert "T0" not in q and "T1" not in q and sum(v for k, v in q.items() if k.startswith("T")) == 1
        plan, _ = plan_with([], high_max=1_500_000)       # before the change: nothing over 1.5M
        assert not any(p["sku"].startswith("T") for p in plan)
        plan, _ = plan_with([], rules={})                 # rule off: deep 5 kept for over-1.5M
        q = {p["sku"]: p["qty"] for p in plan}
        assert max(v for k, v in q.items() if k.startswith("T")) == 5
    finally:
        PU.MAX_LINES = old


def test_warranty_through_strategy():
    # the A grade rate decides (11/11 ~20:50); the whole category's rate (cat_break) is ignored
    st = {"cat_break_a": {"TV": 0.05, "소파/의자/스툴": 0.0776}, "cat_n_a": {"TV": 500, "소파/의자/스툴": 26000},
          "cat_break": {"TV": 0.09}, "cat_n": {"TV": 900}}
    u = lambda g, cost, cat=CAT, sku="T0": {"unit": "u1", "sku": sku, "cost": cost, "grade": g, "kind": "goods",
                                            "category": cat}
    b_rules = {"enabled": True, "on_cats": ["TV"]}
    with mock.patch.object(top_high, "RULES", tmp_json(RULES, "top_high")), \
            mock.patch.object(b_warranty, "RULES", tmp_json(b_rules, "b_warranty")), \
            mock.patch.object(sofa, "RULES", tmp_json({"enabled": True, "warranty": True}, "sofa")):
        assert strategy.warranty_hours(u("A", 2_000_000), "normal", st) == 48        # A, TV 5% <= 6%
        assert strategy.warranty_hours(u("B", 2_000_000), "normal", st) == 0         # B over 1.5M: none (b_warranty would give 48)
        assert strategy.warranty_hours(u("B", 1_000_000), "normal", st) == 48        # B under 1.5M: b_warranty as before
        assert strategy.warranty_hours(u("C", 2_000_000), "normal", st) == 0
        assert strategy.warranty_hours(u("A", 2_000_000), "markdown", st) == 0
        st2 = dict(st, cat_break_a={"TV": 0.07, "소파/의자/스툴": 0.0776})
        assert strategy.warranty_hours(u("A", 2_000_000), "normal", st2) == 0        # TV 7% > 6%
        st3 = dict(st, cat_n_a={"TV": 29})
        assert strategy.warranty_hours(u("A", 2_000_000), "normal", st3) == 0        # too few market sales
        with mock.patch.object(sofa, "is_sofa", lambda sku: True):
            assert strategy.warranty_hours(u("A", 2_000_000, "가구>소파/의자/스툴", "S1"), "normal", st) == 0   # 7.8%
            st4 = dict(st, cat_break_a={"소파/의자/스툴": 0.0593}, cat_n_a={"소파/의자/스툴": 18000})
            assert strategy.warranty_hours(u("A", 2_000_000, "가구>소파/의자/스툴", "S1"), "normal", st4) == 72  # sofa hours
            assert strategy.warranty_hours(u("B", 2_000_000, "가구>소파/의자/스툴", "S1"), "normal", st4) == 0
    with mock.patch.object(top_high, "RULES", tmp_json({}, "top_high")), \
            mock.patch.object(b_warranty, "RULES", tmp_json(b_rules, "b_warranty")):
        assert strategy.warranty_hours(u("B", 2_000_000), "normal", st) == 48        # rule off: as before


def test_cat_break():
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE settles (sku TEXT)")
    d.execute("CREATE TABLE broken (sku TEXT)")
    d.executemany("INSERT INTO settles VALUES (?)", [("T0",)] * 60 + [("T1",)] * 40 + [("Z",)] * 5)
    d.executemany("INSERT INTO broken VALUES (?)", [("T0",)] * 4 + [("T1",)])
    r, n = top_high.cat_break(d, catalog())
    assert n["TV"] == 100 and r["TV"] == 0.05


def test_cat_break_a():
    """A break counts for the grade of the buyer's last purchase of the SKU before it."""
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE settles (ts, buyer, sku, grade)")
    d.execute("CREATE TABLE broken (ts, buyer, sku)")
    rows = [(i, f"b{i}", "T0", "A") for i in range(40)] + [(i, f"c{i}", "T1", "B") for i in range(10)]
    d.executemany("INSERT INTO settles VALUES (?,?,?,?)", rows + [(50, "b0", "T0", "B")])
    d.executemany("INSERT INTO broken VALUES (?,?,?)", [(45, "b1", "T0"), (46, "b2", "T0"),     # A purchases: A breaks
                                                       (60, "b0", "T0"),                       # b0's last buy was B
                                                       (20, "c1", "T1")])                      # B
    r, n = top_high.cat_break_a(d, catalog())
    assert n["TV"] == 40 and r["TV"] == 0.05
    c = os.path.join(tempfile.mkdtemp(), "c.json")
    assert top_high.cat_break_a_cached(d, catalog(), path=c) == (r, n)
    d.execute("INSERT INTO broken VALUES (70, 'b3', 'T0')")
    assert top_high.cat_break_a_cached(d, catalog(), path=c)[0]["TV"] == 0.05             # cached (30 min)
    assert top_high.cat_break_a_cached(d, catalog(), path=c, max_age=0)[0]["TV"] == 0.075


def test_check_top15_report():
    """The 3 h report through its sending path: temp db (my_stock, settles, broken, cash, board_hist), fake board."""
    import time
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import check_top15 as C
    now = time.time()
    t0 = now - 3 * 3600
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE my_stock (unit, sku, name, cost, grade, supplied_at, market_avg_at)")
    d.execute("CREATE TABLE settles (seq, ts, buyer, shop_url, shop, sku, name, grade, amount, claim_honest, "
              "market_avg_at, unit, warranty_hours)")
    d.execute("CREATE TABLE broken (seq, ts, buyer, shop_url, shop, sku, hours_after_sale, warranty_hours, refunded)")
    d.execute("CREATE TABLE cash (ts, balance, stock_cost, units, storage_per_day)")
    d.execute("CREATE TABLE board_hist (ts REAL PRIMARY KEY, shop TEXT, buyer TEXT)")
    d.executemany("INSERT INTO my_stock VALUES (?,?,?,?,?,?,?)", [
        ("u1", "T0", "", 2_000_000, "A", t0 + 600, 0), ("u2", "T0", "", 2_000_000, "B", t0 + 700, 0),
        ("u3", "MID", "", 600_000, "A", t0 + 800, 0)])
    d.execute("INSERT INTO settles VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
              (t0 + 4200, "b1", "", "choo-chu", "T0", "", "A", 2_300_000, 1, 0, "u1", 48))
    d.execute("INSERT INTO broken VALUES (1,?,?,?,?,?,?,?,?)", (t0 + 7200, "b1", "", "choo-chu", "T0", 1, 48, 2_300_000))
    d.executemany("INSERT INTO cash VALUES (?,?,?,?,?)", [(t0, 50_000_000, 60_000_000, 1, 1),
                                                          (now, 47_000_000, 64_000_000, 1, 1)])
    sh = lambda b, r: json.dumps({"name": "choo-chu", "book": b, "revenue": r, "rank": 19})
    d.executemany("INSERT INTO board_hist VALUES (?,?,?)", [(t0 - 3 * 3600, sh(1, 1), "{}"), (t0, sh(100, 1000), "{}")])
    d.commit()
    d.close()
    st = tempfile.mkdtemp()
    with open(os.path.join(st, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump({"units": [{"sku": "T0", "cost": 2_000_000, "grade": "B"}]}, f)
    sent = []
    live = lambda: {"shops": [{"name": "choo-chu", "book": 400, "revenue": 4000, "all": 0.7}], "buyers": []}
    C.send_with_retry({"t0": t0}, lambda lv, k, t: sent.append(t), now=now, sleep=lambda s: None, db_path=p,
                      board=live, clock=lambda: now, state=st)
    t = sent[0]
    assert "매입 2개 원가 4,000,000" in t and "판매 1개 매출 2,300,000 이익 300,000 (이익률 13.0%)" in t, t
    assert "회전(입고→판매 중앙값) 1.00시간" in t and "보증 붙은 판매 1" in t and "환불 1건 2,300,000" in t, t
    assert "지금 보유 1개" in t and "현금 50,000,000 -> 47,000,000 (-3,000,000)" in t and "Book 100 vs 33" in t, t


def test_actual_cost_and_20_3():
    """11/12 ~09:50 (person): held at most 20 over 1.5M, 3 a SKU; "over 1.5M" on max(estimate, recent actual supply
    cost): MID is estimated at 600k but was supplied at 1.6M -> capped like an over-1.5M SKU."""
    r = dict(RULES, max_units=20, max_per_sku=3)
    old = PU.MAX_LINES
    PU.MAX_LINES = 20
    try:
        plan, logs = plan_with([], rules=dict(r, max_units=100), actual={"MID": 1_600_000})
        q = {p["sku"]: p["qty"] for p in plan}
        assert q.get("MID") == 3, q                                   # deep 5 cut to 3 by the actual cost
        assert all(v <= 3 for k, v in q.items() if k.startswith("T"))
        plan, logs = plan_with([], rules=r, actual={"MID": 1_600_000})
        q = {p["sku"]: p["qty"] for p in plan}
        assert sum(v for k, v in q.items() if k.startswith("T") or k == "MID") == 20, q   # MID counts in the 20
        plan, _ = plan_with([], rules=r)                              # no actual cost known: MID stays at 5
        assert {p["sku"]: p["qty"] for p in plan}.get("MID") == 5
        plan, _ = plan_with(unit("T0", 2_400_000, 3) + unit("T1", 2_400_000, 16), rules=r)   # 19 held: room 1
        q = {p["sku"]: p["qty"] for p in plan}
        assert "T0" not in q and sum(v for k, v in q.items() if k.startswith("T")) == 1, q
    finally:
        PU.MAX_LINES = old


def test_last_costs_reads_recent_max():
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE my_stock (unit, sku, cost, supplied_at)")
    now = 1_000_000_000
    d.executemany("INSERT INTO my_stock VALUES (?,?,?,?)", [
        ("a", "S", 1_537_011, now - 3600), ("b", "S", 1_317_353, now - 60),        # recent: the A-grade cost wins
        ("c", "OLD", 1_900_000, now - 100 * 3600), ("d", "OLD", 900_000, now - 90 * 3600)])   # none in 48 h: last supply
    d.commit()
    d.close()
    a = top_high.last_costs(p, now=now)
    assert a == {"S": 1_537_011, "OLD": 900_000}, a
    assert top_high.judge_cost("S", 1_392_000, a) == 1_537_011 and top_high.judge_cost("Z", 1_392_000, a) == 1_392_000


if __name__ == "__main__":
    test_actual_cost_and_20_3()
    test_last_costs_reads_recent_max()
    test_plan_caps()
    test_warranty_through_strategy()
    test_cat_break()
    test_cat_break_a()
    test_check_top15_report()
    print("ok")
