"""11/10 ~12:30 (person): deep stock for fast-selling high-price SKUs (depts/deep_high.py). Temp files only.
    python tests/test_deep_high.py"""
import os
import sqlite3
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import tmp_json, tmp_path  # noqa: E402
from depts import deep_high as D  # noqa: E402

NOW = 1_000_000.0
R = dict(D.DEFAULT, enabled=True)


def db(rows):
    p = tmp_path(f"dh{len(rows)}.db")
    if os.path.exists(p):
        os.remove(p)
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE settles (sku TEXT, ts REAL, amount INTEGER, unit TEXT, shop TEXT, name TEXT)")
    d.execute("CREATE TABLE my_stock (unit TEXT, cost INTEGER, supplied_at REAL, name TEXT, sku TEXT)")
    for i, (sku, age_h, sold_ago_h, amt, cost) in enumerate(rows):
        d.execute("INSERT INTO settles VALUES (?, ?, ?, ?, 'choo-chu', ?)", (sku, NOW - sold_ago_h * 3600, amt, f"u{i}", sku))
        d.execute("INSERT INTO my_stock VALUES (?, ?, ?, ?, ?)", (f"u{i}", cost, NOW - (sold_ago_h + age_h) * 3600, sku, sku))
    d.commit()
    d.close()
    return p


def test_compute():
    rows = ([("A", 1, h, 300_000, 250_000) for h in (1, 2, 3, 20, 22)]          # 5 sold, 3 in 12 h, 16.7%: limit 3
            + [("B", 1, h, 300_000, 250_000) for h in (1, 2, 3, 4, 5, 6, 7)]   # 7 in 12 h: capped at 5
            + [("C", 1, 20, 300_000, 250_000), ("C", 1, 21, 300_000, 250_000)]  # 0 in 12 h: min 2
            + [("S", 5, 1, 300_000, 250_000), ("S", 4, 2, 300_000, 250_000)]   # median 4.5 h > 3: out
            + [("M", 1, 1, 300_000, 280_000), ("M", 1, 2, 300_000, 280_000)]   # margin 6.7% < 10%: out
            + [("O", 1, 1, 300_000, 250_000)]                                     # 1 sale: out
            + [("L", 1, 1, 90_000, 50_000), ("L", 1, 2, 90_000, 50_000)]       # cost <= 100,000: not high price
            + [("X", 1, 30, 300_000, 250_000), ("X", 1, 1, 300_000, 250_000)])  # one sale outside 24 h: out
    out = D.compute(NOW, R, db(rows))
    assert {k: v["limit"] for k, v in out.items()} == {"A": 3, "B": 5, "C": 2}, out
    assert D.limit("A", out) == 3 and D.limit("Z", out) == 1


def test_current_off_and_files():
    with mock.patch.object(D, "RULES", tmp_json({"enabled": False}, "dh")):
        assert D.current(NOW) == {}
    with mock.patch.object(D, "RULES", tmp_json({"enabled": True}, "dh")), \
            mock.patch.object(D, "OUT", tmp_path("dh_out.json")), mock.patch.object(D, "HIST", tmp_path("dh.jsonl")), \
            mock.patch.object(D, "compute", return_value={"A": {"limit": 3, "name": "a"}}):
        assert D.current(NOW)["A"]["limit"] == 3
        assert D.skus_between(NOW - 1, NOW + 1) == {"A"} and D.skus_between(0, NOW - 1) == set()


def test_window_stats():
    # A: one unit supplied at 0 h, sold at 1 h (of a 4 h window from NOW-4h); B: never held -> out all 4 h
    p = db([("A", 1, 3, 300_000, 250_000)])                  # supplied NOW-4h, sold NOW-3h
    w = D.window_stats({"A", "B"}, NOW - 4 * 3600, NOW, p, step_s=3600)
    assert w["buy_n"] == 1 and w["sell_n"] == 1 and w["profit"] == 50_000 and w["median_h"] == 1.0, w
    assert w["out_h"] == 3 + 4, w                             # A out from its sale (3 samples), B all 4


def test_fast_cap_7():
    """11/12 ~09:50 (person): median supply -> sale <= 1.5 h -> held up to 7; the rest of the targets stay at 5."""
    r = dict(R, fast_median_h=1.5, fast_max_hold=7)
    rows = ([("F", 1, h, 300_000, 250_000) for h in range(1, 10)]               # median 1 h, 9 in 12 h: 7
            + [("G", 2, h, 300_000, 250_000) for h in range(1, 10)]             # median 2 h: 5
            + [("H", 1.5, h, 300_000, 250_000) for h in (1, 2, 3)])             # median 1.5 h, 3 in 12 h: 3
    out = D.compute(NOW, r, db(rows))
    assert {k: v["limit"] for k, v in out.items()} == {"F": 7, "G": 5, "H": 3}, out
    assert D.compute(NOW, R, db(rows))["F"]["limit"] == 5                       # without the rule: 5 as before


if __name__ == "__main__":
    test_fast_cap_7()
    test_window_stats()
    test_compute()
    test_current_off_and_files()
    print("ok")
