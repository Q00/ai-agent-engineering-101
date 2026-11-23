"""Request-based hold target (11/08 ~16:50): 5 for a SKU whose request-based units of the last 24 h went supply -> sale
in 1 h or less at the median (unsold units count with their age), else 2.
    python tests/test_request_target.py"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import request_buy as rb  # noqa: E402

NOW = 1_800_000_000


def db():
    d = sqlite3.connect(":memory:")
    d.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, cost INTEGER, supplied_at REAL)")
    d.execute("CREATE TABLE settles (unit TEXT, shop TEXT, ts REAL)")
    return d


def add(d, unit, sku, sup_ago_h, sold_after_h=None, shop=rb.ME):
    d.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (unit, sku, 1000, NOW - sup_ago_h * 3600))
    if sold_after_h is not None:
        d.execute("INSERT INTO settles VALUES (?,?,?)", (unit, shop, NOW - sup_ago_h * 3600 + sold_after_h * 3600))


def test_fast_and_slow():
    d = db()
    st = {"F": {"first_buy": NOW - 20 * 3600}, "S": {"first_buy": NOW - 20 * 3600}, "U": {"first_buy": NOW - 20 * 3600},
          "O": {"first_buy": NOW - 20 * 3600}, "N": {}}
    add(d, "f1", "F", 10, 0.5); add(d, "f2", "F", 8, 0.8); add(d, "f3", "F", 5, 3)
    add(d, "s1", "S", 10, 2); add(d, "s2", "S", 9, 1.5)                                # 1.75 h -> 2
    add(d, "u1", "U", 6, 0.2); add(d, "u2", "U", 5); add(d, "u3", "U", 4)              # unsold ages 5 h, 4 h -> 2
    add(d, "o1", "O", 30, 0.1)                                                         # older than 24 h: no data -> 2
    add(d, "x1", "F", 3, 0.1, shop="other")                                            # someone else's sale: unsold
    t = rb.turnover(d, st, NOW)
    # F: 0.5, 0.8, 3 and x1 (sold by another shop = unsold for me, age 3 h) -> median 1.9 h -> 2
    assert abs(t["F"] - 1.9) < 1e-6 and rb.target("F", t) == 2, t
    assert abs(t["U"] - 4) < 1e-6, t
    assert rb.target("S", t) == 2 and rb.target("U", t) == 2 and rb.target("O", t) == 2 and rb.target("N", t) == 2
    assert "O" not in t and "N" not in t


def test_fast():
    d = db()
    st = {"F": {"first_buy": NOW - 20 * 3600}}
    add(d, "f1", "F", 10, 0.5); add(d, "f2", "F", 8, 0.8); add(d, "f3", "F", 5, 3)     # median 0.8 h -> 8 (5 until 11/08 ~20:35)
    t = rb.turnover(d, st, NOW)
    assert abs(t["F"] - 0.8) < 1e-6 and rb.target("F", t) == rb.FAST_TARGET == 8, t


def test_fast_exactly():
    d = db()
    st = {"F": {"first_buy": NOW - 20 * 3600}}
    add(d, "f1", "F", 10, 0.5); add(d, "f2", "F", 8, 1.0); add(d, "f3", "F", 5, 0.9)
    t = rb.turnover(d, st, NOW)
    assert abs(t["F"] - 0.9) < 1e-6 and rb.target("F", t) == rb.FAST_TARGET == 8, t


if __name__ == "__main__":
    test_fast_and_slow()
    test_fast()
    test_fast_exactly()
    print("ok")
