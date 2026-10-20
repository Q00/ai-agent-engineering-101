"""Observe opening-price guards through the real A2A route and tool loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, override

import anyio
import httpx2
import pytest
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage
from pydantic import JsonValue, TypeAdapter

from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.models import Product
from market_a2a.seller import create_app
from market_a2a.seller_policy import Decision
from market_a2a.seller_turn import Negotiator, Turn
from tests.test_signed_seller import send, task_from

if TYPE_CHECKING:
    from a2a.server.events.event_queue import Event
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam


@pytest.mark.parametrize(
    "case",
    [
        ("lamp", 100, 105, 100),
        ("lamp", 100, 105, 105),
        ("chair", 180, 189, 180),
        ("chair", 180, 189, 189),
        ("bookshelf", 260, 273, 273),
        ("lamp", 101, 107, 107),
        ("lamp", 1, 2, 2),
    ],
)
def test_opening_ask_when_model_corrects_out_of_range_prices(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: tuple[str, int, int, int],
) -> None:
    # Given: a real catalog and model attempts above/below the valid opening range.
    item, reserve, ceiling, accepted = case
    product = Product(sku=item, name=item, description=item, kind=item, reserve=reserve)
    catalog_path = tmp_path / "catalog.json"
    _ = catalog_path.write_text(f"[{product.model_dump_json()}]", encoding="utf-8")
    replies = iter(
        [
            ("search_catalog", {"query": item}),
            (
                "decide",
                {
                    "action": "quote",
                    "sku": item,
                    "price": ceiling + 1,
                    "reason": "High",
                },
            ),
            (
                "decide",
                {"action": "quote", "sku": item, "price": reserve - 1, "reason": "Low"},
            ),
            (
                "decide",
                {"action": "quote", "sku": item, "price": accepted, "reason": "Valid"},
            ),
        ]
    )
    feedback: list[JsonValue] = []

    async def complete(
        _self: Harness,
        messages: list[ChatCompletionMessageParam],
        _tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        last = messages[-1]
        if last["role"] == "tool":
            content = last["content"]
            assert isinstance(content, str)
            feedback.append(TypeAdapter(JsonValue).validate_json(content))
        name, arguments = next(replies)
        return ChatCompletionMessage.model_validate(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "offline",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        )

    monkeypatch.setattr(Harness, "complete", complete)

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            app = create_app(
                "http://seller/",
                Harness(model, lambda _line: None),
                Catalog(catalog_path),
                "Opening test",
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://seller",
                timeout=5,
                trust_env=False,
            ) as client:
                # When: one buyer opens a negotiation.
                response = await send(client, f"I want a {item}")

                # Then: only the corrected quote reaches the buyer.
                task = task_from(response)
                assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                assert f"Asking price: {accepted}" in task.status.message.parts[0].text
                assert not task.artifacts
                assert len(feedback) == 3
                candidates = feedback[0]
                assert isinstance(candidates, list)
                candidate = candidates[0]
                assert isinstance(candidate, dict)
                assert candidate["opening_max"] == ceiling
                guard = feedback[1]
                assert isinstance(guard, dict)
                assert guard["code"] == "opening_ask_limit"
                assert guard["minimum"] == reserve
                assert guard["maximum"] == ceiling
                assert isinstance(feedback[2], dict)
                assert "error" in feedback[2]

    anyio.run(scenario)


class EventCollector(EventQueue):
    """Record the SDK's outbound events for a direct tool-boundary assertion."""

    def __init__(self) -> None:
        """Start an empty per-test queue that mutates only through enqueue_event."""
        self.events: list[Event] = []

    @override
    async def enqueue_event(self, event: Event) -> None:
        self.events.append(event)


@pytest.mark.parametrize("price", [99, 106])
def test_invalid_opening_when_guard_leaves_task_and_events_untouched(
    price: int,
) -> None:
    # Given: one freshly retrieved product and no committed negotiation.
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    queue = EventCollector()
    decision = Decision(action="quote", sku="lamp", price=price, reason="Out of range")

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            seller = Negotiator(Harness(model, lambda _line: None), catalog, "Test")
            turn = Turn(
                "new-task",
                TaskUpdater(queue, "new-task", "context"),
                None,
                {"lamp": catalog.get("lamp")},
                None,
            )

            # When: the tool attempts to publish an invalid opening quote.
            result = await seller.commit(turn, decision)

            # Then: no task, artifact, or negotiation state was committed.
            assert not result.terminal
            assert queue.events == []
            assert seller.negotiations == {}
            assert seller.closed == set()

    anyio.run(scenario)
