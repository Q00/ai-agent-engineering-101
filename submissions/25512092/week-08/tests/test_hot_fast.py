"""my_share 0.8 for hot SKUs with a 24 h supply -> sale median <= 1 h (11/08 ~20:35), base share for the rest;
request-based target 8 / 2 and 4 lines a run.
    python tests/test_hot_fast.py"""
import os
import sqlite3
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from depts import hot_fast as hf, request_buy as rb  # noqa: E402

NOW = 1_800_000_000


def db():
    d = sqlite3.connect(":memory:")
    d.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, cost INTEGER, supplied_at REAL)")
    d.execute("CREATE TABLE settles (unit TEXT, shop TEXT, ts REAL)")
    return d


def add(d, unit, sku, sup_ago_h, sold_after_h=None, shop=hf.ME):
    d.execute("INSERT INTO my_stock VALUES (?,?,?,?)", (unit, sku, 1000, NOW - sup_ago_h * 3600))
    if sold_after_h is not None:
        d.execute("INSERT INTO settles VALUES (?,?,?)", (unit, shop, NOW - sup_ago_h * 3600 + sold_after_h * 3600))


def test_shares():
    d = db()
    add(d, "f1", "F", 5, 0.5); add(d, "f2", "F", 4, 0.8); add(d, "f3", "F", 3, 2)      # median 0.8 h -> fast
    add(d, "s1", "S", 5, 2); add(d, "s2", "S", 4, 3)                                     # 2.5 h -> slow
    add(d, "u1", "U", 3); add(d, "u2", "U", 2, 0.2)                                      # unsold 3 h + 0.2 -> 1.6 h
    add(d, "o1", "O", 30, 0.1)                                                           # older than 24 h -> none
    add(d, "x1", "X", 5, 0.1, shop="other")                                             # sold by another shop: unsold 5 h
    add(d, "n1", "N", 2, 0.3)                                                            # fast but not hot
    with mock.patch.object(hf, "rules", return_value={"enabled": True, "share": 0.8, "fast_h": 1.0}):
        out = hf.shares(["F", "S", "U", "O", "X"], 0.5, log=lambda m: None, now=NOW, db=d)
    assert out == {"F": 0.8}, out
    with mock.patch.object(hf, "rules", return_value={"enabled": False}):
        assert hf.shares(["F"], 0.5, log=lambda m: None, now=NOW, db=d) == {}


def test_request_numbers():
    assert (rb.FAST_TARGET, rb.TARGET, rb.MAX_LINES, rb.FAST_ONLY) == (8, 2, 4, True)   # 11/10 ~14:15: 2 -> 4 (3 + 1 trial), fast only
    assert rb.target("A", {"A": 0.9}) == 8 and rb.target("B", {"B": 1.5}) == 2 and rb.target("C", {}) == 2


if __name__ == "__main__":
    test_shares()
    test_request_numbers()
    print("ok")
