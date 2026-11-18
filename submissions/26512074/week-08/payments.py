"""Discover ACP tools from the card; guard every payment before complete."""
import base64
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx
from jsonschema import validate

from policy import event, load_policy
from signing import whole
from vendor import market_kit_v3 as kit

CREATE_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["line_items", "receipt"], "properties": {
        "line_items": {"type": "array", "minItems": 1, "maxItems": 1, "items": {
            "type": "object", "required": ["id", "quantity"], "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "quantity": {"const": 1}}}},
        "receipt": {"type": "object"}}}
PAY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["payment_data"],
    "properties": {"payment_data": {"type": "object", "additionalProperties": False,
        "required": ["order", "checkout_mandate", "payment_mandate"], "properties": {
            "order": {"type": "string"}, "checkout_mandate": {"type": "object"},
            "payment_mandate": {"type": "object"}}}}}
EMPTY_SCHEMA = {"type": "object", "additionalProperties": False}
CONTRACTS = {
    "create_checkout_session": ("POST", "/checkout_sessions", CREATE_SCHEMA),
    "get_checkout_session": ("GET", "/checkout_sessions/{id}", EMPTY_SCHEMA),
    "complete_checkout_session": ("POST", "/checkout_sessions/{id}/complete", PAY_SCHEMA),
    "cancel_checkout_session": ("POST", "/checkout_sessions/{id}/cancel", EMPTY_SCHEMA)}


def origin(url):
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.fragment:
        raise ValueError("invalid ACP URL")
    if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("non-local payments require HTTPS")
    return u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80)


class DiscoveredTools:
    def __init__(self, card, shop_url, client):
        if card.get("protocol") != "acp" or origin(card["endpoint"]) != origin(shop_url):
            raise ValueError("ACP endpoint must have the registered shop's origin")
        shop_path = urlparse(shop_url).path.rstrip("/")
        endpoint = urlparse(card["endpoint"])
        if endpoint.query or ".." in endpoint.path.split("/") or (shop_path and not endpoint.path.startswith(shop_path + "/")):
            raise ValueError("ACP endpoint escaped the registered merchant's namespace")
        caps = card.get("capabilities", {})
        if "checkout" not in caps.get("services", []) or "ap2-mandate" not in caps.get("payment_methods", []):
            raise ValueError("ACP/AP2 checkout capability missing")
        self.base, self.client, self.tools = card["endpoint"].rstrip("/"), client, {}
        for tool in card.get("tools", []):
            name = tool.get("name")
            if name not in CONTRACTS:
                continue
            method, path, schema = CONTRACTS[name]
            if tool.get("method") != method or tool.get("path") != path or name in self.tools:
                raise ValueError("untrusted descriptor changed or duplicated a payment operation")
            self.tools[name] = {"method": method, "path": path,
                                "schema": tool.get("inputSchema", schema)}
        for required in load_policy()["checkout_tools"]:
            if required not in self.tools:
                raise ValueError(f"descriptor lacks required tool {required}")

    def call(self, name, arguments, session_id=None):
        tool = self.tools[name]
        # Check our authority contract even if a hostile card advertises a weaker schema.
        validate(arguments, CONTRACTS[name][2])
        validate(arguments, tool["schema"])
        path = tool["path"]
        if "{id}" in path:
            if not session_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in session_id):
                raise ValueError("invalid checkout session identity")
            path = path.replace("{id}", session_id)
        response = self.client.request(tool["method"], self.base + path,
                                       **({"json": arguments} if tool["method"] != "GET" else {}))
        response.raise_for_status()
        event("acp_tool", name=name, session=session_id, status=response.status_code)
        return response.json()


class SchemaAcpShop(kit.AcpShop):
    def card(self, public):
        card = super().card(public)
        for tool in card["tools"]:
            tool["inputSchema"] = CONTRACTS[tool["name"]][2]
        return card

    def create(self, request, public):
        try:
            validate(request, CREATE_SCHEMA)
        except Exception:
            from starlette.responses import JSONResponse
            return JSONResponse({"error": "one item, quantity one, signed receipt required"}, status_code=400)
        return super().create(request, public)


def payment_limit(order):
    payment = order["mandates"]["payment"]["mandate"]
    ranges = [c for c in payment["constraints"] if c.get("type") == "payment.amount_range"]
    if len(ranges) != 1:
        raise ValueError("one unambiguous payment amount range is required")
    lower = whole(ranges[0].get("min", 0))
    upper = min(whole(ranges[0]["max"]), whole(order["budget"]))
    if lower > upper:
        raise ValueError("invalid payment range")
    if order.get("expires") is not None and float(order["expires"]) <= time.time():
        raise ValueError("order expired")
    return lower, upper


def verify_checkout(session, order, receipt, shop_url, shop_pub):
    co = kit.jwt_payload(session.get("checkout_jwt", ""), shop_pub)
    if not isinstance(co, dict):
        raise ValueError("checkout JWT is not signed by the registered shop")
    if (co.get("item"), whole(co.get("price")), co.get("task")) != (order["item"], whole(receipt["price"]), receipt["task"]):
        raise ValueError("checkout item, price or task differs from the authorized receipt")
    if co.get("shop") != receipt["shop"] or co.get("shop_url", "").rstrip("/") != shop_url.rstrip("/"):
        raise ValueError("checkout payee changed")
    if co.get("currency") != "credits" or co.get("id") != session.get("id"):
        raise ValueError("checkout identity/currency mismatch")
    totals = [x for x in session.get("totals", []) if x.get("type") == "total"]
    if len(totals) != 1 or whole(totals[0]["amount"]) != whole(co["price"]):
        raise ValueError("displayed total differs from the signed checkout")
    if session.get("line_items") != [{"id": order["item"], "quantity": 1}]:
        raise ValueError("checkout cart changed")
    low, high = payment_limit(order)
    if not low <= whole(co["price"]) <= high:
        raise ValueError("checkout exceeds the open mandate or budget")
    return co


def pay(order, shop_url, shop_pub, receipt, buyer_key, *, client=None, journal_dir=None):
    """Only the kit invokes this after A2A. Never retry an ambiguous complete."""
    directory = Path(journal_dir or ".market/payments")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(order["order"].encode()).hexdigest() + ".json")
    fingerprint = hashlib.sha256(kit.canon({"shop": shop_url, "receipt": receipt,
        "item": order["item"], "order": order["order"]})).hexdigest()
    if path.exists():
        prior = json.loads(path.read_text(encoding="utf-8"))
        event("payment_replay_blocked", order=order["order"], phase=prior["phase"])
        return prior.get("mandate_receipt") if prior["phase"] == "paid" and prior.get("fingerprint") == fingerprint else None
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"phase": "preparing", "fingerprint": fingerprint}, output)
    except FileExistsError:
        return None
    state = {"order": order["order"], "phase": "preparing", "fingerprint": fingerprint}
    def save(**fields):
        state.update(fields)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(path)
    owned = client is None
    client = client or httpx.Client(timeout=45, trust_env=False, follow_redirects=False)
    registry, session = None, None
    try:
        origin(shop_url)
        payment_limit(order)
        normalized = {**receipt, "price": whole(receipt["price"])}
        if normalized["item"] != order["item"] or not kit.verify_seller(shop_pub, normalized):
            raise ValueError("invalid seller receipt before checkout")
        response = client.get(shop_url.rstrip("/") + "/.well-known/acp.json")
        response.raise_for_status()
        registry = DiscoveredTools(response.json(), shop_url, client)
        session = registry.call("create_checkout_session", {
            "line_items": [{"id": order["item"], "quantity": 1}], "receipt": normalized})
        save(phase="created", session=session["id"])
        session = registry.call("get_checkout_session", {}, session["id"])
        if session.get("status") != "ready_for_payment":
            raise ValueError("checkout is not ready for payment")
        co = verify_checkout(session, order, normalized, shop_url, shop_pub)
        token = session["checkout_jwt"]
        hashed = kit.b64url(hashlib.sha256(token.encode()).digest())
        opens = {k: hashlib.sha256(kit.canon(v["mandate"])).hexdigest() for k, v in order["mandates"].items()}
        cm = {"vct": "mandate.checkout.1", "order": order["order"], "open_mandate": opens["checkout"],
              "checkout_jwt": token, "checkout_hash": hashed}
        pm = {"vct": "mandate.payment.1", "order": order["order"], "open_mandate": opens["payment"],
              "payee": shop_url, "amount": co["price"], "currency": "credits", "checkout_hash": hashed}
        sign = lambda m: {"mandate": m, "sig": base64.b64encode(buyer_key.sign(kit.canon(m))).decode()}
        save(phase="sent", checkout_hash=hashed)  # Persist before the payment-capable network call.
        completed = registry.call("complete_checkout_session", {"payment_data": {
            "order": order["order"], "checkout_mandate": sign(cm), "payment_mandate": sign(pm)}}, session["id"])
        mr = completed["order"]["mandate_receipt"]
        if not isinstance(mr, dict) or "ledger" not in mr:
            raise ValueError("settlement receipt missing")
        save(phase="paid", mandate_receipt=mr)
        kit.log(f"[acp] paid {co['price']} through {registry.base}, settled at ledger #{mr['ledger']}")
        event("payment_settled", order=order["order"], amount=co["price"], ledger=mr["ledger"])
        return mr
    except Exception as exc:
        phase = "unknown" if state["phase"] == "sent" else "refused"
        save(phase=phase, error=f"{type(exc).__name__}: {exc}")
        if phase == "refused" and registry and session and "cancel_checkout_session" in registry.tools:
            try:
                registry.call("cancel_checkout_session", {}, session["id"])
            except Exception:
                pass
        kit.log(f"[acp] payment {phase}: {type(exc).__name__}: {exc}")
        event("payment_refused", order=order["order"], phase=phase, reason=str(exc))
        return None
    finally:
        if owned:
            client.close()
