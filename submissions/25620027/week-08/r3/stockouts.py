"""Sales we missed because a SKU we carry was out of stock, for restock to size up.

Owner 11/5: "품절돼서 못 판 거 없는지 보면서 재고를 자율적으로 최대한 관리해". A buyer
only sees shops that list a SKU, so a stockout costs sales without any refusal on
our side. We rebuild our own stock level per SKU from the public ledger (supply
rows name the shop, settle rows carry the unit), count the market's sales of that
SKU while we held none, and scale by our share of its sales while we had stock.
On 11/5 08:20-14:20 that was ~42M of revenue, mostly two SKUs we led when stocked.
"""

from __future__ import annotations

import bisect
import contextlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

STOCKOUTS_FILE: Final = "r3-stockouts.json"
WINDOW_HOURS: Final = 6.0
HISTORY_HOURS: Final = 96.0  # supply rows this old still explain units we hold
MIN_LOST: Final = 2.0  # missed sales in the window before restock reacts
COVER_HOURS: Final = 1.0  # supply opens every ~20 min; hold an hour of our sales
MAX_AGE_SECONDS: Final = 3600.0


def _levels(
    supplied: Mapping[str, tuple[str, float]],
    sold: Mapping[str, float],
    held_now: Mapping[str, int],
) -> dict[str, tuple[list[float], list[int]]]:
    """Our stock level per SKU after each supply or sale, anchored to the live count."""
    events: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for unit, (sku, ts) in supplied.items():
        events[sku].append((ts, 1))
        if unit in sold:
            events[sku].append((sold[unit], -1))
    out: dict[str, tuple[list[float], list[int]]] = {}
    for sku, rows in events.items():
        rows.sort()
        stamps: list[float] = []
        levels: list[int] = []
        level = 0
        for ts, step in rows:
            level += step
            stamps.append(ts)
            levels.append(level)
        shift = held_now.get(sku, 0) - (levels[-1] if levels else 0)
        out[sku] = (stamps, [lv + shift for lv in levels])
    return out


def _held_at(timeline: tuple[list[float], list[int]], ts: float) -> int:
    stamps, levels = timeline
    i = bisect.bisect_right(stamps, ts) - 1
    if i >= 0:
        return levels[i]
    return levels[0] - 1 if levels else 0


def missed_sales(  # noqa: PLR0913, PLR0917 - the ledger, our two names and the live stock
    db: Path,
    shop_url: str,
    shop_name: str,
    held_now: Mapping[str, int],
    now: float | None = None,
    hours: float = WINDOW_HOURS,
) -> dict[str, dict[str, float]]:
    """Return per SKU: market sales/h, our share while stocked, sales missed at zero."""
    stamp = now if now is not None else time.time()
    since = stamp - hours * 3600
    url = shop_url.rstrip("/")
    supplied: dict[str, tuple[str, float]] = {}
    sold: dict[str, float] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for ts, data in con.execute(
            "select ts, data from rows where kind='supply' and shop=? and ts>?",
            (shop_name, stamp - HISTORY_HOURS * 3600),
        ):
            row = json.loads(data or "{}")
            for unit in row.get("units") or []:
                supplied[str(unit)] = (str(row.get("sku") or ""), float(ts))
        for ts, data in con.execute(
            "select ts, data from rows where kind='settle' and shop=? and ts>?",
            (url, stamp - HISTORY_HOURS * 3600),
        ):
            unit = json.loads(data or "{}").get("unit")
            if unit:
                sold[str(unit)] = float(ts)
        timelines = _levels(supplied, sold, held_now)
        market: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        for ts, sku, shop in con.execute(
            "select ts, sku, shop from rows where kind='settle' and ts>=?", (since,)
        ):
            if sku in timelines:
                market[sku].append((float(ts), (shop or "").rstrip("/") == url))
    finally:
        con.close()
    out: dict[str, dict[str, float]] = {}
    for sku, sales in market.items():
        ours = sum(1 for _, mine in sales if mine)
        at_zero = sum(
            1 for ts, mine in sales if not mine and _held_at(timelines[sku], ts) <= 0
        )
        stocked_sales = len(sales) - at_zero
        share = ours / stocked_sales if stocked_sales else 0.0
        out[sku] = {
            "market_per_h": round(len(sales) / hours, 2),
            "share": round(share, 3),
            "at_zero": float(at_zero),
            "missed": round(at_zero * share, 1),
        }
    return out


def write_stockouts(
    path: Path, rows: Mapping[str, Mapping[str, float]], now: float
) -> None:
    """Persist the latest report (restock reads it every run)."""
    with contextlib.suppress(OSError):
        _ = path.write_text(json.dumps({"ts": now, "skus": rows}), "utf-8")


def load_stockouts(
    path: Path, now: float | None = None
) -> dict[str, tuple[float, float]]:
    """SKU -> (market sales/h, our share) for SKUs with missed sales; stale = none."""
    stamp = now if now is not None else time.time()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or stamp - float(raw.get("ts") or 0) > MAX_AGE_SECONDS:
        return {}
    out: dict[str, tuple[float, float]] = {}
    for sku, row in (raw.get("skus") or {}).items():
        try:
            if float(row["missed"]) >= MIN_LOST:
                out[str(sku)] = (float(row["market_per_h"]), float(row["share"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def stockout_target(market_per_h: float, share: float, cap: int) -> int:
    """Units to hold so an hour of our sales of this SKU does not run dry."""
    return max(2, min(cap, math.ceil(market_per_h * share * COVER_HOURS) + 1))
