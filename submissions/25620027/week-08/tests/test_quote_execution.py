"""Replay confirmed quotes through A2A, including restart and a model outage."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import anyio
import pytest
from a2a.types import TaskState
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf.json_format import MessageToJson
from openai import AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from market_a2a.harness import Harness
from market_a2a.receipts import ReceiptSigner, SignedReceipt
from tests.test_seller_offer_release import action, scripted
from tests.test_seller_persistence import fresh_client
from tests.test_signed_seller import send, task_from

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("round_number", ["2", "3"])
@pytest.mark.parametrize("acceptance", ["105", "I accept", "I can pay 110"])
def test_persisted_quote_executes_without_another_model_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    round_number: str, acceptance: str,
) -> None:
    monkeypatch.setenv("AX_MARKET_ROUND", round_number)
    monkeypatch.setenv("AX_PRICE_COMPARISON", "0")
    monkeypatch.setenv("AX_SELLER_STATE_DB", str(tmp_path / "seller.sqlite3"))
    calls = scripted(monkeypatch, [action("quote", 105)])
    key = Ed25519PrivateKey.generate()
    signer = ReceiptSigner(key)
    price = 110 if acceptance.endswith("110") else 105
    logs: list[str] = []

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as client:
            harness = Harness(client, logs.append)
            async with fresh_client(harness, signer) as first:
                opened = task_from(await send(first, "lamp"))
            # The restarted app has a valid quote, but no model replies remain.
            async with fresh_client(harness, signer) as second:
                completed = task_from(await send(second, acceptance, opened.id))
            async with fresh_client(harness, signer) as third:
                replay = await send(third, acceptance, opened.id)
            assert completed.status.state == TaskState.TASK_STATE_COMPLETED
            raw = TypeAdapter(dict[str, JsonValue]).validate_json(
                MessageToJson(completed.artifacts[0].parts[0].data)
            )
            receipt = SignedReceipt.model_validate({
                **{k: raw[k] for k in ("shop", "item", "task", "sig_seller")},
                "price": price,
            })
            assert raw["price"] == price
            assert raw["sku"] == "lamp"
            assert receipt.task == opened.id
            key.public_key().verify(
                base64.b64decode(receipt.sig_seller), receipt.canonical()
            )
            assert isinstance(replay, dict)
            assert "error" in replay
            assert len(calls) == 1
            assert any("seller.quote_execution " in line for line in logs)

    anyio.run(scenario)


def test_r3_lower_offer_still_requires_a_new_model_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_MARKET_ROUND", "3")
    monkeypatch.setenv("AX_PRICE_COMPARISON", "0")
    calls = scripted(monkeypatch, [action("quote", 110), action("quote", 108)])

    async def scenario() -> None:
        async with (
            AsyncOpenAI(api_key="offline-placeholder") as client,
            fresh_client(Harness(client, print)) as seller,
        ):
            opened = task_from(await send(seller, "lamp"))
            continued = task_from(await send(seller, "105", opened.id))
            assert continued.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            assert not continued.artifacts
            assert continued.status.message.metadata["ask"] == 108
            assert len(calls) == 2

    anyio.run(scenario)


def test_repeated_infeasible_bid_cannot_raise_a_persisted_quote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AX_MARKET_ROUND", "3")
    monkeypatch.setenv("AX_PRICE_COMPARISON", "0")
    monkeypatch.setenv("AX_SELLER_STATE_DB", str(tmp_path / "seller.sqlite3"))
    calls = scripted(monkeypatch, [
        action("quote", 110), action("quote", 100),
        action("quote", 130), action("quote", 100),
    ])

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as client:
            harness = Harness(client, print)
            async with fresh_client(harness) as first:
                opened = task_from(await send(first, "lamp"))
                lowered = task_from(await send(first, "90", opened.id))
            async with fresh_client(harness) as second:
                repeated = task_from(await send(second, "90", opened.id))
            assert [task.status.message.metadata["ask"] for task in
                    (opened, lowered, repeated)] == [110, 100, 100]
            assert repeated.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            assert not repeated.artifacts
            assert "100" in repeated.status.message.parts[0].text
            assert len(calls) == 4

    anyio.run(scenario)


def test_repricing_cannot_search_for_a_different_selected_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_MARKET_ROUND", "3")
    calls = scripted(monkeypatch, [
        action("quote", 110), ("search_catalog", {"query": "chair"}),
        action("quote", 108),
    ])
    logs: list[str] = []

    async def scenario() -> None:
        async with (
            AsyncOpenAI(api_key="offline-placeholder") as client,
            fresh_client(Harness(client, logs.append)) as seller,
        ):
            opened = task_from(await send(seller, "lamp"))
            continued = task_from(await send(seller, "105", opened.id))
            assert continued.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            assert continued.status.message.metadata["sku"] == "lamp"
            assert continued.status.message.metadata["ask"] == 108
            assert any("SKU already selected" in line for line in logs)
            assert len(calls) == 3

    anyio.run(scenario)
