"""Serve cached comparison evidence while one bounded background monitor runs."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from fastapi.responses import FileResponse, JSONResponse

from market_a2a.comparison import (
    BASIC_ITEMS,
    MAX_AGE_SECONDS,
    REFRESH_SECONDS,
    ComparisonDocument,
    ComparisonFeed,
    compare_price,
    is_fresh,
)
from market_a2a.price_monitor import PriceMonitor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

    from market_a2a.catalog import Catalog
    from market_a2a.models import Product


class ComparisonService:
    """Own one process-local feed and public routes; visitors do not trigger probes."""

    def __init__(self, catalog: Catalog, *, enabled: bool) -> None:
        """Use the same basic products and price rules as the seller."""
        self.enabled: bool = enabled
        self.feed: ComparisonFeed = ComparisonFeed()
        self.products: tuple[Product, ...] = tuple(
            product for product in catalog.products if product.sku in BASIC_ITEMS
        )
        self.directory: Path = Path(__file__).resolve().parents[1]
        self.monitor: PriceMonitor = PriceMonitor(
            self.feed, self.directory / ".market" / "price-comparison.json"
        )

    @asynccontextmanager
    async def lifespan(self, _app: FastAPI) -> AsyncGenerator[None]:
        """Cancel the periodic task with the seller process, including shutdown."""
        async with anyio.create_task_group() as group:
            if self.enabled:
                _ = group.start_soon(self.monitor.run)
            yield
            group.cancel_scope.cancel()

    def install(self, app: FastAPI) -> None:
        """Expose dated data and a same-origin display script."""
        app.add_api_route(
            "/price-comparison.json",
            self.view,
            methods=["GET"],
            include_in_schema=False,
        )
        app.add_api_route(
            "/price-comparison.js",
            self.javascript,
            methods=["GET"],
            include_in_schema=False,
        )

    async def view(self) -> JSONResponse:
        """Compute only numeric own ceilings; never start a network query here."""
        snapshot = self.feed.snapshot if self.enabled else None
        document = ComparisonDocument(
            enabled=self.enabled,
            fresh=is_fresh(snapshot),
            refresh_seconds=REFRESH_SECONDS,
            max_age_seconds=MAX_AGE_SECONDS,
            snapshot=snapshot,
            own=tuple(compare_price(product, snapshot) for product in self.products),
        )
        return JSONResponse(
            document.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    async def javascript(self) -> FileResponse:
        """Return the local script without an external hosting dependency."""
        return FileResponse(
            self.directory / "assets" / "price-comparison.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )
