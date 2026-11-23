"""depts/cap_brake.measure condition 2 (11/09 ~19:00 person): share > 25% AND unsold cost > 3M, judged only once the
eligible cost (supplied after the raise, 6 h+ old) is at least 5M."""
import json
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts import cap_brake as C  # noqa: E402

R = {"median_h": 3, "window_h": 6, "min_sales": 10, "old_h": 6, "old_cost": 3_000_000, "old_scope": "since",
     "cash_min": 2_000_000, "low": 20_000_000, "since": 0, "old_share": 0.25, "min_old_n": 20,
     "old_unsold_min": 3_000_000, "min_old_cost": 5_000_000}


def setup(units, now):
    """units: [(cost, sold)] all supplied 7 h ago."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".market"))
    json.dump({"balance": 5_000_000, "units": []}, open(os.path.join(d, ".market", "inventory.json"), "w"))
    db = sqlite3.connect(os.path.join(d, "market.db"))
    db.execute("CREATE TABLE my_stock (unit TEXT, cost INT, supplied_at REAL)")
    db.execute("CREATE TABLE settles (unit TEXT, shop TEXT, ts REAL, amount INT)")
    for i, (cost, sold) in enumerate(units):
        db.execute("INSERT INTO my_stock VALUES (?,?,?)", (f"u{i}", cost, now - 7 * 3600))
        if sold:
            db.execute("INSERT INTO settles VALUES (?,?,?,?)", (f"u{i}", C.ME, now - 3600, cost))
    db.commit()
    db.close()
    C.HERE = d


def trips(units):
    now = time.time()
    setup(units, now)
    return [t for t in C.measure(R, now)[1] if "미판매 비율" in t]


def test_condition2():
    # the 18:47 trip: 39.8% of 1.97M -> no longer (under 5M judged, and 0.78M unsold < 3M)
    assert not trips([(39_381, i < 30) for i in range(50)])
    # 6M eligible, 40% unsold = 2.4M: share over but unsold under 3M -> no trip
    assert not trips([(100_000, i >= 24) for i in range(60)])
    # 10M eligible, 35% unsold = 3.5M -> trip
    assert trips([(100_000, i >= 35) for i in range(100)])
    # 4M eligible, 100% unsold (4M > 3M) but under the 5M minimum -> no trip
    assert not trips([(100_000, False) for _ in range(40)])
    # 20M eligible, 20% unsold = 4M: share under 25% -> no trip
    assert not trips([(200_000, i >= 20) for i in range(100)])


if __name__ == "__main__":
    test_condition2()
    print("ok test_condition2")
