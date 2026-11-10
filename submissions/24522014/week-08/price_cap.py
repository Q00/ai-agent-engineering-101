"""A ceiling on what the shop asks, as a share of the catalogue list price, read from price_cap.json on every deal.

Base: the list price of the unit, not any earlier sale price. Boundary: the ceiling is floor(list price x ratio), so a sale at .79 of list is allowed and nothing goes above it. Units whose list price is max_list_price or more are left alone.
The floor stays in charge: when it is above the ceiling the quote is the floor, recorded as state "floor_bound". A missing or broken setting, or enabled other than true, means today's quote.
"""

import hashlib
import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "price_cap.json"
# pq1, approved by the user on 11-10: an opening-quote A/B on goods, split by task hash (sha256(f"{salt}:{task}") % 10000 < share x 10000).
# The test arm opens at max(floor, floor(list x ratio)) on every goods unit, 1M+ included; the control arm keeps the cap above. The
# floor is never lowered and the quote never raised. Off unless price_cap.json's "pq1" section is valid and enabled is true.


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


def pq1_settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8")).get("pq1")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    share, salt, ratio = value.get("share"), value.get("salt"), value.get("ratio")
    if not (number(share) and 0 <= share <= 1 and isinstance(salt, str) and salt and number(ratio) and .5 <= ratio <= 1):
        return None
    return {"share": share, "salt": salt, "ratio": ratio}


def pq1_arm(task, setting):
    return "test" if int(hashlib.sha256(f"{setting['salt']}:{task}".encode()).hexdigest(), 16) % 10000 < setting["share"] * 10000 else "control"


def capped(terms, unit, path=None, task=None):
    """The terms with the quote lowered to the ceiling, with what happened in terms["price_cap"]. Never lowers the floor and never raises the quote."""
    test = pq1_settings(path)
    listed = unit.get("list_price")
    if test is not None and task is not None and unit.get("kind") == "goods" and unit.get("unit") and number(listed) and listed > 0:
        arm = pq1_arm(task, test)
        if arm == "test":
            quote, floor = terms["quote"], terms["floor"]
            limit = math.floor(listed*test["ratio"])
            note = {"ratio": test["ratio"], "before": quote, "limit": limit, "test": test["salt"], "arm": arm}
            if quote <= limit:
                return {**terms, "price_cap": {**note, "state": "under"}}
            return {**terms, "quote": min(quote, max(limit, floor)), "price_cap": {**note, "state": "floor_bound" if floor > limit else "capped"}}
        out = capped(terms, unit, path)
        return {**out, "price_cap": {**(out.get("price_cap") or {"state": "no_cap"}), "test": test["salt"], "arm": arm}}
    setting = settings(path)
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
