"""User-selected price/guarantee policy and factual, model-free shop copy."""
from datetime import datetime, timezone
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
    return {"price_floor": (cost * (100 + margin) + 99) // 100,
            "margin_percent": margin, "observed_grade": grade,
            "warranty_hours": policy["goods_warranty_hours"][grade] if product.get("kind") == "goods" else 0}


def warranty_copy(hours):
    return (f"{hours}-hour breakage warranty: market refunds the full price if it breaks within that period."
            if hours else "No breakage warranty offered.")


def quote_copy(product, price, terms, advice=None):
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
    parts.append(f"Asking price: {price}.")  # Keep the exact, labelled quote last.
    return " ".join(parts)


def shop_copy(policy, advice=None):
    guarantees = [f"{grade}-grade goods: {hours}-hour breakage warranty"
                  for grade, hours in policy["goods_warranty_hours"].items() if hours]
    terms = "; ".join(guarantees) + ". Other products have no breakage warranty." if guarantees else "No breakage warranty offered."
    return ("Inventory shop: ask by SKU. Market certificates and signed receipts. "
            + FOCUS_COPY.get((advice or {}).get("focus"), FOCUS_COPY["clarity"]) + " " + terms)
