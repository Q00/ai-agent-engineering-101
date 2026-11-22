"""Ledger evidence of a cheap sale puts that shop first and lifts its penalties."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

from r3.buyer import (
    CHEAP_BOOST,
    DEAR_WEIGHT,
    cheap_first,
    ledger_cheap_pairs,
    ledger_dear_pairs,
)
from r3.events import FRUITLESS_MIN_TRIES, EventLedger
from r3.ledger_db import LEDGER_FILE
from tests.r3_stock import CURTAIN
from tests.test_r3_big_orders import Peers, prepare, run_buy
from tests.test_r3_slow_fruitless import record

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

LIST = 236900


def ledger(path: Path, rows: list[tuple[float, str, str, int, str, dict]]) -> Path:
    """Build a market ledger holding the given settle rows."""
    with sqlite3.connect(path) as con:
        _ = con.execute(
            """CREATE TABLE rows (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT,
            round INTEGER, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER,
            grade TEXT, claim TEXT, claim_honest INTEGER, data TEXT)"""
        )
        _ = con.executemany(
            "INSERT INTO rows (ts, kind, buyer, shop, sku, amount, grade, data) "
            "VALUES (?, 'settle', 'other', ?, ?, ?, ?, ?)",
            [
                (ts, shop, sku, amount, grade, json.dumps(d))
                for ts, shop, sku, amount, grade, d in rows
            ],
        )
    return path


def test_cheap_pairs_need_a_b_under_the_line_recently_and_a_warranty_for_b(
    tmp_path: Path,
) -> None:
    now = time.time()
    db = ledger(
        tmp_path / "l.sqlite",
        [
            (now - 60, "http://a.test/", "G1", 55000, "B", {"warranty_hours": 72}),
            (now - 60, "http://b.test", "G1", 55000, "B", {}),  # B, no warranty
            (now - 60, "http://c.test", "G1", 70000, "A", {}),  # above 0.59
            (now - 7200, "http://d.test", "G1", 50000, "A", {}),  # too old
            (now - 60, "http://e.test", "G1", 40000, "C", {}),  # grade C
            (now - 60, "http://f.test", "G1", 58000, "A", {}),
        ],
    )
    pairs = ledger_cheap_pairs(db, "goods", {"G1": 100000}, now)
    assert pairs == frozenset({("http://a.test", "G1"), ("http://f.test", "G1")})
    # Food takes any A/B under 0.68 of list.
    assert ("http://b.test", "G1") in ledger_cheap_pairs(
        db, "food", {"G1": 100000}, now
    )
    assert (
        ledger_cheap_pairs(tmp_path / "none.sqlite", "goods", {"G1": 1}, now)
        == frozenset()
    )


def test_cheap_first_multiplies_only_the_cheap_pair() -> None:
    weight = cheap_first(lambda _u, _s: 0.5, frozenset({("http://a.test", "G1")}))
    assert weight("http://a.test/", "G1") == 0.5 * CHEAP_BOOST
    assert weight("http://a.test", "G2") == 0.5
    assert weight("http://b.test", "G1") == 0.5


def test_a_slow_shop_that_sold_this_sku_cheaply_is_tried_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the second shop is on our slow-shop skip list, but another buyer
    # just paid it 0.50 of list for this SKU, grade A.
    folder = prepare(tmp_path, ("first.test", "second.test"))
    record(EventLedger(tmp_path / "events.sqlite"), "second", FRUITLESS_MIN_TRIES, 7.0)
    _ = ledger(
        folder.resolve().parent / LEDGER_FILE,
        [(time.time() - 60, "http://second.test", CURTAIN, LIST // 2, "A", {})],
    )
    peers = Peers({})
    # When: the order runs.
    _ = run_buy(folder, peers, monkeypatch)
    # Then: the penalty is lifted for this order and the cheap pair goes first.
    assert peers.visits[0] == "second.test"
    assert any(
        "cheap_evidence pairs=1 dear=0 lifted=second.test" in line
        for line in peers.logs
    )


def test_dear_pairs_need_two_recent_a_b_sales_all_clearly_over_the_line(
    tmp_path: Path,
) -> None:
    now = time.time()
    db = ledger(
        tmp_path / "l.sqlite",
        [
            (now - 60, "http://dear.test", "G1", 70000, "A", {}),
            (now - 120, "http://dear.test", "G1", 72000, "B", {}),
            (now - 60, "http://once.test", "G1", 80000, "A", {}),  # one sale only
            (now - 60, "http://near.test", "G1", 62000, "A", {}),  # within +0.05
            (now - 90, "http://near.test", "G1", 63000, "A", {}),
            (now - 60, "http://mixed.test", "G1", 80000, "A", {}),
            (now - 90, "http://mixed.test", "G1", 55000, "A", {}),  # one cheap
        ],
    )
    assert ledger_dear_pairs(db, "goods", {"G1": 100000}, now) == frozenset(
        {("http://dear.test", "G1")}
    )


def test_dear_pairs_go_last() -> None:
    weight = cheap_first(
        lambda _u, _s: 1.0,
        frozenset({("http://cheap.test", "G1")}),
        frozenset({("http://dear.test", "G1")}),
    )
    assert weight("http://cheap.test", "G1") == CHEAP_BOOST
    assert weight("http://dear.test", "G1") == DEAR_WEIGHT
    assert weight("http://plain.test", "G1") == 1.0


def test_a_shop_that_hung_three_times_this_hour_stays_skipped_despite_cheap_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: second.test sold this SKU cheaply to another buyer, but it left us
    # waiting three times in the last hour, under a URL it no longer uses.
    folder = prepare(tmp_path, ("first.test", "second.test"))
    events = EventLedger(tmp_path / "events.sqlite")
    for number, reason in enumerate(("shop timeout", "timed out", "shop timeout")):
        events.record_attempt(
            order=f"hung-{number}",
            shop="https://old-tunnel.trycloudflare.com",
            shop_name="Second.test",
            sku="G1",
            list_price=10000,
            price=None,
            outcome="no_deal",
            reason=reason,
            elapsed=6.0,
        )
    _ = ledger(
        folder.resolve().parent / LEDGER_FILE,
        [(time.time() - 60, "http://second.test", CURTAIN, LIST // 2, "A", {})],
    )
    peers = Peers({})
    # When: the order runs.
    _ = run_buy(folder, peers, monkeypatch)
    # Then: it never waits at second.test again this hour.
    assert "second.test" not in peers.visits
    assert any("hung_shops skipped=second.test" in line for line in peers.logs)
    assert events.hung_shop_names(1.0, 4) == frozenset()
