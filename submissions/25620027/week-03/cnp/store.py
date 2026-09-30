"""SQLite persistence with atomic state/message/event commits."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from pydantic import TypeAdapter

from cnp.records import Contract, Event

if TYPE_CHECKING:
    import sqlite3

    from cnp.domain import ContractId

ROW: Final[TypeAdapter[tuple[str] | None]] = TypeAdapter(tuple[str] | None)
ROWS: Final = TypeAdapter(tuple[tuple[str], ...])
INSERTS: Final = {
    "protocol_messages": "INSERT INTO protocol_messages(payload) VALUES (?)",
    "system_events": "INSERT INTO system_events(payload) VALUES (?)",
}
QUERIES: Final = {
    "protocol_messages": "SELECT payload FROM protocol_messages ORDER BY seq",
    "system_events": "SELECT payload FROM system_events ORDER BY seq",
}


class StoreError(Exception):
    """Persistence conflict or unknown contract."""

    def __init__(self, code: str) -> None:
        self.code: str = code
        super().__init__(code)


class Store:
    """Own transaction boundaries; the caller owns connection lifetime."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db: sqlite3.Connection = db
        db.executescript(
            "CREATE TABLE IF NOT EXISTS contracts "
            "(id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS protocol_messages "
            "(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS system_events "
            "(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL);"
        )

    def load(self, contract_id: ContractId) -> Contract:
        """Parse the stored snapshot at the database boundary."""
        row = ROW.validate_python(
            self.db.execute("SELECT payload FROM contracts WHERE id=?", (contract_id,)).fetchone()
        )
        if row is None:
            msg = "contract_error"
            raise StoreError(msg)
        return Contract.model_validate_json(row[0])

    def save(self, before: Contract | None, after: Contract, events: tuple[Event, ...]) -> Contract:
        """Commit snapshot and both event streams in one transaction."""
        current = after.model_copy(update={"version": after.version + 1})
        with self.db:
            if before is None:
                self.db.execute(
                    "INSERT INTO contracts VALUES (?,?,?)",
                    (current.id, current.version, current.model_dump_json()),
                )
            else:
                changed = self.db.execute(
                    "UPDATE contracts SET version=?,payload=? WHERE id=? AND version=?",
                    (current.version, current.model_dump_json(), before.id, before.version),
                ).rowcount
                if changed != 1:
                    msg = "concurrent_update"
                    raise StoreError(msg)
            for event in (
                *events,
                Event(
                    stream="system_events",
                    contract_id=current.id,
                    kind="STATE",
                    detail=current.phase,
                ),
            ):
                self.db.execute(INSERTS[event.stream], (event.model_dump_json(),))
        return current

    def record(self, event: Event) -> None:
        """Persist diagnostics without changing allocation state."""
        with self.db:
            self.db.execute(INSERTS[event.stream], (event.model_dump_json(),))

    def events(self, stream: Literal["protocol_messages", "system_events"]) -> tuple[Event, ...]:
        """Read a stream in insertion order for audit/export."""
        rows = ROWS.validate_python(self.db.execute(QUERIES[stream]).fetchall())
        return tuple(Event.model_validate_json(row[0]) for row in rows)
