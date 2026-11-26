"""Saved selector candidate: normalized ranking and deterministic fair ties."""

from __future__ import annotations

from r3.buyer import choose
from r3.market_files import CatalogItem, Order, ShopEntry, ShopOffer


def shops_and_catalog() -> tuple[tuple[ShopEntry, ...], dict[str, CatalogItem]]:
    """Return a high-price/low-discount and low-price/high-discount pair."""
    shops = tuple(
        ShopEntry(name=n, url=f"https://{n}.test", offers=(ShopOffer(sku=n, qty=3),))
        for n in ("high", "low")
    )
    catalog = {
        n: CatalogItem(sku=n, name=n, list_price=p)
        for n, p in [("high", 1_000_000), ("low", 100_000)]
    }
    return shops, catalog


def test_prefers_normalized_discount_to_currency_amount() -> None:
    shops, catalog = shops_and_catalog()
    order = Order(order="one", acceptable=("high", "low"))
    ratios = {"https://high.test": 0.8, "https://low.test": 0.6}
    old = choose(order, shops, catalog, {}, {}, ratios=ratios, surplus_score=False)
    new = choose(order, shops, catalog, {}, {}, ratios=ratios, surplus_score=True)
    assert old is not None
    assert old.sku == "low"
    assert new is not None
    assert new.sku == "low"


def test_saved_rank_does_not_depend_on_relative_list_scale() -> None:
    shops, catalog = shops_and_catalog()
    order = Order(order="two", acceptable=("high", "low"))
    ratios = {"https://high.test": 0.8, "https://low.test": 0.6}
    catalog["high"] = catalog["high"].model_copy(update={"list_price": 10_000_000})
    new = choose(order, shops, catalog, {}, {}, ratios=ratios, surplus_score=True)
    assert new is not None
    assert new.sku == "low"


def test_ties_ignore_shop_and_offer_order_and_vary_across_orders() -> None:
    offers = tuple(ShopOffer(sku=str(n), qty=1) for n in range(3))
    catalog = {
        str(n): CatalogItem(sku=str(n), list_price=(n + 1) * 10000) for n in range(3)
    }
    shops = tuple(
        ShopEntry(name=n, url=f"https://{n}.test", offers=offers) for n in ("a", "b")
    )
    reversed_shops = tuple(
        s.model_copy(update={"offers": tuple(reversed(s.offers))})
        for s in reversed(shops)
    )
    selected = set()
    for number in range(100):
        order = Order(order=str(number), acceptable=("0", "1", "2"))
        first = choose(order, shops, catalog, {}, {}, surplus_score=True)
        second = choose(order, reversed_shops, catalog, {}, {}, surplus_score=True)
        assert first is not None
        assert second is not None
        assert (first.shop.url, first.sku) == (second.shop.url, second.sku)
        selected.add((first.shop.url, first.sku))
    assert len(selected) == 6


def test_missing_list_is_unknown_and_skipped_in_saved_mode() -> None:
    shops, catalog = shops_and_catalog()
    catalog.pop("low")
    new = choose(Order(acceptable=("low",)), shops, catalog, {}, {}, surplus_score=True)
    assert new is None


def test_blocked_and_skip_offer_filters_remain_effective() -> None:
    shops, catalog = shops_and_catalog()
    order = Order(acceptable=("high", "low"))
    new = choose(
        order, shops, catalog, {}, {}, surplus_score=True, blocked=frozenset({"low"})
    )
    assert new is not None
    assert new.sku == "high"
    new = choose(
        order,
        shops,
        catalog,
        {},
        {},
        surplus_score=True,
        skip_offer=lambda _url, sku: sku == "low",
    )
    assert new is not None
    assert new.sku == "high"


def test_food_need_is_kept_in_request() -> None:
    shops, catalog = shops_and_catalog()
    order = Order(acceptable=("high", "low"), kind="food", use_within_hours=24)
    new = choose(order, shops, catalog, {}, {}, surplus_score=True, need_margin_hours=2)
    assert new is not None
    assert "need 26 h" in new.request


def test_false_mode_uses_normalized_discount_without_list_multiplier() -> None:
    shops, catalog = shops_and_catalog()
    new = choose(
        Order(acceptable=("high", "low")), shops, catalog, {}, {}, surplus_score=False
    )
    assert new is not None
    assert new.sku == "high"


def test_round2_without_acceptable_list_keeps_generic_request() -> None:
    shops, _ = shops_and_catalog()
    new = choose(Order(item="lamp"), shops, {}, {}, {}, surplus_score=True)
    assert new is not None
    assert new.list_price == 0
    assert new.request == "I want to buy lamp"
