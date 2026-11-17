"""Our own event ledger: what we tried, what the kit paid, what the market settled.

Separate from decision memory and from the public ledger copy. Rows are appended
by the buyer (attempts), the seller (signed receipts) and the manager (payment
outcomes read back from payments.jsonl). Nothing here is ever rewritten; counts for
the report are derived with the market's own ledger numbers as the identity.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

    from r3.market_files import PaymentLine

EVENTS_ENV: Final = "AX_R3_EVENTS"
EVENTS_FILE: Final = "r3-events.sqlite"
ROUND_TWO: Final = 2
Outcome = Literal[
    "agreed", "no_deal", "paid", "refused", "unknown", "error", "suspicious"
]


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


def refusal_kind(reason: str) -> str:
    """Name the cause of one unpaid agreed deal from the kit's reason text."""
    text = reason.lower()
    for key, needle in (
        ("warranty", "warranty"),
        ("grade", "grade"),
        ("certificate", "certificate"),
        ("shelf_life", "shelf life"),
        ("out_of_stock", "not in stock"),
        ("market_refused", "refused by the market"),
    ):
        if needle in text:
            return key
    return "other"


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
                    elapsed REAL, market_round INTEGER NOT NULL DEFAULT 0)"""
            )
            # Retain old evidence; unknown rounds must not be guessed from a
            # missing catalog price. Older releases did not store this column.
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(buyer_attempts)")
            }
            if "market_round" not in columns:
                # Buyer and manager can start together against an old database.
                _ = db.execute("BEGIN IMMEDIATE")
                columns = {
                    row[1] for row in db.execute("PRAGMA table_info(buyer_attempts)")
                }
                if "market_round" not in columns:
                    _ = db.execute(
                        "ALTER TABLE buyer_attempts ADD COLUMN "
                        "market_round INTEGER NOT NULL DEFAULT 0"
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
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS policy_changes (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL,
                    layer TEXT NOT NULL, field TEXT NOT NULL, before TEXT, after TEXT,
                    reason TEXT, expires_at REAL, version INTEGER,
                    accepted INTEGER NOT NULL, note TEXT)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS model_decisions (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, role TEXT NOT NULL,
                    model TEXT NOT NULL, latency_ms INTEGER, input_tokens INTEGER,
                    output_tokens INTEGER, cost_usd REAL, accepted INTEGER NOT NULL,
                    summary TEXT, input_sha256 TEXT)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS model_spend (
                    day TEXT PRIMARY KEY, usd REAL NOT NULL, calls INTEGER NOT NULL)"""
            )
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)"
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS loop_runs (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, role TEXT NOT NULL,
                    outcome TEXT NOT NULL, detail TEXT)"""
            )
            _ = db.execute(
                "CREATE INDEX IF NOT EXISTS changes_field ON policy_changes(field, ts)"
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
        market_round: int = 0,
    ) -> None:
        """Append what the buyer did for one order."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                """INSERT INTO buyer_attempts
                (ts, order_id, shop, shop_name, sku, list_price, price, outcome,
                 reason, elapsed, market_round) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
                    market_round,
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
                bucket = counts.setdefault(str(shop).rstrip("/"), {})
                bucket[str(outcome)] = bucket.get(str(outcome), 0) + int(n)
            for shop, paid, n in db.execute(
                """SELECT shop, paid, count(*) FROM payments
                WHERE ts >= ? GROUP BY shop, paid""",
                (since,),
            ):
                key = "paid" if paid else "refused"
                bucket = counts.setdefault(str(shop).rstrip("/"), {})
                bucket[key] = bucket.get(key, 0) + int(n)
        return counts

    def failure_streaks(self, hours: float) -> dict[str, tuple[int, float]]:
        """Count failed orders; only a completed payment clears an R3 streak.

        Pending R3 agreements are unknown, so neither clear nor extend a streak.
        R2 has no payment step; its explicitly marked signed agreements still clear
        failures. Payment rows must match both our order and the selected shop.
        """
        now = time.time()
        since = now - hours * 3600
        streaks: dict[str, tuple[int, float]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, outcome, ts, market_round, paid in db.execute(
                """SELECT a.shop, a.outcome, a.ts, a.market_round, p.paid
                FROM buyer_attempts a LEFT JOIN payments p
                ON p.order_id = a.order_id
                AND rtrim(p.shop, '/') = rtrim(a.shop, '/')
                WHERE a.ts >= ? ORDER BY a.ts, a.id""",
                (since,),
            ):
                key = str(shop).rstrip("/")
                count, _ = streaks.get(key, (0, 0.0))
                if (
                    outcome in {"no_deal", "suspicious", "error", "refused"}
                    or paid == 0
                ):
                    streaks[key] = (count + 1, float(ts))
                elif paid == 1 or (outcome == "agreed" and market_round == ROUND_TWO):
                    streaks[key] = (0, float(ts))
        return {k: (n, now - last) for k, (n, last) in streaks.items() if n > 0}

    def stock_misses(self, hours: float) -> dict[str, tuple[int, int]]:
        """Per shop URL: (asks answered "none in stock", asks) within the window."""
        since = time.time() - hours * 3600
        counts: dict[str, tuple[int, int]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, reason in db.execute(
                "SELECT shop, reason FROM buyer_attempts WHERE ts >= ?", (since,)
            ):
                text = str(reason or "").lower()
                miss = "in stock" in text or "out of stock" in text
                key = str(shop).rstrip("/")
                misses, asks = counts.get(key, (0, 0))
                counts[key] = (misses + int(miss), asks + 1)
        return counts

    def price_ratios(self, hours: float) -> dict[str, float]:
        """Median actual paid/list share, excluding pending and refused agreements."""
        since = time.time() - hours * 3600
        ratios: dict[str, list[float]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, price, list_price in db.execute(
                """SELECT a.shop, p.price, a.list_price FROM buyer_attempts a
                JOIN payments p ON p.order_id = a.order_id
                AND rtrim(p.shop, '/') = rtrim(a.shop, '/')
                WHERE a.ts >= ? AND a.outcome = 'agreed' AND p.paid = 1
                AND p.price > 0 AND a.list_price > 0""",
                (since,),
            ):
                key = str(shop).rstrip("/")
                ratios.setdefault(key, []).append(float(price) / float(list_price))
        return {k: statistics.median(v) for k, v in ratios.items()}

    def refusal_counts(self, hours: float, floor: float = 0.0) -> dict[str, int]:
        """Agreed deals the kit did not pay, by reason, for the strategy model.

        Without the breakdown the strategy saw only "118 refused" and asked for
        it (engineer notes, 11/4): on 11/4 most were our own accept conditions
        (warranty under 6 h, grade C), not shops.
        """
        since = max(time.time() - hours * 3600, floor)
        counts: dict[str, int] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for (reason,) in db.execute(
                "SELECT reason FROM payments WHERE ts >= ? AND paid = 0", (since,)
            ):
                key = refusal_kind(str(reason or ""))
                counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def unfit_offers(self, hours: float) -> list[tuple[float, str, str, str]]:
        """Unpaid agreed deals refused for the unit itself: (ts, shop, item, reason).

        The same shop usually offers the same unit again, so the buyer avoids a
        (shop, SKU) whose last unit was grade C or too short-lived for the order.
        """
        since = time.time() - hours * 3600
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = db.execute(
                """SELECT ts, shop, item, reason FROM payments
                WHERE ts >= ? AND paid = 0 AND reason IS NOT NULL ORDER BY ts""",
                (since,),
            ).fetchall()
        return [
            (float(ts), str(shop).rstrip("/"), str(item), str(reason))
            for ts, shop, item, reason in rows
            if refusal_kind(str(reason)) in {"grade", "shelf_life"}
        ]

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
                reasons[str(shop).rstrip("/")] = str(reason or "")
        return reasons

    def record_policy_change(  # noqa: PLR0913 - one row, explicit columns
        self,
        *,
        actor: str,
        layer: str,
        field: str,
        before: object,
        after: object,
        reason: str,
        expires_at: float | None,
        version: int | None,
        accepted: bool,
        note: str = "",
    ) -> None:
        """Append one proposed policy change and whether code accepted it."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                """INSERT INTO policy_changes
                (ts, actor, layer, field, before, after, reason, expires_at, version,
                 accepted, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(),
                    actor[:40],
                    layer[:16],
                    field[:64],
                    json.dumps(before)[:200],
                    json.dumps(after)[:200],
                    reason[:300],
                    expires_at,
                    version,
                    int(accepted),
                    note[:200],
                ),
            )

    def record_loop_run(self, role: str, outcome: str, detail: str = "") -> None:
        """Append one manager loop run: what it did, or why it kept the policy."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                "INSERT INTO loop_runs (ts, role, outcome, detail) VALUES (?,?,?,?)",
                (time.time(), role[:16], outcome[:24], detail[:300]),
            )

    def loop_runs(self, since: float) -> list[dict[str, object]]:
        """Return loop runs after ``since`` (epoch), oldest first."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = db.execute(
                """SELECT ts, role, outcome, detail FROM loop_runs
                WHERE ts > ? ORDER BY ts DESC, id DESC LIMIT 500""",
                (since,),
            ).fetchall()
        return [
            {
                "ts": float(ts),
                "role": str(role),
                "outcome": str(outcome),
                "detail": str(detail or ""),
            }
            for ts, role, outcome, detail in reversed(rows)
        ]

    def tactical_signals(self, hours: float) -> dict[str, int]:
        """Count what the tactical triggers read: buyer closes, seller first asks."""
        since = time.time() - hours * 3600
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            attempts, agreed = db.execute(
                """SELECT count(*), coalesce(sum(outcome='agreed'), 0)
                FROM buyer_attempts WHERE ts >= ?""",
                (since,),
            ).fetchone()
            # Attempts where a price was on the table: a shop that had no stock,
            # timed out, or broke the protocol is not a buyer walking away (first
            # night: 54 of 60 no-deals were "none of that in stock").
            priced = db.execute(
                """SELECT count(*) FROM buyer_attempts WHERE ts >= ? AND NOT (
                    outcome = 'no_deal' AND (
                        reason LIKE '%REJECTED%' OR reason LIKE '%timed out%'
                        OR reason LIKE '%Error%' OR reason LIKE '%not signed%'
                        OR reason LIKE '%HTTP%' OR reason LIKE '%stock%'))""",
                (since,),
            ).fetchone()[0]
            receipts, first_ask = db.execute(
                """SELECT count(*), coalesce(sum(coalesce(rounds, 0) <= 1), 0)
                FROM seller_receipts WHERE ts >= ?""",
                (since,),
            ).fetchone()
        return {
            "buyer_attempts": int(attempts),
            "buyer_priced": int(priced),
            "buyer_agreed": int(agreed),
            "seller_receipts": int(receipts),
            "seller_first_ask": int(first_ask),
        }

    def last_direction(self, field: str, within_seconds: float) -> int | None:
        """Return +1/-1 for the latest accepted move of a field inside the window."""
        since = time.time() - within_seconds
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            row = db.execute(
                """SELECT before, after FROM policy_changes
                WHERE field = ? AND accepted = 1 AND ts >= ?
                ORDER BY ts DESC LIMIT 1""",
                (field, since),
            ).fetchone()
        if row is None:
            return None
        try:
            before, after = float(json.loads(row[0])), float(json.loads(row[1]))
        except (TypeError, ValueError):
            return None
        if after == before:
            return None
        return 1 if after > before else -1

    def recent_policy_changes(self, hours: float) -> list[dict[str, object]]:
        """Return accepted and refused changes of the window, oldest first."""
        since = time.time() - hours * 3600
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = db.execute(
                """SELECT ts, actor, layer, field, before, after, reason, accepted, note
                FROM policy_changes WHERE ts >= ?
                ORDER BY ts DESC, id DESC LIMIT 200""",
                (since,),
            ).fetchall()
        return [
            {
                "ts": round(float(ts)),
                "actor": str(actor),
                "layer": str(layer),
                "field": str(field),
                "before": before,
                "after": after,
                "reason": str(reason or "")[:120],
                "accepted": bool(accepted),
                "note": str(note or "")[:80],
            }
            for (
                ts,
                actor,
                layer,
                field,
                before,
                after,
                reason,
                accepted,
                note,
            ) in reversed(rows)
        ]

    def record_model_decision(  # noqa: PLR0913 - one row, explicit columns
        self,
        *,
        role: str,
        model: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        accepted: bool,
        summary: str,
        input_sha256: str,
        day: str,
    ) -> None:
        """Append one model call and add its cost to the day's spend."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                """INSERT INTO model_decisions
                (ts, role, model, latency_ms, input_tokens, output_tokens, cost_usd,
                 accepted, summary, input_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(),
                    role[:24],
                    model[:48],
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    int(accepted),
                    summary[:300],
                    input_sha256[:64],
                ),
            )
            _ = db.execute(
                """INSERT INTO model_spend(day, usd, calls) VALUES (?, ?, 1)
                ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd,
                calls = calls + 1""",
                (day, cost_usd),
            )

    def spend(self, day: str) -> tuple[float, int]:
        """Return (usd, calls) recorded for a KST day."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            row = db.execute(
                "SELECT usd, calls FROM model_spend WHERE day = ?", (day,)
            ).fetchone()
        return (float(row[0]), int(row[1])) if row else (0.0, 0)

    def model_summary(self, hours: float) -> dict[str, dict[str, float]]:
        """Count calls, acceptances, mean latency and cost per model role."""
        since = time.time() - hours * 3600
        out: dict[str, dict[str, float]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for role, n, accepted, latency, cost in db.execute(
                """SELECT role, count(*), sum(accepted), avg(latency_ms), sum(cost_usd)
                FROM model_decisions WHERE ts >= ? GROUP BY role""",
                (since,),
            ):
                out[str(role)] = {
                    "calls": int(n),
                    "accepted": int(accepted or 0),
                    "avg_latency_ms": round(float(latency or 0)),
                    "cost_usd": round(float(cost or 0), 4),
                }
        return out

    def get_value(self, key: str) -> str | None:
        """Read one small shared setting (cooldowns, alert counters)."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def set_value(self, key: str, value: str) -> None:
        """Write one shared setting or a cached public document (bounded)."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                "INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)",
                (key, value[:200_000]),
            )

    def summary(self, hours: float, floor: float = 0.0) -> dict[str, int]:
        """Count attempts, agreed, paid, refused and receipts for the manager.

        ``floor`` cuts the window at an epoch (Round 3 open): a 24 h window on
        the first night still held 1,582 Round 0-2 agreements that were never
        paid, which the strategy read as an 8.7 % payment rate.
        """
        since = max(time.time() - hours * 3600, floor)
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
