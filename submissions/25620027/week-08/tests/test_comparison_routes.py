"""Public comparison requests consume cached evidence without probing shops."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import anyio
import httpx2
from fastapi import FastAPI

from market_a2a.catalog import Catalog
from market_a2a.comparison import ComparisonDocument
from market_a2a.comparison_service import ComparisonService
from tests.test_comparison_prices import snapshot_for


def test_comparison_when_visitors_read_json_uses_same_opening_bound() -> None:
    # Given: one published peer ask of 103 and a running route with no lifespan task.
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    service = ComparisonService(catalog, enabled=True)
    service.feed.snapshot = snapshot_for(103, dt.datetime.now(dt.UTC))
    app = FastAPI()
    service.install(app)

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://seller"
        ) as client:
            # When: a visitor reads public data and its same-origin script.
            response = await client.get("/price-comparison.json")
            script = await client.get("/price-comparison.js")
            # Then: the cap is one below the observed ask and no external I/O is needed.
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            body = ComparisonDocument.model_validate_json(response.content)
            lamp = next(item for item in body.own if item.item == "lamp")
            assert lamp.ceiling == 102
            assert lamp.observed_shop_count == 1
            assert body.fresh is True
            assert script.status_code == 200
            assert script.headers["Content-Type"].startswith("application/javascript")

    anyio.run(scenario)
