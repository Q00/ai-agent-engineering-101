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


def certified_choice(task, *, item, shop_name, order, policy, market_dir, seconds_left):
    """Return no decision on missing/conflicting evidence; checkout remains authoritative."""
    if (policy.get("fast_quote_accept_enabled") is not True or order.get("round") != 3
            or item not in (order.get("acceptable") or []) or not shop_name
            or task.get("artifacts") or seconds_left <= 0):
        return None
    message = task.get("status", {}).get("message") or {}
    parts = message.get("parts", [])
    if not isinstance(parts, list) or len(parts) > 32 or any(not isinstance(p, dict) for p in parts):
        return None
    data = [p["data"] for p in parts if "data" in p]
    if len(data) != 1 or not isinstance(data[0], dict):
        return None
    quote = data[0]
    ask = quote.get("ask")
    if (quote.get("sku") != item or not isinstance(quote.get("unit"), str)
            or not quote["unit"] or not number(ask) or not float(ask).is_integer()
            or not 0 < ask <= min(policy["target_price"], policy["price_limit"])):
        return None
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    for key in ("ask", "sku", "unit"):
        if key in metadata and metadata[key] != quote.get(key):
            return None
    # A labelled catalog price is not an ask. Any other conflicting price disables the shortcut.
    text = " ".join(p["text"] for p in parts if isinstance(p.get("text"), str))
    text = re.sub(r"(?:catalog\s+)?list[ _]price\s*[:=]?\s*[₩$]?\s*[0-9][0-9,.]*", "", text, flags=re.I)
    prices = re.findall(r"\b(?:asking(?: price)?|counteroffer|final price|ask|price)\s*(?:is\s*)?[:=]?\s*[₩$]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text, re.I)
    if not prices or any(float(p.replace(",", "")) != ask for p in prices):
        return None
    cert = quote.get("cert")
    try:
        body = cert["cert"]
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(order["market_pubkey"], validate=True)).verify(
            base64.b64decode(cert["sig"], validate=True), raw)
        if (body.get("sku") != item or body.get("unit") != quote["unit"]
                or body.get("shop") != shop_name or body.get("kind") != order.get("kind")):
            return None
    except (InvalidSignature, KeyError, ValueError, TypeError, AttributeError):
        return None
    accept = policy.get("accept", {})
    if order.get("kind") == "goods":
        grade = body.get("grade")
        advertised = quote.get("grade", quote.get("grade_now"))
        hours = quote.get("warranty_hours")
        if (grade not in accept.get("grades", []) or advertised != grade
                or not number(hours) or not accept.get("warranty_hours", 0) <= hours <= 72):
            return None
    elif order.get("kind") == "food":
        if market_dir is None or set(accept.get("grades", [])) != {"A", "B", "C"}:
            return None
        clock = schedule_now(market_dir, order["round"])
        expiry = body.get("expires_at")
        if (clock.get("status") != "synced" or not number(clock.get("now"))
                or not number(expiry) or not number(accept.get("min_hours_left"))):
            return None
        # The kit also checks the host clock. Reserve the remaining negotiation time.
        now = max(time.time(), clock["now"])
        if expiry - now - seconds_left < accept["min_hours_left"] * 3600:
            return None
        if "expires_at" in quote and quote["expires_at"] != expiry:
            return None
    else:
        return None
    return {"action": "accept", "price": int(ask),
            "reason": "Current certified quote meets the target price and required terms; verify again at checkout."}
