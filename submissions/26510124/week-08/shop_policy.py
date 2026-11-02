"""User-selected price/guarantee policy and factual, model-free shop copy."""
from datetime import datetime, timezone
from copy import deepcopy
import math
from pathlib import Path

from feedback import load_json

ROOT = Path(__file__).resolve().parent
GRADE_LABELS = {"A": "normal", "B": "damaged packaging", "C": "refurbished"}
FOCUS_COPY = {
    "value": "Compare this quote with the catalog price before deciding.",
    "clarity": "One unit at the quoted price; check the certificate and terms at checkout.",
    "quality": "Use the market certificate to verify the stated product condition.",
}


def policy_at(root=ROOT):
    cfg = load_json(root / "shop-policy.json")
    if not isinstance(cfg, dict):
        raise ValueError("Shop policy unavailable")
    for field, upper in (("grade_margin_percent", 50), ("goods_warranty_hours", 6)):
        values = cfg.get(field)
        if (not isinstance(values, dict) or set(values) != set(GRADE_LABELS)
                or any(type(n) is not int or not 0 <= n <= upper for n in values.values())):
            raise ValueError("Invalid shop policy: " + field)
    margins = cfg["grade_margin_percent"]
    if not margins["A"] >= margins["B"] >= margins["C"]:
        raise ValueError("Higher grades must not have lower floor margins")
    closing = cfg.get("closing")
    if not isinstance(closing, dict):
        raise ValueError("Missing closing policy")
    floors = closing.get("grade_margin_percent")
    if (not isinstance(floors, dict) or set(floors) != set(GRADE_LABELS)
            or any(type(n) is not int or not 0 <= n <= margins[g] for g, n in floors.items())
            or not floors["A"] >= floors["B"] >= floors["C"]):
        raise ValueError("Invalid closing grade margins")
    for key, low, high in (("start_offer", 2, 3), ("final_offer", 2, 5),
                           ("near_floor_percent", 1, 5), ("min_improvement_percent", 1, 5),
                           ("goods_age_hours", 6, 72), ("food_remaining_hours", 1, 6)):
        n = closing.get(key)
        if type(n) is not int or not low <= n <= high:
            raise ValueError("Invalid closing policy: " + key)
    if closing["final_offer"] < closing["start_offer"]:
        raise ValueError("Final offer cannot precede closing")
    stock = cfg.get("restock")
    if not isinstance(stock, dict) or type(stock.get("enabled")) is not bool:
        raise ValueError("Invalid restock policy")
    for key, low, high in (("cash_reserve_percent", 25, 80), ("max_units_per_run", 1, 10),
                           ("max_stock_units", 1, 50), ("max_units_per_sku", 1, 5),
                           ("max_food_units_per_sku", 1, 2), ("interval_seconds", 900, 86400),
                           ("exploration_units", 0, 2)):
        n = stock.get(key)
        if type(n) is not int or not low <= n <= high:
            raise ValueError("Invalid restock policy: " + key)
    return cfg


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


def warranty_copy(hours):
    return (f"{hours}-hour breakage warranty: market refunds the full price if it breaks within that period."
            if hours else "No breakage warranty offered.")


def quote_copy(product, price, terms, advice=None, *, final=False):
    # Only fixed templates and actual inventory fields reach customers, never analyst free text.
    parts = [f"{product['name']} ({product['sku']})."]
    if product["kind"] == "goods":
        grade = product["grade"]
        parts.append(f"Grade {grade}: {GRADE_LABELS[grade]}.")
    else:
        expiry = datetime.fromtimestamp(product["expires_at"], timezone.utc).isoformat()
        parts.append(f"Food expiry: {expiry}; remaining shelf life is confirmed at checkout.")
    parts.append(warranty_copy(terms["warranty_hours"]))
    parts.append(FOCUS_COPY.get((advice or {}).get("focus"), FOCUS_COPY["clarity"]))
    parts.append("Market certificate included.")
    if final:
        parts.append("Final offer: send this asking price to accept; a lower offer ends this negotiation.")
    parts.append(f"Asking price: {price}.")  # Keep the exact, labelled quote last.
    return " ".join(parts)


def shop_copy(policy, advice=None):
    guarantees = [f"{grade}-grade goods: {hours}-hour breakage warranty"
                  for grade, hours in policy["goods_warranty_hours"].items() if hours]
    terms = "; ".join(guarantees) + ". Other products have no breakage warranty." if guarantees else "No breakage warranty offered."
    return ("Inventory shop: ask by SKU. Market certificates and signed receipts. "
            + FOCUS_COPY.get((advice or {}).get("focus"), FOCUS_COPY["clarity"]) + " " + terms)
