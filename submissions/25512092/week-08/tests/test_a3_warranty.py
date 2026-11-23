"""High-price A-grade warranty on low-break SKUs (person 11/13 ~10:40): the list from a temp db (depts/a3_warranty.
compute / refresh, the hourly watcher's step), then the seller's path strategy.warranty_hours with a temp
a3_warranty.json: 48 h only for listed A-grade high-price non-food units in the normal case, on top of the other rules.
    python tests/test_a3_warranty.py"""
import json
import os
import sqlite3
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

policy()
import strategy  # noqa: E402
from depts import a3_warranty as A, top_high  # noqa: E402

NOW = 1794540000.0
KINDS = {"OK": "goods", "HI5": "goods", "FEW": "goods", "FOOD": "food", "SOFA": "goods", "LOW": "goods"}


def make_db():
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE settles (ts REAL, buyer TEXT, shop TEXT, sku TEXT, grade TEXT, unit TEXT)")
    d.execute("CREATE TABLE my_stock (unit TEXT, sku TEXT, grade TEXT, cost INTEGER)")
    d.execute("CREATE TABLE broken (ts REAL, buyer TEXT, shop TEXT, sku TEXT)")
    rows, stock, broken = [], [], []
    for sku, n, nb, cost in (("OK", 40, 1, 300_000), ("HI5", 40, 2, 300_000), ("FEW", 19, 0, 300_000),
                             ("FOOD", 40, 0, 300_000), ("SOFA", 40, 0, 300_000), ("LOW", 40, 0, 50_000)):
        rows.append((NOW - 3600, "me-buyer", "choo-chu", sku, "A", f"u-{sku}"))   # my A sale (counted in n too)
        stock.append((f"u-{sku}", sku, "A", cost))
        for i in range(n - 1):
            rows.append((NOW - 7200 - i, f"b{i}", "other", sku, "A", None))
        for i in range(nb):
            broken.append((NOW - 600, f"b{i}", "other", sku))                   # A-grade breaks
        rows.append((NOW - 7200, "bx", "other", sku, "C", None))                 # a C sale ...
        broken.append((NOW - 600, "bx", "other", sku))                          # ... and its break: not A
    rows.append((NOW - 80 * 3600, "old", "choo-chu", "OLD", "A", "u-OLD"))      # outside 72 h
    stock.append(("u-OLD", "OLD", "A", 300_000))
    d.executemany("INSERT INTO settles VALUES (?,?,?,?,?,?)", rows)
    d.executemany("INSERT INTO my_stock VALUES (?,?,?,?)", stock)
    d.executemany("INSERT INTO broken VALUES (?,?,?,?)", broken)
    d.commit()
    d.close()
    return p


def test_compute():
    out = A.compute(NOW, db_path=make_db(), r=dict(A.DEFAULT), is_sofa=lambda s: s == "SOFA", kinds=KINDS)
    assert set(out) == {"OK"}, out                     # HI5 5% > 3%, FEW 19 < 20, FOOD / SOFA out, LOW not high
    assert out["OK"] == {"n": 40, "break": 0.025, "mine": 1}, out


def test_seller_path():
    path = tmp_json(dict(A.DEFAULT, enabled=True), "a3")
    with mock.patch.object(A, "RULES", path):
        import a3_warranty_watch as W
        W.LOG = tmp_path("a3.log")
        assert set(W.step(path=path, now=NOW, db_path=make_db(), is_sofa=lambda s: s == "SOFA", kinds=KINDS)) == {"OK"}
        assert json.load(open(path, encoding="utf-8"))["computed_ts"] == round(NOW)
        u = lambda **k: dict({"sku": "OK", "grade": "A", "cost": 300_000, "kind": "goods"}, **k)
        is_sofa = mock.patch("depts.sofa.is_sofa", side_effect=lambda s: s == "SOFA")
        with is_sofa:
            assert strategy.warranty_hours(u(), "normal", {}) == 48
            assert strategy.warranty_hours(u(grade="B"), "normal", {}) == 0
            assert strategy.warranty_hours(u(sku="HI5"), "normal", {}) == 0          # not on the list
            assert strategy.warranty_hours(u(), "endgame", {}) == 0
            assert strategy.warranty_hours(u(kind="food"), "normal", {}) == 0
            assert strategy.warranty_hours(u(cost=90_000), "normal", {}) == 0
            # the other rules first: the 5% rule gives HI5 its 48 h as before
            assert strategy.warranty_hours(u(sku="HI5"), "normal", {"sku_n": {"HI5": 40}, "sku_break": {"HI5": 0.05}}) == 48
            with mock.patch.object(top_high, "warranty", return_value=0):            # over 1.5M, category too high
                assert strategy.warranty_hours(u(cost=2_000_000), "normal", {}) == 48
        with mock.patch.object(A, "compute", side_effect=OSError("db")):            # a failed hour keeps the list
            assert W.step(path=path, now=NOW) is None
        assert set(A.rules(path)["skus"]) == {"OK"}
    off = tmp_json(dict(A.DEFAULT, enabled=False, skus={"OK": {}}), "a3off")
    with mock.patch.object(A, "RULES", off), mock.patch("depts.sofa.is_sofa", return_value=False):
        assert strategy.warranty_hours({"sku": "OK", "grade": "A", "cost": 300_000, "kind": "goods"}, "normal", {}) == 0


if __name__ == "__main__":
    test_compute()
    test_seller_path()
    print("ok")
