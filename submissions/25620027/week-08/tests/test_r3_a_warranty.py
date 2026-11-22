"""11/16 10:2x: grade A goods carry the policy's 1 h warranty unless asked for more."""

from __future__ import annotations

import time

from r3.market_files import Unit
from r3.policy import SellerPolicy
from r3.seller import Deal, Quote, asked_warranty, deal_warranty

POLICY = SellerPolicy(warranty_a=1, warranty_b=1, warranty_c=1)


def deal(grade: str, kind: str = "goods", ask: int | None = None) -> Deal:
    unit = Unit(
        unit="u",
        sku="Gabcdef01",
        category="가구",
        kind=kind,
        list_price=100_000,
        cost=50_000,
        grade=grade,
        supplied_at=time.time(),
    )
    quote = Quote(60_000, 55_000, 55_000, grade, clearance=False, stale=False)
    return Deal(unit, None, quote, 60_000, warranty_ask=ask)


def test_openings_that_name_a_warranty_are_read() -> None:
    assert (
        asked_warranty("I want to buy X (G1). Please include a 6-hour warranty.") == 6
    )
    assert (
        asked_warranty("Quote price, grade and warranty. Require 6-hour warranty.") == 6
    )
    assert asked_warranty("with a warranty of at least 24 hours") == 24
    assert asked_warranty("need a 200-hour warranty") == 72
    assert asked_warranty("Quote price, grade and warranty.") is None


def test_grade_a_goods_get_one_hour_unless_asked() -> None:
    assert deal_warranty(deal("A"), POLICY) == 1
    assert deal_warranty(deal("A", ask=6), POLICY) == 6
    # B, C and food are untouched by the ask.
    assert deal_warranty(deal("B", ask=6), POLICY) == 1
    assert deal_warranty(deal("A", kind="food", ask=6), POLICY) == 0
