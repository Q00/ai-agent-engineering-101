"""11/10 ~12:10 (person): summary line 12 (12 h+ unsold stock, high / other; storage paid). Temp files only.
    python tests/test_summary_stock.py"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import tmp_json, tmp_path  # noqa: E402
sys.argv = ["x"]
import morning_summary as MS  # noqa: E402

T = 1_000_000.0


def test_stock_line():
    inv = tmp_json({"units": [{"cost": 500_000, "supplied_at": T - 13 * 3600}, {"cost": 50_000, "supplied_at": T - 20 * 3600},
                              {"cost": 50_000, "supplied_at": T - 3600}, {"cost": 900_000, "supplied_at": T - 30 * 3600,
                                                                         "grade": "expired"}]}, "inv")
    db = tmp_path("cash.db")
    d = sqlite3.connect(db)
    d.execute("CREATE TABLE cash (ts REAL, balance INTEGER, stock_cost INTEGER, units INTEGER, storage_per_day INTEGER)")
    for i in range(3):                                   # 3 snapshots 12 h apart, 86,400 a day
        d.execute("INSERT INTO cash VALUES (?, 0, 0, 0, 86400)", (T - 24 * 3600 + i * 12 * 3600 - 1,))
    d.commit()
    d.close()
    s = MS.stock_line(T, inv, db)
    assert "원가 550,000 2개" in s and "고가 500,000 1개" in s and "일반 50,000 1개" in s, s
    assert "보관비 누적 86,400" in s and "지금 하루 86,400" in s, s




def test_b_refunds():
    d = sqlite3.connect(":memory:")
    d.execute("CREATE TABLE settles (ts REAL, sku TEXT, buyer TEXT, shop TEXT, grade TEXT, warranty_hours REAL)")
    d.execute("CREATE TABLE broken (ts REAL, sku TEXT, buyer TEXT, shop TEXT, refunded INTEGER)")
    cat = {"T": {"category": "가전>TV"}, "B": {"category": "가구>침대/매트리스"}}
    for ts, sku, buyer, g, w in ((100, "T", "b1", "B", 48), (200, "T", "b2", "B", 48), (210, "B", "b3", "B", 48),
                                 (220, "T", "b4", "A", 72), (50, "T", "b5", "B", 48)):
        d.execute("INSERT INTO settles VALUES (?, ?, ?, 'choo-chu', ?, ?)", (ts, sku, buyer, g, w))
    for ts, sku, buyer, amt in ((150, "T", "b1", 300), (250, "T", "b2", 400), (260, "B", "b3", 500), (270, "T", "b4", 600),
                                (90, "T", "b2", 700)):       # b2's refund at 90 is before its sale: not counted
        d.execute("INSERT INTO broken VALUES (?, ?, ?, 'choo-chu', ?)", (ts, sku, buyer, amt))
    n, win, tot = MS.b_refunds(d, 180, 300, ["TV"], since=60, catalog=cat)
    assert n == 1 and win == [1, 400] and tot == [2, 700], (n, win, tot)


if __name__ == "__main__":
    test_stock_line()
    test_b_refunds()
    print("ok")


def test_all_line():
    import morning_summary as ms
    j = {"buyers": [{"name": "a", "all": 0.9, "saved": 0.41}, {"name": "choo-chu", "all": 0.86, "saved": 0.4145},
                    {"name": "c", "all": 0.85, "saved": 0.37}, {"name": "x", "all": None, "saved": 0}]}
    t = ms.all_line(j)
    assert "2위/3" in t and "a 0.9 (차이 +0.0400)" in t and "c 0.85 (차이 -0.0100)" in t
    assert "바로 위 없음" in ms.all_line({"buyers": j["buyers"][1:3]})


if __name__ == "__main__":
    test_all_line()
    print("ok all_line")
