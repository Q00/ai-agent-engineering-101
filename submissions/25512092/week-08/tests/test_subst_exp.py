"""Substitute option A (depts/subst_exp.py) and the receipts it makes (seller_r3.build_receipt): the order's category
name as the item, with the receipt check (depts/receipt_check.py) and the grade claim experiment."""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts import receipt_check, subst_exp  # noqa: E402

os.environ.setdefault("HARNESS_CMD", "false")     # no LLM, and harness.py skips the live strict_mcp.json
_spec = importlib.util.spec_from_file_location("seller_r3", os.path.join(HERE, "seller_r3.py"))
seller = importlib.util.module_from_spec(_spec)
_argv, sys.argv = sys.argv, ["seller_r3.py"]
_spec.loader.exec_module(seller)
sys.argv = _argv

UNIT = {"unit": "u-1", "sku": "G0d2e767d", "category": "식품>김치>김치", "grade": "B", "kind": "goods", "list_price": 30000}


def with_rules(r):
    old = subst_exp.RULES
    t = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(r, t)
    t.close()
    subst_exp.RULES = t.name
    return old


def test_arm_window_and_modes():
    old = with_rules({"mode": "A_exp", "share": 1.0, "since": 100, "until": 200})
    try:
        assert subst_exp.arm(150) == "A" and subst_exp.arm(250) is None and subst_exp.arm(50) is None
        with_rules({"mode": "B"})
        assert subst_exp.arm(150) == "B"
        with_rules({"mode": "off"})
        assert subst_exp.arm(150) is None
    finally:
        subst_exp.RULES = old


def test_near_keeps_tiers_1_2():
    assert [t for t, _ in subst_exp.near([(1, {}), (2, {}), (3, {})])] == [1, 2]


def test_number_offer():
    assert subst_exp.number_offer("4000") == 4000
    assert subst_exp.number_offer("I can do 10,400.") == 10400
    assert subst_exp.number_offer("G0d2e767d please") is None          # named a SKU: the normal choice path
    assert subst_exp.number_offer("need 360 hours of shelf life") is None


def test_category_receipt_with_claim():
    deal = {"receipt_item": subst_exp.category_name(UNIT), "claim_arm": "claim", "warranty": 0}
    r = seller.build_receipt(deal, UNIT, 27000, "t1")
    assert r["item"] == "김치" and r["unit"] == "u-1" and r["claim"] == "B" and r["price"] == 27000
    assert "warranty_hours" not in r
    s = seller.sign(dict(r))
    assert s["item"] == "김치" and s["sig_seller"]


def test_category_receipt_without_claim_arm():
    deal = {"receipt_item": "김치", "claim_arm": "none", "warranty": 48}
    r = seller.build_receipt(deal, UNIT, 27000, "t2")
    assert "claim" not in r and r["warranty_hours"] == 48 and r["item"] == "김치"


def test_receipt_check_on_substitute_number_reply():
    # A: the reply "4000" is the offer for the first candidate; the receipt amount is checked the same way
    assert receipt_check.agreed("4000", 4000, 11546) == (4000, None)
    assert receipt_check.agreed("That is more than I want. x quoted me 9313. I can do 8257.", 9313, 11546)[0] == 8257
    assert receipt_check.agreed("40000", 40000, 11546)[0] == 11546   # over 2x the ask: a typo -> my ask
