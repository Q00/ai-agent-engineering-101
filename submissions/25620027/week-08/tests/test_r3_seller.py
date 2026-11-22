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
            # Cheap A goods cap at 0.70 of list (28000), below the pilot's
            # accept line, so they open at cost 26000 * 1.10 = 28600 (A_LOW_FLOOR).
            assert ask == 28600
            assert "asking" in task.observation()
            # When: offer above the pilot acceptance line (28600).
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


def test_below_market_line_only_when_the_public_ledger_backs_it(
    tmp_path: Path,
) -> None:
    import sqlite3  # noqa: PLC0415 - test only
    from contextlib import closing  # noqa: PLC0415 - test only

    from r3.ledger_db import LedgerDB, ledger_path  # noqa: PLC0415 - test only

    def seed(folder: Path, price: int) -> None:
        path = ledger_path(folder.resolve())
        _ = LedgerDB(path)
        with closing(sqlite3.connect(path)) as db, db:
            for seq in range(5):
                _ = db.execute(
                    "INSERT INTO rows (seq, ts, kind, sku, amount) VALUES (?,?,?,?,?)",
                    (seq + 1, time.time() - 60, "settle", LAMP, price),
                )

    async def opening(folder_root: Path, price: int) -> str:
        units = [unit(LAMP, 1, grade="A", now=time.time())]
        seed(write_market(folder_root / ".market", units), price)
        async with (
            shop(folder_root, units) as url,
            httpx2.AsyncClient(timeout=10) as client,
        ):
            remote = Remote(client, lambda _m: None)
            endpoint = (await remote.card(url)).endpoint()
            task = await remote.send(endpoint, f"I want to buy {LAMP}")
            return task.observation()

    # Given: five recent settlements well above our ask; the line appears, digit-free.
    above = anyio.run(opening, tmp_path / "a", 90000)
    assert "Priced below this item's recent market median." in above
    middle = above.splitlines()[1:-1]
    assert not any(ch.isdigit() for line in middle for ch in line)
    # And: when the market settles below our ask, no claim is made.
    below = anyio.run(opening, tmp_path / "b", 1000)
    assert "market median" not in below


def test_grade_c_goes_last_unless_the_buyer_accepts_c() -> None:
    from r3.market_files import Unit  # noqa: PLC0415 - local fixture
    from r3.seller import Request, pick_unit  # noqa: PLC0415

    now = time.time()
    # Given: a C lamp that arrived first and an A lamp that arrived later.
    old_c = Unit.model_validate(unit(LAMP, 1, grade="C", now=now, supplied_hours_ago=5))
    new_a = Unit.model_validate(unit(LAMP, 2, grade="A", now=now, supplied_hours_ago=1))
    plain = Request(skus=(LAMP,), offer=None, text=f"I want to buy lamp ({LAMP})")
    # Then: the kit-payable A unit goes first although the C unit is older (a
    # non-plain form; the plain goods ask gets C first since 11/17 07:5x)...
    asks = Request(
        skus=(LAMP,), offer=None, text=f"I want to buy lamp ({LAMP}). Price please."
    )
    assert pick_unit((old_c, new_a), asks, now, set()) is new_a
    assert pick_unit((old_c, new_a), plain, now, set()) is old_c
    # ...the C unit goes first when the buyer says it takes C...
    takes_c = Request(
        skus=(LAMP,), offer=None, text=f"I want to buy lamp ({LAMP}), grade C is fine"
    )
    assert pick_unit((old_c, new_a), takes_c, now, set()) is old_c
    # ...and when C is all that fits it is still offered (disclosed as C).
    assert pick_unit((old_c,), plain, now, set()) is old_c
    # And: within one grade the unit that expires first still wins (food).
    soon = Unit.model_validate(
        unit(KIMCHI, 1, grade="A", now=now, shelf_left_ratio=0.85)
    )
    later = Unit.model_validate(
        unit(KIMCHI, 2, grade="A", now=now, shelf_left_ratio=0.95)
    )
    food = Request(skus=(KIMCHI,), offer=None, text=f"I want to buy kimchi ({KIMCHI})")
    assert pick_unit((later, soon), food, now, set()) is soon
