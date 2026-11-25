"""The shop's own ACP checkout (Agentic Commerce Protocol 2026-04-17, the checkout subset) for Round 3.

On Render the kit only registers the shop (--deployed), so it cannot serve ACP for it. This module serves the
same checkout the kit serves for a tunneled shop, with the wire format of scripts/market_kit.py (AcpShop, v4):

    GET  /.well-known/acp.json                       the ACP card; endpoint <public>/acp
    POST /acp/checkout_sessions                      {line_items, receipt} -> session with a signed checkout_jwt
    GET  /acp/checkout_sessions/{id}
    POST /acp/checkout_sessions/{id}/cancel
    POST /acp/checkout_sessions/{id}/complete        {payment_data: {order, checkout_mandate, payment_mandate}}

A session is opened only for a receipt this shop signed over A2A, so its item and price are the agreed ones.
checkout_jwt is an EdDSA JWT over {id, shop, shop_url, item, price, task, currency, iat}, signed with the shop key
the market knows. In Round 3 it names the SKU shipped and adds unit, cert, claim and warranty_hours. Those are not
under sig_seller, so they come from what the seller recorded in `terms`, never from the buyer's copy. complete forwards the buyer's closed AP2 mandates to the market's /api/settle, which checks
the signatures and the limits and moves the credits. The shop never sees the buyer's budget.
"""
import asyncio
import base64
import collections
import json
import os
import time

import httpx
from cryptography.exceptions import InvalidSignature
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
VERSION = "2026-04-17"
MAX_SESSIONS = 500
MAX_TERMS = 2000
RECEIPT_KEYS = ("shop", "item", "price", "task")
TERM_KEYS = ("unit", "cert", "claim", "warranty_hours")


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt_sign(key, payload):
    head = b64url(canon({"alg": "EdDSA", "typ": "JWT"})) + "." + b64url(canon(payload))
    return head + "." + b64url(key.sign(head.encode()))


def signed_by(key, receipt):
    """True when sig_seller is this key's Ed25519 signature over canon({shop, item, price, task})."""
    try:
        body = {k: receipt.get(k) for k in RECEIPT_KEYS}
        key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canon(body))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


class AcpCheckout:
    """Checkout sessions in memory. load_key is called per request, like the seller does for receipts."""

    def __init__(self, public, load_key, log, market=MARKET, transport=None, stock=None, gate=None):
        self.public = public.rstrip("/")
        self.load_key, self.log = load_key, log
        self.market = market.rstrip("/")
        self.transport = transport            # tests pass an httpx.MockTransport in place of the market
        self.sessions = collections.OrderedDict()
        self.stock = stock                    # shop_stock.StockBook; None or before Round 3: no units
        self.terms = collections.OrderedDict()    # task -> the receipt the seller sent
        self.gate = gate                      # acp_gate.Gate: buyers refused at payment (pricing.json seller.refuse_buyers)

    def note_terms(self, receipt):
        self.terms[receipt["task"]] = receipt
        while len(self.terms) > MAX_TERMS:
            self.terms.popitem(last=False)

    def card(self):
        tools = [("create_checkout_session", "POST", "/checkout_sessions",
                  {"line_items": "[{id: item, quantity: 1}]", "receipt": "the receipt the shop signed over A2A"}),
                 ("get_checkout_session", "GET", "/checkout_sessions/{id}", {}),
                 ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
                  {"payment_data": "{order, checkout_mandate, payment_mandate}"}),
                 ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", {})]
        return {"protocol": "acp", "version": VERSION, "endpoint": f"{self.public}/acp",
                "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
                "tools": [{"name": n, "method": m, "path": p, "input": i} for n, m, p, i in tools]}

    def routes(self):
        return [Route("/.well-known/acp.json", self.acp_json, methods=["GET"]),
                Route("/acp/checkout_sessions", self.create, methods=["POST"]),
                Route("/acp/checkout_sessions/{sid}", self.get, methods=["GET"]),
                Route("/acp/checkout_sessions/{sid}/cancel", self.cancel, methods=["POST"]),
                Route("/acp/checkout_sessions/{sid}/complete", self.complete, methods=["POST"])]

    async def acp_json(self, req: Request):
        return JSONResponse(self.card())

    async def create(self, req: Request):
        b = await body_of(req)
        if b is None:
            return JSONResponse({"error": "body is not a JSON object"}, status_code=400)
        key = self.load_key()
        if key is None:
            return JSONResponse({"error": "the shop has no signing key"}, status_code=503)
        rec = b.get("receipt") if isinstance(b.get("receipt"), dict) else {}
        items = b.get("line_items") if isinstance(b.get("line_items"), list) and b["line_items"] else [{}]
        if not (rec.get("sig_seller") and signed_by(key, rec)):
            return JSONResponse({"error": "the receipt is not signed by this shop"}, status_code=400)
        if not isinstance(items[0], dict) or items[0].get("id") != rec.get("item") or type(rec.get("price")) is not int:
            return JSONResponse({"error": "line_items do not match the receipt"}, status_code=400)
        if any(s["task"] == rec["task"] for s in self.sessions.values()):
            return JSONResponse({"error": "this receipt already has a checkout session"}, status_code=409)
        own = self.terms.get(rec["task"]) or {}
        rec = dict({k: rec.get(k) for k in RECEIPT_KEYS}, **{k: own[k] for k in TERM_KEYS if k in own})
        u = None
        if self.stock is not None and self.stock.economy():
            acceptable = b.get("acceptable") if isinstance(b.get("acceptable"), list) else None
            u, problem = self.stock.ship(rec, acceptable)
            if problem:
                self.log(f"[acp] checkout refused: {problem}")
                return JSONResponse({"error": problem}, status_code=409)
            self.stock.hold(u["unit"], rec["task"])
        sid = "cs_" + os.urandom(8).hex()
        co = {"id": sid, "shop": rec["shop"], "shop_url": self.public, "item": u["sku"] if u else rec["item"],
              "price": rec["price"], "task": rec["task"], "currency": "credits", "iat": int(time.time())}
        if u:
            co["unit"] = u["unit"]
            if rec.get("cert") is not False:
                co["cert"] = u.get("cert")
            if rec.get("claim") in ("A", "B", "C"):
                co["claim"] = rec["claim"]
            if type(rec.get("warranty_hours")) in (int, float) and rec["warranty_hours"] > 0:
                co["warranty_hours"] = rec["warranty_hours"]
        self.sessions[sid] = s = {
            "id": sid, "status": "ready_for_payment", "task": rec["task"], "unit": co.get("unit"),
            "line_items": [{"id": co["item"], "quantity": 1}],
            "totals": [{"type": "total", "amount": rec["price"]}],
            "capabilities": {"payment_methods": ["ap2-mandate"]},
            "fulfillment": {k: co[k] for k in TERM_KEYS if k in co},
            "checkout_jwt": jwt_sign(key, co)}
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        self.log(f"[acp] checkout {sid}: {rec['item']} at {rec['price']}, task {rec['task']}")
        return JSONResponse(s, status_code=201)

    async def get(self, req: Request):
        s = self.sessions.get(req.path_params["sid"])
        if s is None:
            return JSONResponse({"error": "no such checkout session"}, status_code=404)
        return JSONResponse(s)

    async def cancel(self, req: Request):
        s = self.sessions.get(req.path_params["sid"])
        if s is None:
            return JSONResponse({"error": "no such checkout session"}, status_code=404)
        if s["status"] == "completed":
            return JSONResponse({"error": "session is completed"}, status_code=409)
        s["status"] = "canceled"
        if self.stock is not None and s.get("unit"):
            self.stock.release(s["unit"])
        return JSONResponse(s)

    async def complete(self, req: Request):
        s = self.sessions.get(req.path_params["sid"])
        if s is None:
            return JSONResponse({"error": "no such checkout session"}, status_code=404)
        if s["status"] != "ready_for_payment":
            return JSONResponse({"error": f"session is {s['status']}"}, status_code=409)
        b = await body_of(req)
        pay = (b or {}).get("payment_data")
        if not isinstance(pay, dict):
            return JSONResponse({"error": "payment_data is missing"}, status_code=400)
        try:
            no = await asyncio.to_thread(self.gate.refusal, pay.get("order"), s) if self.gate else None
        except Exception as e:                # a broken gate never blocks a sale
            self.log(f"[acp] gate failed: {type(e).__name__}: {e}")
            no = None
        if no:
            s["status"] = "canceled"          # nothing goes to /api/settle; the unit is on sale again
            if self.stock is not None and s.get("unit"):
                self.stock.release(s["unit"])
            return JSONResponse({"error": no}, status_code=409)
        total = s["totals"][0]["amount"]
        amount = ((pay.get("payment_mandate") or {}).get("mandate") or {}).get("amount")
        if amount != total:                   # the market checks this too; refusing here saves a round trip
            return JSONResponse({"error": f"payment amount {amount} is not the checkout total {total}"}, status_code=400)
        if self.stock is not None and s.get("unit"):
            if not self.stock.available(s["unit"], s["task"]):
                return JSONResponse({"error": f"unit {s['unit']} went to another buyer"}, status_code=409)
            self.stock.hold(s["unit"])            # paying now: the unit stays promised while the market answers
        body = {"order": pay.get("order"), "checkout_jwt": s["checkout_jwt"],
                "checkout_mandate": pay.get("checkout_mandate"), "payment_mandate": pay.get("payment_mandate")}
        try:
            async with httpx.AsyncClient(timeout=60, transport=self.transport) as c:
                r = await c.post(f"{self.market}/api/settle", json=body)
            res = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.log(f"[acp] settle failed for {s['id']}: {type(e).__name__}: {e}")
            return JSONResponse({"error": "the market did not answer"}, status_code=502)
        if r.status_code != 200 or not res.get("mandate_receipt"):
            failed = [k for k, v in (res.get("checks") or {}).items() if not v]
            self.log(f"[acp] settle refused for {s['id']} ({s['line_items'][0]['id']} at {total}): {failed or res}")
            if self.stock is not None and s.get("unit"):
                self.stock.release(s["unit"])     # the payment failed: the unit is on sale again (a retry re-checks it)
            return JSONResponse({"error": "payment refused by the market", "market": res}, status_code=402)
        s["status"] = "completed"
        s["order"] = {"status": "confirmed", "mandate_receipt": res["mandate_receipt"]}
        if self.stock is not None and s.get("unit"):
            self.stock.mark_sold(s["unit"])
        self.log(f"[acp] settled {s['line_items'][0]['id']} at {total}, order {pay.get('order')}, "
                 f"ledger #{res.get('ledger', res['mandate_receipt'].get('ledger'))}")
        return JSONResponse(s)


async def body_of(req):
    try:
        b = json.loads(await req.body() or b"{}")
    except ValueError:
        return None
    return b if isinstance(b, dict) else None
