"""Observed inventory assets and R3 accrual profit, separate from cash rankings.

The recorder watches the kit's existing inventory file; there are no extra market
requests and no work on the negotiation path. Missing costs, refund terms or stock
coverage make profit unknown, never zero. Storage is the published cost-time rule,
checked against cash plus inventory; it is not a new debit to the market wallet.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from itertools import pairwise
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

STOCK_FILE: Final = "r3-stock.sqlite"
DAY: Final = 86400.0
STORAGE_RATE: Final = 0.01
MAX_GAP: Final = 30.0
# Net profit is built from per-unit facts (sale amounts, unit costs, refunds,
# expiries); the cash-plus-assets reconciliation only cross-checks it. During heavy
# restock the residual ran 0.6-2% of the window's flows (61,537 on 9.6M restock),
# which nulled every review the strategy needed (engineer notes, 11/4). A residual
# within this share of restock plus revenue, and snapshot spacing over MAX_GAP,
# are kept as notes; anything else still withholds the number.
RECONCILE_SHARE: Final = 0.02
ADVISORY_GAPS: Final = frozenset(
    {"inventory_observation_gap", "cash_asset_reconciliation_minor"}
)
RETENTION: Final = 2 * DAY
ECON_ROUND: Final = 3


class StockJournal:
    """Keep cost identities and two days of compact kit inventory observations."""

    def __init__(self, path: Path) -> None:
        """Initialize a private derived journal, without reading any credentials."""
        self.path = path
        self.fingerprint: tuple[int, int, int] | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=1)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS stock (
                unit TEXT PRIMARY KEY, cost INTEGER, kind TEXT, supplied REAL,
                expires REAL, first_seen REAL, last_seen REAL, missing_at REAL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS snapshots (
                ts REAL PRIMARY KEY, balance INTEGER, capital INTEGER,
                injected INTEGER, assets INTEGER, storage_rate REAL)"""
            )

    def capture_file(self, source: Path) -> bool:
        """Observe only a stable changed file, dated by its write time, not now."""
        try:
            before = source.stat()
            fingerprint = (before.st_ino, before.st_mtime_ns, before.st_size)
            if fingerprint == self.fingerprint:
                return False
            raw = json.loads(source.read_bytes())
            after = source.stat()
            if fingerprint != (after.st_ino, after.st_mtime_ns, after.st_size):
                return False
            result = self.observe(raw, stamp=before.st_mtime)
            if result:
                self.fingerprint = fingerprint
        except (OSError, ValueError, TypeError):
            return False
        return result

    def observe(self, raw: object, *, stamp: float) -> bool:  # noqa: C901, PLR0911 - reject incomplete source before one transaction
        """Persist a complete R3 snapshot; a missing file is never empty stock."""
        if not isinstance(raw, dict) or raw.get("round") != ECON_ROUND:
            return False
        units = raw.get("units")
        if not isinstance(units, list) or not _money(raw.get("balance")):
            return False
        if not _money(raw.get("start")) or not _money(raw.get("injected")):
            return False
        if not _number(raw.get("storage_per_day")):
            return False
        if any(not _valid_unit(unit) for unit in units):
            return False
        if len({u["unit"] for u in units}) != len(units):
            return False
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            latest = db.execute("SELECT max(ts) FROM snapshots").fetchone()[0]
            if latest is not None and stamp <= latest:
                return False
            present = {u["unit"] for u in units}
            for (unit,) in db.execute(
                "SELECT unit FROM stock WHERE missing_at IS NULL"
            ):
                if unit not in present:
                    db.execute(
                        "UPDATE stock SET missing_at=? WHERE unit=?", (stamp, unit)
                    )
            for unit in units:
                old = db.execute(
                    "SELECT cost, kind, supplied, expires FROM stock WHERE unit=?",
                    (unit["unit"],),
                ).fetchone()
                identity = (
                    unit["cost"],
                    unit["kind"],
                    unit["supplied_at"],
                    unit.get("expires_at"),
                )
                if old is not None and tuple(old) != identity:
                    message = (
                        "Inventory cost identity changed; profit cannot be verified"
                    )
                    raise ValueError(message)
                db.execute(
                    """INSERT INTO stock VALUES(?,?,?,?,?,?,?,NULL)
                    ON CONFLICT(unit) DO UPDATE SET last_seen=excluded.last_seen,
                    missing_at=NULL""",
                    (unit["unit"], *identity, stamp, stamp),
                )
            db.execute(
                "INSERT INTO snapshots VALUES(?,?,?,?,?,?)",
                (
                    stamp,
                    raw["balance"],
                    raw["start"],
                    raw["injected"],
                    sum(u["cost"] for u in units),
                    raw["storage_per_day"],
                ),
            )
            db.execute("DELETE FROM snapshots WHERE ts < ?", (stamp - RETENTION,))
            db.execute(
                "DELETE FROM stock WHERE missing_at IS NOT NULL AND missing_at < ?",
                (stamp - RETENTION,),
            )
        return True


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _money(value: object) -> bool:
    return type(value) is int and value >= 0


def _valid_unit(unit: object) -> bool:
    return (
        isinstance(unit, dict)
        and isinstance(unit.get("unit"), str)
        and bool(unit["unit"])
        and _money(unit.get("cost"))
        and unit.get("kind") in ("food", "goods")
        and _number(unit.get("supplied_at"))
        and (unit["kind"] != "food" or _number(unit.get("expires_at")))
    )


def _refund(
    data: dict[str, object], sale: tuple[float, dict[str, object]] | None, stamp: float
) -> int | None:
    """Use an explicit amount/flag, else exact-unit sale and warranty terms."""
    for key in ("refund_amount", "refund", "refunded"):
        value = data.get(key)
        if _money(value):
            return int(value)
        if value is False:
            return 0
        if value is True:
            amount = sale[1].get("amount") if sale is not None else None
            return int(amount) if _money(amount) else None
    if sale is None:
        return None
    sold_at, terms = sale
    warranty, amount = terms.get("warranty_hours"), terms.get("amount")
    if not _number(warranty) or not _money(amount) or stamp < sold_at:
        return None
    return (
        int(amount)
        if float(warranty) > 0 and stamp - sold_at <= float(warranty) * 3600
        else 0
    )


def financial_window(
    path: Path,
    ledger: sqlite3.Connection,
    *,
    start: float,
    end: float,
    shop: str,
) -> dict[str, object]:
    """Compute observed operating profit for one half-open R3 window."""
    unknown: dict[str, object] = {
        "shop_net_profit": None,
        "financial_status": "unavailable",
        "financial_gaps": ["inventory_history_missing"],
    }
    if not path.is_file():
        return unknown
    try:
        with closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)
        ) as db:
            return _financial_window(db, ledger, start, end, shop)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {**unknown, "financial_gaps": ["financial_source_unreadable"]}


def _financial_window(  # noqa: C901, PLR0912, PLR0915 - reconcile explicit cost components in one read-only pass
    stock: sqlite3.Connection,
    ledger: sqlite3.Connection,
    start: float,
    end: float,
    shop: str,
) -> dict[str, object]:
    before = stock.execute(
        "SELECT * FROM snapshots WHERE ts<=? ORDER BY ts DESC LIMIT 1",
        (start,),
    ).fetchone()
    after = stock.execute(
        "SELECT * FROM snapshots WHERE ts>=? ORDER BY ts LIMIT 1",
        (end,),
    ).fetchone()
    gaps: set[str] = set()
    if before is None or after is None:
        return {
            "shop_net_profit": None,
            "financial_status": "incomplete",
            "financial_gaps": ["inventory_window_not_covered"],
        }
    times = [
        r[0]
        for r in stock.execute(
            "SELECT ts FROM snapshots WHERE ts>=? AND ts<=? ORDER BY ts",
            (before[0], after[0]),
        )
    ]
    if (
        start - before[0] > MAX_GAP
        or after[0] - end > MAX_GAP
        or any(right - left > MAX_GAP for left, right in pairwise(times))
    ):
        gaps.add("inventory_observation_gap")
    if any(
        r[0] != STORAGE_RATE
        for r in stock.execute(
            "SELECT DISTINCT storage_rate FROM snapshots WHERE ts>=? AND ts<=?",
            (before[0], after[0]),
        )
    ):
        gaps.add("storage_rule_changed")
    if after[3] != before[3]:
        gaps.add("bankruptcy_or_capital_injection")
    units = {row[0]: row[1:] for row in stock.execute("SELECT * FROM stock")}
    sales: dict[str, tuple[float, dict[str, object]]] = {}
    breaks: list[tuple[float, dict[str, object]]] = []
    revenue = cogs = refunds = refunded_count = 0
    for stamp, kind, raw in ledger.execute(
        """SELECT ts,kind,data FROM rows WHERE round=3 AND ts<?
        AND (kind IN ('broken','break') OR (kind='settle' AND shop IN (?,?)))
        ORDER BY ts,seq""",
        (end, shop.rstrip("/"), shop.rstrip("/") + "/"),
    ):
        data = json.loads(raw)
        if not isinstance(data, dict):
            continue
        own = str(data.get("shop", "")).rstrip("/") == shop.rstrip("/") and bool(shop)
        unit = str(data.get("unit", ""))
        if kind == "settle" and own:
            if unit in sales:
                gaps.add("duplicate_unit_settlement")
            sales[unit] = (float(stamp), data)
            if start <= stamp:
                if not _money(data.get("amount")):
                    gaps.add("sale_amount_missing")
                else:
                    revenue += data["amount"]
                if unit not in units:
                    gaps.add("sold_unit_cost_missing")
                else:
                    cogs += units[unit][0]
        elif kind != "settle" and start <= stamp:
            breaks.append((float(stamp), data))
    seen_breaks: set[str] = set()
    for stamp, data in breaks:
        unit = str(data.get("unit", ""))
        sale = sales.get(unit)
        own = str(data.get("shop", "")).rstrip("/") == shop.rstrip("/") and bool(shop)
        if not own and sale is None:
            continue
        if unit and unit in seen_breaks:
            gaps.add("duplicate_unit_break")
            continue
        if unit:
            seen_breaks.add(unit)
        refund = _refund(data, sale, stamp)
        if refund is None:
            gaps.add("refund_amount_or_warranty_unknown")
        else:
            refunds += refund
            refunded_count += int(refund > 0)
    expired = restock = 0
    storage_cost = 0.0
    for unit, (cost, kind, supplied, expires, _first, _last, missing) in units.items():
        sale = sales.get(unit)
        sold_at = sale[0] if sale is not None else None
        terminal = sold_at
        if kind == "food" and expires is not None and sold_at is None:
            if missing is not None and missing >= expires:
                terminal = expires
                if start <= expires < end:
                    expired += cost
            elif expires < end:
                gaps.add("expired_unit_disposal_unconfirmed")
        if missing is not None and missing <= end and terminal is None:
            gaps.add("unexplained_inventory_removal")
        if start <= supplied < end:
            restock += cost
        if kind == "goods":
            until = min(end, terminal if terminal is not None else end)
            storage_cost += (
                cost * STORAGE_RATE * max(0, until - max(start, supplied)) / DAY
            )
    net = revenue - cogs - refunds - expired - storage_cost
    cash_change, assets_change = after[1] - before[1], after[4] - before[4]
    residual = cash_change + assets_change - (after[3] - before[3]) - net
    # Storage is accrued here per second but the market charges it on its own
    # schedule, so a residual that is exactly the accrued storage is explained
    # (first night: every review carried gap == storage, net profit null).
    unexplained = min(abs(residual), abs(residual - storage_cost))
    if unexplained > max(1.0, RECONCILE_SHARE * (restock + revenue)):
        gaps.add("cash_asset_reconciliation_gap")
    elif unexplained > 1:
        gaps.add("cash_asset_reconciliation_minor")
    blocking = gaps - ADVISORY_GAPS
    return {
        "shop_cost_of_goods_sold": cogs,
        "shop_refunds": refunds,
        "shop_refunded_breaks": refunded_count,
        "shop_expired_food_cost": expired,
        "shop_storage_cost": round(storage_cost, 6),
        "shop_restock_spend": restock,
        "shop_cash_change": cash_change,
        "shop_inventory_asset_change": assets_change,
        "official_cash_profit_at_end": after[1] - after[2] - after[3],
        "balance_observed_at": [before[0], after[0]],
        "cash_asset_reconciliation_gap": round(residual, 6),
        "storage_not_yet_charged": round(residual, 6)
        if abs(residual - storage_cost) <= 1 and abs(residual) > 1
        else 0.0,
        "storage_basis": "observed_unit_cost_times_stock_seconds_times_0.01_per_day",
        "financial_status": "incomplete"
        if blocking
        else "complete_with_notes"
        if gaps
        else "complete",
        "financial_gaps": sorted(gaps),
        "shop_net_profit": round(net, 6) if not blocking else None,
    }
