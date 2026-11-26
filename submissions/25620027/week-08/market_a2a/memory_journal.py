"""Fast durable observations and compact shop notebooks shared by local processes."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from market_a2a.memory import Memory
from market_a2a.memory_models import ShopKnowledge, Trade
from market_a2a.memory_retention import MAX_NAME_BYTES, MAX_SHOPS
from market_a2a.models import HarnessError

if TYPE_CHECKING:
    from pathlib import Path

EVENT_ROWS: Final = TypeAdapter(tuple[tuple[str, str], ...])
PROFILE_ROWS: Final = TypeAdapter(tuple[tuple[str, str], ...])
PAIR: Final = TypeAdapter(tuple[int, int])
COUNT: Final = TypeAdapter(tuple[int])
MAX_PENDING: Final = 4096
MAX_EVENT_BYTES: Final = 16384
IDLE_COMPACTION_SECONDS: Final = 3600


class MemoryJournal:
    """WAL commits a small record; another process owns compaction and Markdown."""

    def __init__(self, path: Path) -> None:
        """Initialize a private bounded journal without loading model credentials."""
        self.path: Path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=0.05)) as db, db:
            _ = db.execute("PRAGMA journal_mode=WAL")
            _ = db.execute("PRAGMA synchronous=FULL")
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS events
                (id TEXT PRIMARY KEY, shop TEXT, payload TEXT)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS profiles
                (name TEXT PRIMARY KEY, payload TEXT)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS revision
                (id INTEGER PRIMARY KEY CHECK(id=1),
                current INTEGER, exported INTEGER)"""
            )
            _ = db.execute("INSERT OR IGNORE INTO revision VALUES(1, 0, -1)")
        path.chmod(0o600)

    def record(self, name: str, trade: Trade) -> None:
        """Commit the event and per-shop aggregate once; no summary/model work."""
        payload = trade.model_dump_json()
        if (
            len(payload.encode()) > MAX_EVENT_BYTES
            or len(name.encode()) > MAX_NAME_BYTES
        ):
            message = "Observation exceeds cache bounds; preserve raw transaction log"
            raise HarnessError(message)
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            _ = db.execute("BEGIN IMMEDIATE")
            names = COUNT.validate_python(
                db.execute("SELECT count(*) FROM profiles").fetchone()
            )[0]
            rows = PROFILE_ROWS.validate_python(
                db.execute(
                    "SELECT name, payload FROM profiles WHERE name=?", (name,)
                ).fetchall()
            )
            pending = COUNT.validate_python(
                db.execute("SELECT count(*) FROM events").fetchone()
            )[0]
            if (not rows and names >= MAX_SHOPS) or pending >= MAX_PENDING:
                message = "Recorder capacity reached; decision notebook was not reset"
                raise HarnessError(message)
            added = db.execute(
                "INSERT OR IGNORE INTO events VALUES(?, ?, ?)",
                (trade.event_id, name, payload),
            ).rowcount
            if not added:
                return
            previous = ShopKnowledge.model_validate_json(rows[0][1]) if rows else None
            profile = ShopKnowledge(
                observations=(previous.observations if previous else 0) + 1,
                negotiated_completions=(
                    previous.negotiated_completions if previous else 0
                )
                + int(trade.purchase.completed),
                last_seen=trade.observed_at,
                last_item=trade.item,
                last_observed_price=trade.purchase.price,
            )
            _ = db.execute(
                """INSERT INTO profiles VALUES(?, ?) ON CONFLICT(name)
                DO UPDATE SET payload=excluded.payload""",
                (name, profile.model_dump_json()),
            )
            _ = db.execute("UPDATE revision SET current=current+1 WHERE id=1")

    def review(self, trade: Trade) -> None:
        """Amend the same recent event without counting a second purchase."""
        payload = trade.model_dump_json()
        if len(payload.encode()) > MAX_EVENT_BYTES:
            message = "Review exceeds the bounded decision observation size"
            raise HarnessError(message)
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            _ = db.execute(
                "UPDATE events SET payload=? WHERE id=?", (payload, trade.event_id)
            )
            _ = db.execute("UPDATE revision SET current=current+1 WHERE id=1")

    def snapshot(self) -> tuple[int, Memory]:
        """Read one consistent revision, including every shop's small notebook."""
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            _ = db.execute("BEGIN")
            current, _ = PAIR.validate_python(
                db.execute(
                    "SELECT current, exported FROM revision WHERE id=1"
                ).fetchone()
            )
            events = EVENT_ROWS.validate_python(
                db.execute("SELECT shop, payload FROM events ORDER BY rowid").fetchall()
            )
            profiles = PROFILE_ROWS.validate_python(
                db.execute("SELECT name, payload FROM profiles").fetchall()
            )
        shops: dict[str, tuple[Trade, ...]] = {}
        for name, payload in events:
            shops[name] = (*shops.get(name, ()), Trade.model_validate_json(payload))
        return current, Memory(
            shops=shops,
            profiles={
                name: ShopKnowledge.model_validate_json(value)
                for name, value in profiles
            },
        )

    def compact(self, snapshot_path: Path) -> bool:
        """Publish a bounded snapshot, then remove only expired decision detail."""
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db:
            current, exported = PAIR.validate_python(
                db.execute(
                    "SELECT current, exported FROM revision WHERE id=1"
                ).fetchone()
            )
        fresh = (
            snapshot_path.exists()
            and datetime.now(UTC).timestamp() - snapshot_path.stat().st_mtime
            < IDLE_COMPACTION_SECONDS
        )
        if current == exported and fresh:
            return False
        revision, memory = self.snapshot()
        memory.save(snapshot_path)
        retained = Memory.load(snapshot_path)
        keep = tuple(
            trade.event_id for trades in retained.shops.values() for trade in trades
        )
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            # Do not delete a producer's newer event while exporting an older snapshot.
            _ = db.execute("BEGIN IMMEDIATE")
            newest = COUNT.validate_python(
                db.execute("SELECT current FROM revision WHERE id=1").fetchone()
            )[0]
            if newest == revision:
                _ = db.execute("CREATE TEMP TABLE keep(id TEXT PRIMARY KEY)")
                _ = db.executemany(
                    "INSERT OR IGNORE INTO keep VALUES(?)",
                    ((event_id,) for event_id in keep),
                )
                _ = db.execute(
                    "DELETE FROM events WHERE id NOT IN (SELECT id FROM keep)"
                )
            _ = db.execute("UPDATE revision SET exported=? WHERE id=1", (revision,))
        return True
