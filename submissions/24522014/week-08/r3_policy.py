"""Explicit user-approved limits; discount stages are experimental heuristics."""

import json
import math
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent


def load_policy():
    return json.loads((ROOT / "r3_policy.json").read_text(encoding="utf-8"))


def selling_terms(unit, catalog=None, now=None):
    now = time.time() if now is None else now
    cost = unit["cost"]
    stage = -1
    reason = "standard"
    config = load_policy()["selling"]
    if unit.get("unit"):
        if unit.get("grade") == "expired" or (unit.get("expires_at") is not None and unit["expires_at"] <= now):
            raise ValueError("Inventory unit has expired")
        if unit.get("kind") == "food":
            shelf = (catalog or {}).get(unit["sku"], {}).get("shelf_hours")
            if shelf and unit.get("expires_at") is not None:
                ratio = (unit["expires_at"] - now) / (shelf * 3600)
                for i, limit in enumerate(config["food_remaining_ratios"]):
                    if ratio < limit:
                        stage = i
                if stage >= 0:
                    reason = "food_expiry_clearance"
        elif unit.get("kind") == "goods" and unit.get("supplied_at") is not None:
            age = max(0, now - unit["supplied_at"]) / 3600
            for i, limit in enumerate(config["goods_age_hours"]):
                if age >= limit:
                    stage = i
            if stage >= 0:
                reason = "long_stock_clearance"
    floor = max(1, math.ceil(cost * (config["cost_multipliers"][stage] if stage >= 0 else 1)))
    # A clearance quote is the stage price; ordinary goods retain a price above cost.
    quote = floor if stage >= 0 else max(floor, math.ceil(unit["list_price"]))
    return {"floor": floor, "quote": quote, "discount_reason": reason,
            "discount_stage": stage + 1, "below_cost_allowed": stage >= 0 and floor < cost}


def buyer_accept(order):
    config = load_policy()["buying"]
    result = {"cert": config["certificate_required"], "grades": config["grades"]}
    if order.get("kind") == "food":
        hours = order.get("use_within_hours")
        if type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0:
            raise ValueError("Food order requires a nonnegative use period")
        result["min_hours_left"] = hours + config["delivery_hours"] + config["safety_hours"]
    return result
