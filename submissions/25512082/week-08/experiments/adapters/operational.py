"""Decision-only adapters. They never sign, pay, buy, or access the network."""
from __future__ import annotations

from typing import Any

from competition.strategies import DiscountBuyer, SafeDiscountSeller1


def seller_preview(inventory: dict[str, Any], catalog: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    acceptable = order.get("acceptable") or ([order.get("item")] if order.get("item") else [])
    products = catalog.get("products", catalog) if isinstance(catalog, dict) else {}
    now = int(order.get("at", 0))
    units = [u for u in inventory.get("units", []) if u.get("sku") in acceptable]
    units = [u for u in units if u.get("grade") != "expired" and isinstance(u.get("cost"), (int, float))]
    units = [u for u in units if not u.get("expires_at") or u["expires_at"] > now]
    if order.get("kind") == "food" and order.get("use_within_hours"):
        need = now + float(order["use_within_hours"]) * 3600
        units = [u for u in units if isinstance(u.get("expires_at"), (int, float)) and u["expires_at"] >= need]
    if not units:
        return {"action": "reject", "reason": "no_verified_inventory"}
    unit = min(units, key=lambda u: (u.get("expires_at") or float("inf"), u.get("unit", "")))
    product = products.get(unit["sku"])
    if not isinstance(product, dict) or not isinstance(product.get("list_price"), (int, float)):
        return {"action": "reject", "reason": "missing_catalog_data"}
    terms = SafeDiscountSeller1().offer(unit, product, now)
    return {"action": "quote", "sku": unit["sku"], "unit": unit.get("unit"), "ask": terms.ask,
            "floor": terms.floor, "policy": "B-safe-1", "requires_kit_certificate": True}


def buyer_preview(order: dict[str, Any], offers: list[dict[str, Any]], history: dict[str, Any] | None = None) -> dict[str, Any]:
    safe = []
    acceptable = set(order.get("acceptable") or ([order.get("item")] if order.get("item") else []))
    budget = order.get("budget")
    for offer in offers:
        if offer.get("sku") not in acceptable or not isinstance(offer.get("ask"), (int, float)):
            continue
        if not isinstance(budget, (int, float)) or offer["ask"] > budget:
            continue
        # Advertised quality is not treated as cryptographic evidence. Explicitly invalid evidence is rejected;
        # unknown evidence is checked later by kit-v4 ACP `check_accept`.
        if offer.get("certificate_status") == "invalid":
            continue
        safe.append(offer)
    if not safe:
        return {"action": "reject", "reason": "no_safe_offer"}
    policy = DiscountBuyer()
    policy.history = dict(history or {})
    _, chosen, reason = policy.rank(order, safe)[0]
    accept = {"cert": True, "grades": ["A", "B"]}
    if chosen.get("kind") == "food":
        accept["min_hours_left"] = max(0, order.get("use_within_hours", 0))
    if chosen.get("kind") == "goods" and chosen.get("warranty_hours", 0) > 0:
        accept["warranty_hours"] = chosen["warranty_hours"]
    return {"action": "select", "shop": chosen["shop"], "sku": chosen["sku"], "max_price": budget,
            "reason": reason, "accept": accept, "policy": "B-original-with-kit-safety"}


def negotiation_guard(price: Any, floor: Any, budget: Any, turns: int, max_turns: int = 3) -> dict[str, Any]:
    if turns > max_turns:
        return {"action": "reject", "reason": "negotiation_limit"}
    if not all(isinstance(value, (int, float)) for value in (price, floor, budget)):
        return {"action": "reject", "reason": "invalid_price"}
    if price < floor or price > budget:
        return {"action": "reject", "reason": "price_out_of_bounds"}
    return {"action": "accept", "price": int(price)}


class SettlementGuard:
    """Process-local duplicate guard; durable IDs must also be restored from payment/history logs."""
    def __init__(self, settled: set[str] | None = None) -> None:
        self.settled = set(settled or ())

    def reserve(self, transaction_id: str) -> bool:
        if not transaction_id or transaction_id in self.settled:
            return False
        self.settled.add(transaction_id)
        return True


def restock_decision(inventory: dict[str, Any], catalog: dict[str, Any], demand: dict[str, Any],
                     mode: str = "safe") -> dict[str, Any]:
    """SAFE emits the exact kit contract. Preview diagnostics never authorize a purchase."""
    if mode not in {"safe", "preview"}:
        raise ValueError("only safe and preview modes are available before deployment approval")
    if mode == "safe":
        return {"buy": []}
    balance = inventory.get("balance")
    if not isinstance(balance, (int, float)) or balance < 0:
        return {"buy": [], "preview": [], "reason": "invalid_balance"}
    products = catalog.get("products", catalog) if isinstance(catalog, dict) else {}
    requested = demand if isinstance(demand, list) else demand.get("items", demand.get("demand", [])) if isinstance(demand, dict) else []
    preview = []
    for row in requested if isinstance(requested, list) else []:
        sku = row.get("sku") if isinstance(row, dict) else None
        product = products.get(sku) if sku else None
        # Official supply cost is not guaranteed by catalog.json. Preview only when an explicit estimate exists.
        estimate = product.get("estimated_cost", product.get("cost")) if isinstance(product, dict) else None
        if isinstance(product, dict) and isinstance(estimate, (int, float)):
            qty = 1 if product.get("kind") == "food" else min(2, int(row.get("qty", 1)))
            cost = qty * estimate
            held = sum(u.get("cost", 0) for u in inventory.get("units", []) if isinstance(u.get("cost"), (int, float)))
            if cost <= balance * 0.10 and held + cost <= 5_000_000 * 0.20:
                preview.append({"sku": sku, "qty": qty, "estimated_cost": cost})
    return {"buy": [], "preview": preview, "limits": {"purchase_share": 0.10, "stock_share": 0.20}}
