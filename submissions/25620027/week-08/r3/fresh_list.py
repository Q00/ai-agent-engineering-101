"""Food SKUs bought here mostly by the shelf-sensitive plain-form buyers.

11/15 15:0x (owner, Codex review .omo/evidence/buyer02-review-20261115T053545Z):
buyer-02 rated food sold with 0.75+ of its shelf life left 4.89 (167, no 2 stars),
0.5-0.75 4.24 (25 % 2 stars), 0.25-0.5 3.40 (53 %) and under 0.25 2.79 (74 %) in
24 h. Its 2-star SKUs (G7fa48eaa 40, G0033fd0a 9, Gdf79cd07 8, ...) were bought in
the plain "I want to buy NAME (SKU)" form almost only by it. On the SKUs where
buyer-02 and buyer-01 make LIST_SHARE or more of the plain-form buyers' settles
(and buyer-02 LIST_MIN or more), or where buyer-02 gave LOW_MIN or more ratings
of 2 stars or less, the seller keeps fresh units for that form and
restock tops up fresh stock each supply window.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import time
from collections import Counter
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

FRESH_LIST_FILE: Final = "r3-fresh-list.json"
PLAIN_BUYERS: Final = ("buyer-02", "buyer-01", "usagi", "chanarun", "ddolcom")
SHELF_BUYERS: Final = ("buyer-02", "buyer-01")
LIST_HOURS: Final = 24.0
LIST_SHARE: Final = 0.70
LIST_MIN: Final = 5  # 10 until 11/15 16:47 (owner)
# Also listed: SKUs buyer-02 rated 2 stars or less here LOW_MIN times in LIST_HOURS.
LOW_MIN: Final = 2
LIST_MAX: Final = 10
MAX_AGE_SECONDS: Final = 2 * 3600.0
# Shares of shelf life left the seller uses for listed SKUs in the plain form.
FRESH_RATIO: Final = 0.75
FAIR_RATIO: Final = 0.50


def find_fresh_list(db: Path, shop: str, now: float | None = None) -> dict[str, float]:
    """Return listed food SKUs -> buyer-02's settles per hour here over LIST_HOURS."""
    stamp = now if now is not None else time.time()
    plain: Counter[str] = Counter()
    shelf: Counter[str] = Counter()
    main: Counter[str] = Counter()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for sku, buyer in con.execute(
            "select sku, buyer from rows where kind='settle' and ts>=?"
            " and rtrim(shop,'/')=? and json_extract(data,'$.expires_at') is not null",
            (stamp - LIST_HOURS * 3600, shop.rstrip("/")),
        ):
            if not sku or buyer not in PLAIN_BUYERS:
                continue
            plain[str(sku)] += 1
            if buyer in SHELF_BUYERS:
                shelf[str(sku)] += 1
            if buyer == SHELF_BUYERS[0]:
                main[str(sku)] += 1
        order_sku = {
            str(order): str(sku)
            for order, sku in con.execute(
                "select json_extract(data,'$.order'), sku from rows where kind='settle'"
                " and buyer=? and ts>=? and rtrim(shop,'/')=?",
                (SHELF_BUYERS[0], stamp - LIST_HOURS * 3600 - 3600, shop.rstrip("/")),
            )
            if order and sku
        }
        low: Counter[str] = Counter()
        for (order,) in con.execute(
            "select json_extract(data,'$.order') from rows where kind='result'"
            " and buyer=? and ts>=? and rtrim(shop,'/')=?"
            " and json_extract(data,'$.verified') and json_extract(data,'$.stars')<=2",
            (SHELF_BUYERS[0], stamp - LIST_HOURS * 3600, shop.rstrip("/")),
        ):
            if order and str(order) in order_sku:
                low[order_sku[str(order)]] += 1
    finally:
        con.close()
    listed = {
        sku
        for sku, total in plain.items()
        if main[sku] >= LIST_MIN and shelf[sku] / total >= LIST_SHARE
    } | {sku for sku, n in low.items() if n >= LOW_MIN and sku in plain}
    # 11/16 06:3x (owner): back to buyer-02's top LIST_MAX SKUs; 24 listed SKUs
    # with fresh-first selling cut the plain form's close rate (11/15 23:4x).
    top = sorted(listed, key=lambda sku: -main[sku])[:LIST_MAX]
    return {sku: round(main[sku] / LIST_HOURS, 3) for sku in top}


def write_fresh_list(path: Path, skus: dict[str, float], now: float) -> None:
    """Persist the list (the seller and restock read it)."""
    with contextlib.suppress(OSError):
        _ = path.write_text(json.dumps({"ts": now, "skus": skus}), "utf-8")


def load_fresh_list(path: Path, now: float | None = None) -> dict[str, float]:
    """Return the current list; a missing or stale file means none."""
    stamp = now if now is not None else time.time()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or stamp - float(raw.get("ts") or 0) > MAX_AGE_SECONDS:
        return {}
    skus = raw.get("skus")
    if not isinstance(skus, dict):
        return {}
    out: dict[str, float] = {}
    for sku, rate in skus.items():
        with contextlib.suppress(TypeError, ValueError):
            out[str(sku)] = float(rate)
    return out


def fresh_need(per_hour: float) -> int:
    """Fresh units to keep on hand: at least one hour of buyer-02's buying."""
    return max(1, math.ceil(per_hour))


# 11/17 07:2x (owner, priority 2): buyer-02 rated 65 of 346 deals in 3 h 2 stars, all
# "Only X h of shelf life left; I needed Y h." (shortfall median 47.7 h). Its plain
# form states no need, so the largest Y it named per SKU in 24 h becomes the need
# the plain form must meet (plus NEED_MARGIN_HOURS); SKUs without such a review sell
# as before.
NEEDS_FILE: Final = "r3-buyer02-needs.json"
NEED_MARGIN_HOURS: Final = 2.0
NEED_TEXT: Final = re.compile(r"I needed ([\d.]+) h")


def find_buyer02_needs(
    db: Path, shop: str, now: float | None = None
) -> dict[str, float]:
    """Return SKU -> the largest shelf need buyer-02 named in a review here (24 h)."""
    stamp = now if now is not None else time.time()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        order_sku = {
            str(order): str(sku)
            for order, sku in con.execute(
                "select json_extract(data,'$.order'), sku from rows where kind='settle'"
                " and buyer=? and ts>=? and rtrim(shop,'/')=?",
                (SHELF_BUYERS[0], stamp - LIST_HOURS * 3600 - 3600, shop.rstrip("/")),
            )
            if order and sku
        }
        needs: dict[str, float] = {}
        for order, review in con.execute(
            "select json_extract(data,'$.order'), json_extract(data,'$.review')"
            " from rows where kind='result' and buyer=? and ts>=?"
            " and rtrim(shop,'/')=?",
            (SHELF_BUYERS[0], stamp - LIST_HOURS * 3600, shop.rstrip("/")),
        ):
            found = NEED_TEXT.search(str(review or ""))
            sku = order_sku.get(str(order))
            if found and sku:
                needs[sku] = max(needs.get(sku, 0.0), float(found.group(1)))
    finally:
        con.close()
    return needs


# 11/17 13:5x (owner, strategy 13:52): cheapest-in-town's food form states "It must
# last at least N hours"; asks no B unit met are recorded here (seller) and, while
# food A+B is over CIT_FOOD_AB_LINE, join the buyer-02 fresh fill (restock).
CIT_NEEDS_FILE: Final = "r3-cit-needs.json"
CIT_NEEDS_HOURS: Final = 3.0
CIT_FOOD_AB_LINE: Final = 250
CIT_ROW_LEN: Final = 3  # [sku, need hours, time]


def record_cit_need(path: Path, sku: str, need: float, now: float) -> None:
    """Append one unmet cheapest-in-town food ask (kept CIT_NEEDS_HOURS)."""
    rows: list[list[object]] = []
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(path.read_text("utf-8"))
        if isinstance(raw, list):
            rows = [r for r in raw if isinstance(r, list) and len(r) == CIT_ROW_LEN]
    keep = [r for r in rows if now - float(r[2]) <= CIT_NEEDS_HOURS * 3600]  # type: ignore[arg-type]
    keep.append([sku, need, now])
    with contextlib.suppress(OSError):
        _ = path.write_text(json.dumps(keep), "utf-8")


def load_cit_needs(path: Path, now: float) -> tuple[dict[str, float], dict[str, float]]:
    """Return (SKU -> unmet asks per hour, SKU -> largest stated need) lately."""
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}, {}
    rate: dict[str, float] = {}
    need: dict[str, float] = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, list) or len(row) != CIT_ROW_LEN:
            continue
        sku, hours, ts = str(row[0]), float(row[1]), float(row[2])
        if now - ts > CIT_NEEDS_HOURS * 3600:
            continue
        rate[sku] = rate.get(sku, 0.0) + 1.0 / CIT_NEEDS_HOURS
        need[sku] = max(need.get(sku, 0.0), hours)
    return rate, need
