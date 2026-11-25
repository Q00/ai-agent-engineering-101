"""Food selection hints and bounded cooldowns; no purchase authorization."""
import math
import time

from buyer_policy import checkout_policy
from payment_memory import record_time


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def prioritize_history(values, order, config):
    """For orders longer than 72h, rank suitable history ahead of unknown expiry.

    History is still only a lead: it never proves the next unit's remaining life.
    Short-use orders retain the existing discounted-food exploration ranking.
    """
    if order.get("kind") != "food" or order.get("round") != 3:
        return values
    required = checkout_policy(order, config)["min_hours_left"]
    if order["use_within_hours"] <= 72:
        return values
    result = {}
    for key, value in values.items():
        supported = any(g.get("kind") == "food" and g.get("source") != "public_food_discovery"
                        and finite(g.get("minimum_hours_left")) and g["minimum_hours_left"] >= required
                        for g in value.get("groups", []))
        result[key] = {**value, "food_horizon_supported": supported}
    return result


def filter_recent_short_life(shops, memory, order, config, *, now=None):
    """Avoid same stock/horizon for at most 15m after verified insufficient life.

    Only the latest completed no-deal with a locally verified certificate counts.
    Unknown/newer evidence, changed URL/quantity/horizon, and expired cooldowns
    permit retry. Same quantity cannot prove the same units: exclusion is bounded.
    """
    if order.get("kind") != "food" or order.get("round") != 3:
        return shops, 0
    now = time.time() if now is None else now
    seconds = min(900, config.get("failure_cooldown_seconds", 900))
    required = checkout_policy(order, config)["min_hours_left"]
    latest = {}
    for row in memory:
        stamp = record_time(row.get("time"))
        if row.get("round") != 3 or stamp is None or not now-seconds <= stamp <= now:
            continue
        key = (row.get("name"), row.get("item"))
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, row)
    ready, skipped = [], 0
    for shop in shops:
        offers = []
        for offer in shop.get("offers", []):
            row = latest.get((shop["name"], offer["sku"]), (None, {}))[1]
            proof = row.get("food_lifetime_observation")
            proof = proof if isinstance(proof, dict) else {}
            diag = row.get("negotiation_diagnostics") or {}
            blocked = (row.get("result") == "no_deal" and diag.get("cancel_confirmed") is True
                       and row.get("observed_receipt") is None
                       and row.get("url") == shop.get("url")
                       and type(row.get("observed_stock_qty")) is int
                       and row["observed_stock_qty"] == offer.get("qty")
                       and proof.get("source") == "verified_market_certificate"
                       and isinstance(proof.get("unit"), str) and bool(proof["unit"])
                       and proof.get("insufficient") is True and proof.get("required_hours") == required
                       and all(finite(proof.get(k)) for k in ("observed_at", "expires_at", "checked_market_time"))
                       and now-seconds <= proof["observed_at"] <= now
                       and proof["expires_at"] - proof["checked_market_time"] < required*3600)
            if blocked:
                skipped += 1
            else:
                offers.append(offer)
        if offers:
            ready.append({**shop, "offers": offers})
    return ready, skipped
