"""Incremental copy of the public market ledger, the agent's largest memory.

The market publishes nothing about demand, but every settled deal, every broken
good and every order appear on ``/api/ledger``. This module pulls the ledger forward
from the last sequence number (500 rows a page), stores the rows in SQLite and
answers the questions the policy loop and the restock hook ask: which categories
and SKUs settle, at what share of list price, how often goods of each grade break,
and how many settlements our own shop and buyer have.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import httpx

    from r3.market_files import CatalogItem

LEDGER_ENV: Final = "AX_R3_LEDGER"
LEDGER_FILE: Final = "r3-ledger.sqlite"
PAGE: Final = 500
BREAK_WINDOW: Final = 6 * 3600
# Goods can break up to 6 h after a sale, so the quality cohort is settlements old
# enough to have matured; the manager's 6 h trend window alone would hold none.
MATURE_COHORT_HOURS: Final = 24


def ledger_path(market_dir: Path | None = None) -> Path:
    """Resolve the ledger database path: explicit env, else beside ``.market``."""
    configured = os.environ.get(LEDGER_ENV)
    if configured:
        return Path(configured)
    base = (
        market_dir
        if market_dir is not None
        else Path(os.environ.get("MARKET_DIR", ".market"))
    )
    return base.resolve().parent / LEDGER_FILE


class LedgerDB:
    """SQLite mirror of the rows we have seen; ``cursor`` is the last seq ingested."""

    def __init__(self, path: Path) -> None:
        """Create the schema on first use."""
        self.path: Path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=2.0)) as db, db:
            _ = db.execute("PRAGMA journal_mode=WAL")
            _ = db.execute(
                """CREATE TABLE IF NOT EXISTS rows (
                    seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, round INTEGER,
                    buyer TEXT, shop TEXT, sku TEXT, amount INTEGER, grade TEXT,
                    claim TEXT, claim_honest INTEGER, data TEXT)"""
            )
            _ = db.execute("CREATE INDEX IF NOT EXISTS rows_kind_ts ON rows(kind, ts)")
            _ = db.execute("CREATE INDEX IF NOT EXISTS rows_sku ON rows(sku)")
            _ = db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )

    @property
    def cursor(self) -> int:
        """The highest sequence number stored, or 0."""
        with closing(sqlite3.connect(self.path, timeout=2.0)) as db:
            row = db.execute("SELECT max(seq) FROM rows").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def ingest(self, rows: Iterable[Mapping[str, object]]) -> int:
        """Store ledger rows idempotently; return how many were new."""
        added = 0
        with closing(sqlite3.connect(self.path, timeout=2.0)) as db, db:
            for row in rows:
                data = row.get("data")
                if not isinstance(data, dict):
                    data = {}
                honest = data.get("claim_honest")
                cursor = db.execute(
                    """INSERT OR IGNORE INTO rows
                    (seq, ts, kind, round, buyer, shop, sku, amount, grade, claim,
                     claim_honest, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(str(row.get("seq", 0))),
                        float(str(row.get("ts", 0.0))),
                        str(row.get("kind", "")),
                        int(str(row.get("round", 0) or 0)),
                        str(data.get("buyer", "") or ""),
                        str(data.get("shop", "") or ""),
                        str(data.get("item", "") or ""),
                        int(data["amount"])
                        if isinstance(data.get("amount"), int)
                        else None,
                        str(data.get("grade", "") or ""),
                        str(data.get("claim", "") or ""),
                        None if honest is None else int(bool(honest)),
                        json.dumps(data, ensure_ascii=False)[:4000],
                    ),
                )
                added += cursor.rowcount
        return added

    def settles(self, since: float) -> list[tuple[float, str, str, str, int, str, str]]:
        """Settle rows since ``since``: (ts, buyer, shop, sku, amount, grade, claim)."""
        with closing(sqlite3.connect(self.path, timeout=2.0)) as db:
            return [
                (float(ts), str(b), str(s), str(k), int(a or 0), str(g), str(c))
                for ts, b, s, k, a, g, c in db.execute(
                    """SELECT ts, buyer, shop, sku, amount, grade, claim FROM rows
                    WHERE kind='settle' AND ts >= ? ORDER BY ts""",
                    (since,),
                )
            ]

    def break_rates(self, since: float) -> dict[str, tuple[int, int]]:
        """Per grade: (broken, settled goods observed 6 h or older) since ``since``."""
        now = time.time()
        with closing(sqlite3.connect(self.path, timeout=2.0)) as db:
            settled = db.execute(
                """SELECT buyer, sku, grade, ts FROM rows
                WHERE kind='settle' AND ts >= ? AND ts <= ?""",
                (since, now - BREAK_WINDOW),
            ).fetchall()
            broken = db.execute(
                "SELECT buyer, sku, ts FROM rows WHERE kind='broken' AND ts >= ?",
                (since,),
            ).fetchall()
        broken_keys = {(str(b), str(s)) for b, s, _ in broken}
        rates: dict[str, tuple[int, int]] = {}
        for buyer, sku, grade, _ in settled:
            key = str(grade or "?")
            hit, total = rates.get(key, (0, 0))
            rates[key] = (hit + int((str(buyer), str(sku)) in broken_keys), total + 1)
        return rates

    def summary(
        self,
        catalog: Mapping[str, CatalogItem],
        *,
        hours: float,
        our_shop: str = "",
        our_buyer: str = "",
    ) -> dict[str, object]:
        """Compact market picture for the policy loop and the forum."""
        since = time.time() - hours * 3600
        rows = self.settles(since)
        by_category: dict[str, list[float]] = {}
        by_sku: dict[str, int] = {}
        ratios: list[float] = []
        our_shop_n = our_buyer_n = 0
        dishonest = 0
        for _, buyer, shop, sku, amount, grade, claim in rows:
            item = catalog.get(sku)
            category = item.category if item is not None else "?"
            share = (
                amount / item.list_price
                if item is not None and item.list_price
                else 0.0
            )
            by_category.setdefault(category, []).append(share)
            by_sku[sku] = by_sku.get(sku, 0) + 1
            if share:
                ratios.append(share)
            if our_shop and shop.rstrip("/") == our_shop.rstrip("/"):
                our_shop_n += 1
            if our_buyer and buyer == our_buyer:
                our_buyer_n += 1
            if claim and grade and claim != grade:
                dishonest += 1
        top_categories = sorted(
            (
                (len(v), statistics.median(v) if v else 0.0, k)
                for k, v in by_category.items()
            ),
            reverse=True,
        )[:12]
        top_skus = sorted(by_sku.items(), key=lambda kv: -kv[1])[:20]
        return {
            "hours": hours,
            "settles": len(rows),
            "median_price_to_list": round(statistics.median(ratios), 3)
            if ratios
            else None,
            "top_categories": [
                {"category": k, "settles": n, "median_price_to_list": round(m, 3)}
                for n, m, k in top_categories
            ],
            "top_skus": [{"sku": s, "settles": n} for s, n in top_skus],
            "break_rates": {
                g: {"broken": b, "observed": t}
                for g, (b, t) in self.break_rates(
                    since - BREAK_WINDOW - MATURE_COHORT_HOURS * 3600
                ).items()
            },
            "dishonest_claims": dishonest,
            "our_shop_settles": our_shop_n,
            "our_buyer_settles": our_buyer_n,
        }

    def demand_counts(
        self,
        catalog: Mapping[str, CatalogItem],
        hours: float,
    ) -> tuple[dict[str, int], dict[str, int]]:
        """(settles per category, settles per SKU) for the restock planner."""
        since = time.time() - hours * 3600
        by_category: dict[str, int] = {}
        by_sku: dict[str, int] = {}
        for _, _b, _s, sku, _a, _g, _c in self.settles(since):
            item = catalog.get(sku)
            if item is not None:
                by_category[item.category] = by_category.get(item.category, 0) + 1
            by_sku[sku] = by_sku.get(sku, 0) + 1
        return by_category, by_sku


def pull(
    db: LedgerDB, market: str, client: httpx.Client, *, max_pages: int = 20
) -> int:
    """Fetch ledger rows after the stored cursor; return rows added."""
    added = 0
    after = db.cursor
    for _ in range(max_pages):
        reply = client.get(f"{market.rstrip('/')}/api/ledger", params={"after": after})
        if reply.status_code != 200:  # noqa: PLR2004 - HTTP OK
            break
        rows = reply.json()
        if not isinstance(rows, list) or not rows:
            break
        added += db.ingest(r for r in rows if isinstance(r, dict))
        last = rows[-1]
        after = int(str(last.get("seq", after))) if isinstance(last, dict) else after
        if len(rows) < PAGE:
            break
    return added
