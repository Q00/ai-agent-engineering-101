"""A small, capped food trial: named categories only, one unit per SKU, and never a second buy until it is judged.

Off unless food_trial.json says otherwise, and off if that file is missing or exceeds the approved bounds. The state file
keeps what was planned and what arrived, so a sold unit is never bought again. A unit's grade is drawn on arrival and
cannot be chosen; cost is estimated at the contract's worst share of list price.
"""

import json
import math
import os
from pathlib import Path
import tempfile
import time

ROOT = Path(__file__).resolve().parent
OFF = {"enabled": False}
WORST_COST_SHARE = .75
MAX_UNITS, MAX_COST = 4, 150000
MIN_SETTLES_6H, MIN_BUYERS = 3, 3


def load_settings(path=None):
    """Only a complete file within the approved bounds turns the trial on."""
    try:
        value = json.loads(Path(path or ROOT / "food_trial.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(OFF)
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return dict(OFF)
    categories = value.get("categories")
    if (not isinstance(categories, dict) or not categories or any(type(n) is not int or n < 1 for n in categories.values())
            or sum(categories.values()) > MAX_UNITS
            or type(value.get("max_total_units")) is not int or not 1 <= value["max_total_units"] <= MAX_UNITS
            or type(value.get("max_total_estimated_cost")) not in (int, float) or not 0 < value["max_total_estimated_cost"] <= MAX_COST):
        return dict(OFF)
    return {"enabled": True, "categories": categories, "max_total_units": value["max_total_units"], "max_total_estimated_cost": value["max_total_estimated_cost"]}


def state_path(md):
    return Path(md) / "food-trial.json"


def load_state(md):
    """A missing file is a fresh trial. A damaged one returns None, so nothing more is bought."""
    path = state_path(md)
    if not path.exists():
        return {"planned": {}, "units": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or not isinstance(state.get("planned"), dict) or not isinstance(state.get("units"), dict):
        return None
    return state


def save_state(md, state):
    path = state_path(md)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".food-trial.", suffix=".tmp")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=1)
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def observe(state, inventory, now):
    """Keep each arrived food unit's facts, so the trial can be judged after the unit has left the inventory."""
    for unit in inventory.get("units") or []:
        if unit.get("kind") == "food" and unit.get("sku") in state["planned"] and unit.get("unit") not in state["units"]:
            state["units"][unit["unit"]] = {"sku": unit["sku"], "cost": unit.get("cost"), "grade_on_arrival": unit.get("grade"), "supplied_at": unit.get("supplied_at"),
                                            "expires_at": unit.get("expires_at"), "first_seen_at": now}
    return state


def estimated_cost(product, surge):
    """Worst contract share of list price, raised by any published surge factor since its effect on cost is unproven."""
    return product["list_price"] * WORST_COST_SHARE * max(1, surge or 1)


def choose(settings, state, catalog, intelligence, surge_rows, now):
    """SKUs to buy now, one unit each. Surged SKUs are avoided, then the longest shelf life and the most public sales win."""
    if not settings.get("enabled") or state is None:
        return []
    surge = {row.get("sku"): row.get("factor") for row in surge_rows or [] if isinstance(row, dict)}
    planned = state["planned"]
    spent = sum(row.get("estimated_cost", 0) for row in planned.values())
    picks = []
    count = len(planned)
    for category, wanted in settings["categories"].items():
        have = sum(row.get("category") == category for row in planned.values())
        rows = []
        for sku, product in catalog.items():
            signal = intelligence.get(sku) or {}
            price = product.get("list_price")
            if (product.get("kind") == "food" and product.get("category") == category and sku not in planned
                    and type(price) in (int, float) and math.isfinite(price) and price > 0
                    and signal.get("settles_6h", 0) >= MIN_SETTLES_6H and signal.get("distinct_buyers", 0) >= MIN_BUYERS):
                rows.append((sku in surge, -(product.get("shelf_hours") or 0), -signal["settles_6h"], sku))
        for _, _, _, sku in sorted(rows):
            if have >= wanted or count >= settings["max_total_units"]:
                break
            product, signal = catalog[sku], intelligence[sku]
            cost = estimated_cost(product, surge.get(sku))
            if spent + cost > settings["max_total_estimated_cost"]:
                continue
            picks.append({"sku": sku, "category": category, "estimated_cost": cost, "surge": surge.get(sku), "shelf_hours": product.get("shelf_hours"),
                          "reason": f"public sales {signal['settles_6h']} in 6h from {signal['distinct_buyers']} buyers; shelf {product.get('shelf_hours')} h"})
            spent += cost
            have += 1
            count += 1
    return picks


def record_plan(state, picks, bought, now):
    """Count a planned purchase before it is sent, so a repeat can never follow even if the sale happens at once."""
    for pick in picks:
        if pick["sku"] in bought:
            state["planned"][pick["sku"]] = dict(pick, planned_at=now)
    return state
