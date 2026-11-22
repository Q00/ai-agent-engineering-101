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

from pydantic import Field, TypeAdapter

from market_a2a.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from r3.market_files import Order, PaymentLine

EVENTS_ENV: Final = "AX_R3_EVENTS"
EVENTS_FILE: Final = "r3-events.sqlite"
ROUND_TWO: Final = 2
_TIMEOUT_COUNTS: Final = TypeAdapter(list[tuple[str, int]])
_ATTEMPT_TIMES: Final = TypeAdapter(list[tuple[str, str, str, float]])
_CLOSE_ROWS: Final = TypeAdapter(list[tuple[str, str, int, int]])
_YIELD_ROWS: Final = TypeAdapter(list[tuple[str, str, str, float, str | None]])
# A shop that rarely sells to us and takes long to say so spends the order's
# seconds other shops could use. 11/10 09:30-15:30: seven such shops won 3 of 409
# tries yet took 2,400 of 8,233 try-seconds (29 %), while olstjr took 5.9 s a try
# and won 53 %, so speed alone is not the test.
FRUITLESS_MIN_TRIES: Final = 10
FRUITLESS_MAX_WIN: Final = 0.03
FRUITLESS_MIN_SECONDS: Final = 4.0
_RECEIPT_SKUS: Final = TypeAdapter(list[tuple[str, str]])
# 11/16 08:5x (owner, Codex 08:12): psyoung82 settled 22 food orders and none of 68
# goods tries in 2 h, yet its food deals lifted it for goods orders too. Price, time
# and close statistics are kept per shop x order kind (food / goods); a shop with
# under KIND_MIN attempts of that kind mixes in its other-kind evidence in
# proportion, so a thin side is not judged on a handful of tries. No shop is blocked.
KIND_MIN: Final = 10


def _kind_weight(n_kind: int) -> float:
    return min(1.0, n_kind / KIND_MIN)


def blend_counts(
    mine: dict[str, int], every: dict[str, int], n_kind: int
) -> dict[str, int]:
    """Return this kind's counts plus the other kind's, scaled by how thin it is."""
    rest = 1.0 - _kind_weight(n_kind)
    keys = set(mine) | set(every)
    out = {
        k: mine.get(k, 0) + round(rest * (every.get(k, 0) - mine.get(k, 0)))
        for k in keys
    }
    return {k: v for k, v in out.items() if v}


def blend_value(mine: float | None, every: float, n_kind: int) -> float:
    """Return this kind's value mixed with the all-kinds value while samples are few."""
    if mine is None:
        return every
    w = _kind_weight(n_kind)
    return w * mine + (1.0 - w) * every


Outcome = Literal[
    "agreed", "no_deal", "paid", "refused", "unknown", "error", "suspicious"
]


class FoodEvidence(FrozenModel):
    """Observed food inputs, not proof of certificate validity or final utility.

    Later payments and public settlements/breaks remain authoritative and join
    by order/unit. Missing historical fields stay null; no outcome is inferred.
    """

    order_id: str = Field(min_length=1, strict=True)
    stage: Literal["order", "quote", "acceptance", "receipt"]
    observed_at: float = Field(ge=0, allow_inf_nan=False, strict=True)
    task_id: str | None = Field(default=None, strict=True)
    shop: str | None = Field(default=None, strict=True)
    sku: str | None = Field(default=None, strict=True)
    unit: str | None = Field(default=None, strict=True)
    use_within_hours: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    food_margin_hours: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    certificate_expires_at: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    seller_expires_at: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    stated_hours_left: float | None = Field(
        default=None, allow_inf_nan=False, strict=True
    )
    grade: Literal["A", "B", "C"] | None = None
    warranty_hours: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    price: int | None = Field(default=None, ge=0, strict=True)
    list_price: int | None = Field(default=None, ge=0, strict=True)
    certificate_attached: bool | None = Field(default=None, strict=True)
    policy_version: int | None = Field(default=None, ge=0, strict=True)


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


# Our own price walk-aways (owner emergency, 11/7): the shop answered, we left.
OWN_WALKAWAY_MARKERS: Final = (
    "above the emergency line",
    "expected credit below",
    "receipt expected credit below",
)


# 11/11: before this time our fake-settlement screen fired on the plain word
# "ledger" (r3/guard.py), so those "suspicious" rows say nothing about the shop.
SCREEN_BUG_UNTIL: Final = 1794360600.0  # 2026-11-11 10:30 KST
SCREEN_BUG_REASON: Final = "suspicious seller text: fake_settlement"


def screen_bug(reason: object, ts: object) -> bool:
    """Tell whether an attempt was dropped only by the old ledger-word bug."""
    try:
        when = float(str(ts))
    except ValueError:
        return False
    return str(reason or "") == SCREEN_BUG_REASON and when < SCREEN_BUG_UNTIL


def own_walkaway(reason: object) -> bool:
    """Tell whether an attempt ended because we left on price, not the shop."""
    text = str(reason or "")
    return any(marker in text for marker in OWN_WALKAWAY_MARKERS)


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
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS buyer_orders (
                    order_id TEXT PRIMARY KEY, observed_at REAL NOT NULL,
                    budget INTEGER, kind TEXT, expires REAL, policy_version INTEGER)"""
            )
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS buyer_food_evidence (
                    order_id TEXT NOT NULL, shop TEXT NOT NULL,
                    task_id TEXT NOT NULL, stage TEXT NOT NULL,
                    observed_at REAL NOT NULL, unit TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(order_id, shop, task_id, stage))"""
            )
            _ = db.execute(
                """CREATE INDEX IF NOT EXISTS buyer_attempt_order
                ON buyer_attempts(order_id)"""
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

    def record_order_metadata(
        self,
        order: Order,
        *,
        observed_at: float | None = None,
        policy_version: int | None = None,
    ) -> None:
        """Capture issued orders before selection; preserve the first observation."""
        if not order.order:
            return
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db, db:
            _ = db.execute(
                "INSERT OR IGNORE INTO buyer_orders VALUES (?,?,?,?,?,?)",
                (
                    order.order,
                    time.time() if observed_at is None else observed_at,
                    order.budget if order.budget > 0 else None,
                    order.kind,
                    order.expires,
                    policy_version,
                ),
            )

    def record_food_evidence(self, evidence: FoodEvidence) -> None:
        """Append one observed stage through the bounded batch writer."""
        self.record_food_evidence_batch((evidence,))

    def record_food_evidence_batch(self, evidence: Iterable[FoodEvidence]) -> None:
        """Flush after negotiation; SQLite lock waiting is limited to 50 ms."""
        with closing(sqlite3.connect(self.path, timeout=0.05)) as db, db:
            _ = db.executemany(
                """INSERT OR IGNORE INTO buyer_food_evidence
                (order_id, shop, task_id, stage, observed_at, unit, payload)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    (
                        row.order_id,
                        row.shop or "",
                        row.task_id or "",
                        row.stage,
                        row.observed_at,
                        row.unit,
                        row.model_dump_json(),
                    )
                    for row in evidence
                ),
            )

    def shop_timeout_counts(self, hours: float = 6) -> dict[str, int]:
        """Count first-reply timeouts only, indexed by normalized shop URL."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _TIMEOUT_COUNTS.validate_python(
                db.execute(
                    """SELECT rtrim(shop,'/'),count(*) FROM buyer_attempts
                    WHERE ts>=? AND reason='shop timeout' GROUP BY rtrim(shop,'/')""",
                    (time.time() - hours * 3600,),
                ).fetchall()
            )
        return dict(rows)

    def hung_shop_names(self, hours: float, min_count: int) -> frozenset[str]:
        """Lower-cased names of shops that left us waiting ``min_count`` times.

        Counted by shop name, not URL: tunnel shops come back under a new
        trycloudflare URL and a URL count starts again from zero (11/12
        outseop-26510122 hung 14 times across three URLs in two hours). A first
        reply over six seconds ("shop timeout") and an order clock that ran out
        at the shop ("timed out") both count.
        """
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _TIMEOUT_COUNTS.validate_python(
                db.execute(
                    """SELECT lower(shop_name),count(*) FROM buyer_attempts
                    WHERE ts>=? AND reason IN ('shop timeout','timed out')
                    GROUP BY lower(shop_name)""",
                    (time.time() - hours * 3600,),
                ).fetchall()
            )
        return frozenset(name for name, count in rows if count >= min_count)

    def close_counts(
        self, hours: float, skus: Collection[str]
    ) -> dict[str, tuple[int, int]]:
        """Lower-cased shop name -> (tries, deals) on these SKUs lately.

        By name for the same reason as :meth:`hung_shop_names`. The SKUs are the
        order's acceptable list, so the count is per product category.
        """
        wanted = frozenset(skus)
        counts: dict[str, tuple[int, int]] = {}
        if not wanted:
            return counts
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _CLOSE_ROWS.validate_python(
                # 11/15 10:5x (owner): a close is a paid agreement, not an agreed
                # price (cheapest-in-town: 26 agreed, 15 paid in 6 h).
                db.execute(
                    """SELECT lower(a.shop_name),a.sku,count(*),
                    sum(a.outcome='agreed' AND coalesce(p.paid,0)=1)
                    FROM buyer_attempts a LEFT JOIN payments p
                        ON p.order_id=a.order_id
                        AND rtrim(p.shop,'/')=rtrim(a.shop,'/')
                    WHERE a.ts>=? AND a.shop_name!=''
                    GROUP BY lower(a.shop_name),a.sku""",
                    (time.time() - hours * 3600,),
                ).fetchall()
            )
        for name, sku, tries, deals in rows:
            if sku in wanted:
                had = counts.get(name, (0, 0))
                counts[name] = (had[0] + tries, had[1] + deals)
        return counts

    def time_yield(
        self, hours: float, kind: str | None = None
    ) -> dict[str, tuple[float, int]]:
        """Lower-cased shop name -> (seconds of our order time spent, deals) lately.

        Seconds are each attempt's own share of its order's elapsed time, the same
        split :meth:`slow_fruitless_shops` uses. With ``kind`` both are mixed per
        :func:`blend_counts` (by attempts of that kind).
        """
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _YIELD_ROWS.validate_python(
                db.execute(
                    """SELECT a.order_id, lower(a.shop_name),
                    CASE WHEN a.outcome='agreed' AND coalesce(p.paid,0)=1
                        THEN 'agreed' ELSE 'no_deal' END,
                    coalesce(a.elapsed, 0.0), o.kind FROM buyer_attempts a
                    LEFT JOIN payments p ON p.order_id=a.order_id
                        AND rtrim(p.shop,'/')=rtrim(a.shop,'/')
                    LEFT JOIN buyer_orders o ON o.order_id=a.order_id
                    WHERE a.ts>=? AND a.shop_name!=''
                    ORDER BY a.order_id, a.ts, a.id""",
                    (time.time() - hours * 3600,),
                ).fetchall()
            )
        out: dict[str, tuple[float, int]] = {}
        mine: dict[str, tuple[float, int, int]] = {}
        order, before = "", 0.0
        for order_id, name, outcome, elapsed, order_kind in rows:
            if order_id != order:
                order, before = order_id, 0.0
            spent, deals = out.get(name, (0.0, 0))
            took = max(0.0, elapsed - before)
            won = int(outcome == "agreed")
            out[name] = (spent + took, deals + won)
            if kind is not None and order_kind == kind:
                s0, d0, n0 = mine.get(name, (0.0, 0, 0))
                mine[name] = (s0 + took, d0 + won, n0 + 1)
            before = elapsed
        if kind is None:
            return out
        mixed: dict[str, tuple[float, int]] = {}
        for name, (spent, deals) in out.items():
            s0, d0, n0 = mine.get(name, (0.0, 0, 0))
            rest = 1.0 - _kind_weight(n0)
            mixed[name] = (s0 + rest * (spent - s0), d0 + round(rest * (deals - d0)))
        return mixed

    def slow_fruitless_shops(self, hours: float = 6) -> frozenset[str]:
        """Return normalized URLs of shops both slow and almost never selling to us."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _ATTEMPT_TIMES.validate_python(
                db.execute(
                    """SELECT order_id, rtrim(shop,'/'), outcome, coalesce(elapsed, 0.0)
                    FROM buyer_attempts WHERE ts>=? AND NOT (reason=? AND ts<?)
                    ORDER BY order_id, ts, id""",
                    (time.time() - hours * 3600, SCREEN_BUG_REASON, SCREEN_BUG_UNTIL),
                ).fetchall()
            )
        seconds: dict[str, list[float]] = {}
        wins: dict[str, int] = {}
        order, before = "", 0.0
        for order_id, shop, outcome, elapsed in rows:
            if order_id != order:
                order, before = order_id, 0.0
            seconds.setdefault(shop, []).append(max(0.0, elapsed - before))
            wins[shop] = wins.get(shop, 0) + (outcome == "agreed")
            before = elapsed
        return frozenset(
            shop
            for shop, spent in seconds.items()
            if len(spent) >= FRUITLESS_MIN_TRIES
            and wins[shop] / len(spent) < FRUITLESS_MAX_WIN
            and statistics.median(spent) >= FRUITLESS_MIN_SECONDS
        )

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

    def shop_outcomes(
        self, hours: float, kind: str | None = None
    ) -> dict[str, dict[str, int]]:
        """Count paid/refused/agreed/no_deal per shop URL within the window.

        With ``kind`` ("food" / "goods") the counts are that kind's orders, mixed
        with the other kind's while the shop has under KIND_MIN of this kind.
        """
        since = time.time() - hours * 3600
        counts: dict[str, dict[str, int]] = {}
        mine: dict[str, dict[str, int]] = {}
        tries: dict[str, int] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, outcome, reason, ts, order_kind in db.execute(
                """SELECT a.shop, a.outcome, a.reason, a.ts, o.kind
                FROM buyer_attempts a LEFT JOIN buyer_orders o
                    ON o.order_id = a.order_id
                WHERE a.ts >= ?""",
                (since,),
            ):
                if screen_bug(reason, ts):
                    continue
                url = str(shop).rstrip("/")
                key = "walked" if own_walkaway(reason) else str(outcome)
                bucket = counts.setdefault(url, {})
                bucket[key] = bucket.get(key, 0) + 1
                if kind is not None and order_kind == kind:
                    own = mine.setdefault(url, {})
                    own[key] = own.get(key, 0) + 1
                    tries[url] = tries.get(url, 0) + 1
            for shop, paid, order_kind, n in db.execute(
                """SELECT p.shop, p.paid, o.kind, count(*) FROM payments p
                LEFT JOIN buyer_orders o ON o.order_id = p.order_id
                WHERE p.ts >= ? GROUP BY p.shop, p.paid, o.kind""",
                (since,),
            ):
                url = str(shop).rstrip("/")
                key = "paid" if paid else "refused"
                bucket = counts.setdefault(url, {})
                bucket[key] = bucket.get(key, 0) + int(n)
                if kind is not None and order_kind == kind:
                    own = mine.setdefault(url, {})
                    own[key] = own.get(key, 0) + int(n)
        if kind is None:
            return counts
        return {
            url: blend_counts(mine.get(url, {}), every, tries.get(url, 0))
            for url, every in counts.items()
        }

    def wall_offers(self, hours: float) -> dict[tuple[str, str], int]:
        """(shop URL, SKU) -> times the shop would not come down to our line lately."""
        since = time.time() - hours * 3600
        out: dict[tuple[str, str], int] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, sku, n in db.execute(
                """SELECT shop, sku, count(*) FROM buyer_attempts
                WHERE ts >= ? AND reason LIKE '%above the emergency line%'
                GROUP BY shop, sku""",
                (since,),
            ):
                out[(str(shop).rstrip("/"), str(sku))] = int(n)
        return out

    def receipt_refusals(self, hours: float) -> dict[str, int]:
        """Shop URL -> signed receipts we refused (grade, warranty or credit line)."""
        since = time.time() - hours * 3600
        out: dict[str, int] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, n in db.execute(
                """SELECT shop, count(*) FROM buyer_attempts
                WHERE ts >= ? AND reason LIKE 'receipt expected credit below%'
                GROUP BY shop""",
                (since,),
            ):
                out[str(shop).rstrip("/")] = int(n)
        return out

    def receipt_refused_skus(
        self, hours: float, minimum: int = 3
    ) -> frozenset[tuple[str, str]]:
        """Find repeated receipt failures for a shop and selected SKU only.

        Count distinct orders per price, quality or identity cause. Recovery
        requires a later agreed attempt with the same order/shop/SKU in a paid
        kit row carrying its ledger number. Import time is not transaction time.
        The selected SKU is known here; unit and grade identity are not. This
        memory narrows exploration, never replaces the final receipt checks.
        """
        now = time.time()
        since = now - hours * 3600
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _RECEIPT_SKUS.validate_python(
                db.execute(
                    """WITH paid_attempts AS (
                        SELECT rtrim(a.shop, '/') AS shop, a.sku, a.ts, a.id,
                            row_number() OVER (
                                PARTITION BY rtrim(a.shop, '/'), a.sku
                                ORDER BY a.ts DESC, a.id DESC
                            ) AS recency
                        FROM buyer_attempts a JOIN payments p
                            ON p.order_id = a.order_id
                            AND rtrim(p.shop, '/') = rtrim(a.shop, '/')
                            AND p.item = a.sku
                        WHERE a.ts BETWEEN ? AND ? AND a.market_round = 3
                            AND a.outcome = 'agreed' AND p.paid = 1
                            AND p.ledger IS NOT NULL
                    ), failures AS (
                        SELECT rtrim(a.shop, '/') AS shop, a.sku, a.order_id,
                            CASE
                                WHEN a.reason LIKE '%shipped SKU unknown:%'
                                    THEN 'identity'
                                WHEN a.reason LIKE '%of the shipped list%'
                                    THEN 'price'
                                ELSE 'quality'
                            END AS cause
                        FROM buyer_attempts a LEFT JOIN paid_attempts p
                            ON p.shop = rtrim(a.shop, '/') AND p.sku = a.sku
                            AND p.recency = 1
                        WHERE a.ts BETWEEN ? AND ? AND a.market_round = 3
                            AND a.outcome = 'no_deal'
                            AND trim(a.sku) <> '' AND trim(a.shop) <> ''
                            AND trim(a.order_id) <> ''
                            AND a.reason LIKE 'receipt expected credit below%'
                            AND (p.id IS NULL OR a.ts > p.ts
                                OR (a.ts = p.ts AND a.id > p.id))
                    )
                    SELECT shop, sku FROM failures
                    GROUP BY shop, sku, cause
                    HAVING count(DISTINCT order_id) >= ?""",
                    (since, now, since, now, minimum),
                ).fetchall()
            )
        return frozenset(rows)

    def repeated_receipt_refusals(
        self, minutes: float, minimum: int = 2
    ) -> frozenset[tuple[str, str]]:
        """(shop URL, SKU) refused at the receipt for one reason in ``minimum`` orders.

        11/15 10:5x (owner): the same shop and SKU failed the receipt check for the
        same cause again and again (Kiro729 G67e6f916 "claim B, warranty 0 h" in 8
        orders over 2 h, dae-yangkim B with a 3 h warranty 22 times). Only that pair
        rests for ``minutes``; the shop's other SKUs are still bought.
        """
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _RECEIPT_SKUS.validate_python(
                db.execute(
                    """SELECT rtrim(shop,'/'), sku FROM buyer_attempts
                    WHERE ts>=? AND outcome='no_deal'
                        AND reason LIKE 'receipt expected credit below%'
                        AND trim(sku)<>'' AND trim(shop)<>''
                    GROUP BY rtrim(shop,'/'), sku, reason
                    HAVING count(DISTINCT order_id) >= ?""",
                    (time.time() - minutes * 60, minimum),
                ).fetchall()
            )
        return frozenset(rows)

    def repeated_stock_misses(
        self, minutes: float, minimum: int = 2
    ) -> frozenset[tuple[str, str]]:
        """(shop URL, SKU) that answered "none in stock" in ``minimum`` orders lately.

        11/15 17:0x (owner): after 15:00, 22 shop/SKU pairs said none in stock 3+
        times for 125 attempts (dae-yangkim G1957829c 28, G16822d88 10).
        """
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _RECEIPT_SKUS.validate_python(
                db.execute(
                    """SELECT rtrim(shop,'/'), sku FROM buyer_attempts
                    WHERE ts>=? AND outcome='no_deal'
                        AND (reason LIKE 'NoStockError%'
                            OR reason LIKE '%none of that%')
                        AND trim(sku)<>'' AND trim(shop)<>''
                    GROUP BY rtrim(shop,'/'), sku
                    HAVING count(DISTINCT order_id) >= ?""",
                    (time.time() - minutes * 60, minimum),
                ).fetchall()
            )
        return frozenset(rows)

    def losing_streak_pairs(
        self, streak: int, minutes: float, memory_hours: float = 6.0
    ) -> frozenset[tuple[str, str]]:
        """(shop URL, SKU) whose last ``streak`` attempts all failed, lately.

        11/17 08:5x (owner, strategy 08:51): in 3 h 24 pairs took 242 of 1,967
        attempts (12 %) with 6+ tries and no deal. A pair whose last ``streak``
        attempts were all ``no_deal`` rests until ``minutes`` after the last one; an
        agreed attempt clears it.
        """
        now = time.time()
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = db.execute(
                """SELECT rtrim(shop,'/'), sku, ts, outcome FROM buyer_attempts
                WHERE ts>=? AND trim(sku)<>'' AND trim(shop)<>''
                ORDER BY ts DESC""",
                (now - memory_hours * 3600,),
            ).fetchall()
        recent: dict[tuple[str, str], list[tuple[float, str]]] = {}
        for shop, sku, ts, outcome in rows:
            seen = recent.setdefault((str(shop), str(sku)), [])
            if len(seen) < streak:
                seen.append((float(ts), str(outcome)))
        return frozenset(
            pair
            for pair, seen in recent.items()
            if len(seen) == streak
            and all(outcome == "no_deal" for _, outcome in seen)
            and now - seen[0][0] <= minutes * 60
        )

    def losing_shops(
        self, streak: int, minutes: float, memory_hours: float = 6.0
    ) -> frozenset[str]:
        """Shop URLs whose last ``streak`` attempts all ended in no_deal, lately.

        11/17 10:5x (owner, strategy 10:52): gom31-certified-goods took 35 attempts
        and 5.4 minutes in an hour with no deal. A shop rests until ``minutes``
        after its last such attempt; a deal clears it.
        """
        now = time.time()
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = db.execute(
                """SELECT rtrim(shop,'/'), ts, outcome FROM buyer_attempts
                WHERE ts>=? AND trim(shop)<>'' ORDER BY ts DESC""",
                (now - memory_hours * 3600,),
            ).fetchall()
        recent: dict[str, list[tuple[float, str]]] = {}
        for shop, ts, outcome in rows:
            seen = recent.setdefault(str(shop), [])
            if len(seen) < streak:
                seen.append((float(ts), str(outcome)))
        return frozenset(
            shop
            for shop, seen in recent.items()
            if len(seen) == streak
            and all(outcome == "no_deal" for _, outcome in seen)
            and now - seen[0][0] <= minutes * 60
        )

    def stale_unit_pairs(self, minutes: float) -> frozenset[tuple[str, str]]:
        """(shop URL, SKU) whose checkout said the shipped unit is out of stock.

        11/15 10:5x (owner): cheapest-in-town agreed 11 deals in 6 h on one unit
        (u-a4ee3e5ee801) that every checkout refused with 409 "not in stock". The
        pair rests for ``minutes`` so the order goes to the next shop at once.
        """
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            rows = _RECEIPT_SKUS.validate_python(
                db.execute(
                    """SELECT DISTINCT rtrim(shop,'/'), item FROM payments
                    WHERE ts>=? AND paid=0 AND item IS NOT NULL AND shop IS NOT NULL
                        AND reason LIKE 'checkout refused: 409%not in stock%'""",
                    (time.time() - minutes * 60,),
                ).fetchall()
            )
        return frozenset(rows)

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
            for shop, outcome, ts, market_round, paid, reason in db.execute(
                """SELECT a.shop, a.outcome, a.ts, a.market_round, p.paid, a.reason
                FROM buyer_attempts a LEFT JOIN payments p
                ON p.order_id = a.order_id
                AND rtrim(p.shop, '/') = rtrim(a.shop, '/')
                WHERE a.ts >= ? ORDER BY a.ts, a.id""",
                (since,),
            ):
                if own_walkaway(reason) or screen_bug(reason, ts):
                    continue  # we left on price, or our old screen bug: not the shop
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

    def stock_misses(
        self, hours: float, kind: str | None = None
    ) -> dict[str, tuple[int, int]]:
        """Per shop URL: (asks answered "none in stock", asks) within the window.

        With ``kind`` the counts are mixed per :func:`blend_counts`.
        """
        since = time.time() - hours * 3600
        counts: dict[str, tuple[int, int]] = {}
        mine: dict[str, tuple[int, int]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, reason, order_kind in db.execute(
                """SELECT a.shop, a.reason, o.kind FROM buyer_attempts a
                LEFT JOIN buyer_orders o ON o.order_id = a.order_id
                WHERE a.ts >= ?""",
                (since,),
            ):
                text = str(reason or "").lower()
                miss = "in stock" in text or "out of stock" in text
                key = str(shop).rstrip("/")
                misses, asks = counts.get(key, (0, 0))
                counts[key] = (misses + int(miss), asks + 1)
                if kind is not None and order_kind == kind:
                    misses, asks = mine.get(key, (0, 0))
                    mine[key] = (misses + int(miss), asks + 1)
        if kind is None:
            return counts
        out: dict[str, tuple[int, int]] = {}
        for key, (misses, asks) in counts.items():
            own = mine.get(key, (0, 0))
            mixed = blend_counts(
                {"miss": own[0], "ask": own[1]}, {"miss": misses, "ask": asks}, own[1]
            )
            out[key] = (mixed.get("miss", 0), mixed.get("ask", 0))
        return out

    def price_ratios(self, hours: float, kind: str | None = None) -> dict[str, float]:
        """Median actual paid/list share, excluding pending and refused agreements.

        With ``kind`` the shop's median for that kind, mixed per :func:`blend_value`.
        """
        since = time.time() - hours * 3600
        ratios: dict[str, list[float]] = {}
        mine: dict[str, list[float]] = {}
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            for shop, price, list_price, order_kind in db.execute(
                """SELECT a.shop, p.price, a.list_price, o.kind FROM buyer_attempts a
                JOIN payments p ON p.order_id = a.order_id
                AND rtrim(p.shop, '/') = rtrim(a.shop, '/')
                LEFT JOIN buyer_orders o ON o.order_id = a.order_id
                WHERE a.ts >= ? AND a.outcome = 'agreed' AND p.paid = 1
                AND p.price > 0 AND a.list_price > 0""",
                (since,),
            ):
                key = str(shop).rstrip("/")
                ratio = float(price) / float(list_price)
                ratios.setdefault(key, []).append(ratio)
                if kind is not None and order_kind == kind:
                    mine.setdefault(key, []).append(ratio)
        every = {k: statistics.median(v) for k, v in ratios.items()}
        if kind is None:
            return every
        return {
            k: blend_value(
                statistics.median(mine[k]) if mine.get(k) else None,
                v,
                len(mine.get(k, [])),
            )
            for k, v in every.items()
        }

    def receipt_costs(self, since: float) -> dict[str, int]:
        """Return the cost of each unit our seller signed a receipt for since then."""
        with closing(sqlite3.connect(self.path, timeout=1.0)) as db:
            return {
                str(unit): int(cost)
                for unit, cost in db.execute(
                    "SELECT unit, cost FROM seller_receipts WHERE ts >= ?", (since,)
                )
                if unit and cost is not None
            }

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
