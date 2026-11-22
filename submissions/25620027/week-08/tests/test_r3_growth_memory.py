from __future__ import annotations

import json
import sqlite3
import tracemalloc
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from r3.growth_db import read_growth
from r3.market_files import Inventory

if TYPE_CHECKING:
    from pathlib import Path

NOW = 200_000.0
SHOP = "https://own-shop"


def journals(path: Path, count: int, padding: str = "") -> None:
    with closing(sqlite3.connect(path / "r3-ledger.sqlite")) as db, db:
        _ = db.execute(
            """CREATE TABLE rows (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT,
            round INTEGER, shop TEXT, sku TEXT, data TEXT)"""
        )
        _ = db.executemany(
            "INSERT INTO rows VALUES(?,?, 'settle',3,?,'sku',?)",
            (
                (
                    i,
                    NOW - 1,
                    SHOP if i % 2 else "https://other",
                    json.dumps({"unit": f"u-{i}", "amount": 1000, "unused": padding}),
                )
                for i in range(count)
            ),
        )
    with closing(sqlite3.connect(path / "r3-stock.sqlite")) as db, db:
        _ = db.execute(
            """CREATE TABLE stock (unit TEXT PRIMARY KEY, cost INTEGER,
            supplied REAL, first_seen REAL, missing_at REAL)"""
        )
        _ = db.execute("CREATE TABLE snapshots (ts REAL)")
        _ = db.execute("INSERT INTO snapshots VALUES(?)", (NOW,))


def test_complete_market_cohort_when_payload_exceeds_memory_budget(
    tmp_path: Path,
) -> None:
    # Given 25 MiB of irrelevant JSON, created before tracing the actual reader.
    count = 3200
    padding = "x" * 8192
    journals(tmp_path, count, padding)
    # When reading a complete market cohort from the real SQLite journals.
    tracemalloc.start()
    try:
        result = read_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(
        json.dumps(
            {"rows": count, "payload_bytes": count * len(padding), "peak_bytes": peak}
        )
    )
    # Then raw JSON cannot consume the payload's full size or drop market sales.
    assert result is not None
    assert len(result.sales) == count
    assert [sale.unit for sale in result.sales] == [f"u-{i}" for i in range(count)]
    assert sum(sale.own for sale in result.sales) == count // 2
    assert peak < 8 * 1024 * 1024


def test_last_break_by_timestamp_and_sequence_when_refunds_conflict(
    tmp_path: Path,
) -> None:
    # Given equal-time conflicting breaks and a later-inserted older break.
    journals(tmp_path, 2)
    with closing(sqlite3.connect(tmp_path / "r3-ledger.sqlite")) as db, db:
        _ = db.execute("UPDATE rows SET ts=?", (NOW - 7 * 3600,))
        _ = db.executemany(
            "INSERT INTO rows(ts,kind,round,shop,sku,data) VALUES(?,?,3,?,'sku',?)",
            (
                (at, kind, SHOP, json.dumps({"unit": "u-1", "refund_amount": refund}))
                for at, kind, refund in (
                    (NOW - 3600, "broken", 1000),
                    (NOW - 3600, "break", 125),
                    (NOW - 7200, "break", 222),
                    (NOW, "heartbeat", 0),
                )
            ),
        )
    # When both passes join by unit while preserving the ledger chronology.
    result = read_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    # Then the last equal-time break wins and the other shop's sale survives.
    assert result is not None
    assert [(sale.unit, sale.own, sale.refund) for sale in result.sales] == [
        ("u-0", False, None),
        ("u-1", True, 125),
    ]


@pytest.mark.parametrize("kind", ["settle", "broken"])
def test_unavailable_when_late_payload_invalid(tmp_path: Path, kind: str) -> None:
    # Given valid rows followed by malformed evidence beyond the first batch.
    journals(tmp_path, 300)
    with closing(sqlite3.connect(tmp_path / "r3-ledger.sqlite")) as db, db:
        _ = db.execute("UPDATE rows SET kind=?", (kind,))
        _ = db.execute(
            "INSERT INTO rows(ts,kind,round,shop,sku,data) VALUES(?,?,3,?,'sku',?)",
            (NOW, kind, SHOP, '{"unit": 7}'),
        )
    # When the reader eventually reaches the malformed payload.
    result = read_growth(tmp_path, SHOP, {}, Inventory(round=3), NOW)
    # Then a partial cohort cannot authorize growth.
    assert result is None
