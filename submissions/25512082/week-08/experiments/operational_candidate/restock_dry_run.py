"""B-safe-1 purchase preview. It never emits an executable buy plan."""
from __future__ import annotations

from typing import Any


def preview(inventory: dict[str, Any], catalog: dict[str, Any], demand: list[dict[str, Any]]) -> dict[str, Any]:
    balance = inventory.get("balance")
    if not isinstance(balance, (int, float)) or balance < 0:
        return {"buy": [], "would_buy": [], "reason": "invalid_balance"}
    held = sum(u.get("cost", 0) for u in inventory.get("units", []) if isinstance(u.get("cost"), (int, float)))
    call_cap, stock_cap = balance * .10, 5_000_000 * .20
    demand_categories = {row.get("category") for row in demand if isinstance(row, dict)}
    candidates = []
    for sku, product in catalog.items():
        if not isinstance(product, dict) or product.get("category") not in demand_categories: continue
        units = [u for u in inventory.get("units", []) if u.get("sku") == sku and isinstance(u.get("cost"), (int, float))]
        if not units: continue  # no observed cost: fail closed instead of inventing one
        estimate = round(sum(u["cost"] for u in units) / len(units))
        desired = 1 if product.get("kind") == "food" else 3
        qty = max(0, desired - len(units)); cost = qty * estimate
        if qty and cost <= call_cap and held + cost <= stock_cap:
            candidates.append({"sku": sku, "qty": qty, "estimated_unit_cost": estimate,
                               "estimated_total": cost, "within_limits": True})
    return {"buy": [], "would_buy": candidates,
            "limits": {"per_call_share": .10, "held_stock_share": .20}, "balance_before": balance,
            "balance_after": balance}
