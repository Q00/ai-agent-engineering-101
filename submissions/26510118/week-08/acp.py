"""Round 3 payments: the shop's ACP checkout and the buyer's AP2 mandates.

The kit serves both for students who run it. This shop and buyer run on
Cloudflare without the kit, so this module does what the kit's AcpShop and
acp_pay do (scripts/market_kit.py, KIT_VERSION 3), in the same bytes: the
market verifies the same signatures whichever side made them. No transport
here -- the Worker brings fetch, the PC tests bring httpx -- so the same code
is checked on the PC and run on Cloudflare, like shop_core and buyer_core.

    shop    /.well-known/acp.json says "checkout, paid with ap2-mandate". A
            buyer brings the receipt this shop signed over A2A; the checkout
            checks that signature, the item and the reserve, and signs a
            checkout_jwt with the shop key. Completing it forwards the buyer's
            closed mandates to the market's /api/settle, which moves credits.
    buyer   a Round 3 order carries two open mandates the market signed; the
            payment one holds the budget as payment.amount_range max. After the
            deal the buyer opens the shop's checkout, checks the checkout_jwt
            against the receipt and that limit (in code: above it, no payment),
            signs the closed checkout and payment mandates, and completes.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import json
import os
import time
from urllib.parse import urlparse

RECEIPT_KEYS = ("shop", "item", "price", "task")
VERSION = "2026-04-17"                                  # the ACP spec version the market's kit speaks
KEEP_SESSIONS = 500
KEEP_SECONDS = 3600                                     # a checkout_jwt older than this is not restored


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def pub_b64(key) -> str:
    from cryptography.hazmat.primitives import serialization
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)).decode()


def jwt_sign(key, payload: dict) -> str:
    head = b64url(canon({"alg": "EdDSA", "typ": "JWT"})) + "." + b64url(canon(payload))
    return head + "." + b64url(key.sign(head.encode()))


def jwt_payload(token: str, pubkey_b64: str) -> dict | None:
    """The payload when the shop's key signed it, else None."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        head, body, sig = token.split(".")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(unb64url(sig), f"{head}.{body}".encode())
        return json.loads(unb64url(body))
    except (InvalidSignature, ValueError, TypeError, AttributeError):
        return None


def verify_receipt(pubkey_b64: str, receipt: dict) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(
            base64.b64decode(receipt["sig_seller"]), canon({k: receipt.get(k) for k in RECEIPT_KEYS}))
        return True
    except Exception:                                   # noqa: BLE001  (any malformed receipt is unsigned)
        return False


def signed_join(key, name: str, role: str, url: str | None, owner: str | None) -> dict:
    """The /api/join body the kit sends: the signature over a fresh timestamp
    proves the key, so only its holder can join again under this name."""
    ts = int(time.time())
    body = {"name": name, "role": role, "pubkey": pub_b64(key), "url": url, "ts": ts,
            "sig": base64.b64encode(key.sign(canon({"name": name, "role": role, "url": url, "ts": ts}))).decode()}
    if owner:
        body["owner"] = owner
    return body


# ------------------------------------------------------------ the shop

def descriptor(public: str) -> dict:
    """/.well-known/acp.json: what this shop's checkout offers, and where."""
    tools = [("create_checkout_session", "POST", "/checkout_sessions",
              {"line_items": "[{id: item, quantity: 1}]", "receipt": "the receipt the shop signed over A2A"}),
             ("get_checkout_session", "GET", "/checkout_sessions/{id}", {}),
             ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
              {"payment_data": "{order, checkout_mandate, payment_mandate}"}),
             ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", {})]
    return {"protocol": "acp", "version": VERSION, "endpoint": f"{public.rstrip('/')}/acp",
            "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
            "tools": [{"name": n, "method": m, "path": p, "input": i} for n, m, p, i in tools]}


class Checkout:
    """The shop's checkout sessions. `settle(payload)` is the transport to the
    market's /api/settle: an async call returning (status, json). `reserve_of`
    is the catalogue's reserve, the same floor the negotiation keeps."""

    def __init__(self, key, reserve_of, settle, log=print):
        self.key, self.pub = key, pub_b64(key)
        self.reserve_of, self.settle, self.log = reserve_of, settle, log
        self.sessions: collections.OrderedDict[str, dict] = collections.OrderedDict()

    async def handle(self, method: str, path: str, body: str, public: str) -> tuple[int, dict]:
        """One ACP call. `path` is the request path below the host, e.g.
        /acp/checkout_sessions/cs_1/complete."""
        if path.rstrip("/") == "/.well-known/acp.json":
            return 200, descriptor(public)
        try:
            b = json.loads(body or "{}")
        except ValueError:
            return 400, {"error": "body is not JSON"}
        parts = path.strip("/").split("/")[1:]          # acp/checkout_sessions[/id[/complete|cancel]]
        if parts[:1] != ["checkout_sessions"]:
            return 404, {"error": "unknown ACP path"}
        if len(parts) == 1 and method == "POST":
            return self.create(b, public)
        s = self.sessions.get(parts[1]) if len(parts) > 1 else None
        if s is None and parts[2:] == ["complete"]:
            s = self.restore(parts[1], b)
        if s is None:
            return 404, {"error": "no such checkout session"}
        if len(parts) == 2:
            return 200, s
        if parts[2] == "cancel" and s["status"] != "completed":
            s["status"] = "canceled"
            return 200, s
        if parts[2] == "complete" and s["status"] == "ready_for_payment":
            return await self.complete(s, b)
        return 409, {"error": f"session is {s['status']}"}

    def restore(self, sid: str, b: dict) -> dict | None:
        """A session this shop no longer holds (the object restarted between
        create and complete), rebuilt from the checkout_jwt the buyer's closed
        checkout mandate carries: this shop's own signature vouches for it, so
        nothing needs storing. The market settles an order once, so a replay
        cannot pay twice; an old token is refused anyway."""
        cm = (((b.get("payment_data") or {}).get("checkout_mandate") or {}).get("mandate") or {})
        co = jwt_payload(cm.get("checkout_jwt") or "", self.pub)
        if not co or co.get("id") != sid or time.time() - co.get("iat", 0) > KEEP_SECONDS:
            return None
        self.sessions[sid] = s = {
            "id": sid, "status": "ready_for_payment", "task": co["task"],
            "line_items": [{"id": co["item"], "quantity": 1}],
            "totals": [{"type": "total", "amount": co["price"]}],
            "capabilities": {"payment_methods": ["ap2-mandate"]},
            "checkout_jwt": cm["checkout_jwt"]}
        self.log(f"[acp] session {sid} restored from its signed checkout_jwt")
        return s

    def create(self, b: dict, public: str) -> tuple[int, dict]:
        rec = b.get("receipt") if isinstance(b.get("receipt"), dict) else {}
        items = b.get("line_items") or [{}]
        if not (rec.get("sig_seller") and verify_receipt(self.pub, rec)):
            return 400, {"error": "the receipt is not signed by this shop"}
        if items[0].get("id") != rec.get("item") or type(rec.get("price")) is not int:
            return 400, {"error": "line_items do not match the receipt"}
        reserve = self.reserve_of(rec["item"])
        if reserve is None or rec["price"] < reserve:   # the receipt already keeps it; the checkout never sells below it
            return 400, {"error": "the price is below this shop's reserve"}
        if any(x.get("task") == rec["task"] for x in self.sessions.values()):
            return 409, {"error": "this receipt already has a checkout session"}
        sid = "cs_" + os.urandom(8).hex()
        co = {"id": sid, "shop": rec["shop"], "shop_url": public.rstrip("/"), "item": rec["item"],
              "price": rec["price"], "task": rec["task"], "currency": "credits", "iat": int(time.time())}
        self.sessions[sid] = s = {
            "id": sid, "status": "ready_for_payment", "task": rec["task"],
            "line_items": [{"id": rec["item"], "quantity": 1}],
            "totals": [{"type": "total", "amount": rec["price"]}],
            "capabilities": {"payment_methods": ["ap2-mandate"]},
            "checkout_jwt": jwt_sign(self.key, co)}
        while len(self.sessions) > KEEP_SESSIONS:
            self.sessions.popitem(last=False)
        return 201, s

    async def complete(self, s: dict, b: dict) -> tuple[int, dict]:
        pay = b.get("payment_data") or {}
        status, res = await self.settle({"order": pay.get("order"), "checkout_jwt": s["checkout_jwt"],
                                         "checkout_mandate": pay.get("checkout_mandate"),
                                         "payment_mandate": pay.get("payment_mandate")})
        item, amount = s["line_items"][0]["id"], s["totals"][0]["amount"]
        if status != 200 or not isinstance(res, dict) or "mandate_receipt" not in res:
            failed = [k for k, v in ((res or {}).get("checks") or {}).items() if not v] if isinstance(res, dict) else []
            self.log(f"[acp] settle refused: {failed or str(res)[:200]}")
            return 402, {"error": "payment refused by the market", "market": res,
                         "settle": {"order": pay.get("order"), "item": item, "amount": amount, "failed": failed}}
        s["status"] = "completed"
        s["order"] = {"status": "confirmed", "mandate_receipt": res["mandate_receipt"]}
        self.log(f"[acp] settled {item} at {amount}, ledger #{res.get('ledger')}")
        return 200, dict(s, settle={"order": pay.get("order"), "item": item, "amount": amount,
                                    "ledger": res.get("ledger")})


# ------------------------------------------------------------ the buyer

def mandate_limit(order: dict) -> int | None:
    """The budget as the market signed it: the open payment mandate's amount_range max."""
    try:
        return next(x["max"] for x in order["mandates"]["payment"]["mandate"]["constraints"]
                    if x["type"] == "payment.amount_range")
    except (KeyError, TypeError, StopIteration):
        return None


# Why a payment did not happen. The first four are the shop's doing; the rest
# are not clearly anyone's (a market refusal can also be this buyer's fault).
SHOP_FAULT = {"no_checkout": "the shop serves no ACP checkout",
              "foreign_endpoint": "its checkout is on another host",
              "checkout_refused": "the shop refused to open a checkout",
              "checkout_mismatch": "its checkout did not match the agreed receipt"}


async def pay(http, order: dict, shop_url: str, shop_pub: str, receipt: dict, buyer_key,
              log=print) -> tuple[dict | None, str | None]:
    """Open the shop's checkout, check it, sign the closed mandates, complete.
    -> (the market's mandate receipt, None), or (None, why) and the deal then
    cannot count: a SHOP_FAULT key, "over_limit", "not_completed" or "error".
    `http.get(url)` and `http.post(url, body)` return (status, json or None)."""
    try:
        status, card = await http.get(f"{shop_url}/.well-known/acp.json")
        if status != 200 or not isinstance(card, dict) or not card.get("endpoint"):
            log(f"[acp] {shop_url} serves no /.well-known/acp.json ({status}); this deal cannot settle")
            return None, "no_checkout"
        base = card["endpoint"].rstrip("/")
        if urlparse(base).hostname != urlparse(shop_url).hostname:
            log(f"[acp] the ACP endpoint {base} is not on the shop's host; not paying")
            return None, "foreign_endpoint"
        status, s = await http.post(f"{base}/checkout_sessions",
                                    {"line_items": [{"id": receipt["item"], "quantity": 1}], "receipt": receipt})
        if status not in (200, 201) or not isinstance(s, dict):
            log(f"[acp] checkout refused: {status} {str(s)[:200]}")
            return None, "checkout_refused"
        co = jwt_payload(s.get("checkout_jwt", ""), shop_pub)
        limit = mandate_limit(order)
        if not co or (co.get("item"), co.get("price"), co.get("task")) != (receipt["item"], receipt["price"], receipt["task"]):
            log("[acp] the checkout does not match the agreed receipt (or is not signed by the shop); not paying")
            return None, "checkout_mismatch"
        if limit is None or co["price"] > limit:        # the mandate's limit, in code: never paid above it
            log(f"[acp] checkout total {co['price']} is above the mandate's limit {limit}; not paying")
            return None, "over_limit"
        h = b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
        opens = {k: hashlib.sha256(canon(v["mandate"])).hexdigest() for k, v in order["mandates"].items()}
        cm = {"vct": "mandate.checkout.1", "order": order["order"], "open_mandate": opens["checkout"],
              "checkout_jwt": s["checkout_jwt"], "checkout_hash": h}
        pm = {"vct": "mandate.payment.1", "order": order["order"], "open_mandate": opens["payment"], "payee": shop_url,
              "amount": co["price"], "currency": "credits", "checkout_hash": h}
        sign = lambda m: {"mandate": m, "sig": base64.b64encode(buyer_key.sign(canon(m))).decode()}  # noqa: E731
        status, done = await http.post(f"{base}/checkout_sessions/{s['id']}/complete",
                                       {"payment_data": {"order": order["order"], "checkout_mandate": sign(cm),
                                                         "payment_mandate": sign(pm)}})
        if status != 200 or not isinstance(done, dict):
            log(f"[acp] payment not completed: {status} {str(done)[:300]}")
            return None, "not_completed"
        mr = done["order"]["mandate_receipt"]
        log(f"[acp] paid {co['price']} through {base}, settled at ledger #{mr.get('ledger')}")
        return mr, None
    except Exception as e:                              # noqa: BLE001  (a broken checkout costs the deal, not the order)
        log(f"[acp] payment failed: {type(e).__name__}: {e}"[:300])
        return None, "error"
