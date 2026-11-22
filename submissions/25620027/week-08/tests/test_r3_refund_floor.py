"""Grade A goods are not sold under cost x A_REFUND_FLOOR (expected refunds)."""

from __future__ import annotations

from r3.market_files import Unit
from r3.pricing import Quote
from r3.seller import a_floor_factor, refund_floor


def unit(kind: str = "goods", cost: int = 100_000) -> Unit:
    return Unit(
        unit="u",
        sku="G00000001",
        category="가구/침구>가구>소파",
        kind=kind,
        list_price=200_000,
        cost=cost,
        grade="A",
        supplied_at=0.0,
    )


def quote(accept_at: int, grade: str = "A") -> Quote:
    return Quote(
        ask=accept_at + 10_000,
        accept_at=accept_at,
        floor=accept_at,
        grade_now=grade,
        clearance=False,
        stale=False,
    )


def test_cost_floor_a_goods_quote_rises_to_the_refund_line() -> None:
    # Given: an A sofa priced down to cost (a cost-floor buyer) at 100,000.
    raised = refund_floor(unit(), quote(100_000))
    # Then: accepted no lower than cost x a_floor_factor (1.10 under 500k list).
    assert raised.accept_at == round(100_000 * a_floor_factor(unit()))
    assert raised.floor >= raised.accept_at
    assert raised.ask >= raised.accept_at


def test_b_c_food_and_already_higher_quotes_are_untouched() -> None:
    assert refund_floor(unit(), quote(100_000, "B")).accept_at == 100_000
    assert refund_floor(unit(), quote(100_000, "C")).accept_at == 100_000
    assert refund_floor(unit("food"), quote(100_000)).accept_at == 100_000
    assert refund_floor(unit(), quote(120_000)).accept_at == 120_000
