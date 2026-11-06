"""A ceiling on what the shop asks, as a share of the catalogue list price, read from price_cap.json on every deal.

Base: the list price of the unit, not any earlier sale price. Boundary: the ceiling is floor(list price x ratio), so a sale at .79 of list is allowed and nothing goes above it. Units whose list price is max_list_price or more are left alone.
The floor stays in charge: when it is above the ceiling the quote is the floor, recorded as state "floor_bound". A missing or broken setting, or enabled other than true, means today's quote.
"""

import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "price_cap.json"


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    ratio, top = value.get("ratio"), value.get("max_list_price")
    if not number(ratio) or not .5 <= ratio <= 1 or not (top is None or number(top) and top > 0):
        return None
    return {"ratio": ratio, "max_list_price": top}


def capped(terms, unit, path=None):
    """The terms with the quote lowered to the ceiling, with what happened in terms["price_cap"]. Never lowers the floor and never raises the quote."""
    setting = settings(path)
    listed = unit.get("list_price")
    if setting is None or not unit.get("unit") or not number(listed) or listed <= 0:
        return terms
    quote, floor = terms["quote"], terms["floor"]
    note = {"ratio": setting["ratio"], "before": quote}
    if setting["max_list_price"] is not None and listed >= setting["max_list_price"]:
        return {**terms, "price_cap": {**note, "state": "above_max_list_price"}}
    limit = math.floor(listed*setting["ratio"])
    note["limit"] = limit
    if quote <= limit:
        return {**terms, "price_cap": {**note, "state": "under"}}
    return {**terms, "quote": min(quote, max(limit, floor)), "price_cap": {**note, "state": "floor_bound" if floor > limit else "capped"}}
