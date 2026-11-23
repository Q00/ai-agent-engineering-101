"""Sofa warranty on again 11/13 ~10:20 (person): A / B 72 h (C none), auto-off now when the sofa refunds of the last
48 h exceed the sofa profit of the same 48 h (off_rule share 1.0, was 0.3). Through the seller's path
(strategy.warranty_hours -> depts/sales -> depts/sofa, rules read per call from a temp sofa_rules.json) and the restock
check (alerts.sofa_warranty_check writes the temp file and sends an urgent alert with a fresh key).
    python tests/test_sofa_1113.py"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import alerts  # noqa: E402
import strategy  # noqa: E402
from depts import sofa  # noqa: E402

NOW = 1794532800.0
NEW = {"enabled": True, "warranty": True, "since": 1793982279, "warranty_since": round(NOW), "c_no_warranty": True,
       "off_rule": {"window_h": 48, "share": 1.0}, "warranty_off_at": 1794523802}


def unit(grade):
    return {"sku": "S1", "grade": grade, "cost": 400_000, "kind": "goods"}


def numbers(refund, profit):
    return {"negotiations": 100, "receipts": 10, "rate": 0.1, "sold": 10, "revenue": 4_000_000, "profit": profit,
            "refunds": 3, "refund_amount": refund}


def test_seller_path():
    sofa.RULES = tmp_json(NEW, "sofa_rules")
    with mock.patch.object(sofa, "is_sofa", return_value=True):
        assert strategy.warranty_hours(unit("A"), "normal", {}) == 72
        assert strategy.warranty_hours(unit("B"), "normal", {}) == 72
        assert strategy.warranty_hours(unit("C"), "normal", {}) == 0
        assert strategy.warranty_hours(unit("A"), "endgame", {}) == 0


def test_off_rule():
    with mock.patch.object(alerts, "sofa_numbers", return_value=numbers(9_658_969, 30_555_151)):
        assert alerts.sofa_off_trip(NEW, NOW)[0] is False          # last night's numbers: 32% of profit, stays on
        assert alerts.sofa_off_trip(dict(NEW, off_rule={"window_h": 48, "share": 0.3}), NOW)[0] is True   # old rule
    with mock.patch.object(alerts, "sofa_numbers", return_value=numbers(30_600_000, 30_555_151)):
        assert alerts.sofa_off_trip(NEW, NOW)[0] is True           # refunds > profit
    with mock.patch.object(alerts, "sofa_numbers", return_value=numbers(1, -5)):
        assert alerts.sofa_off_trip(NEW, NOW)[0] is True           # a refund with no profit


def test_check_writes_off():
    sofa.RULES = tmp_json(NEW, "sofa_rules2")
    sent = []
    with mock.patch.object(alerts, "sofa_numbers", return_value=numbers(31_000_000, 30_000_000)), \
            mock.patch.object(alerts, "send", lambda *a, **k: sent.append(a)):
        alerts.sofa_warranty_check({}, log=lambda m: None, now=NOW)
    r = json.load(open(sofa.RULES, encoding="utf-8"))
    assert r["warranty"] is False and "100%" in r["warranty_off_reason"], r
    assert sent and sent[0][0] == "urgent" and sent[0][1] == f"sofa_warranty_off:{round(NOW)}", sent
    with mock.patch.object(sofa, "is_sofa", return_value=True):
        assert strategy.warranty_hours(unit("A"), "normal", {}) == 0  # off -> no warranty on the next negotiation


if __name__ == "__main__":
    test_seller_path()
    test_off_rule()
    test_check_writes_off()
    print("ok")
