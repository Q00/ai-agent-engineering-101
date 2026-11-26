"""Incremental facts cache; the market mirror is always opened read-only."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from r3.buyer_report_models import ReportPayload

if TYPE_CHECKING:
    from pathlib import Path

_ROW: Final = TypeAdapter(list[tuple[int, float, str, str]])
_LATEST: Final = TypeAdapter(tuple[int, float] | None)
_CURSOR: Final = TypeAdapter(tuple[int] | None)
BATCH: Final = 128


def open_readonly(path: Path) -> sqlite3.Connection:
    """Never create or migrate a source database while reporting."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)


def update_facts(source: Path, cache: sqlite3.Connection, buyer: str) -> float | None:
    """Persist only this buyer's facts in bounded batches and an atomic cursor."""
    _ = cache.executescript(
        """CREATE TABLE IF NOT EXISTS report_cursor (
          buyer TEXT PRIMARY KEY, seq INTEGER);
        CREATE TABLE IF NOT EXISTS unidentified_orders (
          seq INTEGER PRIMARY KEY, ts REAL);
        CREATE TABLE IF NOT EXISTS issued (
          order_id TEXT PRIMARY KEY, ts REAL, budget INTEGER, expires REAL);
        CREATE TABLE IF NOT EXISTS settlements (
          seq INTEGER PRIMARY KEY, order_id TEXT, ts REAL, unit TEXT, sku TEXT,
          amount INTEGER, value INTEGER);
        CREATE INDEX IF NOT EXISTS settlement_order ON settlements(order_id);
        CREATE UNIQUE INDEX IF NOT EXISTS settlement_unit ON settlements(unit)
          WHERE unit != '';
        CREATE TABLE IF NOT EXISTS breaks (
          unit TEXT PRIMARY KEY, ts REAL, refund INTEGER, refund_full INTEGER);
        """
    )
    columns = TypeAdapter(
        list[tuple[int, str, str, int, str | None, int]]
    ).validate_python(cache.execute("PRAGMA table_info(issued)").fetchall())
    if "expires" not in {column[1] for column in columns}:
        _ = cache.execute("ALTER TABLE issued ADD COLUMN expires REAL")
    previous = _CURSOR.validate_python(
        cache.execute(
            "SELECT seq FROM report_cursor WHERE buyer=?",
            (buyer,),
        ).fetchone()
    )
    cursor = previous[0] if previous else 0
    with closing(open_readonly(source)) as db:
        _ = db.execute("BEGIN")
        latest = _LATEST.validate_python(
            db.execute("SELECT seq,ts FROM rows ORDER BY seq DESC LIMIT 1").fetchone()
        )
        if latest is not None and latest[0] < cursor:
            msg = "Market mirror sequence regressed; report cache must be reconciled"
            raise RuntimeError(msg)
        with (
            cache,
            closing(
                db.execute(
                    """SELECT seq,ts,kind,data FROM rows WHERE seq>? AND buyer=? AND
round=3 AND kind IN ('order','settle','broken') ORDER BY seq""",
                    (cursor, buyer),
                )
            ) as rows,
        ):
            while batch := _ROW.validate_python(rows.fetchmany(BATCH)):
                for seq, ts, kind, raw in batch:
                    payload = ReportPayload.model_validate_json(raw)
                    _store(cache, seq, ts, kind, payload)
            _ = cache.execute(
                """INSERT INTO report_cursor VALUES (?,?) ON CONFLICT(buyer) DO UPDATE
SET seq=excluded.seq""",
                (buyer, latest[0] if latest else cursor),
            )
    return latest[1] if latest else None


def _store(
    db: sqlite3.Connection, seq: int, ts: float, kind: str, data: ReportPayload
) -> None:
    """Deduplicate exact unit breaks and exact ledger settlements."""
    if kind == "order" and not data.order:
        _ = db.execute(
            "INSERT OR IGNORE INTO unidentified_orders VALUES (?,?)",
            (seq, ts),
        )
    if kind == "order" and data.order:
        _ = db.execute(
            "INSERT OR IGNORE INTO issued VALUES (?,?,?,?)",
            (data.order, ts, data.budget, data.expires),
        )
    if kind == "settle" and data.order:
        _ = db.execute(
            "INSERT OR IGNORE INTO settlements VALUES (?,?,?,?,?,?,?)",
            (
                seq,
                data.order,
                ts,
                data.unit,
                data.sku or data.item,
                data.amount,
                data.value,
            ),
        )
    if kind == "broken" and data.unit:
        refund = data.refunded
        full = refund is True
        amount = None if refund is None else int(refund)
        _ = db.execute(
            "INSERT OR IGNORE INTO breaks VALUES (?,?,?,?)",
            (data.unit, ts, amount, int(full)),
        )
