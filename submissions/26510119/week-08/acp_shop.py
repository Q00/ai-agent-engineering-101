"""Our own ACP checkout (Agentic Commerce Protocol 2026-04-17, the checkout subset), served by seller.py.

Baseline "shop deployed without the kit": the shop itself serves /.well-known/acp.json and the checkout session
endpoints, and settles through the market's /api/settle. With "acp": "self" on the seller in agent.json, our copy
of the kit (market_kit.py) stops serving the checkout, forwards these paths here and writes the shop's market token
to .market/shop_auth.json.

Flow, as in the kit's AcpShop so other teams' buyers need nothing new:
  POST /acp/checkout_sessions            the buyer brings the receipt our seller signed over A2A; we check the
                                         signature, pick the unit to ship from /api/inventory and sign a
                                         checkout_jwt with the shop key
  GET  /acp/checkout_sessions/{id}       the session
  POST /acp/checkout_sessions/{id}/complete   forwards the buyer's closed AP2 mandates to /api/settle
  POST /acp/checkout_sessions/{id}/cancel
The unit, claim and warranty come from the receipt our seller kept (remember()), never from the buyer's copy.
"""
import base64
import collections
import json
import os
import time
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from starlette.responses import JSONResponse
from starlette.routing import Route

HERE = Path(__file__).parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
AUTH = MARKET_DIR / "shop_auth.json"
SHOP_KEY = HERE / "shop_key.pem"
RECEIPT_KEYS = ("shop", "item", "price", "task")      # the fields under sig_seller


def log(msg):
    print(f"[acp-self] {msg}", flush=True)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Checkout:
    def __init__(self):
        self.key = serialization.load_pem_private_key(SHOP_KEY.read_bytes(), password=None)
        self.pub = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.sessions = collections.OrderedDict()
        self.terms = collections.OrderedDict()            # task -> the full receipt our seller signed

    def remember(self, receipt):
        """Called by the seller when it signs a receipt: what the checkout will ship."""
        self.terms[receipt["task"]] = dict(receipt)
        while len(self.terms) > 2000:
            self.terms.popitem(last=False)

    # ------------------------------------------------------------------ market access
    def auth(self):
        return json.loads(AUTH.read_text(encoding="utf-8"))   # re-read: the kit rewrites it when it joins again

    async def market(self, method, path, **kw):
        a = self.auth()
        async with httpx.AsyncClient(timeout=30) as c:
            return await c.request(method, a["market"] + path, headers={"Authorization": f"Bearer {a['token']}"}, **kw)

    # ------------------------------------------------------------------ helpers
    def jwt(self, payload):
        head = b64url(canon({"alg": "EdDSA", "typ": "JWT"})) + "." + b64url(canon(payload))
        return head + "." + b64url(self.key.sign(head.encode()))

    def signed_by_us(self, rec):
        try:
            body = {k: rec.get(k) for k in RECEIPT_KEYS}
            Ed25519PublicKey.from_public_bytes(self.pub).verify(base64.b64decode(rec["sig_seller"]), canon(body))
            return True
        except (InvalidSignature, KeyError, ValueError, TypeError):
            return False

    async def pick_unit(self, rec, acceptable):
        """The unit our seller promised if it is still in stock; else the fitting unit that expires first.
        Units held by an open checkout are skipped."""
        r = await self.market("GET", "/api/inventory")
        if r.status_code != 200:
            return None, f"inventory: {r.status_code}"
        held = {s["unit"] for s in self.sessions.values() if s.get("unit") and s["status"] == "ready_for_payment"}
        units = [u for u in r.json().get("units", []) if u["unit"] not in held and u["grade"] != "expired"]
        want = rec.get("item")
        fits = lambda u: (u["sku"] in acceptable if acceptable else
                          u["sku"] == want or (u.get("category") or "").split(">")[-1] == want)
        if rec.get("unit"):
            u = next((u for u in units if u["unit"] == rec["unit"]), None)
            return (u, None) if u and fits(u) else (None, f"unit {rec['unit']} is not in stock for this order")
        u = min((u for u in units if fits(u)), default=None,
                key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0))
        return (u, None) if u else (None, f"no {want} in stock")

    # ------------------------------------------------------------------ endpoints
    async def card(self, request):
        public = self.auth()["url"].rstrip("/")
        tools = [("create_checkout_session", "POST", "/checkout_sessions",
                  {"line_items": "[{id: item, quantity: 1}]", "receipt": "the receipt the shop signed over A2A",
                   "acceptable": "optional: SKUs the order accepts"}),
                 ("get_checkout_session", "GET", "/checkout_sessions/{id}", {}),
                 ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
                  {"payment_data": "{order, checkout_mandate, payment_mandate}"}),
                 ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", {})]
        return JSONResponse({"protocol": "acp", "version": "2026-04-17", "endpoint": f"{public}/acp",
                             "served_by": "the shop itself (seller.py + acp_shop.py), not the kit",
                             "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
                             "tools": [{"name": n, "method": m, "path": p, "input": i} for n, m, p, i in tools]})

    async def handle(self, request):
        parts = request.path_params["path"].strip("/").split("/")
        try:
            b = json.loads(await request.body() or b"{}")
        except ValueError:
            return JSONResponse({"error": "body is not JSON"}, status_code=400)
        if parts[:1] != ["checkout_sessions"]:
            return JSONResponse({"error": "unknown ACP path"}, status_code=404)
        if len(parts) == 1 and request.method == "POST":
            return await self.create(b)
        s = self.sessions.get(parts[1]) if len(parts) > 1 else None
        if s is None:
            return JSONResponse({"error": "no such checkout session"}, status_code=404)
        if len(parts) == 2:
            return JSONResponse(s)
        if parts[2] == "cancel" and s["status"] != "completed":
            s["status"] = "canceled"
            return JSONResponse(s)
        if parts[2] == "complete" and s["status"] == "ready_for_payment":
            return await self.complete(s, b.get("payment_data") or {})
        return JSONResponse({"error": f"session is {s['status']}"}, status_code=409)

    async def create(self, b):
        rec = b.get("receipt") if isinstance(b.get("receipt"), dict) else {}
        items = b.get("line_items") or [{}]
        acceptable = b.get("acceptable") if isinstance(b.get("acceptable"), list) else None
        if not (rec.get("sig_seller") and self.signed_by_us(rec)):
            return JSONResponse({"error": "the receipt is not signed by this shop"}, status_code=400)
        if items[0].get("id") != rec.get("item") or type(rec.get("price")) is not int:
            return JSONResponse({"error": "line_items do not match the receipt"}, status_code=400)
        if any(x.get("task") == rec["task"] for x in self.sessions.values()):
            return JSONResponse({"error": "this receipt already has a checkout session"}, status_code=409)
        own = self.terms.get(rec["task"]) or {}
        rec = dict({k: rec.get(k) for k in RECEIPT_KEYS + ("sig_seller",)},
                   **{k: own[k] for k in ("unit", "cert", "claim", "warranty_hours") if k in own})
        u, problem = await self.pick_unit(rec, acceptable)
        if problem:
            log(f"checkout refused: {problem}")
            return JSONResponse({"error": problem}, status_code=409)
        sid = "cs_" + os.urandom(8).hex()
        public = self.auth()["url"].rstrip("/")
        co = {"id": sid, "shop": rec["shop"], "shop_url": public, "item": u["sku"] if u else rec["item"],
              "price": rec["price"], "task": rec["task"], "currency": "credits", "iat": int(time.time())}
        if u:
            co["unit"] = u["unit"]
            if rec.get("cert") is not False:
                co["cert"] = u["cert"]
            if rec.get("claim") in ("A", "B", "C"):
                co["claim"] = rec["claim"]
            if type(rec.get("warranty_hours")) in (int, float) and rec["warranty_hours"] > 0:
                co["warranty_hours"] = rec["warranty_hours"]
        self.sessions[sid] = s = {
            "id": sid, "status": "ready_for_payment", "task": rec["task"], "unit": co.get("unit"),
            "line_items": [{"id": co["item"], "quantity": 1}],
            "totals": [{"type": "total", "amount": rec["price"]}],
            "capabilities": {"payment_methods": ["ap2-mandate"]},
            "fulfillment": {k: co[k] for k in ("unit", "cert", "claim", "warranty_hours") if k in co},
            "checkout_jwt": self.jwt(co)}
        while len(self.sessions) > 500:
            self.sessions.popitem(last=False)
        log(f"checkout {sid} for {co['item']} at {co['price']}" + (f", unit {co['unit']}" if co.get("unit") else ""))
        return JSONResponse(s, status_code=201)

    async def complete(self, s, pay):
        r = await self.market("POST", "/api/settle", json={
            "order": pay.get("order"), "checkout_jwt": s["checkout_jwt"],
            "checkout_mandate": pay.get("checkout_mandate"), "payment_mandate": pay.get("payment_mandate")})
        res = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:200]}
        if r.status_code != 200:
            log(f"settle refused: {[k for k, v in (res.get('checks') or {}).items() if not v] or res}")
            return JSONResponse({"error": "payment refused by the market", "market": res}, status_code=402)
        s["status"] = "completed"
        s["order"] = {"status": "confirmed", "mandate_receipt": res["mandate_receipt"]}
        mr = res["mandate_receipt"]
        log(f"settled {mr.get('name') or s['line_items'][0]['id']} at {s['totals'][0]['amount']} through our own "
            f"/api/settle call, ledger #{res['ledger']}" + (f", unit {mr['unit']} grade {mr.get('grade')}" if mr.get("unit") else ""))
        return JSONResponse(s)

    def routes(self):
        return [Route("/.well-known/acp.json", self.card, methods=["GET"]),
                Route("/acp/{path:path}", self.handle, methods=["GET", "POST"])]


CHECKOUT = Checkout() if SHOP_KEY.exists() else None
