"""Compare SKU/shop pairs using confirmed prices, catalogue value and existing risk guards."""

import math

from buyer_memory import choose_shop, EXPLORATION_INTERVAL


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def select(shops, memory, catalog, budget, *, policy=None, portfolio=None, kind=None):
    # Reuse risk evidence without comparing unrelated SKU prices.
    masked = [dict(row, item="__past_sku__") for row in memory]
    _, _, risks = choose_shop(shops, masked, "__order__", budget)
    by_name = {row["name"]: row for row in risks}
    candidates = []
    for shop in shops:
        risk = by_name[shop["name"]]
        if risk["blocked_price_mismatch"]:
            continue
        for offer in shop.get("offers", []):
            sku = offer["sku"]
            value = catalog.get(sku, {}).get("list_price")
            if not positive(value):
                continue
            cap = min(budget, int(value))
            protected = False
            faults = 0
            if policy and kind == "goods":
                purchases = [r for r in (portfolio or []) if r["shop"] == shop["url"] and r["sku"] == sku]
                protected = any(r.get("warranty_hours", 0) >= policy["preferred_warranty_hours"] for r in purchases)
                refused = any(r.get("url_at_time") == shop["url"] and r.get("item") == sku
                              and r.get("outcome") == "payment_refused"
                              and "warranty" in str(r.get("payment_reason", "")).lower() for r in memory[-20:])
                protected = protected and not refused
                faults = sum(bool(r.get("fault")) for r in purchases)
                discount = policy["minimum_surplus_ratio"] if protected else policy["uninsured_discount_ratio"]
                cap = min(cap, int(value * (1-discount)))
            if cap < 1:
                continue
            history = [r for r in memory if r.get("verification") == "market_settled"
                       and r.get("shop_name") == shop["name"] and r.get("item") == sku
                       and r.get("outcome") == "purchased" and positive(r.get("price"))]
            # Past prices are references, never current quotes. Unknown pairs use the full cap.
            reference = history[-1]["price"] if history else None
            estimated_price = reference if reference is not None else cap
            feasible = estimated_price <= cap
            surplus = value - estimated_price
            evidence = {"shop": shop["name"], "sku": sku, "catalogue_value": value,
                        "negotiation_cap": cap, "past_settled_price": reference,
                        "reference_within_cap": feasible, "estimated_surplus": surplus,
                        "warranty_required": policy["preferred_warranty_hours"] if protected else 0,
                        "attributed_faults": faults,
                        "estimate_only": True, "risk": risk}
            rank = (risk["recent_transport_errors"] >= 2,
                    risk["repeated_public_price_complaints"], faults > 0, not feasible,
                    -surplus, reference is None, shop["name"], sku)
            candidates.append((rank, shop, sku, cap, evidence))
    evidence = [row[4] for row in candidates]
    if not candidates:
        return None, None, budget, "No eligible SKU with a valid catalogue value.", evidence
    safe = [row for row in candidates if not any(row[0][:4])]
    rotation = len(memory) % EXPLORATION_INTERVAL == EXPLORATION_INTERVAL - 1
    if rotation and safe:
        visits = {row["shop_name"]: i for i, row in enumerate(memory)}
        chosen = min(safe, key=lambda row: (visits.get(row[1]["name"], -1), row[0]))
    else:
        chosen = min(candidates, key=lambda row: row[0])
        rotation = False
    _, shop, sku, cap, detail = chosen
    reason = (f"{shop['name']}/{sku}: {'rotation' if rotation else 'risk then surplus reference'}; "
              f"value={detail['catalogue_value']}, cap={cap}, "
              f"past settled price={detail['past_settled_price']}; no current quote or breakage forecast.")
    return shop, sku, cap, reason, evidence
