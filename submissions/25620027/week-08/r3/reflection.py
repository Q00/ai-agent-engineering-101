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

from r3.accounting import STOCK_FILE, financial_window

if TYPE_CHECKING:
    from r3.events import EventLedger
    from r3.ledger_db import LedgerDB

WINDOW_SECONDS: Final = 900
MAX_VERSIONS: Final = 6
MIN_SAMPLE: Final = 5
# Versions arrive every 3-15 minutes (tactical overlay plus strategy), so equal
# +-15 min windows almost always held another change: on 11/4 every review read
# "confounded" and the strategy model held for hours with nothing to learn from.
# Each window now stops at the neighbouring change, so a review covers only the
# time its version was in force; changes within MERGE_SECONDS (a move and its
# code fold) count as one, and a window under MIN_WINDOW_SECONDS says so.
MERGE_SECONDS: Final = 60.0
MIN_WINDOW_SECONDS: Final = 300.0


def policy_reviews(
    events: EventLedger,
    ledger: LedgerDB,
    *,
    our_shop: str,
    our_buyer: str,
    now: float | None = None,
) -> list[dict[str, object]]:
    """Compare the time before and after up to six accepted policy versions.

    Each window is at most 15 minutes and stops at the neighbouring change.
    """
    stamp = now if now is not None else time.time()
    try:
        return _reviews(events, ledger, our_shop, our_buyer, stamp)
    except (OSError, sqlite3.Error) as exc:
        return [{"status": "unavailable", "reason": type(exc).__name__}]


def _groups(
    rows: list[tuple[int, float, int]],
) -> list[tuple[float, list[int], int]]:
    """Merge versions accepted within MERGE_SECONDS: (first ts, versions, fields)."""
    groups: list[tuple[float, list[int], int]] = []
    for version, changed_at, fields in sorted(rows, key=lambda r: r[1]):
        if groups and changed_at - groups[-1][0] <= MERGE_SECONDS:
            first, versions, total = groups[-1]
            groups[-1] = (first, [*versions, version], total + fields)
        else:
            groups.append((changed_at, [version], fields))
    return groups


def _reviews(
    events: EventLedger,
    ledger: LedgerDB,
    our_shop: str,
    our_buyer: str,
    stamp: float,
) -> list[dict[str, object]]:
    with closing(sqlite3.connect(events.path, timeout=1)) as db:
        found = db.execute(
            """SELECT version, min(ts), count(*) FROM policy_changes
            WHERE accepted = 1 AND version IS NOT NULL AND ts >= ?
            GROUP BY version""",
            (stamp - 86400 - WINDOW_SECONDS,),
        ).fetchall()
    groups = _groups([(int(v), float(t), int(n)) for v, t, n in found])
    picked = [index for index, group in enumerate(groups) if group[0] >= stamp - 86400][
        -MAX_VERSIONS:
    ]
    reviews: list[dict[str, object]] = []
    with closing(sqlite3.connect(ledger.path, timeout=2)) as db:
        latest = db.execute("SELECT max(ts) FROM rows").fetchone()[0]
        for index in reversed(picked):
            changed_at, versions, fields = groups[index]
            previous = groups[index - 1][0] if index > 0 else None
            following = groups[index + 1][0] if index + 1 < len(groups) else None
            start = max(changed_at - WINDOW_SECONDS, previous or -1.0)
            end = min(changed_at + WINDOW_SECONDS, following or float("inf"))
            row: dict[str, object] = {
                "version": versions[-1],
                "changed_at": changed_at,
                "window_seconds": round(end - changed_at),
                "before_seconds": round(changed_at - start),
                "fields_changed": fields,
                "causal_effect_established": False,
                "overlapping_versions": False,
            }
            if len(versions) > 1:
                row["merged_versions"] = versions
            if stamp < end:
                row["status"] = "awaiting_complete_window"
                reviews.append(row)
                continue
            before = _window(db, start, changed_at, our_shop, our_buyer)
            after = _window(db, changed_at, end, our_shop, our_buyer)
            stock_path = ledger.path.resolve().with_name(STOCK_FILE)
            before.update(
                financial_window(
                    stock_path, db, start=start, end=changed_at, shop=our_shop
                )
            )
            after.update(
                financial_window(
                    stock_path, db, start=changed_at, end=end, shop=our_shop
                )
            )
            row.update(before=before, after=after)
            if not before["r3_rows"] or not after["r3_rows"]:
                row["status"] = "round_boundary_or_missing_r3_data"
                reviews.append(row)
                continue
            if latest is None or float(latest) < end:
                row["status"] = "ledger_not_caught_up"
                reviews.append(row)
                continue
            short = min(changed_at - start, end - changed_at) < MIN_WINDOW_SECONDS
            row["status"] = "window_too_short" if short else "descriptive_only"
            # Unequal windows: the before side is scaled to the after side's length.
            scale = (end - changed_at) / max(1.0, changed_at - start)
            row["before_scaled_by"] = round(scale, 4)
            row["small_sample"] = (
                min(
                    before["shop_settles"] + before["buyer_settles"],
                    after["shop_settles"] + after["buyer_settles"],
                )
                < MIN_SAMPLE
            )
            delta: dict[str, object] = {
                key: round(after[key] - before[key] * scale, 6)
                for key in (
                    "shop_settles",
                    "shop_gross_revenue",
                    "buyer_settles",
                    "buyer_paid",
                )
            }
            for key in (
                "shop_cost_of_goods_sold",
                "shop_refunds",
                "shop_expired_food_cost",
                "shop_storage_cost",
                "shop_restock_spend",
                "shop_net_profit",
                "shop_cash_change",
                "shop_inventory_asset_change",
            ):
                left, right = before.get(key), after.get(key)
                delta[key] = (
                    round(right - left * scale, 6)
                    if isinstance(left, (int, float))
                    and isinstance(right, (int, float))
                    else None
                )
            row["delta"] = delta
            profit = delta.get("shop_net_profit")
            row["assessment_basis"] = "shop_net_profit"
            row["assessment"] = "insufficient_evidence"
            row["financial_small_sample"] = (
                min(before["shop_settles"], after["shop_settles"]) < MIN_SAMPLE
            )
            if (
                isinstance(profit, (int, float))
                and not short
                and not row["small_sample"]
                and not row["financial_small_sample"]
            ):
                row["assessment"] = (
                    "improved"
                    if profit > 0
                    else "worsened"
                    if profit < 0
                    else "unchanged"
                )
            reviews.append(row)
    return reviews


def _window(
    db: sqlite3.Connection,
    start: float,
    end: float,
    our_shop: str,
    our_buyer: str,
) -> dict[str, object]:
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
