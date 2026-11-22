"""Goods quotes come down to the SKU's recent market median, never below cost."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

from r3.ledger_db import LedgerDB
from r3.pricing import MARKET_ACCEPT_SHARE, MARKET_ASK_SHARE, Quote, market_anchored

if TYPE_CHECKING:
    from pathlib import Path


def quoted(ask: int, accept_at: int) -> Quote:
    """Build a goods quote as the shop's pricing would."""
    return Quote(
        ask=ask,
        accept_at=accept_at,
        floor=accept_at,
        grade_now="A",
        clearance=False,
        stale=False,
    )


def test_a_bed_priced_over_the_market_comes_down_to_it() -> None:
    # Given: 11/11 beds: cost 0.650 of list, accept 0.767, ask 0.76; market 0.680.
    q = market_anchored(quoted(76000, 76700), 55000, 68000.0, 0.05)
    # Then: accept just under the median, ask a little over it.
    assert q.accept_at == round(68000 * MARKET_ACCEPT_SHARE)
    assert q.ask == round(68000 * MARKET_ASK_SHARE)
    assert q.floor <= q.accept_at


def test_never_below_cost_plus_the_minimum_margin() -> None:
    # Given: 11/11 sofas: cost 0.703 of list, market 0.661.
    q = market_anchored(quoted(76000, 83000), 70300, 66100.0, 0.05)
    assert q.accept_at == round(70300 * 1.02)
    assert q.ask >= q.accept_at


def test_quotes_at_or_under_the_market_and_thin_evidence_stay() -> None:
    cheap = quoted(60000, 58000)
    assert market_anchored(cheap, 50000, 68000.0, 0.05) is cheap
    assert market_anchored(cheap, 50000, None, 0.05) is cheap


def test_median_price_can_count_one_grade_only(tmp_path: Path) -> None:
    db = LedgerDB(tmp_path / "ledger.sqlite")
    now = time.time()
    with sqlite3.connect(db.path) as con:
        _ = con.execute(
            """CREATE TABLE IF NOT EXISTS rows (seq INTEGER PRIMARY KEY, ts REAL,
            kind TEXT, round INTEGER, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER,
            grade TEXT, claim TEXT, claim_honest INTEGER, data TEXT)"""
        )
        rows = [(now - 60, "settle", "G1", 70000, "A")] * 5 + [
            (now - 60, "settle", "G1", 40000, "C")
        ] * 5
        _ = con.executemany(
            "INSERT INTO rows (ts, kind, sku, amount, grade) VALUES (?,?,?,?,?)", rows
        )
    assert db.median_price("G1", now - 3600, grade="A") == 70000
    assert db.median_price("G1", now - 3600) == 55000
    assert db.median_price("G1", now - 3600, grade="B") is None
