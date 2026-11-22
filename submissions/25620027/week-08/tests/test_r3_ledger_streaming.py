from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, NoReturn, cast, override

import pytest

from r3.ledger_db import BREAK_WINDOW, LedgerDB

if TYPE_CHECKING:
    from pathlib import Path

NOW = 2_000_000.0
HOUR = 3600


def row(seq: int, ts: float, kind: str, **data: object) -> dict[str, object]:
    """Build a public-ledger row with the same flattened ingestion boundary."""
    return {"seq": seq, "ts": ts, "kind": kind, "round": 3, "data": data}


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LedgerDB:
    """Give every quality cohort an exact observation clock."""
    monkeypatch.setattr("r3.ledger_db.time.time", lambda: NOW)
    return LedgerDB(tmp_path / "ledger.sqlite")


class NoMaterializeCursor(sqlite3.Cursor):
    """Reject the bulk-copy API that duplicated all source payload strings."""

    @override
    def fetchall(self) -> NoReturn:
        """Fail before a source result can be materialized as a second list."""
        message = "Quality readers must stream their source rows"
        raise AssertionError(message)


class NoMaterializeConnection:
    """A small DB-API proxy that returns the guarded real SQLite cursor."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Keep the real connection; the reader only uses execute and close."""
        self.connection: sqlite3.Connection = connection

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        """Delegate query execution unchanged; only forbid eager fetching."""
        return self.connection.cursor(factory=NoMaterializeCursor).execute(
            sql, parameters
        )

    def close(self) -> None:
        """Release the wrapped SQLite connection normally."""
        self.connection.close()


@pytest.mark.parametrize("reader", ["break_rates", "shop_breaks"])
def test_quality_readers_do_not_bulk_copy_rows(
    ledger: LedgerDB, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    _ = ledger.ingest(
        [
            row(
                1,
                NOW - 8 * HOUR,
                "settle",
                buyer="buyer",
                shop="https://shop/",
                item="lamp",
                grade="A",
                unit="unit",
                unused_payload="x" * 3000,
            ),
            row(
                2,
                NOW - 7 * HOUR,
                "broken",
                buyer="buyer",
                item="lamp",
                unit="unit",
            ),
        ]
    )
    connect = sqlite3.connect

    def guarded_connect(database: Path, *, timeout: float) -> sqlite3.Connection:
        return cast(
            "sqlite3.Connection",
            cast("object", NoMaterializeConnection(connect(database, timeout=timeout))),
        )

    monkeypatch.setattr("r3.ledger_db.sqlite3.connect", guarded_connect)
    since = NOW - 24 * HOUR
    if reader == "break_rates":
        assert ledger.break_rates(since) == {"A": (1, 1)}
    else:
        assert ledger.shop_breaks(since, {"lamp"}) == {"https://shop": (1, 1)}


def test_grade_break_matching_keeps_units_fallback_and_duplicate_semantics(
    ledger: LedgerDB,
) -> None:
    _ = ledger.ingest(
        [
            row(
                1,
                NOW - 10 * HOUR,
                "settle",
                buyer="b",
                item="lamp",
                grade="A",
                unit="u1",
            ),
            row(
                2,
                NOW - 9 * HOUR,
                "settle",
                buyer="b",
                item="lamp",
                grade="B",
                unit="u2",
            ),
            # Unit matching wins even when the event's buyer and SKU differ.
            row(3, NOW - 8 * HOUR, "broken", buyer="other", item="other", unit="u1"),
            row(4, NOW - 7 * HOUR, "broken", buyer="b", item="lamp"),
            row(5, NOW - 6 * HOUR, "broken", buyer="b", item="lamp", unit="u2"),
            row(6, NOW - 6 * HOUR + 1, "broken", buyer="b", item="lamp", unit="u2"),
        ]
    )
    assert ledger.break_rates(NOW - 24 * HOUR) == {"A": (1, 1), "B": (1, 1)}


def test_grade_fallback_uses_latest_still_unmatched_sale(ledger: LedgerDB) -> None:
    _ = ledger.ingest(
        [
            row(1, NOW - 9 * HOUR, "settle", buyer="b", item="lamp", grade="A"),
            row(2, NOW - 8 * HOUR, "settle", buyer="b", item="lamp", grade="B"),
            row(3, NOW - 7 * HOUR, "broken", buyer="b", item="lamp"),
            row(4, NOW - 6 * HOUR, "broken", buyer="b", item="lamp"),
        ]
    )
    assert ledger.break_rates(NOW - 24 * HOUR) == {"A": (1, 1), "B": (1, 1)}


def test_grade_cohort_excludes_food_and_keeps_corrupt_payload_tolerance(
    ledger: LedgerDB,
) -> None:
    _ = ledger.ingest(
        [
            row(
                1,
                NOW - 8 * HOUR,
                "settle",
                buyer="b",
                item="food",
                grade="A",
                expires_at=NOW,
            ),
            row(2, NOW - 8 * HOUR, "settle", buyer="b", item="bad-json", grade="B"),
            row(3, NOW - 8 * HOUR, "settle", buyer="b", item="non-object", grade="C"),
            row(4, NOW - 7 * HOUR, "broken", buyer="b", item="bad-json"),
        ]
    )
    with closing(sqlite3.connect(ledger.path)) as db, db:
        _ = db.execute("UPDATE rows SET data='{' WHERE seq=2")
        _ = db.execute("UPDATE rows SET data='[]' WHERE seq=3")
        _ = db.execute("UPDATE rows SET data='null' WHERE seq=4")
    assert ledger.break_rates(NOW - 24 * HOUR) == {"B": (1, 1), "C": (0, 1)}


def test_grade_maturity_and_fallback_window_boundaries(ledger: LedgerDB) -> None:
    since = NOW - 12 * HOUR
    _ = ledger.ingest(
        [
            row(1, since, "settle", buyer="b1", item="lamp", grade="A"),
            row(2, NOW - BREAK_WINDOW, "settle", buyer="b2", item="lamp", grade="B"),
            row(
                3,
                NOW - BREAK_WINDOW + 0.001,
                "settle",
                buyer="b3",
                item="lamp",
                grade="C",
            ),
            row(4, since + BREAK_WINDOW, "broken", buyer="b1", item="lamp"),
            row(5, NOW + 0.001, "broken", buyer="b2", item="lamp"),
        ]
    )
    # since is inclusive; maturity is inclusive; fallback >6h must not match.
    assert ledger.break_rates(since) == {"A": (1, 1), "B": (0, 1)}


def test_shop_matching_keeps_recent_breaks_and_latest_sale_dedup(
    ledger: LedgerDB,
) -> None:
    _ = ledger.ingest(
        [
            row(
                1, NOW - 8 * HOUR, "settle", buyer="b", item="lamp", shop="https://old/"
            ),
            row(
                2, NOW - 7 * HOUR, "settle", buyer="b", item="lamp", shop="https://new/"
            ),
            row(3, NOW - 6 * HOUR, "broken", buyer="b", item="lamp"),
            row(4, NOW - 5 * HOUR, "broken", buyer="b", item="lamp"),
            row(
                5, NOW - HOUR, "settle", buyer="fresh", item="lamp", shop="https://new/"
            ),
            row(6, NOW - HOUR / 2, "broken", buyer="fresh", item="lamp"),
            row(
                7,
                NOW - 8 * HOUR,
                "settle",
                buyer="food",
                item="food",
                shop="https://food/",
            ),
            row(8, NOW - 7 * HOUR, "broken", buyer="food", item="food"),
        ]
    )
    # Unlike grade fallback, duplicate shop break events do not move to older sales.
    assert ledger.shop_breaks(NOW - 24 * HOUR, {"lamp"}) == {
        "https://new": (2, 2),
        "https://old": (0, 1),
    }


def test_shop_observation_and_match_window_boundaries(ledger: LedgerDB) -> None:
    since = NOW - 10 * HOUR
    _ = ledger.ingest(
        [
            row(
                1,
                since - BREAK_WINDOW,
                "settle",
                buyer="before",
                item="lamp",
                shop="s0/",
            ),
            row(2, since, "broken", buyer="before", item="lamp"),
            row(
                3, NOW - BREAK_WINDOW, "settle", buyer="exact", item="lamp", shop="s1/"
            ),
            row(
                4,
                NOW - BREAK_WINDOW + 0.001,
                "settle",
                buyer="young",
                item="lamp",
                shop="s2/",
            ),
        ]
    )
    assert ledger.shop_breaks(since, {"lamp"}) == {"s0": (1, 1), "s1": (0, 1)}
