"""Shop b, approved 11-09: places of their own in restock for 1M+ goods with our own recent sales. Off unless hi_band_slots.json says so.

Up to `slots` units of goods listed at `min_list_price` or more are held outside the total cap, and planned before the ordinary
SKUs outside the run's units and lines; they pay from the same run budget at worst cost (.75 x list x surge). A place goes only to a
SKU with at least `min_own_sales_24h` of our completed sales in the last `min_own_sales_window_hours` (24 to 72) and a positive Book sum (our price minus our real
cost); held plus planned 1M+ stock stays within `max_worst_cost_total` (at most 24M) at worst cost; that evidence stands in for the market median, as in proposal 2, and a surged SKU keeps the median test and one unit.
Bounds: slots 1 to 20 and the worst-cost cap at most 24M (raised from 10 and 12M on the user's approval); the shipped setting decides the values in use.
Read on every restock run; anything missing, unreadable or malformed means off, which is today's plan.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "hi_band_slots.json"


def settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    slots, floor, sales = value.get("slots"), value.get("min_list_price"), value.get("min_own_sales_24h")
    window, cap = value.get("min_own_sales_window_hours", 24), value.get("max_worst_cost_total")
    if not (type(slots) is int and 1 <= slots <= 20 and type(floor) is int and floor >= 1000000 and type(sales) is int and 1 <= sales <= 10
            and type(window) is int and 24 <= window <= 72 and type(cap) is int and 1 <= cap <= 24000000):
        return None
    return {"slots": slots, "min_list_price": floor, "min_own_sales_24h": sales, "window_hours": window, "max_worst_cost_total": cap}


def for_run(md, now, catalog, path=None):
    """{"slots", "min_list_price", "sales": {sku: our completed sales in 24 h}} for one restock run, or None when off or on any error."""
    try:
        chosen = settings(path)
        if not chosen:
            return None
        import median_bypass
        counts = median_bypass.confirmed_counts(md, now, window=chosen["window_hours"] * 3600)
        if counts is None:
            return None
        sales = {sku: n for sku, (n, book) in counts.items()
                 if n >= chosen["min_own_sales_24h"] and book > 0 and (catalog.get(sku) or {}).get("kind") == "goods"
                 and ((catalog.get(sku) or {}).get("list_price") or 0) >= chosen["min_list_price"]}
        return {"slots": chosen["slots"], "min_list_price": chosen["min_list_price"], "sales": sales, "max_worst_cost_total": chosen["max_worst_cost_total"]}
    except Exception:
        return None
