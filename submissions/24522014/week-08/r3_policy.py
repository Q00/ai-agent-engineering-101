"""Explicit user-approved limits; discount stages are experimental heuristics."""

import json
import math
from pathlib import Path
import time
from datetime import datetime
from strategy_policy import effective

ROOT = Path(__file__).resolve().parent


def load_policy():
    return json.loads((ROOT / "r3_policy.json").read_text(encoding="utf-8"))


def round_end_hours(selling, key, shipped=24):
    """Hours before selling.round_end that a round-end rule starts; 0 turns it off.

    The user's 11-16 choice in the Orchestrator session (about 09:0x KST, "2시간 전까지 구매, 정리 판매 끔 (권장)"): the board
    counts held stock at cost in Book (Book = profit + stock_cost, and shop ALL fits pct(Book) to .0005 over all 68 scored shops),
    so the purchase stop moves from 24 h to 2 h before the end and the below-cost round-end clearance goes off. A missing or bad
    value keeps the rule as it was before (24 h)."""
    value = selling.get(key, shipped)
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 24:
        return value
    return shipped


def food_terms(cost, list_price, left, rule):
    """(floor, quote, reason, clearance) for food by the share of shelf life left, or None for ordinary pricing.

    Orders need 10, 25, 50 or 75% of the shelf life. While some order can still use the unit it sells near what the market
    pays; once none can, it goes at cost. The floor is never below cost.
    """
    if left >= rule["standard_from_left"]:
        return None
    cost = max(1, math.ceil(cost))
    if left < rule["clearance_below_left"]:
        return cost, cost, "food_expiry_clearance", True
    band = next(b for b in rule["market_bands"] if left >= b["left_from"])
    floor = max(cost, math.ceil(list_price * band["floor_share"]))
    return floor, max(floor, math.ceil(list_price * band["quote_share"])), "food_market_price", False


def selling_terms(unit, catalog=None, now=None, signals=None, runtime=None):
    now = time.time() if now is None else now
    cost = unit["cost"]
    stage = -1
    reason = "standard"
    config = load_policy()["selling"]
    food = None
    if unit.get("unit"):
        if unit.get("grade") == "expired" or (unit.get("expires_at") is not None and unit["expires_at"] <= now):
            raise ValueError("Inventory unit has expired")
        if unit.get("kind") == "food":
            shelf = (catalog or {}).get(unit["sku"], {}).get("shelf_hours")
            if shelf and unit.get("expires_at") is not None:
                food = food_terms(cost, unit["list_price"], (unit["expires_at"] - now) / (shelf * 3600), config["food_pricing_v2"])
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
    if food:
        floor, quote, reason, clearance = food
        # Only clearance is a fixed price; a market price stays open to offers down to the floor.
        stage = 0 if clearance else -1
    if unit.get("unit") and unit.get("kind") == "goods":
        age = max(0, now - unit.get("supplied_at", now)) / 3600
        storage = cost * 0.01 * age / 24
        # Ordinary sales cover carrying cost and an initial 5% cost margin.
        floor = max(1, math.ceil(cost + storage + cost * config["ordinary_margin"]))
        quote = max(floor, math.ceil(unit["list_price"]))
        signal = signals or {}
        hot = signal.get("distinct_buyers", 0) >= 3 and signal.get("settles_6h", 0) >= 3
        if stage >= 0:
            ratio = 1 if hot else config["goods_quote_ratios"][stage]
            quote = max(floor, math.ceil(unit["list_price"] * ratio))
            reason = "demand_supported_stock" if hot else "long_stock_margin_clearance"
        reference = signal.get("median_price")
        if hot and type(reference) in (int, float) and math.isfinite(reference) and reference > 0:
            quote = max(floor, min(quote, math.ceil(reference * 1.03)))
        loss_stage = sum(age >= h for h in config["goods_loss_hours"])
        end = datetime.fromisoformat(config["round_end"]).timestamp()
        hours_left = (end - now) / 3600
        # Off at 0 (round_end_clearance_hours); the long-stock loss stages above still apply by age.
        window = round_end_hours(config, "round_end_clearance_hours")
        clearing = window > 0 and 0 <= hours_left <= window
        if clearing:
            loss_stage = max(loss_stage, 2 if hours_left <= 6 else 1)
        if loss_stage:
            floor = max(1, math.ceil(cost * config["cost_multipliers"][loss_stage]))
            quote = floor
            reason = "round_end_clearance" if clearing else "long_stock_loss_clearance"
            stage = 3 + loss_stage
    runtime = effective(now=now) if runtime is None else runtime
    multiplier = runtime["controls"]["quote_multipliers"].get(unit.get("sku"), 1)
    if unit.get("kind") == "goods" and not (stage >= 0 and floor < cost):
        quote = max(floor, min(max(floor, math.ceil(unit["list_price"])), math.ceil(quote*multiplier)))
    # Food floor, approved by the user on 11-10 (price at least cost x 1.08), behind food_pricing_v2.floor_cost_multiple (1 to 1.5;
    # missing or null means off): it binds on every food path, fresh, market band and expiry clearance alike, and never lowers anything.
    multiple = config["food_pricing_v2"].get("floor_cost_multiple") if unit.get("kind") == "food" else None
    if type(multiple) in (int, float) and math.isfinite(multiple) and 1 <= multiple <= 1.5:
        floor = max(floor, math.ceil(cost * multiple))
        quote = max(quote, floor)
    return {"floor": floor, "quote": quote, "discount_reason": reason,
            "strategy_version": runtime["version"], "strategy_experiment": runtime.get("experiment"),
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
