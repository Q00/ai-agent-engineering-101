"""Optional early acceptance of a current, certified quote at the target price."""
import base64
import json
import math
import re
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from market_clock import schedule_now


def number(n):
    return type(n) in (int, float) and math.isfinite(n)


def verified_body(quote, item, shop_name, order):
    if quote.get("sku") != item or not isinstance(quote.get("unit"), str) or not quote["unit"]:
        return None
    cert = quote.get("cert")
    try:
        body = cert["cert"]
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(order["market_pubkey"], validate=True)).verify(
            base64.b64decode(cert["sig"], validate=True), raw)
        if (body.get("sku") == item and body.get("unit") == quote.get("unit")
                and body.get("shop") == shop_name and body.get("kind") == order.get("kind")):
            return body
    except (InvalidSignature, KeyError, ValueError, TypeError, AttributeError):
        pass
    return None


def certified_choice(task, *, item, shop_name, order, policy, market_dir, seconds_left, observation=None):
    """Return no decision on missing/conflicting evidence; checkout remains authoritative."""
    observation = observation if observation is not None else {}
    observation.clear()
    def skip(reason):
        observation["reason"] = reason
        return None
    if (policy.get("fast_quote_accept_enabled") is not True or order.get("round") != 3
            or item not in (order.get("acceptable") or []) or not shop_name
            or task.get("artifacts") or seconds_left <= 0):
        return skip("not_applicable")
    message = task.get("status", {}).get("message") or {}
    parts = message.get("parts", [])
    if not isinstance(parts, list) or len(parts) > 32 or any(not isinstance(p, dict) for p in parts):
        return skip("invalid_quote_parts")
    data = [p["data"] for p in parts if "data" in p]
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return skip("invalid_quote_metadata")
    if len(data) > 1 or any(not isinstance(d, dict) for d in data) or not (data or metadata):
        return skip("structured_quote_missing_or_ambiguous")
    # Only merge fields from THIS message. Never reuse an earlier unit/quote.
    quote = dict(data[0]) if data else {}
    for key in ("ask", "sku", "unit", "cert", "grade", "grade_now", "warranty_hours", "expires_at"):
        if key in metadata:
            if key in quote and quote[key] != metadata[key]:
                return skip("quote_metadata_conflict")
            quote[key] = metadata[key]
    # The signed certificate can supply identity; verified_body below verifies
    # its signature and binds it to the requested SKU, shop and current unit.
    envelope = quote.get("cert")
    identity = envelope.get("cert") if isinstance(envelope, dict) else None
    if isinstance(identity, dict):
        for key in ("sku", "unit"):
            quote.setdefault(key, identity.get(key))
    if "grade" in quote and "grade_now" in quote and quote["grade"] != quote["grade_now"]:
        return skip("grade_not_accepted_or_conflicting")
    observation["missing_fields"] = [k for k in ("ask", "sku", "unit", "cert") if quote.get(k) is None]
    if order.get("kind") == "goods" and quote.get("warranty_hours") is None:
        observation["missing_fields"].append("warranty_hours")
    # Observe certified lifetime independently of the quote's price or formatting.
    body = verified_body(quote, item, shop_name, order)
    food_clock = None
    if body and order.get("kind") == "food" and market_dir is not None:
        food_clock = schedule_now(market_dir, order["round"])
        expiry = body.get("expires_at")
        required = policy.get("accept", {}).get("min_hours_left")
        if (food_clock.get("status") == "synced" and number(food_clock.get("now"))
                and number(expiry) and number(required)):
            observation["food_lifetime"] = {
                "source": "verified_market_certificate", "unit": body["unit"],
                "expires_at": expiry, "checked_market_time": food_clock["now"],
                "observed_at": time.time(), "required_hours": required,
                "insufficient": expiry - food_clock["now"] < required * 3600}
    ask = quote.get("ask")
    if (quote.get("sku") != item or not isinstance(quote.get("unit"), str)
            or not quote["unit"] or not number(ask) or not float(ask).is_integer() or ask <= 0):
        return skip("invalid_quote_identity_or_price")
    if ask > policy["price_limit"]:
        return skip("price_above_limit")
    if ask > policy["target_price"]:
        return skip("price_above_target")
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return skip("invalid_quote_metadata")
    for key in ("ask", "sku", "unit"):
        if key in metadata and metadata[key] != quote.get(key):
            return skip("quote_metadata_conflict")
    # A labelled catalog price is not an ask. Any other conflicting price disables the shortcut.
    text = " ".join(p["text"] for p in parts if isinstance(p.get("text"), str))
    text = re.sub(r"(?:catalog\s+)?list[ _]price\s*[:=]?\s*[₩$]?\s*[0-9][0-9,.]*", "", text, flags=re.I)
    prices = re.findall(r"\b(?:asking(?: price)?|counteroffer|final price|ask|price)\s*(?:is\s*)?[:=]?\s*[₩$]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text, re.I)
    if not prices or any(float(p.replace(",", "")) != ask for p in prices):
        return skip("text_price_missing_or_conflicting")
    if body is None:
        return skip("certificate_missing_or_invalid")
    accept = policy.get("accept", {})
    if order.get("kind") == "goods":
        grade = body.get("grade")
        advertised = quote.get("grade", quote.get("grade_now"))
        hours = quote.get("warranty_hours")
        if grade not in accept.get("grades", []) or advertised != grade:
            return skip("grade_not_accepted_or_conflicting")
        if not number(hours) or not accept.get("warranty_hours", 0) <= hours <= 72:
            return skip("warranty_missing_or_insufficient")
    elif order.get("kind") == "food":
        if market_dir is None or set(accept.get("grades", [])) != {"A", "B", "C"}:
            return skip("food_policy_not_supported")
        clock = food_clock or schedule_now(market_dir, order["round"])
        expiry = body.get("expires_at")
        if (clock.get("status") != "synced" or not number(clock.get("now"))
                or not number(expiry) or not number(accept.get("min_hours_left"))):
            return skip("food_clock_or_expiry_unavailable")
        # The kit also checks the host clock. Reserve the remaining negotiation time.
        now = max(time.time(), clock["now"])
        if expiry - now - seconds_left < accept["min_hours_left"] * 3600:
            return skip("food_lifetime_or_checkout_headroom_insufficient")
        if "expires_at" in quote and quote["expires_at"] != expiry:
            return skip("food_expiry_conflict")
    else:
        return skip("unsupported_kind")
    observation["reason"] = "accepted"
    return {"action": "accept", "price": int(ask),
            "reason": "Current certified quote meets the target price and required terms; verify again at checkout."}
