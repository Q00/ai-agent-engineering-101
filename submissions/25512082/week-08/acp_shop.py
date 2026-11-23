"""Kit-free Round 3 shop: A2A seller + our own ACP checkout + direct market calls.

Week 08 BASE asks for a shop "deployed without the kit" that serves
/.well-known/acp.json and the checkout-session endpoints itself and gets a deal
through the market's /api/settle. This module does that without importing
scripts/market_kit.py:

  buyer --A2A--> SafeSeller (seller.py, unchanged policy)  -> signed receipt
  buyer --ACP--> AcpCheckout.create   (receipt checked against what *we* signed)
                 AcpCheckout.complete -> POST market /api/settle (shop token)
  background:    inventory sync (GET /api/inventory -> .market/inventory.json)
                 optional restock via safe_restock.build_plan (approval-gated)

Run (only after approval; it registers the shop URL with the market):
    python acp_shop.py --tunnel            # or --public-url https://...
    python acp_shop.py --dry-run --port N  # local only: no join, no market calls

Do not run it at the same time as the kit's shop (same name and key: the market
keeps only the last registered URL).
"""
from __future__ import annotations

import argparse
import base64
import collections
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import safe_restock
from seller import SafeSeller, build_app

ACP_VERSION = "2026-04-17"
MARKET = "https://market.ourolabs.ai"
LOG_PATH = Path(__file__).resolve().parent / "logs" / "acp-shop.log"
_log_lock = threading.Lock()


def log(line: str) -> None:
    stamped = f"{time.strftime('%H:%M:%S')} {line}"
    print(stamped, flush=True)
    with _log_lock:
        LOG_PATH.parent.mkdir(exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(stamped + "\n")


# --- signing (same canonical JSON and EdDSA JWT shape the market verifies)

def canon(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def load_key(path: Path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def pub_b64(key) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def jwt_sign(key, payload: dict) -> str:
    head = b64url(canon({"alg": "EdDSA", "typ": "JWT"})) + "." + b64url(canon(payload))
    return head + "." + b64url(key.sign(head.encode()))


# --- market client (join, inventory, settle, supply); the token never leaves memory

class MarketClient:
    def __init__(self, base: str, key, owner: str | None, client: httpx.Client | None = None):
        self.base = base.rstrip("/")
        self.key = key
        self.owner = owner
        self.c = client or httpx.Client(timeout=httpx.Timeout(30, connect=10))
        self.token: str | None = None

    def join_shop(self, name: str, url: str) -> dict:
        ts = int(time.time())
        signed = {"name": name, "role": "shop", "url": url, "ts": ts}
        body = {**signed, "pubkey": pub_b64(self.key),
                "sig": base64.b64encode(self.key.sign(canon(signed))).decode()}
        if self.owner:
            body["owner"] = self.owner
        r = self.c.post(f"{self.base}/api/join", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"join refused: {r.status_code} {r.text[:200]}")
        self.token = r.json()["token"]
        return r.json()

    def call(self, method: str, path: str, **kw) -> httpx.Response:
        return self.c.request(method, f"{self.base}{path}",
                              headers={"Authorization": f"Bearer {self.token}"}, **kw)


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --- the ACP checkout (ACP 2026-04-17, checkout subset, AP2 mandates)

class AcpCheckout:
    """Checkout sessions for receipts this shop's SafeSeller signed.

    The unit, claim and certificate come from our own records (seller.issued and
    the live inventory), never from the buyer's copy of the receipt.
    """

    def __init__(self, key, market: MarketClient, seller: SafeSeller, market_dir: Path):
        self.key, self.market, self.seller, self.market_dir = key, market, seller, market_dir
        self.public = ""
        self.sessions: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
        self.lock = threading.Lock()

    def card(self) -> dict:
        tools = [
            ("create_checkout_session", "POST", "/checkout_sessions",
             {"line_items": "[{id: item, quantity: 1}]", "receipt": "the receipt this shop signed over A2A",
              "acceptable": "optional list of SKUs the order accepts"}),
            ("get_checkout_session", "GET", "/checkout_sessions/{id}", {}),
            ("update_checkout_session", "POST", "/checkout_sessions/{id}", {}),
            ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
             {"payment_data": "{order, checkout_mandate, payment_mandate}"}),
            ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", {}),
        ]
        return {"protocol": "acp", "version": ACP_VERSION, "endpoint": f"{self.public}/acp",
                "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
                "tools": [{"name": n, "method": m, "path": p, "input": i} for n, m, p, i in tools]}

    def _inventory(self) -> dict | None:
        r = self.market.call("GET", "/api/inventory")
        if r.status_code != 200:
            return None
        inv = r.json()
        write_json(self.market_dir / "inventory.json", inv)
        return inv

    def create(self, body: dict) -> tuple[int, dict]:
        rec = body.get("receipt") if isinstance(body.get("receipt"), dict) else {}
        items = body.get("line_items") or [{}]
        acceptable = body.get("acceptable") if isinstance(body.get("acceptable"), list) else None
        ours = self.seller.issued.get(str(rec.get("task")))
        # The buyer's copy must be exactly what we signed (shop, item, price, task, signature).
        if not ours or any(rec.get(k) != ours.get(k) for k in ("shop", "item", "price", "task", "sig_seller")):
            return 400, {"error": "the receipt is not one this shop signed"}
        if not isinstance(items[0], dict) or items[0].get("id") != ours["item"]:
            return 400, {"error": "line_items do not match the receipt"}
        if acceptable and ours["item"] not in acceptable:
            return 409, {"error": "the receipt's item is not acceptable for this order"}
        with self.lock:
            if any(s["task"] == ours["task"] for s in self.sessions.values()):
                return 409, {"error": "this receipt already has a checkout session"}
            inv = self._inventory()
            if inv is None:
                return 503, {"error": "inventory unavailable"}
            held = {s["unit"] for s in self.sessions.values() if s["status"] == "ready_for_payment"}
            unit = next((u for u in inv.get("units", []) if u.get("unit") == ours.get("unit")
                         and u.get("grade") != "expired" and u["unit"] not in held), None)
            if unit is None:
                log(f"[acp] checkout refused: unit {ours.get('unit')} is not in stock")
                return 409, {"error": f"unit {ours.get('unit')} is not in stock"}
            sid = "cs_" + os.urandom(8).hex()
            co = {"id": sid, "shop": ours["shop"], "shop_url": self.public, "item": unit["sku"],
                  "price": ours["price"], "task": ours["task"], "currency": "credits",
                  "iat": int(time.time()), "unit": unit["unit"], "cert": unit["cert"]}
            if ours.get("claim") in ("A", "B", "C"):
                co["claim"] = ours["claim"]
            session = {
                "id": sid, "status": "ready_for_payment", "task": ours["task"], "unit": unit["unit"],
                "line_items": [{"id": co["item"], "quantity": 1}],
                "totals": [{"type": "total", "amount": ours["price"]}],
                "capabilities": {"payment_methods": ["ap2-mandate"]},
                "fulfillment": {k: co[k] for k in ("unit", "cert", "claim") if k in co},
                "checkout_jwt": jwt_sign(self.key, co),
            }
            self.sessions[sid] = session
            while len(self.sessions) > 500:
                self.sessions.popitem(last=False)
        log(f"[acp] checkout {sid} for {co['item']} at {co['price']}, unit {co['unit']}")
        return 201, session

    def complete(self, sid: str, body: dict) -> tuple[int, dict]:
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                return 404, {"error": "no such checkout session"}
            if s["status"] != "ready_for_payment":
                return 409, {"error": f"session is {s['status']}"}
            s["status"] = "in_progress"           # no second /complete while the market answers
        pay = body.get("payment_data") or {}
        try:
            r = self.market.call("POST", "/api/settle", json={
                "order": pay.get("order"), "checkout_jwt": s["checkout_jwt"],
                "checkout_mandate": pay.get("checkout_mandate"),
                "payment_mandate": pay.get("payment_mandate")})
            res = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:200]}
        except httpx.HTTPError as exc:
            s["status"] = "ready_for_payment"
            return 502, {"error": f"market unreachable: {type(exc).__name__}"}
        if r.status_code != 200:
            s["status"] = "ready_for_payment"
            failed = [k for k, v in (res.get("checks") or {}).items() if not v]
            log(f"[acp] settle refused: {failed or res}")
            return 402, {"error": "payment refused by the market", "market": res}
        s["status"] = "completed"
        s["order"] = {"status": "confirmed", "mandate_receipt": res["mandate_receipt"]}
        mr = res["mandate_receipt"]
        log(f"[acp] settled {mr.get('name') or s['line_items'][0]['id']} at {s['totals'][0]['amount']}, "
            f"ledger #{res.get('ledger')}, unit {mr.get('unit')} grade {mr.get('grade')} (kit-free ACP)")
        try:
            self._inventory()
        except httpx.HTTPError:
            pass
        return 200, s

    def cancel(self, sid: str) -> tuple[int, dict]:
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                return 404, {"error": "no such checkout session"}
            if s["status"] == "completed":
                return 409, {"error": "session is completed"}
            s["status"] = "canceled"
            return 200, s

    def routes(self) -> list[Route]:
        async def card(_: Request):
            return JSONResponse(self.card())

        async def create(req: Request):
            code, out = self.create(await _json(req))
            return JSONResponse(out, status_code=code)

        async def get_or_update(req: Request):
            s = self.sessions.get(req.path_params["sid"])
            return JSONResponse(s or {"error": "no such checkout session"}, status_code=200 if s else 404)

        async def complete(req: Request):
            import asyncio
            code, out = await asyncio.to_thread(self.complete, req.path_params["sid"], await _json(req))
            return JSONResponse(out, status_code=code)

        async def cancel(req: Request):
            code, out = self.cancel(req.path_params["sid"])
            return JSONResponse(out, status_code=code)

        return [
            Route("/.well-known/acp.json", card, methods=["GET"]),
            Route("/acp/checkout_sessions", create, methods=["POST"]),
            Route("/acp/checkout_sessions/{sid}", get_or_update, methods=["GET", "POST"]),
            Route("/acp/checkout_sessions/{sid}/complete", complete, methods=["POST"]),
            Route("/acp/checkout_sessions/{sid}/cancel", cancel, methods=["POST"]),
        ]


async def _json(req: Request) -> dict:
    try:
        value = json.loads(await req.body() or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# --- background loops

def inventory_loop(checkout: AcpCheckout, stop: threading.Event, every: float = 10) -> None:
    while not stop.wait(every):
        try:
            checkout._inventory()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log(f"[sync] inventory: {type(exc).__name__}")


def restock_loop(market: MarketClient, market_dir: Path, approval: Path, stop: threading.Event) -> None:
    """Same fail-closed gate as the kit path: nothing is bought without a fresh approval file."""
    while not stop.wait(60):
        plan = safe_restock.build_plan(market_dir, approval)
        for line in plan.get("buy", [])[:10]:
            r = market.call("POST", "/api/supply", json={"sku": line["sku"], "qty": line["qty"]})
            log(f"[supply] {line} -> {r.status_code} {r.text[:160]}")
            time.sleep(10)


def open_tunnel(port: int) -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    found: list[str] = []

    def read() -> None:
        for line in proc.stdout:  # keep draining so the pipe never fills
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found:
                found.append(m.group())
    threading.Thread(target=read, daemon=True).start()
    deadline = time.time() + 60
    while time.time() < deadline and not found and proc.poll() is None:
        time.sleep(0.2)
    if not found:
        proc.terminate()
        raise RuntimeError("cloudflared printed no trycloudflare URL within 60 s")
    return proc, found[0]


def wait_public(url: str, seconds: int = 120) -> bool:
    for _ in range(seconds):
        try:
            if httpx.get(f"{url}/.well-known/acp.json", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def build_shop(port: int, public: str, key_path: Path, market_dir: Path, ledger: Path, market: MarketClient):
    seller = SafeSeller(market_dir, key_path, ledger)
    checkout = AcpCheckout(load_key(key_path), market, seller, market_dir)
    checkout.public = public
    app = build_app(port, market_dir, key_path, ledger, public_url=public,
                    extra_routes=checkout.routes(), executor=seller)
    return app, checkout


def parse_args(argv=None) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description="Kit-free Round 3 shop with its own ACP checkout")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--name", default="sohyunkim11")
    p.add_argument("--market", default=MARKET)
    p.add_argument("--market-dir", type=Path, default=here / ".market")
    p.add_argument("--key", type=Path, default=here / "shop_key.pem")
    p.add_argument("--ledger", type=Path, default=here / ".market" / "seller-ledger.json")
    p.add_argument("--approval", type=Path, default=here / "restock-approval.json")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tunnel", action="store_true")
    g.add_argument("--public-url")
    g.add_argument("--dry-run", action="store_true", help="local only: never joins or calls the market")
    return p.parse_args(argv)


def main(argv=None) -> None:
    a = parse_args(argv)
    port = a.port or _free_port()
    key = load_key(a.key)
    market = MarketClient(a.market, key, os.environ.get("MARKET_OWNER"))
    tunnel = None
    if a.tunnel:
        tunnel, public = open_tunnel(port)
    elif a.public_url:
        public = a.public_url.rstrip("/")
    else:
        public = f"http://127.0.0.1:{port}"
    app, checkout = build_shop(port, public, a.key, a.market_dir, a.ledger, market)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    log(f"[acp-shop] kit-free shop on port {port}, public {public}")
    stop = threading.Event()
    try:
        if a.dry_run:
            log("[acp-shop] dry run: no market join, no inventory sync, no restock")
        else:
            if not wait_public(public):
                raise SystemExit(f"[acp-shop] {public} did not answer within 120 s")
            j = market.join_shop(a.name, public)
            log(f"[market] shop registered as {j['name']}, round {j['round']} (kit-free)")
            checkout._inventory()
            threading.Thread(target=inventory_loop, args=(checkout, stop), daemon=True).start()
            threading.Thread(target=restock_loop, args=(market, a.market_dir, a.approval, stop),
                             daemon=True).start()
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.should_exit = True
        if tunnel:
            tunnel.terminate()


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    main()
