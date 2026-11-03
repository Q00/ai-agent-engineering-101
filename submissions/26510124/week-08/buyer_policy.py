"""User-selected purchasing limits; local arithmetic, no extra model calls."""
import math
from pathlib import Path

from inventory import read_json

ROOT = Path(__file__).resolve().parent


def load_policy():
    config = read_json(ROOT / "buyer-policy.json")
    for key in ("budget_cap_percent", "list_price_cap_percent", "target_percent_of_cap",
                "max_concession_percent_of_cap"):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 100:
            raise ValueError(f"Invalid buyer policy: {key}")
    if type(config.get("max_decisions")) is not int or not 1 <= config["max_decisions"] <= 12:
        raise ValueError("Invalid buyer policy: max_decisions")
    limit = config.get("selection_max_candidates", 6)
    if type(limit) is not int or not 2 <= limit <= 6:
        raise ValueError("Invalid buyer policy: selection_max_candidates")
    if (not isinstance(config.get("grades"), list) or not config["grades"]
            or any(g not in ("A", "B", "C") for g in config["grades"])):
        raise ValueError("Invalid buyer policy: grades")
    for key in ("goods_warranty_hours", "food_buffer_hours"):
        if type(config.get(key)) not in (int, float) or not math.isfinite(config[key]) or config[key] < 0:
            raise ValueError(f"Invalid buyer policy: {key}")
    if not isinstance(config.get("version"), str):
        raise ValueError("Buyer policy needs a version")
    return config


def checkout_policy(order, config=None):
    config = config or load_policy()
    accept = {"cert": True, "grades": list(config["grades"])}
    if order.get("kind") == "food":
        hours = order.get("use_within_hours")
        if type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0:
            raise ValueError("Invalid food use_within_hours")
        accept["min_hours_left"] = hours + config["food_buffer_hours"]
    elif order.get("kind") == "goods":
        accept["warranty_hours"] = config["goods_warranty_hours"]
    else:
        raise ValueError("Round 3 order must identify food or goods")
    return accept


def buying_terms(budget, *, product=None, order=None, config=None):
    config = config or load_policy()
    if type(budget) is not int or budget <= 0:
        raise ValueError("Budget must be a positive integer")
    limit = budget * config["budget_cap_percent"] // 100
    ref = None
    if product is not None:
        ref = product.get("list_price")
        if type(ref) is not int or ref <= 0:
            raise ValueError("Catalog list price must be a positive integer")
        limit = min(limit, ref * config["list_price_cap_percent"] // 100)
    if order and order.get("round", 0) >= 3 and product is None:
        raise ValueError("Round 3 requires the market catalog price; do not guess from seller text")
    if limit < 1:
        raise ValueError("No positive integer price meets this purchasing policy")
    return {
        "version": config["version"], "price_limit": limit,
        "target_price": max(1, limit * config["target_percent_of_cap"] // 100),
        "list_price": ref, "max_decisions": config["max_decisions"],
        "max_concession": max(1, limit * config["max_concession_percent_of_cap"] // 100),
        "accept": checkout_policy(order, config) if order and order.get("round", 0) >= 3 else {},
        "kind": (order or {}).get("kind"),
    }
