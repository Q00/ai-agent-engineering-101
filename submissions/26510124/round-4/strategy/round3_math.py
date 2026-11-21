"""Unchanged pure function bodies extracted from the student's Round 3 code.
Always pass config explicitly; no imports or state access to week-08.
"""
from copy import deepcopy
import math
GRADE_LABELS = {"A": "A", "B": "B", "C": "C"}

def selling_terms(product, policy):
    grade, cost = product.get("grade"), product.get("cost")
    if grade not in GRADE_LABELS or type(cost) is not int or cost <= 0:
        raise ValueError("Cannot price an unverified inventory grade or cost")
    margin = policy["grade_margin_percent"][grade]
    closing_margin = policy["closing"]["grade_margin_percent"][grade]
    return {"price_floor": (cost * (100 + margin) + 99) // 100,
            "closing_price_floor": (cost * (100 + closing_margin) + 99) // 100,
            "closing": deepcopy(policy["closing"]),
            "margin_percent": margin, "observed_grade": grade,
            "warranty_hours": policy["goods_warranty_hours"][grade] if product.get("kind") == "goods" else 0}

def closing_context(product, deal, now):
    """Small, local policy state; never infer demand or a buyer's private budget."""
    terms, offers = deal["terms"], deal["offers"]
    rules, normal = terms["closing"], terms["price_floor"]
    prior = deal.get("negotiation", {})
    reason = prior.get("discount_reason")
    count = len(offers)
    if deal.get("final_quote"):
        return {**prior, "stage": "final_reply", "offer_number": count}
    stage = ("opening" if not offers else "final_offer" if count >= rules["final_offer"]
             else "close" if count >= rules["start_offer"] else "negotiate")
    if count >= rules["start_offer"] and reason is None and terms["closing_price_floor"] < normal:
        offer, previous_best = offers[-1], max(offers[:-1])
        near = (normal - offer) * 100 <= normal * rules["near_floor_percent"]
        improved = (offer - previous_best) * 100 >= normal * rules["min_improvement_percent"]
        supplied, expiry = product.get("supplied_at"), product.get("expires_at")
        if near and improved:
            reason = "improving_close_offer"
        elif (product["kind"] == "goods" and type(supplied) in (int, float)
              and math.isfinite(supplied) and 0 <= supplied <= now - rules["goods_age_hours"] * 3600):
            reason = "aged_goods"
        elif (product["kind"] == "food" and type(expiry) in (int, float)
              and math.isfinite(expiry) and 0 < expiry - now <= rules["food_remaining_hours"] * 3600):
            reason = "expiring_food"
    # Once unlocked, the floor can never rise again within this negotiation.
    return {"stage": stage, "offer_number": count, "discount_reason": reason,
            "price_floor": terms["closing_price_floor"] if reason else normal,
            "previous_best_offer": max(offers[:-1]) if len(offers) > 1 else None}

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

