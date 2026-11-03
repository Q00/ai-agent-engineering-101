"""Bounded, read-only post-change evidence for the next policy and advice pass.

Equal windows describe what followed a version; they never establish causation or
authorize a rollback. Round 2, incomplete windows and missing Round 3 observations
are explicitly separated from a measured zero. No counterpart text is included.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from r3.events import EventLedger
    from r3.ledger_db import LedgerDB

WINDOW_SECONDS: Final = 900
MAX_VERSIONS: Final = 6
MIN_SAMPLE: Final = 5


def policy_reviews(
    events: EventLedger,
    ledger: LedgerDB,
    *,
    our_shop: str,
    our_buyer: str,
    now: float | None = None,
) -> list[dict[str, object]]:
    """Compare equal 15-minute windows around up to six accepted policy versions."""
    stamp = now if now is not None else time.time()
    try:
        return _reviews(events, ledger, our_shop, our_buyer, stamp)
    except (OSError, sqlite3.Error) as exc:
        return [{"status": "unavailable", "reason": type(exc).__name__}]


def _reviews(
    events: EventLedger,
    ledger: LedgerDB,
    our_shop: str,
    our_buyer: str,
    stamp: float,
) -> list[dict[str, object]]:
    with closing(sqlite3.connect(events.path, timeout=1)) as db:
        versions = db.execute(
            """SELECT version, min(ts), count(*) FROM policy_changes
            WHERE accepted = 1 AND version IS NOT NULL AND ts >= ?
            GROUP BY version ORDER BY min(ts) DESC LIMIT ?""",
            (stamp - 86400, MAX_VERSIONS),
        ).fetchall()
        changes = db.execute(
            """SELECT version, ts FROM policy_changes
            WHERE accepted = 1 AND ts >= ?""",
            (stamp - 86400 - WINDOW_SECONDS,),
        ).fetchall()
    reviews: list[dict[str, object]] = []
    with closing(sqlite3.connect(ledger.path, timeout=2)) as db:
        latest = db.execute("SELECT max(ts) FROM rows").fetchone()[0]
        for version, changed_raw, fields in versions:
            changed_at = float(changed_raw)
            start, end = changed_at - WINDOW_SECONDS, changed_at + WINDOW_SECONDS
            row: dict[str, object] = {
                "version": int(version),
                "changed_at": changed_at,
                "window_seconds": WINDOW_SECONDS,
                "fields_changed": int(fields),
                "causal_effect_established": False,
            }
            if stamp < end:
                row["status"] = "awaiting_complete_window"
                reviews.append(row)
                continue
            before = _window(db, start, changed_at, our_shop, our_buyer)
            after = _window(db, changed_at, end, our_shop, our_buyer)
            row.update(before=before, after=after)
            if not before["r3_rows"] or not after["r3_rows"]:
                row["status"] = "round_boundary_or_missing_r3_data"
                reviews.append(row)
                continue
            if latest is None or float(latest) < end:
                row["status"] = "ledger_not_caught_up"
                reviews.append(row)
                continue
            overlap = any(
                v != version and start <= float(ts) < end for v, ts in changes
            )
            row["overlapping_versions"] = overlap
            row["status"] = "confounded" if overlap else "descriptive_only"
            row["small_sample"] = (
                min(
                    before["shop_settles"] + before["buyer_settles"],
                    after["shop_settles"] + after["buyer_settles"],
                )
                < MIN_SAMPLE
            )
            row["delta"] = {
                key: after[key] - before[key]
                for key in (
                    "shop_settles",
                    "shop_gross_revenue",
                    "buyer_settles",
                    "buyer_paid",
                )
            }
            reviews.append(row)
    return reviews


def _window(
    db: sqlite3.Connection,
    start: float,
    end: float,
    our_shop: str,
    our_buyer: str,
) -> dict[str, int]:
    rows = db.execute(
        """SELECT kind, shop, buyer, amount FROM rows
        WHERE round = 3 AND ts >= ? AND ts < ?""",
        (start, end),
    )
    out = {
        "r3_rows": 0,
        "shop_settles": 0,
        "shop_gross_revenue": 0,
        "buyer_settles": 0,
        "buyer_paid": 0,
    }
    for kind, shop, buyer, amount in rows:
        out["r3_rows"] += 1
        if kind != "settle":
            continue
        if our_shop and str(shop).rstrip("/") == our_shop.rstrip("/"):
            out["shop_settles"] += 1
            out["shop_gross_revenue"] += int(amount or 0)
        if our_buyer and str(buyer) == our_buyer:
            out["buyer_settles"] += 1
            out["buyer_paid"] += int(amount or 0)
    return out
