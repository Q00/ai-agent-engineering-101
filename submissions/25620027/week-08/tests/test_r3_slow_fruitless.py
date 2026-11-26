"""Slow shops that almost never sell to us stop taking the order's seconds."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

from r3.events import FRUITLESS_MIN_TRIES, EventLedger
from tests.test_r3_big_orders import Peers, prepare, run_buy

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def record(
    ledger: EventLedger, shop: str, tries: int, seconds: float, wins: int = 0
) -> None:
    """Each try is its own order after a 1 s first try at another shop."""
    for number in range(tries):
        order = f"{shop}-{number}"
        ledger.record_attempt(
            order=order,
            shop="http://other.test",
            shop_name="Other",
            sku="G1",
            list_price=10000,
            price=None,
            outcome="no_deal",
            elapsed=1.0,
        )
        ledger.record_attempt(
            order=order,
            shop=f"http://{shop}.test/",
            shop_name=shop,
            sku="G1",
            list_price=10000,
            price=None,
            outcome="agreed" if number < wins else "no_deal",
            elapsed=1.0 + seconds,
        )


def test_slow_shop_that_never_sells_is_flagged(tmp_path: Path) -> None:
    # Given: 11/10 kcwarni-like shop, 7 s a try and no deal in many tries.
    ledger = EventLedger(tmp_path / "events.sqlite")
    record(ledger, "slow", FRUITLESS_MIN_TRIES, 7.0)
    # Then: its normalized URL is flagged; the fast first shop is not.
    assert ledger.slow_fruitless_shops() == frozenset({"http://slow.test"})


def test_slow_shop_that_sells_is_kept(tmp_path: Path) -> None:
    # Given: 11/10 olstjr-like shop, 6 s a try but half its tries agreed.
    ledger = EventLedger(tmp_path / "events.sqlite")
    record(ledger, "worth", 20, 6.0, wins=10)
    assert ledger.slow_fruitless_shops() == frozenset()


def test_fast_fruitless_and_thin_evidence_are_kept(tmp_path: Path) -> None:
    # Given: a fast refuser and a slow shop seen too few times to judge.
    ledger = EventLedger(tmp_path / "events.sqlite")
    record(ledger, "fast", 30, 1.5)
    record(ledger, "thin", FRUITLESS_MIN_TRIES - 1, 7.0)
    assert ledger.slow_fruitless_shops() == frozenset()


def test_old_tries_expire(tmp_path: Path) -> None:
    # Given: the slow fruitless tries are older than the window.
    ledger = EventLedger(tmp_path / "events.sqlite")
    record(ledger, "slow", FRUITLESS_MIN_TRIES, 7.0)
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        _ = connection.execute("UPDATE buyer_attempts SET ts=?", (time.time() - 21601,))
    # Then: the shop gets tried again (a shop that recovers is not shut out).
    assert ledger.slow_fruitless_shops() == frozenset()


def test_buyer_skips_a_flagged_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the first-ranked shop is slow and fruitless, the second can sell.
    folder = prepare(tmp_path, ("first.test", "second.test"))
    record(EventLedger(tmp_path / "events.sqlite"), "first", FRUITLESS_MIN_TRIES, 7.0)
    peers = Peers({})
    # When: the order runs.
    result = run_buy(folder, peers, monkeypatch)
    # Then: the order goes straight to the second shop and agrees there.
    assert peers.visits == ["second.test"]
    assert result.agreed_price is not None
