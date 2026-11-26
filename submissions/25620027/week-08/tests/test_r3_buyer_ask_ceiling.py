"""A counteroffer must never rise above the seller's current valid ask."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, cast

import anyio
import pytest

from market_a2a.remote import Remote, Task
from r3.buyer import BigOrderClock, Choice, negotiate
from r3.market_files import Order, ShopEntry
from r3.policy import BuyerPolicy

if TYPE_CHECKING:
    from collections.abc import Sequence


def needed(ask: int) -> Task:
    return Task.model_validate(
        {
            "id": "t",
            "contextId": "c",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {
                    "messageId": "m",
                    "role": "ROLE_AGENT",
                    "parts": [
                        {"text": f"Counteroffer: {ask}. Grade A."},
                        {"data": {"grade": "A", "warranty_hours": 72}},
                    ],
                    "metadata": {"ask": ask},
                },
            },
        }
    )


class FallingQuotePeer:
    """Record proposed prices against the ask actually shown for that turn."""

    def __init__(
        self, asks: Sequence[int], *, final: str = "TASK_STATE_COMPLETED"
    ) -> None:
        """Use an exact quote sequence and terminal reply without network calls."""
        self.asks = tuple(asks)
        self.final = final
        self.now = 0.0
        self.sent: list[tuple[int, int]] = []

    async def send(
        self, _endpoint: str, message: str, _task: Task | None = None
    ) -> Task:
        """Accept only a sufficient offer, otherwise show the next quote."""
        ask = self.asks[min(len(self.sent), len(self.asks) - 1)]
        offer = int(message)
        self.sent.append((ask, offer))
        self.now += 1.0
        if offer >= ask:
            return Task.model_validate(
                {"id": "t", "contextId": "c", "status": {"state": self.final}}
            )
        return needed(self.asks[min(len(self.sent), len(self.asks) - 1)])


def run_peer(
    asks: Sequence[int],
    *,
    big: bool,
    quick: float = 0.76,
    late: float = 0.46,
    final: str = "TASK_STATE_COMPLETED",
) -> tuple[FallingQuotePeer, Task, int | None, set[str], BigOrderClock | None]:
    peer = FallingQuotePeer(asks, final=final)
    choice = Choice(
        ShopEntry(name="peer", url="http://peer.test"),
        "sku",
        "item",
        200_000,
        "item",
        100_000.0,
    )
    # Keep the same currency offer/cap while ensuring A72 credit also passes.
    policy = BuyerPolicy(
        first_offer_ratio=0.27,
        quick_accept_ratio=quick / 2,
        max_price_ratio=0.42,
        max_rounds=6,
        order_seconds=26,
        late_accept_share=late,
    )
    order_clock = BigOrderClock(0.0, 24.5) if big else None
    committed: set[str] = set()
    task, price = anyio.run(
        partial(
            negotiate,
            cast("Remote", peer),
            "http://peer.test/rpc",
            choice,
            250_000 if big else 150_000,
            policy=policy,
            emit=lambda _s: None,
            clock=lambda: peer.now,
            order=Order(kind="goods", acceptable=("sku",)),
            opened=needed(asks[0]),
            order_clock=order_clock,
            committed_shops=committed,
        )
    )
    return peer, task, price, committed, order_clock


@pytest.mark.parametrize("big", [False, True])
@pytest.mark.parametrize("quick", [0.75, 0.76])
@pytest.mark.parametrize("late", [0.25, 0.46])
def test_falling_ask_is_not_overbid(*, big: bool, quick: float, late: float) -> None:
    peer, task, price, committed, clock = run_peer(
        [80_000] * 4 + [76_500], big=big, quick=quick, late=late
    )
    assert task.status.state == "TASK_STATE_COMPLETED"
    assert price == 76_500
    assert all(offer <= ask and offer <= 84_000 for ask, offer in peer.sent)
    assert peer.sent[:4] == [(80_000, n) for n in [54_000, 67_000, 73_500, 76_750]]
    assert len(peer.sent) == 5
    assert committed == {"http://peer.test"}
    if clock is not None:
        assert clock.committed == committed


@pytest.mark.parametrize("big", [False, True])
def test_accepts_revised_ask_equal_to_previous_offer(*, big: bool) -> None:
    peer, task, price, committed, _ = run_peer([80_000] * 4 + [76_750], big=big)
    assert task.status.state == "TASK_STATE_COMPLETED"
    assert price == 76_750
    assert peer.sent[-1] == (76_750, 76_750)
    assert committed


@pytest.mark.parametrize("big", [False, True])
def test_binding_lower_ask_keeps_retry_protection_on_rejection(*, big: bool) -> None:
    peer, task, price, committed, clock = run_peer(
        [80_000] * 4 + [76_500], big=big, final="TASK_STATE_REJECTED"
    )
    assert task.status.state == "TASK_STATE_REJECTED"
    assert price is None
    assert peer.sent[-1] == (76_500, 76_500)
    assert committed == {"http://peer.test"}
    if clock is not None:
        assert not clock.retry_allowed(peer.now, 1)


@pytest.mark.parametrize("big", [False, True])
def test_unchanged_ask_keeps_existing_negotiation_sequence(*, big: bool) -> None:
    peer, task, price, _, _ = run_peer([80_000] * 7, big=big)
    assert task.status.state == "TASK_STATE_COMPLETED"
    assert price == 80_000
    assert [n for _, n in peer.sent] == [54_000, 67_000, 73_500, 76_750, 78_375, 80_000]


@pytest.mark.parametrize("big", [False, True])
def test_over_cap_ask_is_not_accepted_or_committed(*, big: bool) -> None:
    peer, task, price, committed, clock = run_peer([85_000] * 8, big=big)
    assert task.status.state != "TASK_STATE_COMPLETED"
    assert price is None
    assert all(n <= 84_000 for _, n in peer.sent)
    assert not committed
    if clock is not None:
        assert not clock.committed
