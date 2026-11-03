"""Selection learns from completed payments and explores safe alternatives."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from r3 import buyer
from r3.events import EventLedger
from r3.market_files import Order, PaymentLine, ShopEntry, ShopOffer
from r3.picture import Picture, ShopTrust
from r3.policy import BuyerPolicy

if TYPE_CHECKING:
    from pathlib import Path


SHOP = "http://shop.test"


def attempt(events: EventLedger, order: str, *, outcome: str = "agreed") -> None:
    """Record one R3 negotiation without assuming it was paid."""
    events.record_attempt(
        order=order,
        shop=SHOP,
        shop_name="Same display name",
        sku="sku",
        list_price=100,
        price=80 if outcome == "agreed" else None,
        outcome=outcome,  # pyright: ignore[reportArgumentType]
    )


def test_refused_payments_cool_down_even_after_negotiated_agreements(
    tmp_path: Path,
) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    for n in range(3):
        order = f"o{n}"
        attempt(events, order)
        events.sync_payments([PaymentLine(order=order, shop=SHOP, paid=False)])
    assert buyer.cooling_shops(events.failure_streaks(24), BuyerPolicy()) == {SHOP}


def test_pending_r3_agreement_does_not_clear_failures_but_paid_success_does(
    tmp_path: Path,
) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    for n in range(3):
        attempt(events, f"o{n}", outcome="no_deal")
    attempt(events, "pending")
    assert SHOP in buyer.cooling_shops(events.failure_streaks(24), BuyerPolicy())
    events.sync_payments(
        [PaymentLine(order="pending", shop=SHOP, price=80, paid=True, ledger=10)]
    )
    assert events.failure_streaks(24) == {}


def test_suspicious_attempts_are_failures_not_successes(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    for n in range(3):
        attempt(events, f"o{n}", outcome="suspicious")
    assert SHOP in buyer.cooling_shops(events.failure_streaks(24), BuyerPolicy())


def test_price_memory_requires_our_paid_order_and_matching_shop(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    attempt(events, "refused")
    events.sync_payments([PaymentLine(order="refused", shop=SHOP, paid=False)])
    attempt(events, "pending")
    attempt(events, "other-shop")
    events.sync_payments(
        [PaymentLine(order="other-shop", shop="http://other.test", price=1, paid=True)]
    )
    assert events.price_ratios(24) == {}
    attempt(events, "paid")
    events.sync_payments(
        [PaymentLine(order="paid", shop=SHOP + "/", price=75, paid=True, ledger=20)]
    )
    assert events.price_ratios(24) == {SHOP: 0.75}


@pytest.mark.parametrize("stocked", [False, True])
@pytest.mark.parametrize("bad_evidence", ["refused", "no_deal", "suspicious", "trust"])
def test_exploration_excludes_penalized_shops_even_with_many_prior_payments(
    stocked: bool, bad_evidence: str,
) -> None:
    offers = (ShopOffer(sku="sku", qty=1),) if stocked else ()
    shops = tuple(
        ShopEntry(name="Same display name", url=f"http://{name}.test", offers=offers)
        for name in ("a", "b", "bad")
    )
    order = Order(item="lamp", acceptable=("sku",) if stocked else ())
    bad_url = "http://bad.test"
    counts = {"paid": 100}
    picture = Picture()
    if bad_evidence == "trust":
        picture = Picture(shops=(ShopTrust(url=bad_url, trust=0.1),))
    else:
        counts[bad_evidence] = 1
    outcomes = {bad_url: counts}
    rng = random.Random(7)  # noqa: S311 - seeded selection, not cryptography
    picks = [
        buyer.choose(order, shops, {}, outcomes, {}, explore=1, rng=rng, picture=picture)
        for _ in range(50)
    ]
    assert {p.shop.url for p in picks if p is not None} == {
        "http://a.test", "http://b.test",
    }
    assert all(p is not None and p.explored for p in picks)


def test_round2_stock_advertisement_does_not_hide_shops_without_offers() -> None:
    shops = (
        ShopEntry(name="willow", url="http://willow.test", offers=(ShopOffer(sku="lamp", qty=1),)),
        ShopEntry(name="good", url="http://good.test"),
    )
    rng = random.Random(7)  # noqa: S311
    picks = [
        buyer.choose(Order(item="lamp"), shops, {}, {}, {}, explore=1, rng=rng)
        for _ in range(50)
    ]
    assert {p.shop.url for p in picks if p is not None} == {
        "http://willow.test", "http://good.test",
    }
