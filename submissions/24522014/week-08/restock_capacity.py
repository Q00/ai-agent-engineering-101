"""Operator-authorized capacity bounds and local confirmed-sale evidence."""

from collections import Counter, defaultdict
import math
from pathlib import Path
import time

from market_state import read

ROOT = Path(__file__).resolve().parent


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def config():
    value = read(ROOT / "restock_capacity.json")
    bounds = {"max_total_units": (1, 80), "max_proven_units_per_sku": (1, 3), "max_units_per_run": (1, 12),
              "max_unproven_units": (0, 2), "min_profitable_own_sales_6h": (3, 1000),
              "min_public_sales_6h": (6, 10000), "min_public_buyers": (3, 10000)}
    if any(type(value[k]) is not int or not lo <= value[k] <= hi for k, (lo, hi) in bounds.items()):
        raise ValueError("Restock capacity exceeds authorized bounds")
    # A line takes about 11 s of a 180 s market window and the kit reads 10 lines a run, so more units a window means more units a line.
    if "max_lines_per_run" in value and (type(value["max_lines_per_run"]) is not int or not 1 <= value["max_lines_per_run"] <= 10):
        raise ValueError("Restock capacity exceeds authorized bounds")
    if not isinstance(value["revision"], str) or not value["revision"]:
        raise ValueError("Missing capacity revision")
    return value


def profitable_counts(md, now=None):
    """Use own ACP receipts and unit-linked cost/SKU, not public own-sale guesses."""
    md = Path(md)
    now = time.time() if now is None else now
    index = read(md / "local-settlements.json", {})
    if (index.get("schema") != 1 or index.get("active_round") != 3 or not index.get("r3_anchor_found")
            or index.get("conflicting_ledgers") or not 0 <= now-index.get("collected_at", 0) <= 3600):
        return {}
    rows = [r for r in index.get("records", {}).values() if r.get("round") == 3 and r.get("source") == "local_acp_settled"
            and type(r.get("ts")) in (int, float) and 0 <= now-r["ts"] <= 21600 and positive(r.get("amount"))]
    wanted = {r.get("unit") for r in rows if r.get("unit")}
    costs = read(md / "strategy-unit-costs.json", {})
    links = defaultdict(set)
    for path in (md / "signed-offers").glob("*.json"):
        offer = read(path, {})
        unit = offer.get("unit")
        cost = offer.get("cost", costs.get(unit))
        if unit in wanted and positive(cost) and isinstance(offer.get("sku"), str):
            if positive(costs.get(unit)) and costs[unit] != cost:
                links[unit].add(("conflicting-cost", costs[unit]))
            links[unit].add((offer["sku"], cost))
    counts, seen = Counter(), set()
    for row in rows:
        unit = row.get("unit")
        if unit in seen or len(links[unit]) != 1:
            continue
        seen.add(unit)
        sku, cost = next(iter(links[unit]))
        # Minimum six-hour storage allowance; this is not warranty-adjusted net profit.
        if sku != "conflicting-cost" and row["amount"] > cost * (1 + .01 * 6 / 24):
            counts[sku] += 1
    return dict(counts)
