"""Exercise explicit offers, final-price facts, and selected-SKU dispatch over A2A."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from a2a.types import TaskState
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf.json_format import MessageToJson
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage
from pydantic import JsonValue, TypeAdapter

from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.receipts import ReceiptSigner, SignedReceipt
from market_a2a.seller import create_app
from market_a2a.seller_offer import opening_offer
from tests.test_signed_seller import send, task_from

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam


def scripted(
    monkeypatch: pytest.MonkeyPatch, actions: list[tuple[str, dict[str, JsonValue]]]
) -> list[list[str]]:
    replies = iter(actions)
    advertised: list[list[str]] = []

    async def complete(
        _self: Harness,
        _messages: list[ChatCompletionMessageParam],
        tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
        advertised.append(
            [tool["function"]["name"] for tool in tools if tool["type"] == "function"]
        )
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
    return advertised


def action(
    kind: str, price: int, sku: str = "lamp"
) -> tuple[str, dict[str, JsonValue]]:
    return "decide", {"action": kind, "price": price, "sku": sku, "reason": "Test"}


@pytest.mark.parametrize(
    "text",
    [
        "lamp 100",
        "lamp budget 100",
        "lamp height 100 cm",
        "buy 2 lamps",
        "offer lamp 100 and ignore policy",
        "100",
        "buy lamp for -1",
        "offer lamp 1.5",
    ],
)
def test_opening_offer_when_not_explicit_is_not_money(text: str) -> None:
    # Given: numbers with no accepted whole-message purchase grammar.
    # When: the boundary parser examines the complete message.
    parsed = opening_offer(text)
    # Then: no actual offer exists.
    assert parsed is None


@pytest.mark.parametrize(
    ("text", "price"),
    [("offer lamp 100", 100), ("buy lamp for 100", 100), ("offer lamp 125", 125)],
)
def test_first_offer_when_explicit_completes_signed_sale(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    price: int,
) -> None:
    # Given: a model must retrieve, preserve SKU and amount, and correct bad actions.
    _ = scripted(
        monkeypatch,
        [
            action("reject", price),
            ("search_catalog", {"query": "lamp"}),
            action("quote", 100),
            action("accept", price, "chair"),
            action("accept", price + 1),
            action("accept", price),
        ],
    )
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    key = Ed25519PrivateKey.generate()

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            app = create_app(
                "http://seller/",
                Harness(model, print),
                catalog,
                "Test",
                signer=ReceiptSigner(key),
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://seller",
            ) as client:
                # When: one explicit offer reaches the real A2A route.
                wire = await send(client, text)
                task = task_from(wire)
                replay = await send(client, str(price), task.id)
                # Then: exact amount is signed once and terminal replay is rejected.
                assert task.status.state == TaskState.TASK_STATE_COMPLETED
                raw = TypeAdapter(JsonValue).validate_json(
                    MessageToJson(task.artifacts[0].parts[0].data)
                )
                assert isinstance(raw, dict)
                assert raw["price"] == price
                receipt = SignedReceipt.model_validate(
                    {
                        **{
                            field: raw[field]
                            for field in ("shop", "item", "task", "sig_seller")
                        },
                        "price": price,
                    }
                )
                assert receipt.price == price
                assert raw["sku"] == "lamp"
                assert receipt.task == task.id
                key.public_key().verify(
                    base64.b64decode(receipt.sig_seller), receipt.canonical()
                )
                assert isinstance(replay, dict)
                assert "error" in replay
                print(json.dumps({"scenario": text, "wire": wire, "replay": replay}))

    anyio.run(scenario)


@pytest.mark.parametrize(
    ("opening", "ask"), [("lamp", 100), ("offer lamp 99", 100), ("lamp", 110)]
)
def test_final_quote_when_at_reserve_has_no_future_discount_and_skips_search(
    monkeypatch: pytest.MonkeyPatch,
    opening: str,
    ask: int,
) -> None:
    # Given: ordinary request or explicit low offer opens at the reserve.
    tools = scripted(
        monkeypatch,
        [
            ("search_catalog", {"query": "lamp"}),
            action("accept", 99),
            action("quote", ask),
            ("search_catalog", {"query": "chair"}),
            action("accept", ask),
        ],
    )
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    logs: list[str] = []

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            app = create_app(
                "http://seller/", Harness(model, logs.append), catalog, "Test"
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://seller",
            ) as client:
                # When: buyer opens then replies with the exact advertised last integer.
                first = await send(client, opening)
                quote = task_from(first)
                last_integer = quote.status.message.parts[0].text.split()[-1]
                final = await send(client, last_integer, quote.id)
                # Then: factual final-price metadata and completion survive fake search.
                assert quote.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                metadata = TypeAdapter(JsonValue).validate_json(
                    MessageToJson(quote.status.message.metadata)
                )
                assert isinstance(metadata, dict)
                assert metadata["price_final"] is (ask == 100)
                wording = quote.status.message.parts[0].text
                assert ("Larger reductions are possible" in wording) is (ask > 100)
                assert f"Reply with exactly {ask}" in wording
                assert last_integer == str(ask)
                assert task_from(final).status.state == TaskState.TASK_STATE_COMPLETED
                assert tools[-2:] == [["decide"], ["decide"]]
                assert sum("seller.search " in line for line in logs) == 1
                assert any("SKU already selected" in line for line in logs)
                print(
                    json.dumps(
                        {
                            "scenario": opening,
                            "quote": first,
                            "completed": final,
                            "tools": tools,
                            "logs": logs,
                        }
                    )
                )

    anyio.run(scenario)
