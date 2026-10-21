"""Typed observed opening asks and conservative ceilings for new negotiations."""

from __future__ import annotations

import datetime as dt
from typing import Final, Literal

from pydantic import AwareDatetime, Field

from market_a2a.models import FrozenModel, Product

REFRESH_SECONDS: Final = 600
MAX_AGE_SECONDS: Final = 900
BASIC_ITEMS: Final = ("lamp", "chair", "bookshelf")
MIN_NEAR_LOWEST_SHOPS: Final = 3


class ObservedQuote(FrozenModel):
    """A received initial ask, never an agreed price or a reviewer claim."""

    item: Literal["lamp", "chair", "bookshelf"]
    ask: int | None = Field(default=None, strict=True, ge=1)
    observed_at: AwareDatetime
    error: str = ""


class ComparedShop(FrozenModel):
    """One other active shop in the board's published ranking order."""

    rank: int = Field(ge=1)
    name: str
    url: str
    deals: int = Field(ge=0)
    quotes: tuple[ObservedQuote, ...] = Field(max_length=3)


class ComparisonSnapshot(FrozenModel):
    """A bounded dated observation of up to five shops, including failures."""

    observed_at: AwareDatetime
    expires_at: AwareDatetime
    shops: tuple[ComparedShop, ...] = Field(max_length=5)
    ranking_source: str = "https://market.ourolabs.ai/api/board"
    price_kind: Literal["observed_initial_ask"] = "observed_initial_ask"


class ComparisonFeed:
    """Publish complete immutable snapshots; readers never see a partial refresh."""

    def __init__(self) -> None:
        """Begin without external price evidence."""
        self.snapshot: ComparisonSnapshot | None = None


class OwnComparison(FrozenModel):
    """The maximum new opening ask compared with currently usable observations."""

    item: str
    ceiling: int
    baseline_ceiling: int
    lowest_peer_ask: int | None
    observed_shop_count: int
    relation: Literal["lower", "equal", "higher", "unavailable"]
    pricing_policy: Literal["baseline", "undercut_lowest", "near_lowest"] = "baseline"
    second_lowest_peer_ask: int | None = None


class ComparisonDocument(FrozenModel):
    """The public JSON contract shared by the page and protocol checks."""

    enabled: bool
    fresh: bool
    refresh_seconds: int
    max_age_seconds: int
    snapshot: ComparisonSnapshot | None
    own: tuple[OwnComparison, ...]


def opening_max(reserve: int) -> int:
    """Round the existing ten-percent baseline upward without floating point."""
    return (reserve * 110 + 99) // 100


def is_fresh(
    snapshot: ComparisonSnapshot | None, now: dt.datetime | None = None
) -> bool:
    """Stop using evidence at fifteen minutes even if a cache expiry is wrong."""
    if snapshot is None:
        return False
    current = now if now is not None else dt.datetime.now(dt.UTC)
    age = (current - snapshot.observed_at).total_seconds()
    return 0 <= age < MAX_AGE_SECONDS and current < snapshot.expires_at


def compare_price(
    product: Product,
    snapshot: ComparisonSnapshot | None,
    now: dt.datetime | None = None,
) -> OwnComparison:
    """Bound near-lowest openings by fresh quotes, reserve and the baseline cap."""
    baseline = opening_max(product.reserve)
    current = now if now is not None else dt.datetime.now(dt.UTC)
    observations: list[int] = []
    seen: set[str] = set()
    if (
        product.sku in BASIC_ITEMS
        and snapshot is not None
        and is_fresh(snapshot, current)
    ):
        for shop in snapshot.shops:
            if shop.name in seen:
                continue
            seen.add(shop.name)
            quote = next(
                (
                    quote
                    for quote in shop.quotes
                    if quote.item == product.sku
                    and quote.ask is not None
                    and not quote.error
                    and 0
                    <= (current - quote.observed_at).total_seconds()
                    < MAX_AGE_SECONDS
                ),
                None,
            )
            if quote is not None and quote.ask is not None:
                observations.append(quote.ask)
    lowest = min(observations) if observations else None
    second = (
        sorted(observations)[1]
        if len(observations) >= MIN_NEAR_LOWEST_SHOPS
        else None
    )
    policy: Literal["baseline", "undercut_lowest", "near_lowest"] = (
        "undercut_lowest" if lowest is not None else "baseline"
    )
    ceiling = (
        max(product.reserve, min(baseline, lowest - 1))
        if lowest is not None
        else baseline
    )
    if lowest is not None and second is not None and second > lowest:
        premium = max(1, product.reserve * 2 // 100)
        ceiling = max(product.reserve, min(baseline, second - 1, lowest + premium))
        policy = "near_lowest"
    relation: Literal["lower", "equal", "higher", "unavailable"] = "unavailable"
    if lowest is not None:
        if ceiling < lowest:
            relation = "lower"
        elif ceiling == lowest:
            relation = "equal"
        else:
            relation = "higher"
    return OwnComparison(
        item=product.sku,
        ceiling=ceiling,
        baseline_ceiling=baseline,
        lowest_peer_ask=lowest,
        observed_shop_count=len(observations),
        relation=relation,
        pricing_policy=policy,
        second_lowest_peer_ask=second,
    )
