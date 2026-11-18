"""Sofa warranty by grade and the refund count (11/09 ~11:10): C grade sofas no warranty, A / B 72 h; refunds counted
by count (broken.refunded holds the amount); buy exclusion list.
    python tests/test_sofa_warranty.py"""
import os
import sqlite3
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alerts  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from depts import purchasing, sales, sofa  # noqa: E402
from _isolate import policy, tmp_json  # noqa: E402

policy()                                          # a temp policy.json (code defaults)
purchasing.BUY_EXCLUDE = tmp_json({"skus": {}}, "buy_exclude")

ON = {"enabled": True, "warranty": True, "since": 1, "warranty_since": 100}


def unit(grade, sku="S1", cost=200_000):
    return {"sku": sku, "grade": grade, "cost": cost, "kind": "goods"}


def test_grade():
    with mock.patch.object(sofa, "rules", return_value=ON), mock.patch.object(sofa, "is_sofa", return_value=True):
        assert sofa.warranty_for(unit("A")) == sofa.WARRANTY_H == 72
        assert sofa.warranty_for(unit("B")) == 72
        assert sofa.warranty_for(unit("C")) == 0
        assert sales.warranty_hours(unit("C"), "normal", {}) == 0
        assert sales.warranty_hours(unit("B"), "normal", {}) == 72
    with mock.patch.object(sofa, "rules", return_value=dict(ON, warranty=False)):
        assert sofa.warranty_for(unit("A")) == 0
    with mock.patch.object(sofa, "rules", return_value=dict(ON, c_no_warranty=False)):
        assert sofa.warranty_for(unit("C")) == 72


def db():
    d = sqlite3.connect(":memory:")
    d.execute("CREATE TABLE shop_deals (sku TEXT, outcome TEXT, ts REAL)")
    d.execute("CREATE TABLE settles (sku TEXT, amount INTEGER, unit TEXT, shop TEXT, buyer TEXT, ts REAL)")
    d.execute("CREATE TABLE my_stock (unit TEXT, cost INTEGER)")
    d.execute("CREATE TABLE broken (sku TEXT, buyer TEXT, shop TEXT, refunded INTEGER, ts REAL)")
    for i, (amt, ts) in enumerate([(384_483, 150), (288_829, 160), (252_251, 170), (300_000, 50), (310_000, 105)]):
        d.execute("INSERT INTO settles VALUES ('S1', ?, ?, 'choo-chu', ?, ?)", (amt, f"u{i}", f"b{i}", ts - 10))
        d.execute("INSERT INTO my_stock VALUES (?, 100000)", (f"u{i}",))
        d.execute("INSERT INTO broken VALUES ('S1', ?, 'choo-chu', ?, ?)", (f"b{i}", amt, ts))
    d.execute("INSERT INTO broken VALUES ('S1', 'bx', 'choo-chu', 0, 155)")      # broke, no refund
    return Conn(d)


class Conn:
    """The in-memory db, with close() a no-op (sofa_numbers closes its connection)."""
    def __init__(self, d):
        self.d = d

    def execute(self, *a):
        return self.d.execute(*a)

    def close(self):
        pass


def test_refund_count():
    d = db()
    with mock.patch("market_db.connect", return_value=d), mock.patch.object(sofa, "is_sofa", return_value=True), \
            mock.patch.object(d, "close"):
        o = alerts.sofa_numbers(100, now=1000)
    assert o["refunds"] == 3 and o["refund_amount"] == 384_483 + 288_829 + 252_251, o   # amount column > 0 counts


def test_check_from_warranty_since():
    d = db()
    calls = []
    with mock.patch("market_db.connect", return_value=d), mock.patch.object(sofa, "is_sofa", return_value=True), \
            mock.patch.object(d, "close"), mock.patch.object(sofa, "rules", return_value=dict(ON, warranty_since=165)), \
            mock.patch.object(alerts, "send", lambda *a, **k: calls.append(a)), mock.patch("builtins.open") as op:
        alerts.sofa_warranty_check({}, log=lambda m: None, now=1000)
    assert not calls and not op.called  # since 165: the one refund there is of a sale at 160, before: stays on
    d = db()
    with mock.patch("market_db.connect", return_value=d), mock.patch.object(sofa, "is_sofa", return_value=True), \
            mock.patch.object(d, "close"), mock.patch.object(sofa, "rules", return_value=dict(ON)), \
            mock.patch.object(alerts, "send", lambda *a, **k: calls.append(a)), mock.patch("builtins.open", mock.mock_open()):
        alerts.sofa_warranty_check({}, log=lambda m: None, now=1000)
    assert calls and "sofa_warranty_off" in calls[0][1]   # 3 refunds since warranty_since 100: off


def test_excluded():
    with mock.patch("builtins.open", mock.mock_open(read_data='{"skus": {"G5e3784e6": "x"}}')):
        assert "G5e3784e6" in purchasing.excluded()
    with mock.patch.object(purchasing, "BUY_EXCLUDE", tmp_json({"skus": {"G5e3784e6": "x", "G1": "y"}}, "buy_exclude")):
        assert set(purchasing.excluded()) == {"G5e3784e6", "G1"}
    assert not purchasing.excluded()                  # the module's temp file: empty


if __name__ == "__main__":
    test_grade()
    test_refund_count()
    test_check_from_warranty_since()
    test_excluded()
    print("ok")
