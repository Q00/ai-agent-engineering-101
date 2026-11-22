"""SKUs the market sells below what they cost us, so restock stops buying them.

Owner 11/10 ("네, 빼줘"): 12 units of 4 SKUs sat 69-90 h without a sale. Each came
in above the market's median paid price (a 6-seat couch cost us 2.39M, the market
paid 2.13M), so it can only sell at a loss, yet restock bought 9 more of two of
them in 3 days. Book counts unsold stock at cost, so the units we hold stay; only
new buying stops. A SKU we still sell above cost keeps its restock: on 11/10 a
1.08x margin line would have dropped two SKUs that earned 6.5M in 48 h.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import statistics
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from r3.market_files import Unit

OVERPRICED_FILE: Final = "r3-overpriced.json"
MARKET_HOURS: Final = 48.0  # market paid prices
COST_HOURS: Final = 168.0  # our supply lots
MIN_MARKET_SETTLES: Final = 5  # fewer is no market price
MIN_OWN_PROFITABLE: Final = 3  # our own sales above cost that keep a SKU
MAX_AGE_SECONDS: Final = 3600.0
# 11/14 05:2x: our goods share of the market fell 0.13-0.15 -> 0.06-0.08 an hour
# while stock cost rose 378M -> 417M. 309 of 489 goods A/B units (269M of 337M)
# had a floor (cost x1.02; cost after 48 h) above the six-hour market median: the
# 3.82M sofa G24f5f979 sold 58 times in two hours across the market (median 2.44M,
# 22 at yms979) and 0 times with us, our cheapest A unit costing 2.43M. The lot
# average above hid this (cheap C units pull it down), so held A/B units that
# cannot clear at the market also stop new buying of their SKU.
STUCK_UNITS: Final = 2
STUCK_SHARE: Final = 0.5
STUCK_MARGIN: Final = 1.02
STUCK_AGED_SECONDS: Final = 48 * 3600.0
# A SKU we still sold this often in STUCK_SALES_HOURS keeps its restock (its
# buyers pay above our floor; G021aae64 sold 10 in two hours with 19 held).
STUCK_KEEP_SALES: Final = 3
STUCK_SALES_HOURS: Final = 6.0


def overpriced(
    market_paid: Mapping[str, list[int]],
    our_costs: Mapping[str, list[int]],
    own_profitable: Mapping[str, int],
) -> dict[str, dict[str, float]]:
    """Return SKUs whose market median paid price is below our average unit cost."""
    out: dict[str, dict[str, float]] = {}
    for sku, costs in our_costs.items():
        paid = market_paid.get(sku) or []
        if not costs or len(paid) < MIN_MARKET_SETTLES:
            continue
        if own_profitable.get(sku, 0) >= MIN_OWN_PROFITABLE:
            continue
        cost = sum(costs) / len(costs)
        median = float(statistics.median(paid))
        if median < cost:
            out[sku] = {
                "our_cost": round(cost),
                "market_median": round(median),
                "market_settles": float(len(paid)),
            }
    return out


def find_overpriced(
    db: Path, shop_url: str, shop_name: str, now: float | None = None
) -> dict[str, dict[str, float]]:
    """Read our supply lots and the market's settles from the public ledger."""
    stamp = now if now is not None else time.time()
    url = shop_url.rstrip("/")
    unit_cost: dict[str, tuple[str, int]] = {}
    our_costs: dict[str, list[int]] = defaultdict(list)
    market_paid: dict[str, list[int]] = defaultdict(list)
    own_profitable: dict[str, int] = defaultdict(int)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for (data,) in con.execute(
            "select data from rows where kind='supply' and shop=? and ts>?",
            (shop_name, stamp - COST_HOURS * 3600),
        ):
            row = json.loads(data or "{}")
            units = [str(u) for u in row.get("units") or []]
            sku = str(row.get("sku") or "")
            if not units or not sku:
                continue
            each = round(int(row.get("total") or 0) / len(units))
            for unit in units:
                unit_cost[unit] = (sku, each)
                our_costs[sku].append(each)
        for sku, amount, shop, data in con.execute(
            "select sku, amount, shop, data from rows where kind='settle' and ts>=?",
            (stamp - MARKET_HOURS * 3600,),
        ):
            if sku not in our_costs:
                continue
            market_paid[sku].append(int(amount))
            if (shop or "").rstrip("/") == url:
                unit = str(json.loads(data or "{}").get("unit") or "")
                known = unit_cost.get(unit)
                if known is not None and int(amount) > known[1]:
                    own_profitable[sku] += 1
    finally:
        con.close()
    return overpriced(market_paid, our_costs, own_profitable)


def own_sales(db: Path, shop_url: str, since: float) -> dict[str, int]:
    """Our settles per SKU since ``since`` (public ledger)."""
    url = shop_url.rstrip("/")
    counts: dict[str, int] = defaultdict(int)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for sku, shop in con.execute(
            "select sku, shop from rows where kind='settle' and ts>=?", (since,)
        ):
            if (shop or "").rstrip("/") == url:
                counts[str(sku)] += 1
    finally:
        con.close()
    return counts


def stuck(
    units: Iterable[Unit],
    median: Callable[[str], float | None],
    now: float,
    sold: Mapping[str, int] | None = None,
) -> dict[str, dict[str, float]]:
    """Goods SKUs whose held A/B units mostly cannot sell at the market median."""
    held: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.kind == "goods" and unit.grade in {"A", "B"}:
            held[unit.sku].append(unit)
    out: dict[str, dict[str, float]] = {}
    for sku, group in held.items():
        if len(group) < STUCK_UNITS or (sold or {}).get(sku, 0) >= STUCK_KEEP_SALES:
            continue
        price = median(sku)
        if price is None:
            continue
        over = [
            u
            for u in group
            if u.cost
            * (1.0 if now - u.supplied_at >= STUCK_AGED_SECONDS else STUCK_MARGIN)
            > price
        ]
        if len(over) >= STUCK_UNITS and len(over) >= STUCK_SHARE * len(group):
            out[sku] = {
                "our_cost": round(min(u.cost for u in over)),
                "market_median": round(price),
                "stuck_units": float(len(over)),
            }
    return out


def write_overpriced(
    path: Path, rows: Mapping[str, Mapping[str, float]], now: float
) -> None:
    """Persist the latest list (restock reads it every run)."""
    with contextlib.suppress(OSError):
        _ = path.write_text(json.dumps({"ts": now, "skus": rows}), "utf-8")


def load_overpriced(path: Path, now: float | None = None) -> frozenset[str]:
    """SKUs restock must not buy; a missing or stale file blocks nothing."""
    stamp = now if now is not None else time.time()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(raw, dict) or stamp - float(raw.get("ts") or 0) > MAX_AGE_SECONDS:
        return frozenset()
    skus = raw.get("skus")
    return frozenset(str(s) for s in skus) if isinstance(skus, dict) else frozenset()
