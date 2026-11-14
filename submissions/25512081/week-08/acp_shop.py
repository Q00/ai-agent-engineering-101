"""A shop deployed without the kit: our seller (seller_r3.py) plus our own ACP checkout, market join, inventory sync
and supply buying, in one process. Nothing here imports scripts/market_kit.py.

    cd <shop dir with its own shop_key.pem>      # a new key is made on the first run (never commit it)
    python <week-08>/acp_shop.py --name kimauve-acp --market https://market.ourolabs.ai

What the kit does for the main shop and this file does itself:
  /.well-known/acp.json                      the ACP card (Agentic Commerce Protocol 2026-04-17, checkout subset)
  POST /acp/checkout_sessions                 a buyer brings the receipt our seller signed over A2A; we check the
                                              signature, pick the unit and return a session with a checkout_jwt
                                              (EdDSA over the checkout terms, signed with the shop key)
  GET  /acp/checkout_sessions/{id}            the session
  POST /acp/checkout_sessions/{id}/complete   forwards the buyer's closed AP2 mandates to the market's /api/settle
  POST /acp/checkout_sessions/{id}/cancel
  POST /api/join (role shop), GET /api/inventory every 10 s -> .market/inventory.json, POST /api/supply in the window,
  a cloudflared tunnel for the public URL.

The market takes no shop without the owner's claim code (MARKET_OWNER), and a second shop under the same GitHub
account would muddle the Players score, so it runs as the main shop itself: same name, same shop_key.pem and .market
folder, with the kit stopped (Nov 14: run for one settled deal through this checkout, then the kit again). The unit, certificate, claim and warranty in a session come from the receipt
our seller made (recorded when it signed), never from the buyer's copy: only shop, item, price and task are signed.
"""
import argparse
import asyncio
import base64
import collections
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

HERE = Path(__file__).resolve().parent
SIGNED = ("shop", "item", "price", "task")          # the receipt fields under sig_seller
LOG = Path("logs/acp-shop.log")


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- signatures (Ed25519): the same canonical JSON the market and the buyers' kits check

def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt_sign(key, payload):
    head = b64url(canon({"alg": "EdDSA", "typ": "JWT"})) + "." + b64url(canon(payload))
    return head + "." + b64url(key.sign(head.encode()))


def shop_key(path=Path("shop_key.pem")):
    if not path.exists():
        path.write_bytes(Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        log(f"[keys] created {path}")
    os.chmod(path, 0o600)
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def pub_b64(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)).decode()


def signed_by(pub, receipt):
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(
            base64.b64decode(receipt["sig_seller"]), canon({k: receipt.get(k) for k in SIGNED}))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


# --- the market API

class Market:
    def __init__(self, base, key):
        self.base, self.key, self.token = base.rstrip("/"), key, None
        self.c = httpx.Client(timeout=httpx.Timeout(30, connect=10))

    def join(self, name, url):
        ts = int(time.time())
        body = {"name": name, "role": "shop", "pubkey": pub_b64(self.key), "url": url, "ts": ts,
                "sig": base64.b64encode(self.key.sign(canon({"name": name, "role": "shop", "url": url, "ts": ts}))).decode()}
        if os.environ.get("MARKET_OWNER"):              # the market takes no shop without the owner's claim code
            body["owner"] = os.environ["MARKET_OWNER"]
        r = self.c.post(f"{self.base}/api/join", json=body)
        if r.status_code != 200:
            raise SystemExit(f"[market] join refused: {r.status_code} {r.text[:300]}")
        self.token = r.json()["token"]
        log(f"[market] shop registered as {name} at {url}: {json.dumps(r.json(), ensure_ascii=False)[:200]}")

    def call(self, method, path, **kw):
        return self.c.request(method, f"{self.base}{path}", headers={"Authorization": f"Bearer {self.token}"}, **kw)


# --- ACP checkout

class Checkout:
    def __init__(self, key, market, receipts):
        self.key, self.market, self.receipts = key, market, receipts
        self.pub = pub_b64(key)
        self.sessions = collections.OrderedDict()

    def card(self, public):
        tools = [("create_checkout_session", "POST", "/checkout_sessions",
                  {"line_items": {"type": "array", "description": "[{id: <sku of the receipt>, quantity: 1}]"},
                   "receipt": {"type": "object", "description": "the receipt this shop signed over A2A (with sig_seller)"},
                   "acceptable": {"type": "array", "description": "optional: the order's acceptable SKUs"}}),
                 ("get_checkout_session", "GET", "/checkout_sessions/{id}", {}),
                 ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
                  {"payment_data": {"type": "object", "description": "{order, checkout_mandate, payment_mandate}"}}),
                 ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", {})]
        return {"protocol": "acp", "version": "2026-04-17", "endpoint": f"{public}/acp",
                "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
                "tools": [{"name": n, "method": m, "path": p, "input": i} for n, m, p, i in tools]}

    def unit_for(self, rec, acceptable):
        r = self.market.call("GET", "/api/inventory")
        if r.status_code != 200:
            return None, f"inventory: {r.status_code}"
        held = {s["unit"] for s in self.sessions.values() if s.get("unit") and s["status"] == "ready_for_payment"}
        units = [u for u in r.json().get("units", []) if u["unit"] not in held and u["grade"] != "expired"]
        fits = lambda u: u["sku"] in acceptable if acceptable else u["sku"] == rec.get("item")
        u = next((u for u in units if u["unit"] == rec.get("unit")), None)
        return (u, None) if u and fits(u) else (None, f"unit {rec.get('unit')} is not in stock for this order")

    async def create(self, b, public):
        rec = b.get("receipt") if isinstance(b.get("receipt"), dict) else {}
        items = b.get("line_items") or [{}]
        acceptable = b.get("acceptable") if isinstance(b.get("acceptable"), list) else None
        if not (rec.get("sig_seller") and signed_by(self.pub, rec)):
            return JSONResponse({"error": "the receipt is not signed by this shop"}, status_code=400)
        if items[0].get("id") != rec.get("item") or type(rec.get("price")) is not int:
            return JSONResponse({"error": "line_items do not match the receipt"}, status_code=400)
        if any(s["task"] == rec["task"] for s in self.sessions.values()):
            return JSONResponse({"error": "this receipt already has a checkout session"}, status_code=409)
        own = self.receipts.get(rec["task"]) or {}          # what our seller put in, not the buyer's copy
        rec = dict({k: rec[k] for k in SIGNED}, **{k: own[k] for k in ("unit", "cert", "claim", "warranty_hours") if k in own})
        u, problem = await asyncio.to_thread(self.unit_for, rec, acceptable)
        if problem:
            log(f"[acp] checkout refused: {problem}")
            return JSONResponse({"error": problem}, status_code=409)
        sid = "cs_" + os.urandom(8).hex()
        co = {"id": sid, "shop": rec["shop"], "shop_url": public, "item": u["sku"], "price": rec["price"],
              "task": rec["task"], "currency": "credits", "iat": int(time.time()), "unit": u["unit"]}
        if rec.get("cert") is not False:
            co["cert"] = u["cert"]
        if rec.get("claim") in ("A", "B", "C"):
            co["claim"] = rec["claim"]
        if type(rec.get("warranty_hours")) in (int, float) and rec["warranty_hours"] > 0:
            co["warranty_hours"] = rec["warranty_hours"]
        self.sessions[sid] = s = {
            "id": sid, "status": "ready_for_payment", "task": rec["task"], "unit": u["unit"],
            "line_items": [{"id": co["item"], "quantity": 1}], "totals": [{"type": "total", "amount": rec["price"]}],
            "capabilities": {"payment_methods": ["ap2-mandate"]},
            "fulfillment": {k: co[k] for k in ("unit", "cert", "claim", "warranty_hours") if k in co},
            "checkout_jwt": jwt_sign(self.key, co)}
        while len(self.sessions) > 500:
            self.sessions.popitem(last=False)
        log(f"[acp] session {sid}: {co['item']} unit {u['unit']} at {rec['price']}")
        return JSONResponse(s, status_code=201)

    async def complete(self, s, b):
        pay = b.get("payment_data") or {}
        r = await asyncio.to_thread(self.market.call, "POST", "/api/settle", json={
            "order": pay.get("order"), "checkout_jwt": s["checkout_jwt"],
            "checkout_mandate": pay.get("checkout_mandate"), "payment_mandate": pay.get("payment_mandate")})
        res = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:200]}
        if r.status_code != 200:
            bad = [k for k, v in (res.get("checks") or {}).items() if not v]
            log(f"[acp] settle refused: {r.status_code} failed checks {bad} "
                f"{json.dumps({k: v for k, v in res.items() if k != 'checks'}, ensure_ascii=False)[:300]}")
            return JSONResponse({"error": "payment refused by the market", "market": res}, status_code=402)
        s["status"] = "completed"
        s["order"] = {"status": "confirmed", "mandate_receipt": res["mandate_receipt"]}
        log(f"[acp] SETTLED {s['line_items'][0]['id']} at {s['totals'][0]['amount']}, ledger #{res.get('ledger')}, "
            f"unit {res['mandate_receipt'].get('unit')} grade {res['mandate_receipt'].get('grade')}")
        return JSONResponse(s)

    async def route(self, req):
        b = {}
        if req.method == "POST":
            try:
                b = json.loads(await req.body() or b"{}")
            except ValueError:
                return JSONResponse({"error": "body is not JSON"}, status_code=400)
        public = req.app.state.public
        parts = req.path_params["path"].strip("/").split("/")
        if parts[:1] != ["checkout_sessions"]:
            return JSONResponse({"error": "unknown ACP path"}, status_code=404)
        if len(parts) == 1 and req.method == "POST":
            return await self.create(b, public)
        s = self.sessions.get(parts[1]) if len(parts) > 1 else None
        if s is None:
            return JSONResponse({"error": "no such checkout session"}, status_code=404)
        if len(parts) == 2:
            return JSONResponse(s)
        if parts[2] == "cancel" and s["status"] != "completed":
            s["status"] = "canceled"
            return JSONResponse(s)
        if parts[2] == "complete" and s["status"] == "ready_for_payment":
            return await self.complete(s, b)
        return JSONResponse({"error": f"session is {s['status']}"}, status_code=409)


# --- background: inventory for the seller, a little supply

def sync(market, md, stop, buy_sku, buy_qty):
    bought = 0
    while not stop.is_set():
        try:
            r = market.call("GET", "/api/inventory")
            if r.status_code == 200:
                inv = r.json()
                tmp = md / "inventory.json.tmp"
                tmp.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")
                tmp.replace(md / "inventory.json")
                win = inv.get("restock") or {}
                if buy_sku and bought < buy_qty and win.get("open"):
                    s = market.call("POST", "/api/supply", json={"sku": buy_sku, "qty": buy_qty - bought})
                    log(f"[supply] {buy_sku} x{buy_qty - bought}: {s.status_code} {s.text[:200]}")
                    if s.status_code == 200:
                        bought = buy_qty
        except httpx.HTTPError as e:
            log(f"[sync] {type(e).__name__}: {e}")
        stop.wait(10)


def tunnel(port):
    proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    found = []

    def read():
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found:
                found.append(m.group())
    threading.Thread(target=read, daemon=True).start()
    for _ in range(300):
        if found:
            return proc, found[0]
        time.sleep(0.2)
    raise SystemExit("[tunnel] cloudflared gave no URL within 60 s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="kimauve-acp")
    ap.add_argument("--market", default="https://market.ourolabs.ai")
    ap.add_argument("--port", type=int, default=9411)
    ap.add_argument("--buy", default="", help="a SKU to buy once in the next supply window")
    ap.add_argument("--qty", type=int, default=3)
    a = ap.parse_args()
    md = Path(".market")
    md.mkdir(exist_ok=True)
    os.environ["MARKET_DIR"], os.environ["SHOP_NAME"] = str(md), a.name
    sys.path.insert(0, str(HERE))
    import seller_r3                                   # our seller, reading this folder's .market and shop_key.pem

    key = shop_key()
    receipts = collections.OrderedDict()
    signer = seller_r3.sign

    def sign_and_keep(receipt):                         # keep what our seller put in each receipt, for the checkout
        out = signer(receipt)
        receipts[out["task"]] = out
        while len(receipts) > 2000:
            receipts.popitem(last=False)
        return out
    seller_r3.sign = sign_and_keep

    proc, public = tunnel(a.port)
    log(f"[tunnel] {public}")
    market = Market(a.market, key)
    checkout = Checkout(key, market, receipts)
    a2a = seller_r3.build(a.port, url=public + "/")
    app = Starlette(routes=[Route("/.well-known/acp.json", lambda req: JSONResponse(checkout.card(public))),
                            Route("/acp/{path:path}", checkout.route, methods=["GET", "POST"])] + list(a2a.routes))
    app.state.public = public
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get(public + "/.well-known/agent-card.json", timeout=3).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    market.join(a.name, public)
    stop = threading.Event()
    threading.Thread(target=sync, args=(market, md, stop, a.buy, a.qty), daemon=True).start()
    try:
        while proc.poll() is None:
            time.sleep(5)
        log("[tunnel] cloudflared exited")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        proc.terminate()


if __name__ == "__main__":
    main()
