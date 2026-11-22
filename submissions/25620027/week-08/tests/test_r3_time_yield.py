"""Shops are also scaled by our deals per second of order time with them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.buyer import TIME_CEILING, TIME_FLOOR, time_weighter
from r3.events import EventLedger
from r3.market_files import ShopEntry
from tests.r3_payments import paid

if TYPE_CHECKING:
    from pathlib import Path


def test_time_yield_splits_each_orders_elapsed_by_attempt(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    # One order: Slow refuses after 12 s, then Fast agrees at 15 s elapsed.
    for name, outcome, elapsed in (("Slow", "no_deal", 12.0), ("Fast", "agreed", 15.0)):
        events.record_attempt(
            order="o-1",
            shop=f"https://{name.lower()}.test",
            shop_name=name,
            sku="G1",
            list_price=10000,
            price=6000 if outcome == "agreed" else None,
            outcome=outcome,
            elapsed=elapsed,
        )
    paid(events, "o-1", "https://fast.test", "G1", ok=True)
    assert events.time_yield(6.0) == {"slow": (12.0, 0), "fast": (3.0, 1)}
    # 11/15: an agreement the checkout refused is not a deal.
    events.record_attempt(
        order="o-2",
        shop="https://fast.test",
        shop_name="Fast",
        sku="G1",
        list_price=10000,
        price=6000,
        outcome="agreed",
        elapsed=4.0,
    )
    paid(events, "o-2", "https://fast.test", "G1", ok=False)
    assert events.time_yield(6.0)["fast"] == (7.0, 1)


def test_slow_refusers_sink_and_quick_closers_rise() -> None:
    shops = [
        ShopEntry(name="YAHO", url="http://yaho.test/"),
        ShopEntry(name="Dragon", url="http://dragon.test"),
        ShopEntry(name="New", url="http://new.test"),
    ]
    # 11/14 06:5x numbers: 1,618 s for 17 deals against 283 s for 27.
    spent = {"yaho": (1618.0, 17), "dragon": (283.0, 27), "other": (8506.0, 300)}
    weight, factors = time_weighter(lambda _u, _s: 1.0, shops, spent)
    assert factors["http://yaho.test"] < 0.5
    assert factors["http://dragon.test"] > 2.0
    assert factors["http://new.test"] == 1.0
    assert min(factors.values()) >= TIME_FLOOR
    assert max(factors.values()) <= TIME_CEILING
    assert weight("http://yaho.test", "G1") == factors["http://yaho.test"]


def test_no_history_leaves_weights_alone() -> None:
    shops = [ShopEntry(name="A", url="http://a.test")]
    weight, factors = time_weighter(lambda _u, _s: 2.0, shops, {})
    assert factors == {}
    assert weight("http://a.test", "G1") == 2.0
