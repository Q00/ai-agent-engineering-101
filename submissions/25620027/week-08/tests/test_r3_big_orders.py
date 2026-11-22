"""Large orders share deadlines and retry only safe initial negotiations."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from functools import partial
from typing import TYPE_CHECKING, final

import anyio
import httpx2
import pytest

from market_a2a.remote import Remote, Task
from market_a2a.security import SecurityEvents
from r3 import buyer
from r3.events import EventLedger
from r3.market_files import ShopEntry, read_shops
from r3.policy import BuyerPolicy
from tests.r3_payments import paid
from tests.r3_stock import CURTAIN, write_market
from tests.test_r3_fast_buyer import SellerPeer
from tests.test_r3_top5_buyer_safety import CertifiedPeer

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from r3.market_files import Order
    from r3.model_client import ModelClient


def last_choice(values: list[str]) -> str:
    """Choose the last eligible exploratory shop deterministically."""
    return values[-1]


def rival_fixture(_board: dict[str, object] | None, _ours: str) -> dict[str, str]:
    """Mark only the first shop as a direct rival."""
    return {"Peer": "rival"}


@final
class Peers:
    """Exercise real Remote HTTP encoding with deterministic local peers."""

    def __init__(self, modes: dict[str, str]) -> None:
        """Select initial-response and commitment behaviors by endpoint."""
        self.modes = modes
        self.visits: list[str] = []
        self.logs: list[str] = []
        self.offers: list[str] = []
        self.peer = CertifiedPeer(ask=150000, receipt_sku=CURTAIN, warranty_hours=72)
        self.peer.sku = CURTAIN
        self.peer.accept_at = 140000

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Respond locally or simulate loss at the requested protocol phase."""
        host = request.url.host
        mode = self.modes.get(host, "deal")
        if request.method == "GET":
            self.visits.append(host)
            if mode == "slow_card":
                await anyio.sleep(30)
        elif b'"taskId"' in request.content:
            self.offers.append(host)
            if mode == "uncertain":
                message = "response lost"
                raise httpx2.ReadTimeout(message, request=request)
        elif mode == "slow":
            await anyio.sleep(30)
        if mode == "pending" and request.method != "GET":
            return httpx2.Response(
                200,
                json={
                    "result": {
                        "task": {
                            "id": "t-1",
                            "contextId": "c-1",
                            "status": {
                                "state": "TASK_STATE_INPUT_REQUIRED",
                                "message": {
                                    "parts": [
                                        {"text": "Counteroffer: 150000."},
                                        {"data": {"grade": "A", "warranty_hours": 72}},
                                    ],
                                    "metadata": {"ask": 150000},
                                },
                            },
                        }
                    }
                },
            )
        response = self.peer.handle(request)
        if request.method != "GET" and mode == "empty":
            response = httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {
                        "task": {
                            "id": "empty",
                            "contextId": "empty-context",
                            "status": {
                                "state": "TASK_STATE_REJECTED",
                                "message": {"parts": [{"text": "out of stock"}]},
                            },
                        }
                    },
                },
            )
        return response


def prepare(
    tmp_path: Path,
    names: tuple[str, ...],
    *,
    budget: int = 200000,
    expires: float | None = None,
) -> Path:
    """Write a kit-shaped order with interchangeable goods at separate shops."""
    folder = write_market(tmp_path / ".market", [])
    _ = (folder / "order.json").write_text(
        json.dumps(
            {
                "order": "big-safety-2"
                if names == ("first.test", "second.test")
                else "big-1",
                "budget": budget,
                "item": "curtain",
                "kind": "goods",
                "acceptable": [CURTAIN],
                "expires": expires,
            }
        )
    )
    _ = (folder / "shops.json").write_text(
        json.dumps(
            [
                {
                    "name": "Peer" if index == 0 else name,
                    "url": f"http://{name}",
                    "offers": [{"sku": CURTAIN, "qty": 3}],
                }
                for index, name in enumerate(names)
            ]
        )
    )
    return folder


def run_buy(
    folder: Path, peers: Peers, monkeypatch: pytest.MonkeyPatch
) -> buyer.Outcome:
    """Use actual buyer and ledger with an in-process HTTP transport, no market."""
    monkeypatch.setattr(
        buyer,
        "create_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(peers.handle)),
    )
    return anyio.run(
        partial(
            buyer.buy,
            folder / "shops.json",
            "curtain",
            200000,
            folder / "order.json",
            peers.logs.append,
            folder=folder,
            policy_file=folder.parent / "policy.json",
            events_file=folder.parent / "events.sqlite",
        )
    )


def test_first_response_six_seconds_then_next_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"))
    peers = Peers({"first.test": "slow"})
    began = time.monotonic()
    result = run_buy(folder, peers, monkeypatch)
    assert 6 <= time.monotonic() - began < 9
    assert peers.visits == ["first.test", "second.test"]
    assert result.agreed_price is not None
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        assert connection.execute(
            "SELECT reason FROM buyer_attempts ORDER BY id LIMIT 1"
        ).fetchone() == ("shop timeout",)


def test_distinct_shops_maximum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = tuple(f"s{i:02d}.test" for i in range(buyer.BIG_ORDER_SHOPS + 2))
    folder = prepare(tmp_path, ("s00.test", *names))
    peers = Peers(dict.fromkeys(names, "empty"))
    result = run_buy(folder, peers, monkeypatch)
    assert result.receipt is None
    assert len(peers.visits) == buyer.BIG_ORDER_SHOPS
    assert len(set(peers.visits)) == buyer.BIG_ORDER_SHOPS


def test_accept_response_loss_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"))
    peers = Peers({"first.test": "uncertain"})
    result = run_buy(folder, peers, monkeypatch)
    assert result.receipt is None
    assert peers.visits == ["first.test"]
    assert peers.offers == ["first.test"]


def test_expired_order_never_contacts_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test",), expires=time.time() - 1)
    peers = Peers({})
    result = run_buy(folder, peers, monkeypatch)
    assert result.receipt is None
    assert peers.visits == []


def test_retry_cutoff_handoff_and_commit() -> None:
    clock = buyer.BigOrderClock(100, 120)
    assert clock.retry_allowed(113.99, 2)
    assert not clock.retry_allowed(114, 2)
    assert not clock.retry_allowed(110, buyer.BIG_ORDER_SHOPS)
    assert not buyer.BigOrderClock(100, 102).retry_allowed(100, 1)
    clock.committed.add("shop")
    assert not clock.retry_allowed(110, 1)


@pytest.mark.parametrize(
    ("budget", "expected"), [(200000, "second.test"), (199999, "first.test")]
)
def test_slow_shop_exclusion_only_for_big_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int, expected: str
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"), budget=budget)
    ledger = EventLedger(tmp_path / "events.sqlite")
    for number in range(2):
        ledger.record_attempt(
            order=f"old-{number}",
            shop="http://first.test",
            shop_name="Peer",
            sku=CURTAIN,
            list_price=236900,
            price=None,
            outcome="agreed",
            reason="shop timeout",
            market_round=3,
        )
        # 11/15: a close counts only when the kit paid it.
        paid(ledger, f"old-{number}", "http://first.test", CURTAIN, ok=True)
    peers = Peers({})
    _ = run_buy(folder, peers, monkeypatch)
    assert peers.visits[0] == expected


@pytest.mark.parametrize(
    ("budget", "first"), [(200000, "first.test"), (199999, "second.test")]
)
def test_big_skips_exploration_small_still_explores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int, first: str
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"), budget=budget)
    monkeypatch.setattr(buyer, "random", random)
    monkeypatch.setattr(random, "random", lambda: 0.0)
    monkeypatch.setattr(random, "choice", last_choice)
    peers = Peers({})
    _ = run_buy(folder, peers, monkeypatch)
    assert peers.visits[0] == first, peers.logs


@pytest.mark.parametrize(
    ("budget", "first"), [(200000, "second.test"), (199999, "second.test")]
)
def test_rival_penalty_applies_to_big_and_small_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int, first: str
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"), budget=budget)
    monkeypatch.setattr(random, "random", lambda: 0.99)
    monkeypatch.setattr(buyer, "rival_view", rival_fixture)
    peers = Peers({})
    _ = run_buy(folder, peers, monkeypatch)
    assert peers.visits[0] == first, peers.logs


def test_first_reply_includes_card_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"))
    monkeypatch.setattr(buyer, "BIG_FIRST_REPLY_SECONDS", 0.02)
    peers = Peers({"first.test": "slow_card"})
    result = run_buy(folder, peers, monkeypatch)
    assert peers.visits == ["first.test", "second.test"]
    assert result.agreed_price is not None


def test_timeout_memory_expires_and_ignores_overall_deadline(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "events.sqlite")
    for number, reason in enumerate(("shop timeout", "shop timeout", "timed out")):
        ledger.record_attempt(
            order=f"old-{number}",
            shop="http://peer.test/",
            shop_name="Peer",
            sku=CURTAIN,
            list_price=236900,
            price=None,
            outcome="no_deal",
            reason=reason,
            market_round=3,
        )
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        _ = connection.execute(
            "UPDATE buyer_attempts SET ts=? WHERE order_id='old-0'",
            (time.time() - 21601,),
        )
    assert ledger.shop_timeout_counts() == {"http://peer.test": 1}


def test_own_deadline_is_not_a_shop_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"), expires=time.time() + 1.6)
    peers = Peers({"first.test": "slow"})
    result = run_buy(folder, peers, monkeypatch)
    assert result.receipt is None
    assert peers.visits == ["first.test"]
    assert EventLedger(tmp_path / "events.sqlite").shop_timeout_counts() == {}


def test_root_clock_makes_late_offer_accept_the_quote() -> None:
    async def scenario() -> list[int]:
        peer = SellerPeer(ask=190000, accept_at=190000, sku=CURTAIN)
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(peer.handle)
        ) as client:
            remote = Remote(client, lambda _: None, SecurityEvents())
            shop = ShopEntry(name="Peer", url="http://peer.test")
            choice = buyer.Choice(shop, CURTAIN, "curtain", 236900, "buy curtain", 1.0)
            task = await remote.send("http://peer.test/rpc", choice.request)
            _ = await buyer.negotiate(
                remote,
                "http://peer.test/rpc",
                choice,
                200000,
                policy=BuyerPolicy(),
                emit=lambda _: None,
                clock=lambda: 15.0,
                opened=task,
                order_clock=buyer.BigOrderClock(0, 20),
            )
        return peer.offers

    assert anyio.run(scenario) == [190000]


def test_one_interpreter_read_across_retried_shops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def interpret(
        _reader: ModelClient | None,
        _task: Task,
        choice: buyer.Choice,
        _emit: Callable[[str], None],
        _order: Order | None = None,
    ) -> tuple[int | None, bool, str]:
        calls.append(choice.shop.url)
        return None, True, ""

    async def scenario() -> None:
        peer = SellerPeer(ask=150000, accept_at=140000, sku=CURTAIN)
        clock = buyer.BigOrderClock(0, 20)
        unknown = Task.model_validate(
            {
                "id": "t-1",
                "contextId": "c-1",
                "status": {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": {"parts": [{"text": "Please describe your needs"}]},
                },
            }
        )
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(peer.handle)
        ) as client:
            remote = Remote(client, lambda _: None, SecurityEvents())
            for name in ("first", "second"):
                choice = buyer.Choice(
                    ShopEntry(name=name, url=f"http://{name}.test"),
                    CURTAIN,
                    "curtain",
                    236900,
                    "buy curtain",
                    1,
                )
                _ = await buyer.negotiate(
                    remote,
                    "http://peer.test/rpc",
                    choice,
                    200000,
                    policy=BuyerPolicy(),
                    emit=lambda _: None,
                    clock=lambda: 1.0,
                    opened=unknown,
                    order_clock=clock,
                )

    monkeypatch.setattr(buyer, "prose_ask", interpret)
    anyio.run(scenario)
    assert calls == ["http://first.test"]


def test_accept_reply_still_pending_does_not_move_to_another_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"))
    peers = Peers({"first.test": "pending"})
    result = run_buy(folder, peers, monkeypatch)
    assert result.receipt is None
    assert peers.visits == ["first.test"]


def test_prework_consumes_the_order_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test",), expires=time.time() + 1.52)
    original = read_shops

    def delayed(path: Path) -> tuple[ShopEntry, ...]:
        time.sleep(0.03)
        return original(path)

    monkeypatch.setattr(buyer, "read_shops", delayed)
    peers = Peers({})
    result = run_buy(folder, peers, monkeypatch)
    assert result.receipt is None
    assert peers.visits == []


def test_empty_shelf_orders_get_a_fourth_and_fifth_shop_while_time_remains() -> None:
    # 11/10: most orders lost to empty shelves stopped at shop three with ~4 s used.
    clock = buyer.BigOrderClock(100, 126)
    assert clock.retry_allowed(104, 3)
    assert clock.retry_allowed(108, 4)
    assert clock.retry_allowed(112, 9)  # 11/10 15:50: time, not count, bounds tries
    assert not clock.retry_allowed(112, buyer.BIG_ORDER_SHOPS)
    assert not clock.retry_allowed(119, 3)  # past 70 % of the order's time
    # 11/11: no new shop after 14 s even inside the 70 % share (orders queue).
    assert clock.retry_allowed(113.9, 3)
    assert not clock.retry_allowed(114.1, 3)
