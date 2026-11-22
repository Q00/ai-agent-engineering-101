"""Goods offers are scaled by each shop's recent close rate on the order's category."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.buyer import CLOSE_CEILING, CLOSE_FLOOR, close_scaled, close_weighter
from r3.events import EventLedger
from r3.market_files import Order, ShopEntry
from tests.r3_payments import paid
from tests.r3_stock import CURTAIN
from tests.test_r3_big_orders import Peers, prepare, run_buy

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def attempts(  # noqa: PLR0913, PLR0917 - one shop's history
    events: EventLedger, name: str, sku: str, tries: int, deals: int, unpaid: int = 0
) -> None:
    """Record ``tries`` attempts at ``name`` on ``sku``, ``deals`` of them agreed.

    The last ``unpaid`` agreements failed at checkout.
    """
    for number in range(tries):
        if number < deals:
            paid(
                events,
                f"{name}-{sku}-{number}",
                f"https://tunnel-{number}.trycloudflare.com",
                sku,
                ok=number < deals - unpaid,
            )
        events.record_attempt(
            order=f"{name}-{sku}-{number}",
            shop=f"https://tunnel-{number}.trycloudflare.com",
            shop_name=name,
            sku=sku,
            list_price=10000,
            price=6000 if number < deals else None,
            outcome="agreed" if number < deals else "no_deal",
            reason="" if number < deals else "seller stays above the emergency line",
            elapsed=3.0,
        )


def test_close_counts_group_by_name_over_the_category_skus(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    attempts(events, "Dragon", "G1", 4, 2)
    attempts(events, "dragon", "G2", 2, 1)
    attempts(events, "Dragon", "F9", 5, 5)  # another category
    assert events.close_counts(6.0, ["G1", "G2"]) == {"dragon": (6, 3)}
    assert events.close_counts(6.0, []) == {}
    # 11/15: an agreement whose checkout failed is not a close.
    attempts(events, "Town", "G1", 4, 3, unpaid=2)
    assert events.close_counts(6.0, ["G1"])["town"] == (4, 1)


def test_a_shop_that_never_closes_falls_to_the_floor_and_a_closer_rises() -> None:
    shops = [
        ShopEntry(name="Wall", url="http://wall.test/"),
        ShopEntry(name="Closer", url="http://closer.test"),
        ShopEntry(name="New", url="http://new.test"),
    ]
    weight, factors = close_weighter(
        lambda _u, _s: 2.0, shops, {"wall": (300, 0), "closer": (40, 20)}
    )
    assert factors["http://wall.test"] == CLOSE_FLOOR
    assert 2.0 < factors["http://closer.test"] <= CLOSE_CEILING
    # A shop we have not tried stays at the category mean.
    assert factors["http://new.test"] == 1.0
    assert weight("http://wall.test/", "G1") == 2.0 * CLOSE_FLOOR
    assert weight("http://other.test", "G1") == 2.0


def test_the_shop_that_closes_on_this_category_is_asked_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the first shop walled us 30 times on this category in the last
    # hours, while second.test closed 6 of 10.
    folder = prepare(tmp_path, ("first.test", "second.test"))
    events = EventLedger(tmp_path / "events.sqlite")
    attempts(events, "Peer", CURTAIN, 30, 0)
    attempts(events, "second.test", CURTAIN, 10, 6)
    peers = Peers({})
    # When: the order runs.
    _ = run_buy(folder, peers, monkeypatch)
    # Then: second.test is asked first, and the log names the ranking.
    assert peers.visits[0] == "second.test"
    assert any("close_rate second.test=" in line for line in peers.logs)


def test_without_history_the_usual_order_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = prepare(tmp_path, ("first.test", "second.test"))
    peers = Peers({})
    _ = run_buy(folder, peers, monkeypatch)
    assert peers.visits[0] == "first.test"


def test_food_and_goods_round_three_orders_are_scaled_but_not_round_two() -> None:
    assert close_scaled(Order(kind="food", acceptable=("F1",)))
    assert close_scaled(Order(kind="goods", acceptable=("G1",)))
    assert not close_scaled(Order(kind="food"))
