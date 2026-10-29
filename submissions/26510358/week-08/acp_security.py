"""Fail-closed checks before the official kit creates payment mandates.

These pure helpers do not verify signatures or settle orders. The caller must
verify checkout_jwt with the registered merchant key before passing its decoded
payload here. The Market remains responsible for the complete AP2 chain and
one-settlement-per-order enforcement.
"""
from collections.abc import Mapping
import ipaddress
import math
import re
from urllib.parse import unquote, urlsplit, urlunsplit


class AcpValidationError(ValueError):
    """Only a fixed diagnostic code; peer data and mandates never enter errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _reject(code):
    raise AcpValidationError(code)


def _url(value, *, allow_local_http=False):
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        _reject("invalid_url")
    if any(c in value for c in ("\\", "?", "#")):
        _reject("unsafe_url_components")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except (ValueError, UnicodeError):
        _reject("invalid_url")
    if parsed.scheme not in {"https", "http"} or not host or parsed.username is not None or parsed.password is not None:
        _reject("unsafe_url_origin")
    if parsed.netloc.endswith(":") or host.endswith(".") or "%" in host:
        _reject("ambiguous_url_origin")
    try:
        address = ipaddress.ip_address(host)
        normalized_host = address.compressed
        local = address.is_loopback
    except ValueError:
        try:
            normalized_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            _reject("invalid_url_host")
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in normalized_host.split(".")):
            _reject("invalid_url_host")
        local = normalized_host == "localhost"
    if parsed.scheme == "http" and not (allow_local_http and local):
        _reject("https_required")
    effective_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    if not 1 <= effective_port <= 65535:
        _reject("invalid_url_port")
    decoded_path = unquote(parsed.path)
    if ("\\" in decoded_path or any(ord(c) < 32 or ord(c) == 127 for c in decoded_path)
            or any(segment in {".", ".."} for segment in decoded_path.split("/"))
            or "//" in decoded_path or "%" in decoded_path
            or decoded_path.count("/") != parsed.path.count("/")):
        _reject("unsafe_url_path")
    authority = "[" + normalized_host + "]" if ":" in normalized_host else normalized_host
    default_port = 443 if parsed.scheme == "https" else 80
    if effective_port != default_port:
        authority += ":" + str(effective_port)
    path = parsed.path.rstrip("/")
    normalized = urlunsplit((parsed.scheme, authority, path, "", ""))
    return normalized, (parsed.scheme, normalized_host, effective_port)


def validate_endpoint(shop_url, endpoint, *, allow_local_http=False):
    """Return a normalized ACP base only when its origin matches the shop.

    Plain HTTP requires an explicit caller opt-in and a literal loopback IP or
    localhost. The caller should enable that only for a local market/demo.
    Redirect following must remain disabled in the HTTP client.
    """
    _, trusted_origin = _url(shop_url, allow_local_http=allow_local_http)
    normalized, endpoint_origin = _url(endpoint, allow_local_http=allow_local_http)
    if endpoint_origin != trusted_origin:
        _reject("endpoint_origin_mismatch")
    return normalized


def _whole(value, code, *, allow_zero=False):
    if type(value) is int:
        result = value
    elif type(value) is float and math.isfinite(value) and value.is_integer() and abs(value) <= 2**53 - 1:
        result = int(value)
    else:
        _reject(code)
    if result < (0 if allow_zero else 1):
        _reject(code)
    return result


def _identifier(value, code):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _reject(code)
    return value


def validate_checkout(order, shop_url, receipt, session, checkout_payload, *, allow_local_http=False):
    """Return the checked integer total before closing mandates/completing.

    checkout_payload must be the result of the official signature verifier, not
    an unverified JWT decode. This checks the course kit's single-unit checkout
    shape; extra line items, totals and unsupported session states fail closed.
    No private order/mandate contents are copied into exception messages.
    """
    if not all(isinstance(value, Mapping) for value in (order, receipt, session, checkout_payload)):
        _reject("invalid_checkout_shape")
    trusted_shop, _ = _url(shop_url, allow_local_http=allow_local_http)
    payload_shop, _ = _url(checkout_payload.get("shop_url"), allow_local_http=allow_local_http)
    if payload_shop != trusted_shop:
        _reject("checkout_shop_url_mismatch")
    shop = _identifier(receipt.get("shop"), "invalid_receipt_shop")
    item = _identifier(receipt.get("item"), "invalid_receipt_item")
    task = _identifier(receipt.get("task"), "invalid_receipt_task")
    price = _whole(receipt.get("price"), "invalid_receipt_price")
    if order.get("item") != item:
        _reject("order_item_mismatch")
    if checkout_payload.get("shop") != shop:
        _reject("checkout_shop_mismatch")
    if checkout_payload.get("item") != item or checkout_payload.get("task") != task:
        _reject("checkout_contract_mismatch")
    if _whole(checkout_payload.get("price"), "invalid_checkout_price") != price:
        _reject("checkout_price_mismatch")
    if checkout_payload.get("currency") != "credits":
        _reject("checkout_currency_mismatch")
    session_id = session.get("id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        _reject("invalid_session_id")
    if checkout_payload.get("id") != session_id:
        _reject("checkout_session_mismatch")
    if session.get("task") != task:
        _reject("session_task_mismatch")
    if session.get("status") != "ready_for_payment":
        _reject("session_not_ready")
    items = session.get("line_items")
    if (not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping)
            or items[0].get("id") != item or type(items[0].get("quantity")) is not int
            or items[0]["quantity"] != 1):
        _reject("session_line_items_mismatch")
    totals = session.get("totals")
    if (not isinstance(totals, list) or len(totals) != 1 or not isinstance(totals[0], Mapping)
            or totals[0].get("type") != "total"):
        _reject("invalid_session_totals")
    if _whole(totals[0].get("amount"), "invalid_session_total") != price:
        _reject("session_total_mismatch")
    capabilities = session.get("capabilities")
    methods = capabilities.get("payment_methods") if isinstance(capabilities, Mapping) else None
    if not isinstance(methods, list) or "ap2-mandate" not in methods:
        _reject("unsupported_payment_method")
    budget = _whole(order.get("budget"), "invalid_order_budget")
    if price > budget:
        _reject("order_budget_exceeded")
    mandates = order.get("mandates")
    payment = mandates.get("payment") if isinstance(mandates, Mapping) else None
    mandate = payment.get("mandate") if isinstance(payment, Mapping) else None
    constraints = mandate.get("constraints") if isinstance(mandate, Mapping) else None
    if not isinstance(constraints, list):
        _reject("invalid_payment_constraints")
    ranges = [constraint for constraint in constraints
              if isinstance(constraint, Mapping) and constraint.get("type") == "payment.amount_range"]
    if len(ranges) != 1:
        _reject("invalid_payment_amount_range")
    maximum = _whole(ranges[0].get("max"), "invalid_payment_amount_max")
    minimum = _whole(ranges[0].get("min", 0), "invalid_payment_amount_min", allow_zero=True)
    if minimum > maximum or not minimum <= price <= maximum:
        _reject("payment_amount_out_of_range")
    if ranges[0].get("currency", "credits") != "credits":
        _reject("payment_currency_mismatch")
    return price
