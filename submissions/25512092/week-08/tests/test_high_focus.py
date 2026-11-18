"""High focus (11/09 ~14:35): line_rank scoring and picking, sofa x2 limits, high_caps ceilings, the high-price brake."""
import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depts import high_caps, line_rank as R, sofa  # noqa: E402
from depts import overcost  # noqa: E402
from _isolate import tmp_json  # noqa: E402

overcost.RULES, overcost.SEEN = tmp_json({"units": []}, "overcost_high"), tmp_json({}, "overcost_units")   # not the live files


def test_score():
    # margin x expected units x price = (avg - cost) x units; units capped by qty and by 24 h sales / 24 x 6 x share
    assert R.score(1000, 600, 5, 48, 0.5) == 400 * 5
    assert R.score(1000, 600, 5, 8, 0.5) == 400 * 1
    assert R.score(0, 600, 5, 8, 0.5) == 0


def test_pick_order_and_budgets():
    c = [{"sku": "lo", "qty": 10, "tier": "low", "cost": 1000, "score": 500, "src": "hot"},
         {"sku": "hi1", "qty": 1, "tier": "high", "cost": 300000, "score": 90000, "src": "rest"},
         {"sku": "hi2", "qty": 1, "tier": "high", "cost": 300000, "score": 80000, "src": "rest"},
         {"sku": "lo2", "qty": 4, "tier": "low", "cost": 2000, "score": 9000, "src": "hot"}]
    out = R.pick(c, {"high": 400000, "low": 5000}, 5, 3, log=lambda m: None)
    assert [o["sku"] for o in out] == ["hi1", "lo2", "lo"] and out[1]["qty"] == 2 and out[2]["qty"] == 1
    out = R.pick(c, {"high": 1e9, "low": 1e9}, 1, 10, log=lambda m: None)     # high-price unit room 1
    assert [o["sku"] for o in out] == ["hi1", "lo2", "lo"]


def test_sofa_x2():
    assert sofa.limits_now({}) == (1, 10, 1, 500_000)
    assert sofa.limits_now({"x2": True}) == (2, 20, 2, 1_000_000)
    assert sofa.limits_now({"x2": True, "x3": True}) == (3, 30, 3, 1_500_000)      # 11/09 ~21:20


def test_high_caps():
    p = high_caps.PATH
    high_caps.PATH = tempfile.mktemp(suffix=".json")
    try:
        assert high_caps.get() == (0.6, 40)
        high_caps.set_caps(0.7, 60, "t")
        assert high_caps.get() == (0.7, 60)
    finally:
        high_caps.PATH = p


def test_brake():
    import high_focus_rollout as F
    f = tempfile.mktemp(suffix=".db")
    d = sqlite3.connect(f)
    d.execute("CREATE TABLE settles (ts REAL, shop TEXT, sku TEXT, amount INTEGER, unit TEXT)")
    d.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, cost INTEGER, supplied_at REAL)")
    d.execute("CREATE TABLE shop_deals (ts REAL, outcome TEXT, cost INTEGER)")
    now = time.time()
    since = now - 20 * 3600
    for i in range(4):                               # sold after 8 h each (condition a)
        d.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (f"s{i}", "S", 300000, now - 9 * 3600))
        d.execute("INSERT INTO settles VALUES (?,?,?,?,?)", (now - 3600, F.ME, "S", 400000, f"s{i}"))
    for i in range(12):                              # bought after the switch, 13 h old (condition b)
        d.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (f"a{i}", "A", 300000, now - 13 * 3600))
    d.execute("INSERT INTO my_stock VALUES (?,?,?,?)", ("old", "O", 900000, since - 3600))   # held before: left out
    for i in range(20):
        d.execute("INSERT INTO shop_deals VALUES (?,?,?)", (now - 600, "receipt" if i < 2 else "fail", 300000))
    d.commit()
    held = lambda ids: (lambda: [{"unit": u, "cost": 300000, "supplied_at": now - 13 * 3600} for u in ids])
    F.held_units = held(["old", "a0", "a1", "a2", "a3"])            # 4 of 12 unsold = 33% > 30%
    st = {n: x for n, x, _ in F.brake_state(d, now, 0.4, since)}
    assert st == {"sell_median": True, "stale_cost": True, "receipt_ratio": True}
    F.held_units = held(["old", "a0", "a1", "a2"])                  # 25%
    st = {n: x for n, x, _ in F.brake_state(d, now, 0.15, since)}
    assert st == {"sell_median": True, "stale_cost": False, "receipt_ratio": False}
    d.execute("DELETE FROM my_stock WHERE unit IN ('a9','a10','a11')")   # 9 aged units: no judgement
    F.held_units = held(["a0", "a1", "a2", "a3", "a4", "a5"])
    st = F.brake_state(d, now, 0.15, since)
    assert st[1][1] is False and "판단 보류" in st[1][2]
    assert F.brake_state(d, now, 0.15, None)[1][1] is False       # before the switch: nothing to judge


def test_old_stale_text():
    import high_focus_rollout as F
    t = F.old_stale_text([{"unit": "u", "sku": "G1", "name": "x", "grade": "B", "age_h": 30.2, "cost": 200000,
                           "floor": 210000, "avg": 300000}, {"unit": "v", "sku": "G2", "name": "y", "grade": "A",
                                                             "age_h": 13, "cost": 150000, "floor": None, "avg": None}])
    assert "2개 (원가 350,000)" in t and "하한 210,000, 평균 300,000 (70%)" in t and "하한 -, 평균 - (-)" in t


if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_"):
            fn()
            print("ok", n)
