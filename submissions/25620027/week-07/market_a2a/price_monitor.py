"""Bounded opening-quote observations from supported public market infrastructure."""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final

import anyio
import httpx2
from pydantic import Field, ValidationError

from market_a2a.comparison import (
    MAX_AGE_SECONDS,
    REFRESH_SECONDS,
    ComparedShop,
    ComparisonFeed,
    ComparisonSnapshot,
    TradePrice,
    is_fresh,
)
from market_a2a.price_probe import TIMEOUT_SECONDS, create_monitor_client, sample_shop
from market_a2a.remote import Wire

LOGGER: Final = logging.getLogger(__name__)
BOARD_URL: Final = "https://market.ourolabs.ai/api/board"
SHOPS_URL: Final = "https://market.ourolabs.ai/api/shops"
STATS_URL: Final = "https://market.ourolabs.ai/api/stats"
MAX_REFRESHES: Final = 7 * 24 * 6
MAX_SHOPS: Final = 5


class BoardShop(Wire):
    """Only active status, identity and published deal count affect selection."""

    name: str
    alive: bool
    deals: int = Field(ge=0)


class Board(Wire):
    """Preserve the board's supplied ranking order."""

    shops: tuple[BoardShop, ...]
    market_round: int | None = Field(default=None, alias="round")


class RoundStats(Wire):
    """Retain only typed price aggregates from the public dashboard response."""

    items: dict[str, TradePrice]


class Stats(Wire):
    """Bind price aggregates to the board's currently active round."""

    market_round: int = Field(alias="round")
    rounds: dict[str, RoundStats]


class Listing(Wire):
    """Ignore reviews, public keys and arbitrary registry extensions."""

    name: str
    url: str


class Registry(Wire):
    """Resolve board names against the actual registry."""

    shops: tuple[Listing, ...]


def normalized_url(url: str) -> str:
    """Deduplicate equivalent listing URLs without making a network request."""
    try:
        return str(httpx2.URL(url)).rstrip("/")
    except httpx2.InvalidURL:
        return url


def select_shops(board: Board, registry: Registry) -> list[ComparedShop]:
    """Keep five unique active identities and URLs, preserving unavailable entries."""
    urls: dict[str, str] = {}
    for listing in registry.shops:
        if listing.name not in urls:
            urls[listing.name] = listing.url
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    selected: list[ComparedShop] = []
    for rank, shop in enumerate(board.shops, 1):
        if not shop.alive or shop.name == "25620027" or shop.name in seen_names:
            continue
        seen_names.add(shop.name)
        url = urls.get(shop.name, "")
        identity = normalized_url(url)
        if identity and identity in seen_urls:
            continue
        if identity:
            seen_urls.add(identity)
        selected.append(
            ComparedShop(
                rank=rank, name=shop.name, url=url, deals=shop.deals, quotes=()
            )
        )
        if len(selected) == MAX_SHOPS:
            break
    return selected


class PriceMonitor:
    """Publish whole snapshots; one bounded loop never performs purchases."""

    def __init__(self, feed: ComparisonFeed, cache_path: Path) -> None:
        """Share an immutable published feed and caller-owned local cache path."""
        self.feed: ComparisonFeed = feed
        self.cache_path: Path = cache_path

    async def refresh_once(self) -> ComparisonSnapshot:
        """Collect up to fifteen openings; preserve the old feed on registry failure."""
        started = dt.datetime.now(dt.UTC)
        async with create_monitor_client() as client:
            try:
                with anyio.fail_after(TIMEOUT_SECONDS):
                    board_reply = await client.get(BOARD_URL)
                    _ = board_reply.raise_for_status()
                    board = Board.model_validate_json(board_reply.content)
                    listing_reply = await client.get(SHOPS_URL)
                    _ = listing_reply.raise_for_status()
                    registry = Registry.model_validate_json(listing_reply.content)
            except (httpx2.HTTPError, ValidationError, TimeoutError):
                LOGGER.info("price_monitor refresh_error=registry_unavailable")
                now = dt.datetime.now(dt.UTC)
                return self.feed.snapshot or ComparisonSnapshot(
                    observed_at=now, expires_at=now, shops=()
                )
            selected = select_shops(board, registry)
            trade_prices = await self._trade_prices(client, board.market_round)
            results: dict[int, ComparedShop] = {}
            limiter = anyio.CapacityLimiter(3)

            async def collect(shop: ComparedShop) -> None:
                async with limiter:
                    results[shop.rank] = await sample_shop(client, shop)

            async with anyio.create_task_group() as group:
                for shop in selected:
                    _ = group.start_soon(collect, shop)
        snapshot = ComparisonSnapshot(
            observed_at=started,
            expires_at=started + dt.timedelta(seconds=MAX_AGE_SECONDS),
            shops=tuple(results[shop.rank] for shop in selected),
            market_round=board.market_round,
            trade_prices=trade_prices,
        )
        self.feed.snapshot = snapshot
        try:
            await anyio.to_thread.run_sync(self.save_cache, snapshot)
        except OSError:
            LOGGER.info("price_monitor cache_error=write_failed")
        LOGGER.info("price_monitor refreshed shops=%s", len(snapshot.shops))
        return snapshot

    async def _trade_prices(
        self, client: httpx2.AsyncClient, market_round: int | None,
    ) -> dict[str, TradePrice]:
        if market_round is None:
            return {}
        try:
            with anyio.fail_after(TIMEOUT_SECONDS):
                reply = await client.get(STATS_URL)
                _ = reply.raise_for_status()
                stats = Stats.model_validate_json(reply.content)
            current = stats.rounds.get(str(market_round))
            if stats.market_round != market_round or current is None:
                return {}
            return {item: value for item, value in current.items.items()
                    if item in ("lamp", "chair", "bookshelf")
                    and value.min <= value.mean <= value.max}
        except (httpx2.HTTPError, ValidationError, TimeoutError):
            LOGGER.info("price_monitor trade_stats=unavailable")
            return {}

    def save_cache(self, snapshot: ComparisonSnapshot) -> None:
        """Atomically replace the cache without leaving a partial snapshot."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.cache_path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                _ = stream.write(snapshot.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
                _ = temporary.replace(self.cache_path)
            finally:
                temporary.unlink(missing_ok=True)

    async def run(self) -> None:
        """Refresh every ten minutes within the week's independently guarded runtime."""
        try:
            cached = await anyio.Path(self.cache_path).read_text(encoding="utf-8")
            self.feed.snapshot = ComparisonSnapshot.model_validate_json(cached)
        except (OSError, UnicodeError, ValidationError):
            LOGGER.info("price_monitor cache_status=unavailable")
        snapshot = self.feed.snapshot
        if is_fresh(snapshot) and snapshot is not None:
            age = (dt.datetime.now(dt.UTC) - snapshot.observed_at).total_seconds()
            await anyio.sleep(max(0, REFRESH_SECONDS - age))
        for _ in range(MAX_REFRESHES):
            _ = await self.refresh_once()
            await anyio.sleep(REFRESH_SECONDS)
