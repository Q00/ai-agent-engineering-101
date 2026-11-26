"""2026-11-09: board saved 0.288 = discount 0.330 minus 0.042 for goods that broke. Grade C goods broke 35% (market
33.5%); without a warranty a broken deal counts -1, so our 683 C deals without one averaged -0.104 saved per deal
while every other grade/warranty mix made +0.21..+0.30. Breaks come within 6 h of the sale (/rules)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_r3

ORDER = {"kind": "goods", "budget": 500_000, "acceptable": ["G1"]}
C = {"max_pay": 400_000}


def rec(**kw):
    return {"item": "G1", "price": 300_000, "sig_seller": "x", **kw}


def test_grade_c_without_a_six_hour_warranty_is_refused_so_the_next_shop_is_tried():
    assert "grade C" in buyer_r3.valid(rec(claim="C"), C, ORDER)
    assert "grade C" in buyer_r3.valid(rec(claim="C", warranty_hours=3), C, ORDER)
    assert buyer_r3.valid(rec(claim="C", warranty_hours=6), C, ORDER) is None
    assert buyer_r3.valid(rec(claim="B"), C, ORDER) is None
    assert buyer_r3.valid(rec(claim="C"), C, {**ORDER, "kind": "food"}) is None    # food is graded by shelf life


def test_accept_lets_the_kit_pay_for_c_only_under_warranty():
    assert buyer_r3.accept_policy(ORDER, rec(claim="A")) == {"cert": True, "grades": ["A", "B"]}
    assert buyer_r3.accept_policy(ORDER, rec(claim="C", warranty_hours=72)) == \
        {"cert": True, "grades": ["A", "B", "C"], "warranty_hours": 6}
    food = {"kind": "food", "use_within_hours": 12}
    assert "grades" not in buyer_r3.accept_policy(food, rec())
