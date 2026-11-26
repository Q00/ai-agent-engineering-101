"""Seller SDK scenarios through real A2A and OpenAI HTTP serialization."""

from __future__ import annotations

import json
from collections import deque
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, assert_never

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from openai import AsyncOpenAI

from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.models import Product
from market_a2a.remote import Card, Response, Task
from market_a2a.seller import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from pydantic import JsonValue


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ModelWire:
    """Consume a finite script at the HTTP boundary, without replacing the loop."""

    def __init__(self) -> None:
        """Start a finite tool-call script."""
        self.calls: deque[tuple[str, str]] = deque()
        self.requests: list[str] = []

    def decide(self, action: str, price: int, sku: str = "lamp") -> None:
        """Queue a model-selected action."""
        self.calls.append(
            (
                "decide",
                json.dumps(
                    {
                        "action": action,
                        "price": price,
                        "sku": sku,
                        "reason": "Chosen action",
                    }
                ),
            )
        )

    def search(self, query: str = "lamp") -> None:
        """Queue model-directed retrieval."""
        self.calls.append(("search_catalog", json.dumps({"query": query})))

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        """Return the next model tool call through the real HTTP client."""
        self.requests.append(request.content.decode())
        name, arguments = self.calls.popleft()
        return httpx2.Response(
            200,
            json={
                "id": "scripted",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"tool-{len(self.requests)}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": arguments},
                                }
                            ],
                        },
                    }
                ],
            },
        )


@asynccontextmanager
async def shop(
    path: Path, model: ModelWire, mount: str = ""
) -> AsyncGenerator[httpx2.AsyncClient]:
    products = [
        Product(
            sku="lamp",
            name="Desk lamp",
            kind="lamp",
            description="Basic lamp",
            reserve=100,
        ),
        Product(
            sku="bookshelf",
            name="Wood shelf",
            kind="bookshelf",
            description="Wooden bookshelf",
            reserve=260,
        ),
    ]
    _ = await anyio.Path(path).write_text(
        json.dumps([p.model_dump() for p in products])
    )
    async with (
        httpx2.AsyncClient(transport=httpx2.MockTransport(model.respond)) as wire,
        AsyncOpenAI(api_key="test-only", http_client=wire) as api,
    ):
        seller = create_app(
            f"http://seller{mount}/",
            Harness(api, print),
            Catalog(path),
            "Test shop",
        )
        app = FastAPI()
        app.mount(mount or "/", seller)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url=f"http://seller{mount}/",
            follow_redirects=True,
            headers={"A2A-Version": "1.0"},
        ) as client:
            yield client


async def send(
    client: httpx2.AsyncClient, text: str, task: Task | None = None
) -> Response:
    message: dict[str, JsonValue] = {
        "role": "ROLE_USER",
        "messageId": str(len(text)),
        "parts": [{"text": text}],
    }
    if task:
        message["taskId"] = task.id
    reply = await client.post(
        "",
        json={
            "jsonrpc": "2.0",
            "id": "test",
            "method": "SendMessage",
            "params": {"message": message},
        },
    )
    print(reply.text)
    return Response.model_validate_json(reply.content)


async def opening(client: httpx2.AsyncClient, model: ModelWire) -> Task:
    model.search()
    model.decide("quote", 130)
    reply = await send(client, "I want to buy a lamp")
    assert reply.result is not None
    return reply.result.task


@pytest.mark.anyio
@pytest.mark.parametrize("mount", ["", "/shop-01"])
async def test_discovery_when_root_or_mounted(tmp_path: Path, mount: str) -> None:
    # Given a real seller mounted at a configurable URL.
    async with shop(tmp_path / "catalog.json", ModelWire(), mount) as client:
        # When a client discovers its card.
        reply = await client.get(".well-known/agent-card.json")
        # Then the advertised interface is exactly reachable at that mount.
        card = Card.model_validate_json(reply.content)
        assert card.endpoint() == f"http://seller{mount}/"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("action", "price", "sku"),
    [
        ("quote", 99, "lamp"),
        ("accept", 99, "lamp"),
        ("accept", 101, "lamp"),
        ("quote", 300, "bookshelf"),
    ],
)
async def test_guard_when_invalid_model_decision(
    tmp_path: Path, action: str, price: int, sku: str
) -> None:
    # Given an open lamp task and an invalid proposed action followed by correction.
    model = ModelWire()
    async with shop(tmp_path / "catalog.json", model) as client:
        task = await opening(client, model)
        model.decide(action, price, sku)
        model.decide("quote", 120)
        # When the buyer offers 100 and the real model loop consumes tool feedback.
        reply = await send(client, "100", task)
        # Then only the corrected valid quote is committed, preserving task/context.
        assert reply.result is not None
        actual = reply.result.task
        assert (actual.id, actual.context_id, actual.status.state) == (
            task.id,
            task.context_id,
            "TASK_STATE_INPUT_REQUIRED",
        )
        assert actual.observation().endswith("120")
        assert actual.artifacts == ()
        assert not model.calls


@pytest.mark.anyio
async def test_receipt_when_model_accepts_actual_offer(tmp_path: Path) -> None:
    # Given a selected lamp and a model choosing the actual offer at reserve.
    model = ModelWire()
    async with shop(tmp_path / "catalog.json", model) as client:
        task = await opening(client, model)
        model.decide("accept", 100)
        # When the buyer offers the exact reserve.
        reply = await send(client, "100", task)
        # Then the task completes with the selected SKU and actual price.
        assert reply.result is not None
        actual = reply.result.task
        receipt = actual.artifacts[0].parts[0].data
        assert actual.status.state == "TASK_STATE_COMPLETED"
        assert receipt is not None
        assert (receipt.sku, receipt.item, receipt.price) == ("lamp", "lamp", 100)
        assert task.status.message.metadata.sku == "lamp"


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["accept", "cancel"])
async def test_terminal_error_when_followup_after_finish(
    tmp_path: Path, method: Literal["accept", "cancel"]
) -> None:
    # Given a completed or canceled task.
    model = ModelWire()
    async with shop(tmp_path / "catalog.json", model) as client:
        task = await opening(client, model)
        match method:
            case "accept":
                model.decide("accept", 100)
                _ = await send(client, "100", task)
            case "cancel":
                _ = await client.post(
                    "",
                    json={
                        "jsonrpc": "2.0",
                        "id": "cancel",
                        "method": "CancelTask",
                        "params": {"id": task.id},
                    },
                )
            case _:
                assert_never(method)
        # When a further message targets the same task.
        reply = await send(client, "110", task)
        # Then the SDK returns a protocol error instead of a second receipt.
        assert reply.error is not None
        assert reply.result is None


@pytest.mark.anyio
async def test_unavailable_when_model_rejects_unknown_item(tmp_path: Path) -> None:
    # Given a model searching for an unavailable product then rejecting.
    model = ModelWire()
    model.search("spaceship")
    model.decide("reject", 0, "")
    async with shop(tmp_path / "catalog.json", model) as client:
        # When an unavailable item is requested.
        reply = await send(client, "spaceship")
        # Then no receipt is issued and the task is rejected.
        assert reply.result is not None
        assert (reply.result.task.status.state, reply.result.task.artifacts) == (
            "TASK_STATE_REJECTED",
            (),
        )
