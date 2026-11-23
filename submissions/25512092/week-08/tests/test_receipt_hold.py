"""depts/receipt_hold.py on a small in-memory market.db."""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import receipt_hold as H  # noqa: E402


def db():
    d = sqlite3.connect(":memory:")
    d.execute("CREATE TABLE shop_deals (task, ts, unit, price, outcome)")
    d.execute("CREATE TABLE shop_msgs (task, ts, who, text)")
    d.execute("CREATE TABLE settles (ts, unit, amount, buyer, shop)")
    return d


def receipt(d, task, ts, unit, price):
    d.execute("INSERT INTO shop_deals VALUES (?,?,?,?,'receipt')", (task, ts - 30, unit, price))
    d.execute("INSERT INTO shop_msgs VALUES (?,?,'shop',?)", (task, ts, f"accept {price}"))


def settle(d, ts, unit, amount, buyer="b"):
    d.execute("INSERT INTO settles VALUES (?,?,?,?,'choo-chu')", (ts, unit, amount, buyer))


def test_compute_clamps():
    d = db()
    for i in range(100):
        receipt(d, f"t{i}", 1000 + i * 100, f"u{i}", 500)
        settle(d, 1000 + i * 100 + 2, f"u{i}", 500)
    r = H.compute(d, since=0)
    assert r["stats"]["p99"] == 2 and r["hold_s"] == H.MIN_S        # p99 2 s -> 3 min


def test_resale_not_a_late_payment():
    d = db()
    receipt(d, "a", 1000, "u1", 500)                  # unpaid
    receipt(d, "b", 2500, "u1", 500)                  # the unit sold again later
    settle(d, 2503, "u1", 500)
    assert H.latencies(d) == [3]                      # only b's 3 s, not a's 1503 s


def test_conflict():
    d = db()
    receipt(d, "a", 1000, "u1", 500)
    receipt(d, "b", 1300, "u1", 520)                  # after a's 180 s hold
    settle(d, 1400, "u1", 500)                        # a's buyer pays late
    c = H.conflicts(d, 0, 2000, hold=180)
    assert len(c) == 1 and c[0][2] == "a" and c[0][3] == "b"


def test_no_conflict_inside_hold_or_without_resale():
    d = db()
    receipt(d, "a", 1000, "u1", 500)
    settle(d, 1100, "u1", 500)                        # inside the hold
    receipt(d, "c", 5000, "u2", 700)
    settle(d, 5600, "u2", 700)                        # late, but no other receipt
    assert H.conflicts(d, 0, 9000, hold=180) == []


def test_hold_file_and_default():
    old = H.PATH
    try:
        with tempfile.TemporaryDirectory() as t:
            H.PATH = os.path.join(t, "h.json")
            H._cache["t"] = 0
            assert H.hold_s() == H.DEFAULT_S          # no file: 15 min as before
            json.dump({"hold_s": 30}, open(H.PATH, "w"))
            H._cache["t"] = 0
            assert H.hold_s() == H.MIN_S              # clamped to 3 min
            json.dump({"hold_s": 5000}, open(H.PATH, "w"))
            H._cache["t"] = 0
            assert H.hold_s() == H.MAX_S
    finally:
        H.PATH = old
        H._cache["t"] = 0


def test_same_price_resale_is_not_a_conflict():
    d = db()
    receipt(d, "a", 1000, "u1", 500)                  # unpaid
    receipt(d, "b", 1300, "u1", 500)                  # resold at the same price, paid at once
    settle(d, 1303, "u1", 500)
    assert H.conflicts(d, 0, 2000, hold=180) == []
