"""Record seller price exposure and outcomes without changing the price policy.

The live table starts empty. Historical log lines are deliberately not treated as
randomized observations, and an issued receipt is not a market-counted deal.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


NEGOTIATION_STATUSES = {"OPEN", "RECEIPT_ISSUED", "COMPLETED", "REJECTED", "TIMED_OUT", "INFRA_ERROR", "CANCELLED"}
LEDGER_STATUSES = {"NONE", "PENDING", "COUNTED", "NOT_COUNTED"}


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
                    status_evidence TEXT,
                    negotiation_status TEXT NOT NULL DEFAULT 'OPEN',
                    ledger_status TEXT NOT NULL DEFAULT 'NONE',
                    ledger_result_at TEXT,
                    ledger_evidence TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES trades(task_id),
                    at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    source_message_id TEXT
                );
                CREATE INDEX IF NOT EXISTS events_task_id ON events(task_id);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_observations (
                    seq INTEGER PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    shop TEXT NOT NULL,
                    item TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    verified INTEGER NOT NULL,
                    checks TEXT NOT NULL,
                    ledger_ts REAL NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_observations_task ON ledger_observations(task_id);
            """)
            # Existing live databases predate the two independent status axes.
            trade_columns = {row["name"] for row in db.execute("PRAGMA table_info(trades)")}
            for name, declaration in (
                ("negotiation_status", "TEXT NOT NULL DEFAULT 'OPEN'"),
                ("ledger_status", "TEXT NOT NULL DEFAULT 'NONE'"),
                ("ledger_result_at", "TEXT"),
                ("ledger_evidence", "TEXT"),
            ):
                if name not in trade_columns:
                    db.execute(f"ALTER TABLE trades ADD COLUMN {name} {declaration}")
            event_columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
            if "source_message_id" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN source_message_id TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS events_message_dedup "
                       "ON events(task_id, kind, source_message_id) WHERE source_message_id IS NOT NULL")
            if db.execute("SELECT value FROM metadata WHERE key='status_axes_migrated'").fetchone() is None:
                db.execute("UPDATE trades SET negotiation_status=CASE "
                           "WHEN terminal_status IN ('REJECTED','TIMED_OUT','INFRA_ERROR','CANCELLED') THEN terminal_status "
                           "WHEN receipt_issued=1 THEN 'RECEIPT_ISSUED' ELSE 'OPEN' END, "
                           "ledger_status=CASE WHEN market_counted=1 THEN 'COUNTED' "
                           "WHEN market_counted=0 THEN 'NOT_COUNTED' "
                           "WHEN receipt_issued=1 THEN 'PENDING' ELSE 'NONE' END, "
                           "ledger_evidence=CASE WHEN market_counted IS NOT NULL THEN status_evidence ELSE NULL END")
                db.execute("UPDATE trades SET last_activity_at=COALESCE((SELECT MAX(at) FROM events "
                           "WHERE events.task_id=trades.task_id AND kind!='market_result'), last_activity_at)")
                db.execute("INSERT INTO metadata(key,value) VALUES ('status_axes_migrated','1')")
            # An older Seller process may have written rows during the migration.
            db.execute("UPDATE trades SET ledger_status='PENDING' WHERE receipt_issued=1 "
                       "AND market_counted IS NULL AND ledger_status='NONE'")

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
    def _event(db: sqlite3.Connection, task_id: str, kind: str, at: str,
               source_message_id: str | None = None, **data: object) -> bool:
        payload = json.dumps(data, ensure_ascii=False)
        changed = db.execute(
            "INSERT OR IGNORE INTO events(task_id, at, kind, data, source_message_id) VALUES (?, ?, ?, ?, ?)",
            (task_id, at, kind, payload, source_message_id),
        ).rowcount
        if not changed and source_message_id is not None:
            previous = db.execute("SELECT data FROM events WHERE task_id=? AND kind=? AND source_message_id=?",
                                  (task_id, kind, source_message_id)).fetchone()
            if previous["data"] != payload:
                raise ValueError("same A2A message ID changed an observed event")
        return bool(changed)

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
            db.execute("UPDATE trades SET opening_ask_actual=?, terminal_status='UNRESOLVED', negotiation_status='OPEN', "
                       "last_activity_at=? WHERE task_id=?", (ask, at, task_id))
            self._event(db, task_id, "opening_sent", at, ask=ask)

    def offer_received(self, task_id: str, offer: int, source_message_id: str | None = None) -> None:
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT offers FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if not self._event(db, task_id, "offer_received", at,
                               source_message_id=source_message_id, offer=offer):
                return
            offers = json.loads(row["offers"])
            offers.append(offer)
            db.execute("UPDATE trades SET offers=?, last_activity_at=? WHERE task_id=?",
                       (json.dumps(offers), at, task_id))

    def counter_sent(self, task_id: str, ask: int, source_message_id: str | None = None) -> None:
        at = now()
        with self._connect() as db:
            if not self._event(db, task_id, "counter_sent", at,
                               source_message_id=source_message_id, ask=ask):
                return
            db.execute("UPDATE trades SET terminal_status='UNRESOLVED', last_activity_at=? "
                       "WHERE task_id=? AND terminal_status IN ('UNRESOLVED', 'INFRA_ERROR')", (at, task_id))
            db.execute("UPDATE trades SET negotiation_status='OPEN' WHERE task_id=? "
                       "AND negotiation_status IN ('OPEN','INFRA_ERROR')", (task_id,))

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
                       "negotiation_status='RECEIPT_ISSUED', ledger_status='PENDING', "
                       "last_activity_at=? WHERE task_id=?", (price, at, task_id))
            self._event(db, task_id, "receipt_issued", at, price=price)

    def complete(self, task_id: str) -> None:
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT receipt_issued, negotiation_status FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if not row["receipt_issued"]:
                raise ValueError("cannot complete before receipt")
            if row["negotiation_status"] == "COMPLETED":
                return
            db.execute("UPDATE trades SET negotiation_status='COMPLETED', last_activity_at=? WHERE task_id=?",
                       (at, task_id))
            self._event(db, task_id, "negotiation_completed", at)

    def confirm_completed_from_log(self, task_id: str, price: int, evidence: str) -> bool:
        """Backfill an older completed A2A task from a matching seller_deal trace."""
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT receipt_issued, final_price, negotiation_status FROM trades "
                             "WHERE task_id=?", (task_id,)).fetchone()
            if row is None or not row["receipt_issued"] or row["final_price"] != price:
                return False
            if row["negotiation_status"] == "COMPLETED":
                return False
            db.execute("UPDATE trades SET negotiation_status='COMPLETED', status_evidence=? WHERE task_id=?",
                       (evidence, task_id))
            self._event(db, task_id, "completion_confirmed_from_log", at, price=price, evidence=evidence)
            return True

    def close(self, task_id: str, status: str, evidence: str) -> None:
        if status not in {"REJECTED", "TIMED_OUT", "INFRA_ERROR", "CANCELLED"}:
            raise ValueError(status)
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT receipt_issued, negotiation_status FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["negotiation_status"] == status:
                return
            db.execute("UPDATE trades SET negotiation_status=?, status_evidence=?, last_activity_at=?, "
                       "terminal_status=CASE WHEN receipt_issued=0 THEN ? ELSE terminal_status END "
                       "WHERE task_id=?", (status, evidence, at, status, task_id))
            self._event(db, task_id, "status_changed", at, status=status, evidence=evidence)

    def market_result(self, task_id: str, counted: bool, evidence: str,
                      ledger_at: str | None = None) -> None:
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
            db.execute("UPDATE trades SET market_counted=?, ledger_status=?, ledger_result_at=?, "
                       "ledger_evidence=?, terminal_status=? WHERE task_id=?",
                       (int(counted), "COUNTED" if counted else "NOT_COUNTED", ledger_at or at, evidence,
                        "DEAL_COUNTED" if counted else "RECEIPT_NOT_COUNTED", task_id))
            self._event(db, task_id, "market_result", at, counted=counted, evidence=evidence)

    def confirm_receipt_from_ledger(self, task_id: str, price: int, evidence: str) -> None:
        """Recover a receipt missed by local logging using a verified seller signature."""
        at = now()
        with self._connect() as db:
            row = db.execute("SELECT reserve, receipt_issued, final_price FROM trades WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if price < row["reserve"]:
                raise ValueError("ledger price below reserve")
            if row["receipt_issued"]:
                if row["final_price"] != price:
                    raise ValueError("ledger price conflicts with local receipt")
                return
            db.execute("UPDATE trades SET receipt_issued=1, final_price=?, ledger_status='PENDING', "
                       "terminal_status='RECEIPT_PENDING' WHERE task_id=?", (price, task_id))
            self._event(db, task_id, "receipt_confirmed_from_ledger", at, price=price, evidence=evidence)

    def save_ledger_observation(self, seq: int, task_id: str, shop: str, item: str,
                                price: int, verified: bool, checks: dict, ledger_ts: float) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO ledger_observations "
                       "(seq,task_id,shop,item,price,verified,checks,ledger_ts,observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (seq, task_id, shop, item, price, int(verified),
                        json.dumps(checks, sort_keys=True), ledger_ts, now()))

    def apply_ledger_observations(self, shop: str) -> dict:
        """Apply saved results even when they arrived before the local receipt row."""
        applied = counted = recovered = 0
        with self._connect() as db:
            observations = db.execute("SELECT * FROM ledger_observations WHERE shop=? ORDER BY seq", (shop,)).fetchall()
        for observation in observations:
            trade = self.get(observation["task_id"])
            if trade is None or trade["ledger_status"] in {"COUNTED", "NOT_COUNTED"}:
                continue
            checks = json.loads(observation["checks"])
            if (trade["item"] != observation["item"]
                    or (trade["final_price"] is not None and trade["final_price"] != observation["price"])
                    or checks.get("sig_seller_ok") is not True):
                continue
            if not trade["receipt_issued"]:
                self.confirm_receipt_from_ledger(observation["task_id"], observation["price"],
                                                 f"ledger seq {observation['seq']}")
                recovered += 1
            accepted = bool(observation["verified"]) and bool(checks) and all(v is True for v in checks.values())
            ledger_at = datetime.fromtimestamp(observation["ledger_ts"], timezone.utc).isoformat()
            self.market_result(observation["task_id"], accepted,
                               f"ledger seq {observation['seq']}", ledger_at)
            applied += 1
            counted += int(accepted)
        return {"matched": applied, "counted": counted, "receipt_recovered": recovered}

    def get(self, task_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM trades WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["offers"] = json.loads(result["offers"])
        return result

    def all_trades(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM trades ORDER BY opened_at, task_id").fetchall()
        return [{**dict(row), "offers": json.loads(row["offers"])} for row in rows]

    def events_for(self, task_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def ledger_timing_rows(self) -> list[dict]:
        """Return only the official result applied to each locally issued receipt."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT o.task_id, o.ledger_ts, o.observed_at, e.receipt_at
                FROM ledger_observations AS o
                JOIN (SELECT task_id, MIN(at) AS receipt_at FROM events
                      WHERE kind='receipt_issued' GROUP BY task_id) AS e ON e.task_id=o.task_id
                JOIN trades AS t ON t.task_id=o.task_id
                  AND t.ledger_evidence='ledger seq ' || o.seq
                WHERE t.ledger_status IN ('COUNTED','NOT_COUNTED')
            """).fetchall()
        return [dict(row) for row in rows]

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
                       "ON CONFLICT(key) DO UPDATE SET value=CAST(MAX(CAST(value AS INTEGER), "
                       "CAST(excluded.value AS INTEGER)) AS TEXT)", (str(cursor),))

    def stats(self, item: str | None = None) -> list[dict]:
        """Descriptive counts only; unknown outcomes stay visible in the denominator."""
        return self._stats_rows(self.all_trades(), item)

    @staticmethod
    def readonly_stats(path: str | Path, item: str) -> list[dict]:
        """Fixed, read-only query for the model-facing statistics tool."""
        if not isinstance(item, str) or item not in {"lamp", "chair", "bookshelf"}:
            raise ValueError("item must be lamp, chair, or bookshelf")
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            rows = [dict(row) for row in db.execute(
                "SELECT item, assigned_arm, opening_ask_actual, receipt_issued, "
                "ledger_status, final_price, reserve, model_calls, negotiation_status "
                "FROM trades WHERE item=?", (item,))]
        return PricingRecorder._stats_rows(rows, item)

    @staticmethod
    def _stats_rows(rows: list[dict], item: str | None = None) -> list[dict]:
        rows = [row for row in rows if item is None or row["item"] == item]
        groups: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["item"], row["assigned_arm"])
            g = groups.setdefault(key, {"item": key[0], "arm": key[1], "attempts": 0, "exposed": 0,
                                        "receipts": 0,
                                        "counted": 0, "rejected": 0, "timed_out": 0,
                                        "infra_error": 0, "unresolved": 0, "receipt_pending": 0,
                                        "cancelled": 0, "receipt_not_counted": 0,
                                        "margin_counted": 0, "model_calls": 0,
                                        "opening_ratio_sum": 0.0, "counted_price_sum": 0})
            g["attempts"] += 1
            g["model_calls"] += row["model_calls"]
            if row["opening_ask_actual"] is not None:
                g["exposed"] += 1
                g["opening_ratio_sum"] += row["opening_ask_actual"] / row["reserve"]
            if row["receipt_issued"]:
                g["receipts"] += 1
            if row["ledger_status"] == "COUNTED":
                g["counted"] += 1
                g["margin_counted"] += row["final_price"] - row["reserve"]
                g["counted_price_sum"] += row["final_price"]
            elif row["ledger_status"] == "PENDING":
                g["receipt_pending"] += 1
            elif row["ledger_status"] == "NOT_COUNTED":
                g["receipt_not_counted"] += 1
            status = row["negotiation_status"]
            if status in {"OPEN", "RECEIPT_ISSUED"}:
                g["unresolved"] += 1
            elif status != "COMPLETED":
                g[{"REJECTED": "rejected", "TIMED_OUT": "timed_out", "INFRA_ERROR": "infra_error",
                   "CANCELLED": "cancelled"}[status]] += 1
        return list(groups.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read the record-only seller pricing statistics")
    parser.add_argument("--db", default="state/pricing_state.sqlite")
    parser.add_argument("--item", choices=["lamp", "chair", "bookshelf"])
    args = parser.parse_args()
    print(json.dumps(PricingRecorder(args.db).stats(args.item), ensure_ascii=False, indent=2))
