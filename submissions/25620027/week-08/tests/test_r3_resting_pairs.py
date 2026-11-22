"""A shop/SKU pair that keeps failing at the receipt or checkout rests 30 minutes."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING

from r3.buyer import REPEAT_REST_MINUTES, REPEAT_REST_ORDERS
from r3.events import EventLedger

if TYPE_CHECKING:
    from pathlib import Path

REASON = "receipt expected credit below the line or quality (claim B, warranty 3 h)"


def refuse(events: EventLedger, order: str, shop: str, sku: str, reason: str) -> None:
    events.record_attempt(
        order=order,
        shop=shop,
        shop_name="dae-yangkim",
        sku=sku,
        list_price=10000,
        price=None,
        outcome="no_deal",
        reason=reason,
        elapsed=5.0,
    )


def test_same_reason_in_two_orders_rests_only_that_pair(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    shop = "https://dae.test/"
    refuse(events, "o-1", shop, "G1", REASON)
    # One refusal is not a pattern yet.
    assert events.repeated_receipt_refusals(REPEAT_REST_MINUTES) == frozenset()
    refuse(events, "o-2", shop, "G1", REASON)
    refuse(events, "o-3", shop, "G2", REASON)
    other = REASON.replace("claim B", "claim A")
    refuse(events, "o-4", shop, "G3", REASON)
    refuse(events, "o-5", shop, "G3", other)
    rested = events.repeated_receipt_refusals(REPEAT_REST_MINUTES, REPEAT_REST_ORDERS)
    # Then: only G1 (same reason, two orders) rests; G2 once, G3 two reasons.
    assert rested == frozenset({("https://dae.test", "G1")})


def test_checkout_409_on_the_unit_rests_the_pair(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    with closing(sqlite3.connect(events.path)) as db, db:
        _ = db.execute(
            "INSERT INTO payments (order_id, ts, shop, item, price, paid, ledger,"
            " reason) VALUES (?,?,?,?,?,?,?,?)",
            (
                "o-9",
                time.time(),
                "https://town.test/",
                "G7",
                9000,
                0,
                None,
                (
                    'checkout refused: 409 {"error":"unit u-a4ee3e5ee801 is not in'
                    ' stock for this order"}'
                ),
            ),
        )
    assert events.stale_unit_pairs(REPEAT_REST_MINUTES) == frozenset(
        {("https://town.test", "G7")}
    )


def test_two_none_in_stock_orders_rest_the_pair(tmp_path: Path) -> None:
    events = EventLedger(tmp_path / "events.sqlite")
    shop = "https://dae.test/"
    miss = "NoStockError: Sorry, we have none of that in stock."
    refuse(events, "o-1", shop, "G9", miss)
    assert events.repeated_stock_misses(REPEAT_REST_MINUTES) == frozenset()
    refuse(events, "o-2", shop, "G9", miss)
    assert events.repeated_stock_misses(
        REPEAT_REST_MINUTES, REPEAT_REST_ORDERS
    ) == frozenset({("https://dae.test", "G9")})
