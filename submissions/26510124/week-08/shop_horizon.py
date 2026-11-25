"""Deterministic buying deadlines and first-quote bounds for a scheduled round.

This module never calls a model, changes a live quote, requests bankruptcy, or
stops a service. A selling-time estimate is a planning limit, not a promise of
future demand or of the shelf life of stock that has not arrived yet.
"""
from datetime import datetime
import math


DEFAULT_CONFIG = {
    "round": 3,
    "ends_at": "2026-11-17T23:59:00+09:00",
    "wind_down_hours": 24,
    "stop_buying_hours": 6,
    "goods_fallback_hours": 24,
    "food_fallback_hours": 6,
    "min_mature_sales": 3,
    "sell_time_safety_factor": 2,
    "min_sell_hours": 1,
    "goods_warranty_buffer_hours": 6,
    "settlement_buffer_hours": 1,
    "clearance_markup_percent": 1,
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _deadline(config):
    value = config.get("ends_at")
    if not isinstance(value, str):
        raise ValueError("Round end must be an ISO timestamp with a timezone")
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError("Timezone required")
        stamp = date.timestamp()
        if not _number(stamp) or stamp <= 0:
            raise ValueError("Invalid deadline")
        return stamp
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Round end must be an ISO timestamp with a timezone") from exc


def validate(config):
    """Validate the horizon sub-config; never infer a schedule for other rounds."""
    if not isinstance(config, dict) or type(config.get("round")) is not int or config["round"] < 1:
        raise ValueError("Horizon policy needs an explicit round")
    _deadline(config)
    for key, lower, upper in (
        ("wind_down_hours", 6, 168), ("stop_buying_hours", 1, 24),
        ("goods_fallback_hours", 1, 168), ("food_fallback_hours", 1, 48),
        ("min_mature_sales", 3, 100), ("sell_time_safety_factor", 1, 5),
        ("min_sell_hours", 1, 24), ("goods_warranty_buffer_hours", 6, 24),
        ("settlement_buffer_hours", 1, 24), ("clearance_markup_percent", 0, 3),
    ):
        value = config.get(key)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError("Invalid horizon policy: " + key)
    if config["stop_buying_hours"] > config["wind_down_hours"]:
        raise ValueError("Buying must stop within the wind-down window")


def phase(config, rnd, now):
    """Return local scheduling state. Unknown input closes buying, not selling."""
    unavailable = {"phase": "unavailable", "round": rnd, "ends_at": None,
                   "remaining_hours": None, "allow_purchases": False,
                   "allow_exploration": False}
    try:
        validate(config)
    except (ValueError, TypeError):
        return {**unavailable, "reason": "invalid_horizon_config"}
    unavailable["ends_at"] = config["ends_at"]
    if type(rnd) is not int or rnd != config["round"]:
        return {**unavailable, "reason": "round_mismatch"}
    if not _number(now) or now < 0:
        return {**unavailable, "reason": "invalid_clock"}
    remaining = (_deadline(config) - now) / 3600
    state = ("ended" if remaining <= 0 else "stop_buying" if remaining <= config["stop_buying_hours"]
             else "wind_down" if remaining <= config["wind_down_hours"] else "normal")
    return {"phase": state, "round": rnd, "ends_at": config["ends_at"],
            "remaining_hours": max(0, remaining), "allow_purchases": state in ("normal", "wind_down"),
            "allow_exploration": state == "normal", "reason": "round_" + state}


def purchase_window(config, rnd, product, sku_economics, now):
    """Whether this candidate has time to sell and mature before the deadline.

Require three mature sales with no unknown/pending observations before using
the mean. Both a measured mean and the conservative fallback get a 2x safety
factor by default; all goods reserve the maximum six-hour warranty because
their grade is unknown before supply. Food freshness still needs validation
after delivery and at checkout.
"""
    state = phase(config, rnd, now)
    if not state["allow_purchases"]:
        return False, state["reason"]
    if not isinstance(product, dict) or product.get("kind") not in ("goods", "food"):
        return False, "unknown_product_kind"
    metrics = sku_economics if isinstance(sku_economics, dict) else {}
    mature, mean = metrics.get("mature"), metrics.get("mean_hours")
    trusted = (type(mature) is int and mature >= config["min_mature_sales"]
               and metrics.get("pending", 0) == 0 and metrics.get("unknown", 0) == 0
               and _number(mean) and mean >= 0)
    if not state["allow_exploration"] and not trusted:
        return False, "unproven_near_round_end"
    hours = mean if trusted else config[product["kind"] + "_fallback_hours"]
    required = max(config["min_sell_hours"], hours * config["sell_time_safety_factor"])
    required += config["settlement_buffer_hours"]
    if product["kind"] == "goods":
        required += config["goods_warranty_buffer_hours"]
    if state["remaining_hours"] < required:
        return False, "insufficient_time_to_sell_and_mature"
    return True, "observed_turnover_window" if trusted else "fallback_turnover_window"


def clearance_bounds(config, rnd, product, terms, now):
    """First quote only: narrow the markup, preserving the normal policy floor.

The caller applies this once while opening a negotiation. An already-issued
quote and its pinned warranty must never be changed by a later clock tick.
"""
    state = phase(config, rnd, now)
    if state["phase"] not in ("wind_down", "stop_buying"):
        return None
    if not isinstance(product, dict) or not isinstance(terms, dict):
        return None
    floor, cost, list_price = terms.get("price_floor"), product.get("cost"), product.get("list_price")
    if (type(floor) is not int or type(cost) is not int or type(list_price) is not int
            or cost <= 0 or list_price <= 0 or floor < cost):
        return None
    # Integer rounding may add one currency unit; never force a loss or a
    # below-floor quote even when the list price is lower than that floor.
    cap = (floor * (100 + config["clearance_markup_percent"]) + 99) // 100
    cap = min(cap, max(floor, list_price))
    return {"minimum": floor, "maximum": cap, "target": cap, "source": "round_end_stock_clearance"}
