"""Candidate exclusions are not falsely counted as empty market shelves."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from r3.events import EventLedger
from tests.test_r3_big_orders import Peers, prepare, run_buy

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("listed", "expected"),
    [(True, "no eligible candidate"), (False, "out of stock")],
)
def test_no_candidate_classification_uses_actual_listed_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, listed: bool, expected: str
) -> None:
    # Given: a slow shop is excluded despite having stock, or no shop lists stock.
    folder = prepare(tmp_path, ("peer.test",) if listed else ())
    events = EventLedger(tmp_path / "events.sqlite")
    for number in range(2):
        events.record_attempt(
            order=f"earlier-{number}",
            shop="http://peer.test",
            shop_name="Peer",
            sku="sku",
            list_price=200000,
            price=None,
            outcome="no_deal",
            reason="shop timeout",
            market_round=3,
        )
    peers = Peers({})
    # When: the real buyer cannot choose an eligible shop.
    outcome = run_buy(folder, peers, monkeypatch)
    # Then: reporting distinguishes a candidate exclusion from no listed stock.
    with closing(sqlite3.connect(events.path)) as db:
        (reason,) = TypeAdapter(tuple[str]).validate_python(
            db.execute(
                "SELECT reason FROM buyer_attempts ORDER BY id DESC LIMIT 1"
            ).fetchone()
        )
    assert reason == expected
    assert outcome.receipt is None
    assert peers.visits == []
