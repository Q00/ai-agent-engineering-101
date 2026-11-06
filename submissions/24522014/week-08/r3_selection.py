"""Compare SKU/shop pairs using confirmed prices, catalogue value and existing risk guards."""

import math
import time
from datetime import datetime
from discount_floor import ceiling, least_required, load as load_floor
from market_intel import settlement_price


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def wording_substitution(row):
    """A mismatch our own name-and-SKU request invited: right price and task, another item. Still refused, not held against the shop."""
    receipt = row.get("receipt")
    return (row.get("outcome") == "receipt_mismatch" and row.get("request_format") == "name_and_sku" and isinstance(receipt, dict)
            and receipt.get("item") is not None and receipt["item"] != row.get("item")
            and receipt.get("price") == row.get("agreed_price") and receipt.get("task") == row.get("task"))


def select(shops, memory, catalog, budget, *, policy=None, portfolio=None, kind=None, public_rows=None, now=None):
    now = time.time() if now is None else now
    candidates = []
    floor_settings = load_floor()
    for shop in shops:
        local = [r for r in memory if r.get("shop_name") == shop["name"]]
        errors = 0
        for row in local[-5:]:
            try:
                age = now-datetime.fromisoformat(row["timestamp"]).timestamp()
            except (KeyError, ValueError, TypeError):
                continue
            if row.get("outcome") == "transport_error" and 0 <= age <= 3600:
                errors += 1
        risk = {"blocked_price_mismatch": any(r.get("outcome") == "receipt_mismatch" and not wording_substitution(r) for r in local),
                "recent_transport_errors": errors}
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
            cap_without_floor = cap
            if policy:
                # No grade is known before a quote, so this is the best case; buyer_decision lowers it once the quote is read.
                cap = min(cap, ceiling(value, least_required(kind, protected, floor_settings, policy.get("grades"))))
            if cap < 1:
                continue
            history = [r for r in memory if r.get("verification") == "market_settled"
                       and r.get("shop_name") == shop["name"] and r.get("item") == sku
                       and r.get("outcome") == "purchased" and positive(r.get("price")) and not r.get("reference_relief")]
            # Past prices are references, never current quotes. Unknown pairs use the full cap.
            # A deal that went above the recent-price cap is left out, or it would lift the next cap.
            reference = history[-1]["price"] if history else None
            reference_source = "own_r3_settlement" if reference is not None else "unknown"
            public = [r for r in (public_rows or []) if r.get("round") == 3 and r.get("kind") == "settle"
                      and 0 <= now-r.get("ts", 0) <= 3600 and (r.get("data") or {}).get("shop") == shop["url"]
                      and r["data"].get("item") == sku and settlement_price(r["data"]) is not None]
            if reference is None and public:
                reference = settlement_price(max(public, key=lambda r: r["ts"])["data"])
                reference_source = "public_r3_settlement"
            estimated_price = reference if reference is not None else cap
            feasible = estimated_price <= cap
            surplus = value - estimated_price
            evidence = {"shop": shop["name"], "sku": sku, "catalogue_value": value, "listed_qty": offer.get("qty"),
                        "negotiation_cap": cap, "cap_without_floor": cap_without_floor, "past_settled_price": reference,
                        "reference_within_cap": feasible, "estimated_surplus": surplus,
                        "warranty_required": policy["preferred_warranty_hours"] if protected else 0,
                        "attributed_faults": faults,
                        "reference_source": reference_source,
                        "estimate_only": True, "risk": risk}
            rank = (risk["recent_transport_errors"] >= 2,
                    faults > 0, not feasible,
                    -surplus, reference is None, shop["name"], sku)
            candidates.append((rank, shop, sku, cap, evidence))
    evidence = [row[4] for row in candidates]
    if not candidates:
        return None, None, budget, "No eligible SKU with a valid catalogue value.", evidence
    chosen = min(candidates, key=lambda row: row[0])
    _, shop, sku, cap, detail = chosen
    reason = (f"{shop['name']}/{sku}: R3 risk then surplus reference; "
              f"value={detail['catalogue_value']}, cap={cap}, "
              f"past settled price={detail['past_settled_price']}; no current quote or breakage forecast.")
    return shop, sku, cap, reason, evidence
