"""Record seller price exposure and outcomes without changing the price policy.

The live table starts empty. Historical log lines are deliberately not treated as
randomized observations, and an issued receipt is not a market-counted deal.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


STATUSES = {
    "UNRESOLVED", "RECEIPT_PENDING", "DEAL_COUNTED", "REJECTED",
    "TIMED_OUT", "INFRA_ERROR", "CANCELLED", "RECEIPT_NOT_COUNTED",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PricingRecorder:
    def __init__(self, path: str | Path = "state/pricing_state.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    task_id TEXT PRIMARY KEY,
                    item TEXT NOT NULL,
                    reserve INTEGER NOT NULL CHECK (reserve > 0),
                    assigned_arm TEXT NOT NULL,
                    opening_ask_actual INTEGER,
                    offers TEXT NOT NULL DEFAULT '[]',
                    opened_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    terminal_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
                    receipt_issued INTEGER NOT NULL DEFAULT 0,
                    market_counted INTEGER,
                    final_price INTEGER,
                    model_calls INTEGER NOT NULL DEFAULT 0,
                    status_evidence TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES trades(task_id),
                    at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS events_task_id ON events(task_id);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, task_id: str, kind: str, at: str, **data: object) -> None:
        db.execute(
            "INSERT INTO events(task_id, at, kind, data) VALUES (?, ?, ?, ?)",
            (task_id, at, kind, json.dumps(data, ensure_ascii=False)),
        )

    def start(self, task_id: str, item: str, reserve: int, arm: str = "legacy_cap_1.12") -> bool:
        """Create one attempt; replaying the same task must not reset its record."""
        at = now()
        with self._connect() as db:
            changed = db.execute(
                "INSERT OR IGNORE INTO trades(task_id, item, reserve, assigned_arm, opened_at, last_activity_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (task_id, item, reserve, arm, at, at),
            ).rowcount
            if changed:
                self._event(db, task_id, "attempt_started", at, arm=arm)
            else:
                row = db.execute("SELECT item, reserve, assigned_arm FROM trades WHERE task_id=?", (task_id,)).fetchone()
                if (row["item"], row["reserve"], row["assigned_arm"]) != (item, reserve, arm):
                    raise ValueError("task ID reused with different transaction details")
            return bool(changed)

    def model_call(self, task_id: str) -> None:
        at = now()
        with self._connect() as db:
            db.execute("UPDATE trades SET model_calls=model_calls+1, last_activity_at=? WHERE task_id=?", (at, task_id))
            self._event(db, task_id, "model_call", at)

    def opening_sent(self, task_id: str, ask: int) -> None:
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT opening_ask_actual, reserve FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if ask < row["reserve"]:
                raise ValueError("opening ask below reserve")
            if row["opening_ask_actual"] is not None:
                if row["opening_ask_actual"] != ask:
                    raise ValueError("opening ask cannot change after exposure")
                return
            db.execute("UPDATE trades SET opening_ask_actual=?, terminal_status='UNRESOLVED', "
                       "last_activity_at=? WHERE task_id=?", (ask, at, task_id))
            self._event(db, task_id, "opening_sent", at, ask=ask)

    def offer_received(self, task_id: str, offer: int) -> None:
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT offers FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            offers = json.loads(row["offers"])
            offers.append(offer)
            db.execute("UPDATE trades SET offers=?, last_activity_at=? WHERE task_id=?",
                       (json.dumps(offers), at, task_id))
            self._event(db, task_id, "offer_received", at, offer=offer)

    def counter_sent(self, task_id: str, ask: int) -> None:
        at = now()
        with self._connect() as db:
            db.execute("UPDATE trades SET terminal_status='UNRESOLVED', last_activity_at=? "
                       "WHERE task_id=? AND terminal_status IN ('UNRESOLVED', 'INFRA_ERROR')", (at, task_id))
            self._event(db, task_id, "counter_sent", at, ask=ask)

    def receipt(self, task_id: str, price: int) -> None:
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT reserve, receipt_issued, final_price FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if price < row["reserve"]:
                raise ValueError("receipt price below reserve")
            if row["receipt_issued"]:
                if row["final_price"] != price:
                    raise ValueError("conflicting receipt price")
                return
            db.execute("UPDATE trades SET receipt_issued=1, final_price=?, terminal_status='RECEIPT_PENDING', "
                       "last_activity_at=? WHERE task_id=?", (price, at, task_id))
            self._event(db, task_id, "receipt_issued", at, price=price)

    def close(self, task_id: str, status: str, evidence: str) -> None:
        if status not in {"REJECTED", "TIMED_OUT", "INFRA_ERROR", "CANCELLED"}:
            raise ValueError(status)
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT receipt_issued, terminal_status FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["receipt_issued"] or row["terminal_status"] == "DEAL_COUNTED":
                return
            if row["terminal_status"] == status:
                return
            db.execute("UPDATE trades SET terminal_status=?, status_evidence=?, last_activity_at=? WHERE task_id=?",
                       (status, evidence, at, task_id))
            self._event(db, task_id, "status_changed", at, status=status, evidence=evidence)

    def market_result(self, task_id: str, counted: bool, evidence: str) -> None:
        """Only call after matching a task to an authoritative market result."""
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT receipt_issued, market_counted FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if not row["receipt_issued"]:
                raise ValueError("no issued receipt to reconcile")
            if row["market_counted"] is not None:
                if bool(row["market_counted"]) != counted:
                    raise ValueError("conflicting market result")
                return
            db.execute("UPDATE trades SET market_counted=?, terminal_status=?, status_evidence=?, "
                       "last_activity_at=? WHERE task_id=?",
                       (int(counted), "DEAL_COUNTED" if counted else "RECEIPT_NOT_COUNTED", evidence, at, task_id))
            self._event(db, task_id, "market_result", at, counted=counted, evidence=evidence)

    def get(self, task_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM trades WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["offers"] = json.loads(result["offers"])
        return result

    def ledger_cursor(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='ledger_cursor'").fetchone()
        return int(row["value"]) if row else 0

    def earliest_attempt_at(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT MIN(opened_at) AS at FROM trades").fetchone()
        return row["at"]

    def set_ledger_cursor(self, cursor: int) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO metadata(key, value) VALUES ('ledger_cursor', ?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(cursor),))

    def stats(self, item: str | None = None) -> list[dict]:
        """Descriptive counts only; unknown outcomes stay visible in the denominator."""
        query = "SELECT * FROM trades WHERE opening_ask_actual IS NOT NULL"
        args: tuple[str, ...] = ()
        if item is not None:
            query += " AND item=?"
            args = (item,)
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        groups: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["item"], row["assigned_arm"])
            g = groups.setdefault(key, {"item": key[0], "arm": key[1], "exposed": 0,
                                        "counted": 0, "rejected": 0, "timed_out": 0,
                                        "infra_error": 0, "unresolved": 0, "receipt_pending": 0,
                                        "cancelled": 0, "receipt_not_counted": 0,
                                        "margin_counted": 0, "model_calls": 0})
            g["exposed"] += 1
            g["model_calls"] += row["model_calls"]
            status = row["terminal_status"]
            if status == "DEAL_COUNTED":
                g["counted"] += 1
                g["margin_counted"] += row["final_price"] - row["reserve"]
            else:
                g[{"REJECTED": "rejected", "TIMED_OUT": "timed_out", "INFRA_ERROR": "infra_error",
                   "UNRESOLVED": "unresolved", "RECEIPT_PENDING": "receipt_pending",
                   "CANCELLED": "cancelled", "RECEIPT_NOT_COUNTED": "receipt_not_counted"}[status]] += 1
        return list(groups.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read the record-only seller pricing statistics")
    parser.add_argument("--db", default="state/pricing_state.sqlite")
    parser.add_argument("--item", choices=["lamp", "chair", "bookshelf"])
    args = parser.parse_args()
    print(json.dumps(PricingRecorder(args.db).stats(args.item), ensure_ascii=False, indent=2))
