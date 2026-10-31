"""Exercise real SQLite recovery and restarted A2A apps without model/network I/O."""

from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import override

import anyio
import httpx2
import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Task, TaskState, TaskStatus
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf.json_format import MessageToJson
from openai import AsyncOpenAI
from pydantic import JsonValue, TypeAdapter, ValidationError

from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.models import HarnessError
from market_a2a.receipts import ReceiptSigner, SignedReceipt
from market_a2a.seller import create_app
from market_a2a.seller_policy import Negotiation
from market_a2a.seller_store import PersistentSellerStore
from tests.test_seller_offer_release import action, scripted
from tests.test_signed_seller import send, task_from


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "seller.sqlite3"
    monkeypatch.setenv("AX_SELLER_STATE_DB", str(path))
    monkeypatch.setenv("AX_PRICE_COMPARISON", "0")
    return path


def fresh_client(
    harness: Harness, signer: ReceiptSigner | None = None
) -> httpx2.AsyncClient:
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    app = create_app("http://seller/", harness, catalog, "Test", signer=signer)
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://seller",
        timeout=5,
        trust_env=False,
    )


def test_signed_sale_when_app_restarts_preserves_offer_and_blocks_terminal_replay(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    # Given: a persisted model quote; acceptance does not need another model decision.
    calls = scripted(
        monkeypatch,
        [
            ("search_catalog", {"query": "lamp"}),
            action("quote", 105),
        ],
    )
    key = Ed25519PrivateKey.generate()
    signer = ReceiptSigner(key)

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            harness = Harness(model, print)
            async with fresh_client(harness, signer) as first:
                opened = task_from(await send(first, "lamp"))
            # When: a new app accepts the existing SDK task, then a third replays it.
            async with fresh_client(harness, signer) as second:
                accepted = await send(second, "105", opened.id)
            async with fresh_client(harness, signer) as third:
                replay = await send(third, "105", opened.id)
            # Then: the actual offer/SKU/task are signed once and replay never calls AI.
            completed = task_from(accepted)
            assert completed.id == opened.id
            assert completed.context_id == opened.context_id
            assert completed.status.state == TaskState.TASK_STATE_COMPLETED
            assert len(completed.artifacts) == 1
            raw = TypeAdapter(JsonValue).validate_json(
                MessageToJson(completed.artifacts[0].parts[0].data)
            )
            assert isinstance(raw, dict)
            assert raw["price"] == 105
            assert raw["sku"] == "lamp"
            receipt = SignedReceipt.model_validate(
                {
                    **{
                        field: raw[field]
                        for field in ("shop", "item", "task", "sig_seller")
                    },
                    "price": 105,
                }
            )
            assert receipt.task == opened.id
            key.public_key().verify(
                base64.b64decode(receipt.sig_seller), receipt.canonical()
            )
            assert isinstance(replay, dict)
            assert "error" in replay
            assert len(calls) == 2
            assert PersistentSellerStore(database).restore() == ({}, {opened.id})
            print(
                json.dumps(
                    {"accepted": accepted, "replay": replay, "model_calls": len(calls)}
                )
            )

    anyio.run(scenario)


def test_floor_and_current_ask_when_restarted_reject_invalid_model_prices(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    # Given: the new app must remember the 105 ask and the selected lamp's 100 floor.
    calls = scripted(
        monkeypatch,
        [
            ("search_catalog", {"query": "lamp"}),
            action("quote", 105),
            action("accept", 99),
            action("quote", 110),
            action("quote", 105),
        ],
    )

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            harness = Harness(model, print)
            async with fresh_client(harness) as first:
                opened = task_from(await send(first, "lamp"))
            # When: a new app handles an offer below the persisted reserve.
            async with fresh_client(harness) as second:
                wire = await send(second, "99", opened.id)
            # Then: floor and non-increasing ask survive; no sale artifact is issued.
            continued = task_from(wire)
            assert continued.id == opened.id
            assert continued.context_id == opened.context_id
            assert continued.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
            assert not continued.artifacts
            states, closed = PersistentSellerStore(database).restore()
            state = states[opened.id]
            assert (state.product.sku, state.product.reserve, state.ask) == (
                "lamp",
                100,
                105,
            )
            assert state.incoming_offers == 1
            assert not closed
            assert len(calls) == 5
            assert calls[2:] == [["decide"], ["decide"], ["decide"]]
            print(json.dumps({"continued": wire, "model_calls": len(calls)}))

    anyio.run(scenario)


@dataclass(frozen=True, slots=True)
class NamedUser(User):
    """Represent an authenticated SDK owner without mocking the owner resolver."""

    name: str

    @property
    @override
    def is_authenticated(self) -> bool:
        return True

    @property
    @override
    def user_name(self) -> str:
        return self.name


def test_task_get_list_delete_when_reopened_preserve_sdk_owner_isolation(
    database: Path,
) -> None:
    # Given: distinct owners share an ID and each has a private task.
    alice = ServerCallContext(user=NamedUser("alice"))
    bob = ServerCallContext(user=NamedUser("bob"))

    async def scenario() -> None:
        original = PersistentSellerStore(database)
        for owner, context in (("alice", alice), ("bob", bob)):
            for task_id in ("shared", owner):
                await original.save(
                    Task(
                        id=task_id,
                        context_id=owner,
                        status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
                    ),
                    context,
                )
        # When: a new store reads both owners, deletes Alice's shared ID, and restarts.
        restored = PersistentSellerStore(database)
        for owner, context in (("alice", alice), ("bob", bob)):
            shared = await restored.get("shared", context)
            assert shared is not None
            assert shared.context_id == owner
            assert {
                task.id
                for task in (await restored.list(ListTasksRequest(), context)).tasks
            } == {"shared", owner}
        assert await restored.get("bob", alice) is None
        assert await restored.get("alice", bob) is None
        await restored.delete("shared", alice)
        final = PersistentSellerStore(database)
        # Then: durable deletion affects only the owning user's record.
        assert await final.get("shared", alice) is None
        assert await final.get("shared", bob) is not None
        assert [
            task.id for task in (await final.list(ListTasksRequest(), alice)).tasks
        ] == ["alice"]
        print(json.dumps({"owner_isolation": True, "durable_owner_delete": True}))

    anyio.run(scenario)


def test_journal_when_closed_cannot_be_reopened(database: Path) -> None:
    # Given: a valid persisted negotiation becomes terminal.
    catalog = Catalog(Path(__file__).resolve().parents[1] / "catalog.json")
    product = next(product for product in catalog.products if product.sku == "lamp")
    state = Negotiation(product, 105)

    async def scenario() -> None:
        store = PersistentSellerStore(database)
        await store.save_negotiation("terminal", state)
        await store.close("terminal")
        reopened = PersistentSellerStore(database)
        # When: a fresh store tries to save a quote over a terminal tombstone.
        with pytest.raises(HarnessError):
            await reopened.save_negotiation("terminal", state)
        # Then: fresh restoration still reports only the terminal ID.
        assert PersistentSellerStore(database).restore() == ({}, {"terminal"})
        print(json.dumps({"closed_reopen_rejected": True}))

    anyio.run(scenario)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        '{"product":{}}',
        (
            '{"product":{"sku":"lamp","name":"lamp","description":"",'
            '"kind":"lamp","reserve":100},"ask":99}'
        ),
    ],
)
def test_startup_when_open_journal_corrupt_fails_closed(
    database: Path, payload: str | None
) -> None:
    # Given: a real database contains an incomplete or malformed open summary.
    _ = PersistentSellerStore(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        _ = connection.execute(
            "INSERT INTO negotiations VALUES (?, ?, 0)", ("broken", payload)
        )

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            # When: normal app startup reads the corrupt journal.
            with pytest.raises((HarnessError, ValidationError)):
                _ = fresh_client(Harness(model, print))
            # Then: no running app or negotiation is exposed.
            print(json.dumps({"corrupt_payload": payload, "startup_rejected": True}))

    anyio.run(scenario)


def test_prior_memory_only_task_when_persistence_enabled_is_not_migrated(
    monkeypatch: pytest.MonkeyPatch, database: Path
) -> None:
    # Given: the pre-persistence operating mode has an open task only in memory.
    calls = scripted(
        monkeypatch, [("search_catalog", {"query": "lamp"}), action("quote", 105)]
    )

    async def scenario() -> None:
        async with AsyncOpenAI(api_key="offline-placeholder") as model:
            harness = Harness(model, print)
            monkeypatch.delenv("AX_SELLER_STATE_DB")
            async with fresh_client(harness) as first:
                opened = task_from(await send(first, "lamp"))
            monkeypatch.setenv("AX_SELLER_STATE_DB", str(database))
            # When: a fresh persistent app receives the prior in-memory task ID.
            async with fresh_client(harness) as second:
                wire = await send(second, "105", opened.id)
            # Then: unknown prior tasks fail; this is not a zero-downtime migration.
            assert isinstance(wire, dict)
            assert "error" in wire
            assert len(calls) == 2
            assert PersistentSellerStore(database).restore() == ({}, set())
            print(json.dumps({"prior_memory_task": opened.id, "response": wire}))

    anyio.run(scenario)
