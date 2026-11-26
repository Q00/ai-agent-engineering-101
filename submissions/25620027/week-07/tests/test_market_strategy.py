from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import anyio
import httpx2
import pytest
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict
from openai import AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from market_a2a.catalog import Catalog
from market_a2a.comparison import (
    ComparedShop,
    ComparisonSnapshot,
    ObservedQuote,
    compare_price,
)
from market_a2a.harness import Harness
from market_a2a.seller import create_app
from tests.test_seller_offer_release import action, scripted
from tests.test_signed_seller import send, task_from


@pytest.mark.parametrize(
    ("item", "prices", "ceiling"),
    [
        ("lamp", (100, 101, 110, 120, 130), 100),
        ("chair", (180, 183, 195, 198, 234), 182),
        ("bookshelf", (260, 265, 275, 290, 338), 264),
        ("bookshelf", (260, 300, 310, 320, 338), 265),
        ("chair", (180, 180, 195, 198, 234), 180),
        ("chair", (180, 183), 180),
    ],
)
def test_opening_when_observed_market_has_room_uses_bounded_near_low_price(
    item: Literal["lamp", "chair", "bookshelf"], prices: tuple[int, ...], ceiling: int
) -> None:
    now = dt.datetime.now(dt.UTC)
    snapshot = ComparisonSnapshot(
        observed_at=now,
        expires_at=now + dt.timedelta(minutes=15),
        shops=tuple(
            ComparedShop(
                rank=index + 1,
                name=f"Peer{index}",
                url=f"https://peer{index}.example",
                deals=100,
                quotes=(
                    ObservedQuote(item=item, ask=price, observed_at=now),
                ),
            )
            for index, price in enumerate(prices)
        ),
    )
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    compared = compare_price(catalog.get(item), snapshot, now)
    assert compared.ceiling == ceiling
    assert compared.ceiling >= catalog.get(item).reserve


def test_first_feasible_counter_when_model_delays_completes_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = scripted(
        monkeypatch,
        [
            ("search_catalog", {"query": "bookshelf"}),
            action("quote", 286, "bookshelf"),
            action("quote", 270, "bookshelf"),
            action("accept", 260, "bookshelf"),
        ],
    )
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            app = create_app(
                "http://seller/", Harness(model, print), catalog, "Test"
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://seller"
            ) as client:
                opening = task_from(await send(client, "I want to buy a bookshelf"))
                closed = task_from(await send(client, "260", opening.id))
                assert closed.status.state == TaskState.TASK_STATE_COMPLETED
                part = TypeAdapter(JsonValue).validate_python(
                    MessageToDict(closed.artifacts[0].parts[0])
                )
                assert isinstance(part, dict)
                receipt = part["data"]
                assert isinstance(receipt, dict)
                assert receipt["price"] == 260

    anyio.run(scenario)
