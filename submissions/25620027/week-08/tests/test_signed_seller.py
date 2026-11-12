"""Drive the real A2A routes and tool host with offline model responses."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from a2a.types import Task, TaskState
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf.json_format import MessageToJson, Parse
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage
from pydantic import JsonValue, TypeAdapter

from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.receipts import ReceiptSigner
from market_a2a.seller import create_app

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam


@pytest.mark.parametrize("signed", [True, False])
def test_seller_receipt_when_real_a2a_task_accepts_offer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    signed: bool,
) -> None:
    # Given: only the external model completion is replaced; tool loop/routes are real.
    replies = iter(
        [
            ("search_catalog", {"query": "lamp"}),
            (
                "decide",
                {"action": "quote", "sku": "lamp", "price": 120, "reason": "Opening"},
            ),
            (
                "decide",
                {"action": "quote", "sku": "lamp", "price": 105, "reason": "Corrected"},
            ),
            (
                "decide",
                {"action": "accept", "sku": "lamp", "price": 1, "reason": "Low bid"},
            ),
            (
                "decide",
                {"action": "quote", "sku": "lamp", "price": 105, "reason": "Reserve"},
            ),
            (
                "decide",
                {"action": "accept", "sku": "lamp", "price": 120, "reason": "Deal"},
            ),
        ]
    )
    logs: list[str] = []
    key = Ed25519PrivateKey.generate()
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")

    async def complete(
        _self: Harness,
        _messages: list[ChatCompletionMessageParam],
        _tools: list[ChatCompletionToolParam],
    ) -> ChatCompletionMessage:
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
            harness = Harness(model, logs.append)
            app = create_app(
                "http://seller/",
                harness,
                catalog,
                "테스트 상점",
                signer=ReceiptSigner(key) if signed else None,
            )
            # ASGI transport stays in process: no socket, API, or network traffic.
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://seller",
                timeout=5,
                trust_env=False,
            ) as client:
                # When: open, submit a below-reserve bid, then agree and try replay.
                opening = await send(client, "I want a lamp")
                assert (
                    "Asking price: 105"
                    in task_from(opening).status.message.parts[0].text
                )
                task_id = task_from(opening).id
                assert task_id
                low = await send(client, "1", task_id)
                accepted = await send(client, "120", task_id)
                replay = await send(client, "120", task_id)

                # Then: reserve holds, receipt belongs to SDK task, task is terminal.
                low_task = task_from(low)
                assert low_task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                assert not low_task.artifacts
                assert any("code=reserve" in line for line in logs)
                completed = task_from(accepted)
                assert completed.status.state == TaskState.TASK_STATE_COMPLETED
                assert len(completed.artifacts) == 1
                assert len(completed.artifacts[0].parts) == 1
                receipt = TypeAdapter(JsonValue).validate_json(
                    MessageToJson(completed.artifacts[0].parts[0].data)
                )
                assert isinstance(receipt, dict)
                assert receipt["price"] == 120
                assert receipt["sku"] == "lamp"
                if signed:
                    assert receipt["task"] == task_id
                    assert receipt["shop"] == "테스트 상점"
                    signature = receipt["sig_seller"]
                    assert isinstance(signature, str)
                    body = {
                        field: receipt[field]
                        for field in ("shop", "item", "price", "task")
                    }
                    body["price"] = 120
                    canonical = json.dumps(
                        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                    key.public_key().verify(
                        base64.b64decode(signature, validate=True), canonical
                    )
                else:
                    assert receipt["seller"] == "테스트 상점"
                    assert "sig_seller" not in receipt
                assert isinstance(replay, dict)
                assert "error" in replay
                print(
                    json.dumps(
                        {
                            "signed": signed,
                            "task": task_id,
                            "receipt": receipt,
                            "reserve_held": True,
                            "terminal_rejected": True,
                        },
                        ensure_ascii=False,
                    )
                )

    anyio.run(scenario)


async def send(client: httpx2.AsyncClient, text: str, task: str = "") -> JsonValue:
    message: dict[str, JsonValue] = {
        "role": "ROLE_USER",
        "messageId": f"message-{text}",
        "parts": [{"text": text}],
    }
    if task:
        message["taskId"] = task
    response = await client.post(
        "/",
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": text,
            "method": "SendMessage",
            "params": {"message": message},
        },
    )
    assert response.status_code == 200
    return TypeAdapter(JsonValue).validate_json(response.content)


def task_from(response: JsonValue) -> Task:
    assert isinstance(response, dict)
    result = response["result"]
    assert isinstance(result, dict)
    return Parse(json.dumps(result["task"]), Task())
