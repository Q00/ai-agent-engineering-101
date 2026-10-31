"""Comparison evidence changes only feasible, fresh basic opening ceilings."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import anyio
import pytest
from a2a.server.tasks import TaskUpdater
from openai import AsyncOpenAI

from market_a2a.catalog import Catalog
from market_a2a.comparison import (
    ComparedShop,
    ComparisonSnapshot,
    ObservedQuote,
    compare_price,
)
from market_a2a.harness import Harness
from market_a2a.models import Product
from market_a2a.seller_policy import Decision, Turn
from market_a2a.seller_turn import Negotiator
from tests.test_opening_ask import EventCollector


def snapshot_for(price: int, observed: dt.datetime) -> ComparisonSnapshot:
    """Build one dated opening ask, separate from local product reserves."""
    return ComparisonSnapshot(
        observed_at=observed,
        expires_at=observed + dt.timedelta(minutes=15),
        shops=(
            ComparedShop(
                rank=1,
                name="Peer",
                url="https://peer.trycloudflare.com",
                deals=100,
                quotes=(ObservedQuote(item="lamp", ask=price, observed_at=observed),),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("peer", "ceiling", "relation"),
    [
        (108, 107, "lower"),
        (100, 100, "equal"),
        (95, 100, "higher"),
        (200, 110, "lower"),
    ],
)
def test_opening_when_observed_price_changes_keeps_reserve_and_baseline(
    peer: int, ceiling: int, relation: str
) -> None:
    # Given: a basic lamp and a specific fresh peer opening ask.
    now = dt.datetime(2026, 10, 21, tzinfo=dt.UTC)
    product = Product(sku="lamp", name="lamp", kind="lamp", description="", reserve=100)
    # When: the code computes the new opening ceiling.
    result = compare_price(product, snapshot_for(peer, now), now)
    # Then: a one-unit undercut is bounded by reserve and the original 110 ceiling.
    assert result.ceiling == ceiling
    assert result.relation == relation
    assert result.observed_shop_count == 1


@pytest.mark.parametrize("age", [-1, 900, 1000])
def test_opening_when_snapshot_is_future_or_expired_uses_baseline(age: int) -> None:
    # Given: cheap evidence is outside the supported age window.
    now = dt.datetime(2026, 10, 21, tzinfo=dt.UTC)
    snapshot = snapshot_for(100, now - dt.timedelta(seconds=age))
    product = Product(sku="lamp", name="lamp", kind="lamp", description="", reserve=100)
    # When: an opening ceiling is requested.
    result = compare_price(product, snapshot, now)
    # Then: neither undercutting nor a lower-price claim survives stale evidence.
    assert result.ceiling == 110
    assert result.observed_shop_count == 0
    assert result.relation == "unavailable"


def test_opening_when_item_is_variant_does_not_use_basic_item_comparison() -> None:
    # Given: an aluminum variant differs from the queried basic lamp.
    now = dt.datetime(2026, 10, 21, tzinfo=dt.UTC)
    product = Product(
        sku="lamp-metal", name="lamp", kind="lamp", description="metal", reserve=150
    )
    # When: basic lamp prices are available.
    result = compare_price(product, snapshot_for(100, now), now)
    # Then: the variant retains its own baseline and no comparable-price claim.
    assert result.ceiling == 165
    assert result.observed_shop_count == 0


def test_opening_when_quotes_duplicate_a_shop_counts_it_once() -> None:
    # Given: a cached source repeats a shop with a duplicate item quote.
    now = dt.datetime(2026, 10, 21, tzinfo=dt.UTC)
    original = snapshot_for(108, now)
    peer = original.shops[0].model_copy(update={"quotes": original.shops[0].quotes * 2})
    duplicate = original.model_copy(update={"shops": (peer, peer)})
    product = Product(sku="lamp", name="lamp", kind="lamp", description="", reserve=100)
    # When: comparison counts independent shop observations.
    result = compare_price(product, duplicate, now)
    # Then: repeated records cannot inflate market coverage.
    assert result.observed_shop_count == 1
    assert result.ceiling == 107


def test_quote_when_model_exceeds_comparison_ceiling_has_no_wire_side_effect() -> None:
    # Given: a fresh peer ask of 103 makes our feasible opening cap 102.
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    snapshot = snapshot_for(103, dt.datetime.now(dt.UTC))
    events = EventCollector()

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            seller = Negotiator(Harness(model, print), catalog, "Test")
            turn = Turn(
                "task",
                TaskUpdater(events, "task", "context"),
                None,
                {"lamp": catalog.get("lamp")},
                None,
                snapshot,
            )
            # When: the model tries the otherwise valid baseline quote of 110.
            result = await seller.commit(
                turn, Decision(action="quote", sku="lamp", price=110, reason="Fixture")
            )
            # Then: the action guard rejects it before publishing or selecting a SKU.
            assert result.terminal is False
            assert events.events == []
            assert seller.negotiations == {}
            assert isinstance(result.data, dict)
            assert result.data["maximum"] == 102

    anyio.run(scenario)
