"""User-selected purchasing limits; local arithmetic, no extra model calls."""
import math
from pathlib import Path

from inventory import read_json

ROOT = Path(__file__).resolve().parent


def load_policy():
    config = read_json(ROOT / "buyer-policy.json")
    if type(config.get("fast_quote_accept_enabled", False)) is not bool:
        raise ValueError("Invalid buyer policy: fast_quote_accept_enabled")
    for key in ("budget_cap_percent", "list_price_cap_percent", "target_percent_of_cap",
                "max_concession_percent_of_cap"):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 100:
            raise ValueError(f"Invalid buyer policy: {key}")
    if type(config.get("max_decisions")) is not int or not 1 <= config["max_decisions"] <= 12:
        raise ValueError("Invalid buyer policy: max_decisions")
    limit = config.get("selection_max_candidates", 6)
    if type(limit) is not int or not 2 <= limit <= 6:
        raise ValueError("Invalid buyer policy: selection_max_candidates")
    for key, default, minimum, maximum in (
            ("selection_timeout_seconds", 15, 1, 45),
            ("selection_cache_seconds", 900, 0, 3600)):
        value = config.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("Invalid buyer policy: " + key)
    cooldown = config.get("failure_cooldown_seconds", 900)
    if type(cooldown) is not int or not 0 <= cooldown <= 3600:
        raise ValueError("Invalid buyer policy: failure_cooldown_seconds")
    for key in ("grades", "food_grades"):
        grades = config.get(key, config.get("grades"))
        if (not isinstance(grades, list) or not grades
                or any(g not in ("A", "B", "C") for g in grades)):
            raise ValueError("Invalid buyer policy: " + key)
    for key in ("goods_warranty_hours", "food_buffer_hours"):
        if type(config.get(key)) not in (int, float) or not math.isfinite(config[key]) or config[key] < 0:
            raise ValueError(f"Invalid buyer policy: {key}")
    targets = config.get("target_list_price_percent")
    if "target_list_price_percent" in config and (not isinstance(targets, dict)
            or set(targets) != {"food", "goods"}
            or any(type(v) is not int or not 1 <= v <= 100 for v in targets.values())):
        raise ValueError("Invalid buyer policy: target_list_price_percent")
    preferred = config.get("preferred_goods_warranty_hours")
    completion = config.get("completion_list_price_percent")
    if "completion_list_price_percent" in config and (not isinstance(completion, dict)
            or set(completion) != {"food", "goods"}
            or any(type(v) is not int or not 1 <= v <= config["list_price_cap_percent"]
                   or v < (targets or {}).get(k, 0) for k, v in completion.items())):
        raise ValueError("Invalid buyer policy: completion_list_price_percent")
    if "preferred_goods_warranty_hours" in config and (type(preferred) not in (int, float)
            or not math.isfinite(preferred)
            or not config["goods_warranty_hours"] <= preferred <= 72):
        raise ValueError("Invalid buyer policy: preferred_goods_warranty_hours")
    if not isinstance(config.get("version"), str):
        raise ValueError("Buyer policy needs a version")
    if type(config.get("allow_accept_above_concession", False)) is not bool:
        raise ValueError("Invalid acceptance concession policy")
    prefs = config.get("review_preferences")
    if prefs is not None:
        if not isinstance(prefs, dict):
            raise ValueError("Review preferences must be an object")
        for key in ("budget_cap_percent", "list_price_cap_percent", "excellent_percent_of_preferred"):
            if type(prefs.get(key)) is not int or not 1 <= prefs[key] <= 100:
                raise ValueError("Invalid review preference: " + key)
        if (not isinstance(prefs.get("grades"), list) or not prefs["grades"]
                or any(g not in ("A", "B", "C") for g in prefs["grades"])):
            raise ValueError("Invalid review grade preferences")
        for key in ("goods_warranty_hours", "food_buffer_hours"):
            if type(prefs.get(key)) not in (int, float) or not math.isfinite(prefs[key]) or prefs[key] < 0:
                raise ValueError("Invalid review preference: " + key)
    return config


def negotiation_policy(policy):
    """Rating preferences stay in local memory, outside the negotiation context."""
    local = {"review_preferences", "selection_max_candidates", "selection_timeout_seconds",
             "selection_cache_seconds", "failure_cooldown_seconds"}
    return {k: v for k, v in policy.items() if k not in local} if policy else policy


def checkout_policy(order, config=None):
    config = config or load_policy()
    accept = {"cert": True, "grades": list(config["grades"])}
    if order.get("kind") == "food":
        accept["grades"] = list(config.get("food_grades", config["grades"]))
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
    terms = {
        "version": config["version"], "price_limit": limit,
        "fast_quote_accept_enabled": config.get("fast_quote_accept_enabled", False),
        "target_price": max(1, limit * config["target_percent_of_cap"] // 100),
        "list_price": ref, "max_decisions": config["max_decisions"],
        "max_concession": max(1, limit * config["max_concession_percent_of_cap"] // 100),
        "accept": checkout_policy(order, config) if order and order.get("round", 0) >= 3 else {},
        "kind": (order or {}).get("kind"),
    }
    target_percent = config.get("target_list_price_percent", {}).get(terms["kind"])
    if ref is not None and target_percent is not None:
        # A negotiation goal, never a new checkout authority or compulsory refusal.
        terms["target_price"] = min(terms["target_price"], max(1, ref * target_percent // 100))
    completion_percent = config.get("completion_list_price_percent", {}).get(terms["kind"])
    if ref is not None and completion_percent is not None:
        # Soft completion guidance, not the final checkout authority. Avoid a
        # fixed live-Saved cutoff or extra board/model calls for every order.
        terms["completion_price"] = min(limit, max(1, ref * completion_percent // 100))
    if terms["kind"] == "goods" and "preferred_goods_warranty_hours" in config:
        terms["preferred_warranty_hours"] = config["preferred_goods_warranty_hours"]
    if "allow_accept_above_concession" in config:
        terms["allow_accept_above_concession"] = config["allow_accept_above_concession"]
    prefs = config.get("review_preferences")
    if prefs and ref is not None:
        preferred = min(budget * prefs["budget_cap_percent"] // 100,
                        ref * prefs["list_price_cap_percent"] // 100)
        terms["review_preferences"] = {
            "price_limit": preferred,
            "excellent_price": preferred * prefs["excellent_percent_of_preferred"] // 100,
            "grades": list(prefs["grades"]),
            "goods_warranty_hours": prefs["goods_warranty_hours"],
            **({"food_min_hours_left": order["use_within_hours"] + prefs["food_buffer_hours"]}
               if order and order.get("kind") == "food" else {}),
        }
    return terms
