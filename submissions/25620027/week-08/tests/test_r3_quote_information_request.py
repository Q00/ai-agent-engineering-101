"""Request missing quote facts without changing price or acceptance criteria."""

from __future__ import annotations

from r3.buyer import Choice, choose
from r3.market_files import CatalogItem, Order, ShopEntry, ShopOffer


def select(order: Order) -> Choice | None:
    """Use a stocked eligible unit and the actual owner discount mode."""
    return choose(
        order,
        (
            ShopEntry(
                name="peer",
                url="http://peer.test",
                offers=(ShopOffer(sku="sku", qty=1),),
            ),
        ),
        {"sku": CatalogItem(sku="sku", name="item", list_price=10000)},
        {},
        {},
        surplus_score=False,
        need_margin_hours=4,
    )


def test_goods_inquiry_does_not_name_a_numeric_price() -> None:
    choice = select(Order(order="g", acceptable=("sku",), kind="goods"))
    assert choice is not None
    assert not any(c.isdigit() for c in choice.request)


def test_food_still_requests_the_original_use_within_plus_four_hours() -> None:
    choice = select(
        Order(order="f", acceptable=("sku",), kind="food", use_within_hours=24)
    )
    assert choice is not None
    assert choice.request.startswith("I want to buy item (sku), need 28 h")
    assert "cheapest unit that still lasts" in choice.request
    assert "warranty" not in choice.request


def test_round_two_generic_request_is_unchanged() -> None:
    choice = select(Order(order="r2", item="lamp"))
    assert choice is not None
    assert choice.request == "I want to buy lamp"
