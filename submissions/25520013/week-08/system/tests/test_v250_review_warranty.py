"""v2.5: a goods item that broke on us before, sold again with no warranty, gets one more star off (5 - 2 - 1 = 2)."""
import time

import policy as P


def deal(warranty=None, grade="A", claim_honest=True, name="이불"):
    s = {"name": name, "sku": "G1", "grade": grade, "claim": grade, "claim_honest": claim_honest, "expires_at": None}
    if warranty is not None:
        s["warranty_hours"] = warranty
    return {"settled": s, "order": {"order": "o-1", "kind": "goods"}}


def test_broke_before_and_no_warranty_is_two_stars():
    stars, text = P.review_for(deal(warranty=0), {"이불"})
    assert stars == 2 and "broke" in text and "no warranty" in text


def test_broke_before_with_warranty_stays_three():
    stars, text = P.review_for(deal(warranty=72.0), {"이불"})
    assert stars == 3 and "no warranty" not in text


def test_no_warranty_alone_is_not_a_reason():
    assert P.review_for(deal(warranty=0), set())[0] == 5
    assert P.review_for(deal(), set())[0] == 5


def test_floor_is_one_star():
    assert P.review_for(deal(warranty=0, grade="C", claim_honest=False), {"이불"})[0] == 1


def test_food_unchanged():
    d = {"settled": {"name": "김치", "sku": "G2", "grade": "A", "claim_honest": True,
                     "expires_at": time.time() + 100 * 3600}, "order": {"use_within_hours": 72}}
    assert P.review_for(d, set())[0] == 5
