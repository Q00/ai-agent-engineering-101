"""A receipt whose certificate is a bare flag is judged by its claim."""

from __future__ import annotations

from r3.emergency_buyer import PurchaseValue, receipt_unit, receipt_value

# Production values: refund reliability 1.0, min credit, MARGIN_LINE 0.75.
QUOTE = PurchaseValue("goods", None, 0, 87_900, 57_838, 1.0, 0.34, 0.75)


def test_cert_flag_receipt_reads_the_claimed_grade() -> None:
    # Given: the receipt sergiobuilds and others sign: "cert": true, claim "A".
    receipt = {
        "item": "Ge62449c9",
        "price": 57_838.0,
        "warranty_hours": 0.0,
        "cert": True,
        "unit": "u-c10ff134f906",
        "claim": "A",
    }
    # When: we value it before paying.
    value = receipt_value(receipt, QUOTE)
    # Then: it is an A unit (not "claim None, warranty 0 h") and passes the line.
    assert value.grade == "A"
    assert value.acceptable()
    assert receipt_unit(receipt) == "u-c10ff134f906"


def test_cert_flag_with_c_claim_is_still_refused() -> None:
    receipt = {"price": 57_838.0, "cert": True, "claim": "C", "warranty_hours": 0.0}
    assert not receipt_value(receipt, QUOTE).acceptable()
