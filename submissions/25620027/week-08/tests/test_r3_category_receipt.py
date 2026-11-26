"""A buyer who ordered by category gets that category back on the receipt."""

from __future__ import annotations

from r3.market_files import Unit
from r3.seller import Request, asked_category

SOFA = Unit(
    unit="u-1",
    sku="G8ec51935",
    name="메이트 멀티 4인 소파",
    category="가구/침구>가구>소파/의자/스툴",
    list_price=1_000_000,
    cost=650_000,
    grade="A",
)


def test_a_category_order_is_echoed_and_a_sku_order_is_not() -> None:
    by_category = Request((), None, "I want to buy 소파/의자/스툴, grade A or B")
    assert asked_category(by_category, SOFA) == "소파/의자/스툴"
    by_sku = Request(("G8ec51935",), None, "I want to buy 소파/의자/스툴 (G8ec51935)")
    assert asked_category(by_sku, SOFA) is None
    other = Request((), None, "I want to buy 김치")
    assert asked_category(other, SOFA) is None
