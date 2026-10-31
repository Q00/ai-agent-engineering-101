"""The stock seller answers real A2A requests from inventory.json in milliseconds."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio
import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts import market_kit as kit

from market_a2a.receipts import ReceiptSigner
from market_a2a.remote import Remote
from r3.seller import create_r3_app
from tests.r3_live import live_app
from tests.r3_stock import KIMCHI, LAMP, MILK, unit, write_market

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from starlette.types import ASGIApp


def shop(
    tmp_path: Path,
    units: list[dict[str, object]],
    *,
    market_round: int = 3,
    key: Ed25519PrivateKey | None = None,
) -> AbstractAsyncContextManager[str]:
    folder = write_market(tmp_path / ".market", units, market_round=market_round)
    signer = ReceiptSigner(key if key is not None else Ed25519PrivateKey.generate())

    def build(url: str) -> ASGIApp:
        return create_r3_app(
            url + "/",
            "Test Shop",
            signer=signer,
            emit=lambda _m: None,
            folder=folder,
            policy_file=tmp_path / "r3-policy.json",
            events_file=tmp_path / "events.sqlite",
        )

    return live_app(build)


def test_sku_request_quotes_from_stock_and_signed_receipt_names_the_unit(
    tmp_path: Path,
) -> None:
    key = Ed25519PrivateKey.generate()
    now = time.time()
    units = [unit(LAMP, 1, grade="A", now=now), unit(KIMCHI, 1, grade="A", now=now)]

    async def run() -> None:
        async with (
            shop(tmp_path, units, key=key) as url,
            httpx2.AsyncClient(timeout=10) as client,
        ):
            remote = Remote(client, lambda _m: None)
            endpoint = (await remote.card(url)).endpoint()
            started = time.monotonic()
            task = await remote.send(
                endpoint, f"I want to buy LED 스탠드 램프 ({LAMP})"
            )
            # Then: the ask is metadata and text, within the policy band.
            assert task.status.state == "TASK_STATE_INPUT_REQUIRED"
            ask = task.asking_price()
            assert ask == round(40000 * 0.95)
            assert "asking" in task.observation()
            # When: offer above the accept line (cost 26000 * 1.15 = 29900).
            done = await remote.send(endpoint, "30000", task)
            assert time.monotonic() - started < 2.0
            assert done.status.state == "TASK_STATE_COMPLETED"
            receipt = done.artifacts[0].parts[0].data
            assert receipt is not None
            assert receipt["item"] == LAMP
            assert receipt["price"] == 30000
            assert receipt["unit"] == f"u-{LAMP}-1"
            assert receipt["claim"] == "A"
            assert receipt["warranty_hours"] == 6
            assert kit.verify_seller(
                kit.pub_b64(key),
                {
                    "shop": receipt["shop"],
                    "item": receipt["item"],
                    "price": 30000,
                    "task": done.id,
                    "sig_seller": receipt["sig_seller"],
                },
            )

    anyio.run(run)


def test_low_offers_get_decreasing_counters_then_rejection_and_unknown_items_refused(
    tmp_path: Path,
) -> None:
    now = time.time()

    async def run() -> None:
        async with (
            shop(tmp_path, [unit(LAMP, 1, grade="B", now=now)]) as url,
            httpx2.AsyncClient(timeout=10) as client,
        ):
            remote = Remote(client, lambda _m: None)
            endpoint = (await remote.card(url)).endpoint()
            task = await remote.send(endpoint, LAMP)
            asks = [task.asking_price()]
            for _ in range(6):
                task = await remote.send(endpoint, "100", task)
                if task.status.state != "TASK_STATE_INPUT_REQUIRED":
                    break
                asks.append(task.asking_price())
            # Then: counters only decrease, never below cost*1.15, then rejection.
            assert task.status.state == "TASK_STATE_REJECTED"
            numeric = [a for a in asks if a is not None]
            assert numeric == sorted(numeric, reverse=True)
            assert min(numeric) >= round(20000 * 1.15)
            # And: a product we do not stock is refused at once.
            other = await remote.send(endpoint, "I want to buy a sofa")
            assert other.status.state == "TASK_STATE_REJECTED"

    anyio.run(run)


def test_one_unit_is_never_promised_to_two_open_negotiations(tmp_path: Path) -> None:
    now = time.time()

    async def run() -> None:
        async with (
            shop(tmp_path, [unit(MILK, 1, grade="A", now=now)]) as url,
            httpx2.AsyncClient(timeout=10) as client,
        ):
            remote = Remote(client, lambda _m: None)
            endpoint = (await remote.card(url)).endpoint()
            first = await remote.send(endpoint, MILK)
            second = await remote.send(endpoint, MILK)
            assert first.status.state == "TASK_STATE_INPUT_REQUIRED"
            assert second.status.state == "TASK_STATE_REJECTED"
            # When: the first buyer accepts the ask, the unit stays committed.
            done = await remote.send(endpoint, str(first.asking_price()), first)
            assert done.status.state == "TASK_STATE_COMPLETED"
            third = await remote.send(endpoint, MILK)
            assert third.status.state == "TASK_STATE_REJECTED"

    anyio.run(run)


def test_before_round_three_the_seller_sells_the_week07_items(tmp_path: Path) -> None:
    async def run() -> None:
        async with (
            shop(tmp_path, [], market_round=2) as url,
            httpx2.AsyncClient(timeout=10) as client,
        ):
            remote = Remote(client, lambda _m: None)
            endpoint = (await remote.card(url)).endpoint()
            task = await remote.send(endpoint, "I want to buy a lamp")
            assert task.asking_price() == 110
            done = await remote.send(endpoint, "offer lamp 100", task)
            assert done.status.state == "TASK_STATE_COMPLETED"
            receipt = done.artifacts[0].parts[0].data
            assert receipt is not None
            assert receipt["item"] == "lamp"
            assert receipt["price"] == 100
            assert "unit" not in receipt

    anyio.run(run)
