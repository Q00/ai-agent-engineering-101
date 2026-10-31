"""Our own event ledger: what we tried, what the kit paid, what the market settled.

Separate from decision memory and from the public ledger copy. Rows are appended
by the buyer (attempts), the seller (signed receipts) and the manager (payment
outcomes read back from payments.jsonl). Nothing here is ever rewritten; counts for
the report are derived with the market's own ledger numbers as the identity.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

    from r3.market_files import PaymentLine

EVENTS_ENV: Final = "AX_R3_EVENTS"
EVENTS_FILE: Final = "r3-events.sqlite"
Outcome = Literal["agreed", "no_deal", "paid", "refused", "unknown", "error"]


def events_path(market_dir: Path | None = None) -> Path:
    """Resolve the events database: explicit env, else beside ``.market``."""
    configured = os.environ.get(EVENTS_ENV)
    if configured:
        return Path(configured)
    base = (
        market_dir
        if market_dir is not None
        else Path(os.environ.get("MARKET_DIR", ".market"))
    )
    return base.resolve().parent / EVENTS_FILE


class EventLedger:
    """Small WAL SQLite file; every write is one short transaction."""

    def __init__(self, path: Path) -> None:
        """Create tables on first use; the file is private to the agent."""
        self.path: Path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=1.0)) as db, db:
            _ = db.execute("PRAGMA journal_mode=WAL")
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS buyer_attempts (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, order_id TEXT NOT NULL,
                    shop TEXT NOT NULL, shop_name TEXT, sku TEXT, list_price INTEGER,
                    price INTEGER, outcome TEXT NOT NULL, reason TEXT, ledger INTEGER,
                    elapsed REAL)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS seller_receipts (
                    task TEXT PRIMARY KEY, ts REAL NOT NULL, unit TEXT, sku TEXT,
                    price INTEGER, cost INTEGER, grade TEXT, claim TEXT,
                    warranty_hours INTEGER, rounds INTEGER)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS payments (
                    order_id TEXT PRIMARY KEY, ts REAL NOT NULL, shop TEXT, item TEXT,
                    price INTEGER, paid INTEGER NOT NULL, ledger INTEGER,
                    reason TEXT)"""
            )
            _ = db.execute(
                "CREATE INDEX IF NOT EXISTS attempts_shop ON buyer_attempts(shop, ts)"
            )
        if os.name != "nt":
            path.chmod(0o600)

    def record_attempt(  # noqa: PLR0913 - one row, explicit columns
        self,
        *,
        order: str,
        shop: str,
        shop_name: str,
        sku: str,
        list_price: int,
        price: int | None,
        outcome: Outcome,
        reason: str = "",
        elapsed: float = 0.0,
    ) -> None:
        """Append what the buyer did for one order."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                """INSERT INTO buyer_attempts
                (ts, order_id, shop, shop_name, sku, list_price, price, outcome,
                 reason, elapsed) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(),
                    order,
                    shop,
                    shop_name,
                    sku,
                    list_price,
                    price,
                    outcome,
                    reason[:300],
                    elapsed,
                ),
            )

    def record_receipt(  # noqa: PLR0913 - one row, explicit columns
        self,
        *,
        task: str,
        unit: str,
        sku: str,
        price: int,
        cost: int,
        grade: str,
        claim: str,
        warranty_hours: int,
        rounds: int,
    ) -> None:
        """Append one signed sale; settlement is confirmed later from the ledger."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                """INSERT OR IGNORE INTO seller_receipts
                (task, ts, unit, sku, price, cost, grade, claim, warranty_hours, rounds)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    task,
                    time.time(),
                    unit,
                    sku,
                    price,
                    cost,
                    grade,
                    claim,
                    warranty_hours,
                    rounds,
                ),
            )

    def sync_payments(self, lines: Iterable[PaymentLine]) -> int:
        """Import payments.jsonl outcomes once per order; return rows added."""
        added = 0
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            for line in lines:
                if not line.order:
                    continue
                cursor = db.execute(
                    """INSERT OR IGNORE INTO payments
                    (order_id, ts, shop, item, price, paid, ledger, reason)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        line.order,
                        time.time(),
                        line.shop,
                        line.item,
                        line.price,
                        int(line.paid),
                        line.ledger,
                        line.reason,
                    ),
                )
                added += cursor.rowcount
        return added

    def shop_outcomes(self, hours: float) -> dict[str, dict[str, int]]:
        """Count paid/refused/agreed/no_deal per shop URL within the window."""
        since = time.time() - hours * 3600
        counts: dict[str, dict[str, int]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, outcome, n in db.execute(
                """SELECT shop, outcome, count(*) FROM buyer_attempts
                WHERE ts >= ? GROUP BY shop, outcome""",
                (since,),
            ):
                counts.setdefault(str(shop), {})[str(outcome)] = int(n)
            for shop, paid, n in db.execute(
                """SELECT shop, paid, count(*) FROM payments
                WHERE ts >= ? GROUP BY shop, paid""",
                (since,),
            ):
                key = "paid" if paid else "refused"
                bucket = counts.setdefault(str(shop), {})
                bucket[key] = bucket.get(key, 0) + int(n)
        return counts

    def refusal_reasons(self, hours: float) -> dict[str, str]:
        """Return the latest refusal reason per shop URL within the window."""
        since = time.time() - hours * 3600
        reasons: dict[str, str] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, reason in db.execute(
                """SELECT shop, reason FROM payments
                WHERE ts >= ? AND paid = 0 ORDER BY ts""",
                (since,),
            ):
                reasons[str(shop)] = str(reason or "")
        return reasons

    def summary(self, hours: float) -> dict[str, int]:
        """Count attempts, agreed, paid, refused and receipts for the manager."""
        since = time.time() - hours * 3600
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            attempts = db.execute(
                "SELECT count(*) FROM buyer_attempts WHERE ts >= ?", (since,)
            ).fetchone()[0]
            agreed = db.execute(
                "SELECT count(*) FROM buyer_attempts "
                "WHERE ts >= ? AND outcome='agreed'",
                (since,),
            ).fetchone()[0]
            paid = db.execute(
                "SELECT count(*) FROM payments WHERE ts >= ? AND paid = 1", (since,)
            ).fetchone()[0]
            refused = db.execute(
                "SELECT count(*) FROM payments WHERE ts >= ? AND paid = 0", (since,)
            ).fetchone()[0]
            receipts = db.execute(
                "SELECT count(*) FROM seller_receipts WHERE ts >= ?", (since,)
            ).fetchone()[0]
        return {
            "buyer_attempts": int(attempts),
            "buyer_agreed": int(agreed),
            "buyer_paid": int(paid),
            "buyer_refused": int(refused),
            "seller_receipts": int(receipts),
        }
