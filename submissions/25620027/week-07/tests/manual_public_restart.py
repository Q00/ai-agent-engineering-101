#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "anyio", "httpx2[http2,brotli,zstd]", "cryptography", "pydantic", "typer",
# ]
# ///
# How to run (from the week-07 project root):
# Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# PYTHONPATH=. uv run tests/manual_public_restart.py open
# Recreate the seller container with its existing persistent database, then:
# PYTHONPATH=. uv run tests/manual_public_restart.py finish
"""Drive public restart and first-offer canaries without market submission."""

from __future__ import annotations

import base64
import json
import socket
from enum import StrEnum
from typing import TYPE_CHECKING, assert_never
from uuid import uuid4

import anyio
import httpx2
import typer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from market_a2a.receipts import SignedReceipt
from market_a2a.remote import Checkout, Response, Task, Wire, purchase
from tests.manual_public_smoke import BASE_URL, EVIDENCE, Registry, VersionedCard

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue


class Phase(StrEnum):
    """Require an explicit operator-selected phase before any network call."""

    OPEN = "open"
    FINISH = "finish"
    OFFER = "offer"


class Checkpoint(Wire):
    """Retain only public task and discovery data across the operator restart."""

    version: str
    endpoint: str
    task: Task


def save_exclusive(path: Path, payload: str) -> None:
    """Never replace earlier canary evidence."""
    with path.open("x", encoding="utf-8") as stream:
        _ = stream.write(payload + "\n")


async def send(
    client: httpx2.AsyncClient, endpoint: str, text: str, task_id: str = ""
) -> Response:
    """Send taskId only; deliberately omit contextId from every request."""
    message: dict[str, JsonValue] = {
        "role": "ROLE_USER",
        "messageId": str(uuid4()),
        "parts": [{"text": text}],
    }
    if task_id:
        message["taskId"] = task_id
    reply = await client.post(
        endpoint,
        headers={"A2A-Version": "1.0"},
        json={
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "SendMessage",
            "params": {"message": message},
        },
    )
    _ = reply.raise_for_status()
    return Response.model_validate_json(reply.content)


def verify_sale(task: Task, key: Ed25519PublicKey) -> SignedReceipt:
    """Verify exact price, SKU, task and registered seller signature."""
    assert task.status.state == "TASK_STATE_COMPLETED"
    assert len(task.artifacts) == 1
    result = purchase(
        task, Checkout(item="lamp", sku="lamp", price=100, budget=100, shop=BASE_URL)
    )
    assert result.completed
    assert result.price == 100
    raw = result.receipt
    assert raw is not None
    receipt = SignedReceipt.model_validate(
        {
            **{field: raw[field] for field in ("shop", "item", "task", "sig_seller")},
            "price": 100,
        }
    )
    assert receipt.task == task.id
    assert receipt.item == "lamp"
    key.verify(base64.b64decode(receipt.sig_seller, validate=True), receipt.canonical())
    return receipt


async def registered_key(client: httpx2.AsyncClient) -> Ed25519PublicKey:
    """Read the registered public key for this seller's public URL."""
    listed = await client.get("https://market.ourolabs.ai/api/shops")
    _ = listed.raise_for_status()
    registry = Registry.model_validate_json(listed.content)
    shop = next(entry for entry in registry.shops if entry.name == "25620027")
    assert shop.url.rstrip("/") == BASE_URL
    return Ed25519PublicKey.from_public_bytes(
        base64.b64decode(shop.pubkey, validate=True)
    )


async def finish(client: httpx2.AsyncClient, checkpoint: Checkpoint) -> None:
    """Complete the old task, reject replay, then check a fresh explicit offer."""
    key = await registered_key(client)
    accepted = await send(client, checkpoint.endpoint, "100", checkpoint.task.id)
    assert accepted.error is None
    assert accepted.result is not None
    task = accepted.result.task
    assert task.id == checkpoint.task.id
    assert task.context_id == checkpoint.task.context_id
    resumed_receipt = verify_sale(task, key)
    replay = await send(client, checkpoint.endpoint, "100", task.id)
    assert replay.error is not None
    assert replay.result is None
    fresh = await send(client, checkpoint.endpoint, "offer lamp 100")
    assert fresh.error is None
    assert fresh.result is not None
    assert fresh.result.task.id != task.id
    fresh_receipt = verify_sale(fresh.result.task, key)
    summary = {
        "version": checkpoint.version,
        "restarted_task_id": task.id,
        "context_id": task.context_id,
        "resumed_receipt": resumed_receipt.model_dump(mode="json"),
        "explicit_first_offer_receipt": fresh_receipt.model_dump(mode="json"),
        "terminal_replay_error": replay.error.model_dump(mode="json"),
        "published_sale_receipts": 2,
        "market_result_submitted": False,
        "public_review_submitted": False,
    }
    save_exclusive(
        EVIDENCE / "public-restart-finish.json", json.dumps(summary, indent=2)
    )
    print(json.dumps(summary))


async def offer(client: httpx2.AsyncClient, card: VersionedCard) -> None:
    """Verify one fresh explicit offer against the registered public key."""
    key = await registered_key(client)
    fresh = await send(client, card.endpoint(), "offer lamp 100")
    assert fresh.error is None
    assert fresh.result is not None
    receipt = verify_sale(fresh.result.task, key)
    summary = {
        "version": card.version,
        "receipt": receipt.model_dump(mode="json"),
        "signature_verified_with_registered_public_key": True,
        "published_sale_receipts": 1,
        "market_result_submitted": False,
        "public_review_submitted": False,
    }
    save_exclusive(
        EVIDENCE / "public-restart-offer.json", json.dumps(summary, indent=2)
    )
    print(json.dumps(summary))


async def run_phase(phase: Phase) -> None:
    """Capture requests and responses in exclusive phase traces."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with (EVIDENCE / f"public-restart-{phase}.log").open(
        "x", encoding="utf-8"
    ) as trace:

        async def log_request(request: httpx2.Request) -> None:
            _ = trace.write(f"REQUEST {request.method} {request.url}\n")
            _ = trace.write(request.content.decode() + "\n")
            trace.flush()

        async def log_response(response: httpx2.Response) -> None:
            _ = await response.aread()
            _ = trace.write(f"RESPONSE {response.status_code}\n{response.text}\n")
            trace.flush()

        limits = httpx2.Limits(
            max_connections=200, max_keepalive_connections=40, keepalive_expiry=30
        )
        transport = httpx2.AsyncHTTPTransport(
            http2=True,
            retries=3,
            limits=limits,
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )
        async with httpx2.AsyncClient(
            transport=transport,
            timeout=httpx2.Timeout(connect=5, read=90, write=10, pool=10),
            follow_redirects=True,
            trust_env=False,
            event_hooks={"request": [log_request], "response": [log_response]},
        ) as client:
            reply = await client.get(
                BASE_URL + "/.well-known/agent-card.json",
                headers={"A2A-Version": "1.0"},
            )
            _ = reply.raise_for_status()
            card = VersionedCard.model_validate_json(reply.content)
            checkpoint_path = EVIDENCE / "public-restart-task.json"
            match phase:
                case Phase.OPEN:
                    assert card.version == "1.3.0"
                    assert not checkpoint_path.exists()
                    opened = await send(client, card.endpoint(), "I want a lamp")
                    assert opened.error is None
                    assert opened.result is not None
                    task = opened.result.task
                    assert task.status.state == "TASK_STATE_INPUT_REQUIRED"
                    assert task.id
                    assert task.context_id
                    assert not task.artifacts
                    checkpoint = Checkpoint(
                        version=card.version, endpoint=card.endpoint(), task=task
                    )
                    save_exclusive(
                        checkpoint_path,
                        checkpoint.model_dump_json(by_alias=True, indent=2),
                    )
                    print(checkpoint.model_dump_json(by_alias=True))
                case Phase.FINISH:
                    assert card.version == "1.3.0"
                    checkpoint = Checkpoint.model_validate_json(
                        checkpoint_path.read_text(encoding="utf-8")
                    )
                    assert checkpoint.endpoint == card.endpoint()
                    assert checkpoint.version == card.version
                    await finish(client, checkpoint)
                case Phase.OFFER:
                    assert card.version == "1.3.1"
                    await offer(client, card)
                case _:
                    assert_never(phase)


def main(phase: Phase) -> None:
    """Open or finish a 1.3.0 restart canary, or verify a fresh 1.3.1 offer."""
    anyio.run(run_phase, phase)


if __name__ == "__main__":
    typer.run(main)
