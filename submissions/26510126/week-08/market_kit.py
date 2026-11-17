#!/usr/bin/env python3
"""Week-07 market kit. Puts your week-06 shop and buyer into the class market.

    python scripts/market_kit.py join --dir submissions/<student-id>/week-07 --market <MARKET_URL> --name <nickname> --tunnel

No working week-06 shop or buyer yet? Copy the starter (an unsigned week-06 shop and buyer) first:
    python scripts/market_kit.py starter --dir submissions/<student-id>/week-07

--dir is your submission directory (where agent.json is; default: the current directory). The kit:
  - creates shop_key.pem and buyer_key.pem (Ed25519) and adds *.pem to .gitignore,
  - starts your seller with the "seller" command and registers its card URL,
  - asks the market for orders and runs your buyer for each one,
  - sends the result (receipt, stars, review) to the market and signs it for you,
  - from Round 3: serves an ACP checkout for your shop (/.well-known/acp.json, /acp/checkout_sessions) and pays
    for your buyer's deals with AP2-shaped mandates; only settled deals count.
  - from Round 3 (kit v4): keeps <dir>/.market/ fresh for your agents (inventory.json, catalog.json, demand.json,
    forum.json; the seller gets the path as MARKET_DIR), buys stock for your shop (your "restock" command, or a
    simple default), ships a unit from stock at checkout, checks the unit against your buyer's "accept" policy
    before paying, and posts what your agents write to .market/outbox.jsonl on the forum. Rules: ROUND3 in /agent.md.

Your seller and buyer never talk to the market. agent.json:
    {
      "seller": {"cmd": "python seller.py --port {port}"},
      "buyer":  {"cmd": "python buyer.py --url {url} --item {item} --budget {budget}"},
      "market_buyer": {"cmd": "python market_buyer.py --shops {shops} --item {item} --budget {budget}"}
    }
"buyer" is the week-06 buyer, used in Round 0 (and later if there is no market_buyer).
"market_buyer" is LAB 1: it reads the shop list in {shops}, picks a shop, trades over A2A,
and prints one JSON line last:
    {"shop": "<card URL>", "receipt": {...} or null, "stars": 1-5, "review": "one sentence"}
From Round 3 the line may also carry "accept" (what the checkout must show before the kit pays):
    "accept": {"cert": true, "min_hours_left": 3, "grades": ["A", "B"], "warranty_hours": 6}
{order} in a command is the path of the order (category, acceptable SKUs, use_within_hours, budget).
Optional: "restock": {"cmd": "... {state}"} prints {"buy": [{"sku": ..., "qty": ...}]} (and "bankrupt": true to file);
"review": {"cmd": "... {deal}"} runs after a settled deal and prints {"stars": 1-5, "review": "..."}.

Options:
  --tunnel             expose your shop with a cloudflared quick tunnel (others can reach it)
  --public-url URL     your own tunnel forwards URL to the kit's shop port (fix it with --port)
  --port N             the kit's shop port (default: a free port)
  --deployed URL       your shop is already deployed (e.g. Workers); the kit only registers it
  --buyer-only / --shop-only
  --owner CODE         claim code from the market's join page (or MARKET_OWNER); shows your GitHub on the board

Needs: pip install "a2a-sdk[http-server]" uvicorn httpx cryptography
Logs: logs/market-kit.log in your submission directory.
"""
import argparse
import asyncio
import base64
import collections
import contextlib
import hashlib
import json
import os
import random
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import uvicorn
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

KIT_VERSION = "4"
BUYER_TIMEOUT = 300
ECON_ROUND = 3
INV_FRESH = 60       # seconds: pick_unit reads the local inventory.json when it is fresher (11/17: /api/inventory timed out)
FOOD_GRADE_CUTS = (0.8, 0.45)                        # share of shelf life left for food grade A, B (the market's)
SYNC_EVERY = 10                                     # seconds between refreshes of .market/
RESTOCK_EVERY = 60
RESTOCK_LOW = 20                                    # the default restock buys when fewer units than this are left
RECEIPT_KEYS = ("shop", "item", "price", "task")
LOG_FILE = None
LOG_MAX = 20_000_000                                # bytes; then market-kit.log moves to market-kit.log.1
LOG_LOCK = threading.Lock()
CHECKOUT = None                                   # acp_pay_tools (default) or acp_pay (--fixed-checkout); set in main
RETRY = (httpx.TransportError, ValueError)        # network trouble, or a body that is not JSON (e.g. a proxy's 502 page)


def log(line=""):
    """Never raises: a failed write must not stop the thread that drains a process's output."""
    stamp = datetime.now().strftime("%H:%M:%S")
    with LOG_LOCK:
        try:
            print(f"{stamp} {line}", flush=True)
        except Exception:
            pass
        if LOG_FILE:
            try:
                if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX:
                    os.replace(LOG_FILE, LOG_FILE.with_suffix(".log.1"))
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{stamp} {line}\n")
            except Exception:
                pass


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_sign(key, payload):
    head = b64url(canon({"alg": "EdDSA", "typ": "JWT"})) + "." + b64url(canon(payload))
    return head + "." + b64url(key.sign(head.encode()))


def jwt_payload(token, pubkey_b64):
    try:
        head, body, sig = token.split(".")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(unb64url(sig), f"{head}.{body}".encode())
        return json.loads(unb64url(body))
    except (InvalidSignature, ValueError, TypeError, AttributeError):
        return None


# --- keys

def load_key(path: Path):
    if not path.exists():
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        log(f"[keys] created {path.name}")
    if os.name != "nt":
        os.chmod(path, 0o600)                       # only you can read your private key
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def pub_b64(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                          serialization.PublicFormat.Raw)).decode()


def ignore_pem(d: Path):
    gi = d / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [x for x in ("*.pem", ".market/") if x not in lines]
    if add:
        gi.write_text("\n".join(lines + add) + "\n", encoding="utf-8")
        log(f"[keys] added {', '.join(add)} to .gitignore")


def verify_seller(pubkey_b64, receipt):
    try:
        body = {k: receipt.get(k) for k in RECEIPT_KEYS}
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(
            base64.b64decode(receipt["sig_seller"]), canon(body))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


# --- processes

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(cmd, cwd, out=subprocess.PIPE):
    kw = {"start_new_session": True} if os.name != "nt" else {}
    return subprocess.Popen(cmd, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", **kw)


def stop(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:                                       # shell=True: kill cmd.exe and the python under it
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        proc.wait(timeout=10)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)     # the whole group: the shell and what it started
            else:
                proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def fill(cmd, **kw):
    for k, v in kw.items():
        cmd = cmd.replace("{" + k + "}", shlex.quote(str(v)) if os.name != "nt" else f'"{v}"')
    return cmd


def pump(proc, prefix, sink=None):
    """Copy a process's output into the kit log, line by line."""
    def run():
        for line in proc.stdout:
            line = line.rstrip("\n")
            if sink is not None:
                sink.append(line)
            log(f"{prefix} {line}")
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# --- A2A proxy: rewrites the card so every request goes through the kit

class Proxy:
    """Serves one shop under a different base URL.

    The card's interface URLs are rewritten to <public>/a2a/<i>, so a buyer that follows the card
    keeps talking through the proxy. The shop side uses it so a tunneled shop does not advertise
    127.0.0.1; the buyer side uses it in Round 0 to see the task state and the receipt.
    """

    def __init__(self, port, same_host=False):
        self.port = port
        self.same_host = same_host         # buyer side: follow a card only to the shop's own host
        self.public = f"http://127.0.0.1:{port}"
        self.upstream = None
        self.ifaces = {}
        self.seen = collections.deque(maxlen=200)   # tasks observed in responses
        self.client = None                 # one keep-alive client, made on uvicorn's loop
        self.acp = None                    # shop side: the kit's ACP checkout (Round 3)
        app = Starlette(routes=[Route("/{path:path}", self.handle, methods=["GET", "POST", "PUT", "DELETE"])])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(50):
            if self.server.started:
                break
            time.sleep(0.1)

    def target(self, base):
        self.upstream, self.ifaces, self.seen = base.rstrip("/"), {}, collections.deque(maxlen=200)

    def rewrite(self, card):
        for i, iface in enumerate(card.get("supportedInterfaces") or []):
            self.ifaces[str(i)] = iface.get("url")
            iface["url"] = f"{self.public}/a2a/{i}"
        if card.get("url"):
            self.ifaces["top"] = card["url"]
            card["url"] = f"{self.public}/a2a/top"
        return card

    async def handle(self, req: Request):
        path = req.path_params["path"]
        body = await req.body()
        if self.acp and (path == ".well-known/acp.json" or path.startswith("acp/")):
            return await self.acp.handle(req, path, body, self.public)
        headers = {k: v for k, v in req.headers.items() if k.lower() in ("content-type", "a2a-version", "authorization", "accept")}
        if path.startswith("a2a/"):
            url = self.ifaces.get(path.split("/", 1)[1])
            if url is None:
                return JSONResponse({"error": "read the agent card first"}, status_code=404)
            if self.same_host and urlparse(url).hostname != urlparse(self.upstream).hostname:
                return JSONResponse({"error": f"the card's interface {url} is not on the shop's host"}, status_code=403)
        else:
            url = f"{self.upstream}/{path}"
        if req.url.query:
            url += "?" + req.url.query
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=240)
        try:
            r = await self.client.request(req.method, url, content=body, headers=headers)
        except httpx.HTTPError as e:
            return JSONResponse({"error": f"upstream: {type(e).__name__}"}, status_code=502)
        content = r.content
        if path == ".well-known/agent-card.json" and r.status_code == 200:
            content = json.dumps(self.rewrite(r.json())).encode()
        elif path.startswith("a2a/"):
            self.observe(content)
        return Response(content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    def observe(self, content):
        try:
            res = json.loads(content).get("result") or {}
        except Exception:
            return
        task = res.get("task", res)
        if isinstance(task, dict) and "status" in task:
            self.seen.append(task)
            if self.acp:                                 # shop side: the terms the seller itself put in its receipt
                for art in task.get("artifacts") or []:
                    for part in art.get("parts") or []:
                        data = part.get("data")
                        if isinstance(data, dict) and data.get("sig_seller"):
                            self.acp.terms[data.get("task", task.get("id"))] = data
                while len(self.acp.terms) > 2000:
                    self.acp.terms.popitem(last=False)


class AcpShop:
    """The shop's ACP checkout (Agentic Commerce Protocol 2026-04-17, the checkout subset), served by the kit.

    A buyer brings the receipt your seller signed over A2A; the kit turns it into a checkout session whose
    checkout_jwt it signs with your shop key. Completing the session forwards the buyer's closed AP2 mandates
    to the market's /api/settle. Your seller does not change. The unit, certificate, claim and warranty come from
    the receipt your seller sent (the proxy keeps it), not from the copy the buyer brings.
    """

    def __init__(self, key, market, md=None):
        self.key, self.market, self.md = key, market, md      # md: the .market folder whose inventory.json to refresh
        self.pub = pub_b64(key)
        self.sessions = collections.OrderedDict()
        self.terms = collections.OrderedDict()           # task -> the receipt the seller sent (Proxy.observe)

    def card(self, public):
        """acp.json. Each tool's input is a JSON Schema (week-08 optional HW; the kit wrote short descriptions)."""
        obj = lambda req, props, desc=None: dict({"type": "object", "required": req, "properties": props},
                                                 **({"description": desc} if desc else {}))
        sid = {"type": "string", "description": "checkout session id (path)"}
        signed = obj(["mandate", "sig"], {"mandate": {"type": "object"},
                                          "sig": {"type": "string", "description": "base64 Ed25519 by the buyer key"}})
        tools = [("create_checkout_session", "POST", "/checkout_sessions", "Open a checkout for a receipt the shop signed over A2A.",
                  obj(["line_items", "receipt"], {
                      "line_items": {"type": "array", "minItems": 1, "items": obj(["id", "quantity"], {
                          "id": {"type": "string", "description": "the receipt's item"},
                          "quantity": {"type": "integer", "minimum": 1}})},
                      "receipt": obj(["shop", "item", "price", "task", "sig_seller"], {
                          "shop": {"type": "string"}, "item": {"type": "string"},
                          "price": {"type": "integer", "minimum": 0}, "task": {"type": "string"},
                          "sig_seller": {"type": "string"}}, "the receipt the shop signed over A2A")})),
                 ("get_checkout_session", "GET", "/checkout_sessions/{id}", "Read a checkout session.",
                  obj(["id"], {"id": sid})),
                 ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
                  "Pay with the buyer's closed AP2 mandates; the shop settles through the market.",
                  obj(["id", "payment_data"], {"id": sid, "payment_data": obj(
                      ["order", "checkout_mandate", "payment_mandate"],
                      {"order": {"type": ["string", "integer"]}, "checkout_mandate": signed, "payment_mandate": signed})})),
                 ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", "Cancel a session not yet completed.",
                  obj(["id"], {"id": sid}))]
        return {"protocol": "acp", "version": "2026-04-17", "endpoint": f"{public}/acp",
                "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
                "tools": [{"name": n, "method": m, "path": p, "description": d, "input": i} for n, m, p, d, i in tools]}

    async def handle(self, req, path, body, public):
        if path == ".well-known/acp.json":
            return JSONResponse(self.card(public))
        try:
            b = json.loads(body or b"{}")
        except ValueError:
            return JSONResponse({"error": "body is not JSON"}, status_code=400)
        parts = path.split("/")[1:]                      # acp/checkout_sessions[/id[/complete|cancel]]
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
            pay = b.get("payment_data") or {}
            who = await asyncio.to_thread(blocked_buyer, self.market, pay.get("order"))
            if who:
                s["status"] = "canceled"
                log(f"[acp] checkout refused: buyer {who} is on blocked_buyers_r3.json")
                return JSONResponse({"error": "this shop does not sell to this buyer"}, status_code=403)
            r = await asyncio.to_thread(self.market.call, "POST", "/api/settle", "shop",
                                        json={"order": pay.get("order"), "checkout_jwt": s["checkout_jwt"],
                                              "checkout_mandate": pay.get("checkout_mandate"),
                                              "payment_mandate": pay.get("payment_mandate")})
            res = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:200]}
            if r.status_code != 200:
                log(f"[acp] settle refused: {[k for k, v in (res.get('checks') or {}).items() if not v] or res}")
                return JSONResponse({"error": "payment refused by the market", "market": res}, status_code=402)
            s["status"] = "completed"
            s["order"] = {"status": "confirmed", "mandate_receipt": res["mandate_receipt"]}
            if self.md:
                await asyncio.to_thread(refresh_inventory, self.market, self.md)
            mr = res["mandate_receipt"]
            log(f"[acp] settled {mr.get('name') or s['line_items'][0]['id']} at {s['totals'][0]['amount']}, "
                f"ledger #{res['ledger']}" + (f", unit {mr['unit']} grade {mr.get('grade')}" if mr.get("unit") else ""))
            return JSONResponse(s)
        return JSONResponse({"error": f"session is {s['status']}"}, status_code=409)

    def pick_unit(self, rec, acceptable):
        """Round 3: the unit to ship. The seller's choice ("unit") if it is in stock; else the in-stock unit of a
        fitting SKU that expires first (oldest first for goods). Units held by an open checkout are skipped.
        The stock is read from .market/inventory.json when it is under INV_FRESH s old (the kit rewrites it every 10 s
        and after each sale); the market's /api/inventory only when it is older (11/17 11:1x: that call timed out at
        30 s on ~60 checkouts in 20 min while the file stayed fresh, and our settles fell from 35 to 5 per 10 min)."""
        inv = None
        if self.md:
            p = Path(self.md) / "inventory.json"
            with contextlib.suppress(OSError, ValueError):
                if time.time() - p.stat().st_mtime < INV_FRESH:
                    inv = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(inv, dict) or "units" not in inv:
            r = self.market.call("GET", "/api/inventory", "shop")
            if r.status_code != 200:
                return None, f"inventory: {r.status_code}"
            inv = r.json()
        if inv.get("round", 0) < ECON_ROUND:
            return False, None                          # no stock before Round 3
        held = {s["unit"] for s in self.sessions.values() if s.get("unit") and s["status"] == "ready_for_payment"}
        units = [u for u in inv["units"] if u["unit"] not in held and u["grade"] != "expired"]
        want = rec.get("item")
        fits = lambda u: (u["sku"] in acceptable if acceptable else
                          u["sku"] == want or (u.get("category") or "").split(">")[-1] == want)
        if rec.get("unit"):
            u = next((u for u in units if u["unit"] == rec["unit"]), None)
            return (u, None) if u and fits(u) else (None, f"unit {rec['unit']} is not in stock for this order")
        u = min((u for u in units if fits(u)), default=None,
                key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]))
        return (u, None) if u else (None, f"no {want} in stock")

    async def create(self, b, public):
        rec = b.get("receipt") if isinstance(b.get("receipt"), dict) else {}
        items = b.get("line_items") or [{}]
        acceptable = b.get("acceptable") if isinstance(b.get("acceptable"), list) else None
        if not (rec.get("sig_seller") and verify_seller(self.pub, rec)):
            return JSONResponse({"error": "the receipt is not signed by this shop"}, status_code=400)
        if items[0].get("id") != rec.get("item") or type(rec.get("price")) is not int:
            return JSONResponse({"error": "line_items do not match the receipt"}, status_code=400)
        if any(x.get("task") == rec["task"] for x in self.sessions.values()):
            return JSONResponse({"error": "this receipt already has a checkout session"}, status_code=409)
        # unit, cert, claim and warranty_hours are not under sig_seller: take them from what the seller sent,
        # never from the buyer's copy
        own = self.terms.get(rec["task"]) or {}
        rec = dict({k: rec.get(k) for k in RECEIPT_KEYS + ("sig_seller",)},
                   **{k: own[k] for k in ("unit", "cert", "claim", "warranty_hours") if k in own})
        u, problem = await asyncio.to_thread(self.pick_unit, rec, acceptable)
        if problem:
            log(f"[acp] checkout refused: {problem}")
            return JSONResponse({"error": problem}, status_code=409)
        sid = "cs_" + os.urandom(8).hex()
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
            "checkout_jwt": jwt_sign(self.key, co)}
        while len(self.sessions) > 500:
            self.sessions.popitem(last=False)
        return JSONResponse(s, status_code=201)


# Buyers whose payments this shop refuses (the user, 11/16 18:0x): two buyers' review hooks rate our paid deals far
# below their own reviews of the same deals - one gives every shop 2 stars, one gave us 5 stars until 16:40 and 1 star
# since (31 of 37, 4.07 at other shops), at ~30 an hour, while the market's rules let a hook print any stars. Stars
# count only for paid deals, and the shop learns who the buyer is only here, from the order in the public ledger.
# The file is read on every checkout, so the list changes without a restart; a lookup that fails lets the sale go.
BLOCKED_BUYERS = Path(__file__).resolve().parent / "blocked_buyers_r3.json"


ORDER_BUYER = collections.OrderedDict()                 # order id -> buyer, from the ledger's order entries
_ORDER_POLL = {"started": False, "last": None}


def _poll_orders(base, every=3.0, keep=20_000):
    """Background: read the public ledger's new entries every few seconds into ORDER_BUYER (a ledger call takes
    0.4-1 s, too slow to make inside each checkout; an order settles a median 12 s after it is placed)."""
    import threading

    def run():
        c = httpx.Client(timeout=10)
        while True:
            try:
                last = _ORDER_POLL["last"]
                rows = c.get(f"{base}/api/ledger", params={"after": -500 if last is None else last}).json()
                rows = rows if isinstance(rows, list) else []
                for e in rows:
                    d = e.get("data") or {}
                    if e.get("kind") == "order" and d.get("order"):
                        ORDER_BUYER[d["order"]] = d.get("buyer")
                    _ORDER_POLL["last"] = max(_ORDER_POLL["last"] or 0, e.get("seq", 0))
                while len(ORDER_BUYER) > keep:
                    ORDER_BUYER.popitem(last=False)
                if len(rows) >= 500:
                    continue                                 # behind: read the next page now
            except Exception:
                pass
            time.sleep(every)
    threading.Thread(target=run, daemon=True, name="order-buyers").start()


def blocked_buyer(market, order, wait=6.0):
    """The buyer of `order` when it is on BLOCKED_BUYERS, else None. Looks the order up in ORDER_BUYER, kept by a
    background reader of the public ledger; waits up to `wait` s for an order not read yet, then lets the sale go."""
    try:
        names = set(json.loads(BLOCKED_BUYERS.read_text(encoding="utf-8")).get("buyers") or [])
    except (OSError, ValueError, AttributeError):
        return None
    if not names or not order:
        return None
    if not _ORDER_POLL["started"]:
        _ORDER_POLL["started"] = True
        _poll_orders(getattr(market, "base", "https://market.ourolabs.ai"))
    t_end = time.time() + wait
    while order not in ORDER_BUYER and time.time() < t_end:
        time.sleep(0.5)
    b = ORDER_BUYER.get(order)
    return b if b in names else None


def check_accept(o, co, accept):
    """Round 3: the unit in the checkout against the buyer's accept policy. Returns the reason to refuse, or None."""
    cert = co.get("cert")
    ok_cert = False
    if cert is not None:
        try:
            body = cert["cert"]
            Ed25519PublicKey.from_public_bytes(base64.b64decode(o["market_pubkey"])).verify(
                base64.b64decode(cert["sig"]), canon(body))
            ok_cert = body.get("unit") == co.get("unit") and body.get("sku") == co.get("item")
        except (InvalidSignature, KeyError, ValueError, TypeError):
            pass
        if not ok_cert:
            return "the unit certificate is not the market's certificate of this unit"
    if not isinstance(accept, dict) or not accept:
        return None
    body = cert["cert"] if ok_cert else {}
    hours = (body["expires_at"] - time.time()) / 3600 if body.get("expires_at") else None
    grade = body.get("grade")
    if hours is not None and o.get("shelf_hours"):
        left = hours / o["shelf_hours"]
        grade = "A" if left >= FOOD_GRADE_CUTS[0] else "B" if left >= FOOD_GRADE_CUTS[1] else "C"
    if accept.get("cert") and not ok_cert:
        return "no certificate, and the policy asks for one"
    if isinstance(accept.get("min_hours_left"), (int, float)) and o.get("kind") == "food":
        if hours is None:
            return "no certificate to show the shelf life"
        if hours < accept["min_hours_left"]:
            return f"{hours:.2f} h of shelf life left, the policy asks for {accept['min_hours_left']}"
    if isinstance(accept.get("grades"), list) and accept["grades"]:
        if grade is None:
            return "no certificate to show the grade"
        if grade not in accept["grades"]:
            return f"grade {grade}, the policy accepts {accept['grades']}"
    if isinstance(accept.get("warranty_hours"), (int, float)) and o.get("kind") == "goods":
        if (co.get("warranty_hours") or 0) < accept["warranty_hours"]:
            return f"warranty {co.get('warranty_hours') or 0} h, the policy asks for {accept['warranty_hours']}"
    return None


def acp_pay(o, shop_url, shop_pub, receipt, buyer_key, accept=None, why=None):
    """Round 3, buyer side: open an ACP checkout on the shop, check it against the agreed receipt, the open
    mandates and the buyer's accept policy, sign the closed AP2 mandates, and complete.
    Returns the market's mandate receipt or None; when it does not pay, the reason is appended to `why`."""
    def log(msg):
        globals()["log"](msg)
        if why is not None and msg.startswith("[acp]") and " paid " not in msg and " ships unit " not in msg:
            why.append(msg[6:])
    c = httpx.Client(timeout=60)
    try:
        r = c.get(f"{shop_url}/.well-known/acp.json")
        if r.status_code != 200:
            log(f"[acp] {shop_url} serves no /.well-known/acp.json ({r.status_code}); this deal cannot settle")
            return None
        base = r.json()["endpoint"].rstrip("/")
        if urlparse(base).hostname != urlparse(shop_url).hostname:
            log(f"[acp] the ACP endpoint {base} is not on the shop's host; not paying")
            return None
        create = {"line_items": [{"id": receipt["item"], "quantity": 1}], "receipt": receipt}
        if o.get("acceptable"):
            create["acceptable"] = o["acceptable"]
        r = c.post(f"{base}/checkout_sessions", json=create)
        if r.status_code not in (200, 201):
            log(f"[acp] checkout refused: {r.status_code} {r.text[:200]}")
            return None
        s = r.json()
        co = jwt_payload(s.get("checkout_jwt", ""), shop_pub)
        budget = next(x["max"] for x in o["mandates"]["payment"]["mandate"]["constraints"] if x["type"] == "payment.amount_range")
        item_ok = co and (co.get("item") == receipt["item"] or co.get("item") in (o.get("acceptable") or []))
        if not item_ok or (co.get("price"), co.get("task")) != (receipt["price"], receipt["task"]):
            log("[acp] the checkout does not match the agreed receipt (or is not signed by the shop); not paying")
            return None
        if co["price"] > budget:
            log(f"[acp] checkout total {co['price']} is above the mandate's limit; not paying")
            return None
        refuse = check_accept(o, co, accept) if o.get("acceptable") else None
        if refuse:
            log(f"[acp] not paying: {refuse}")
            with contextlib.suppress(httpx.HTTPError):
                c.post(f"{base}/checkout_sessions/{s['id']}/cancel", json={})
            return None
        if co.get("unit"):
            log(f"[acp] checkout ships unit {co['unit']}"
                + (f", claim {co['claim']}" if co.get("claim") else "")
                + (f", warranty {co['warranty_hours']} h" if co.get("warranty_hours") else "")
                + (", certificate attached" if co.get("cert") else ", no certificate"))
        h = b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
        opens = {k: hashlib.sha256(canon(v["mandate"])).hexdigest() for k, v in o["mandates"].items()}
        cm = {"vct": "mandate.checkout.1", "order": o["order"], "open_mandate": opens["checkout"],
              "checkout_jwt": s["checkout_jwt"], "checkout_hash": h}
        pm = {"vct": "mandate.payment.1", "order": o["order"], "open_mandate": opens["payment"], "payee": shop_url,
              "amount": co["price"], "currency": "credits", "checkout_hash": h}
        sign = lambda m: {"mandate": m, "sig": base64.b64encode(buyer_key.sign(canon(m))).decode()}
        r = c.post(f"{base}/checkout_sessions/{s['id']}/complete",
                   json={"payment_data": {"order": o["order"], "checkout_mandate": sign(cm), "payment_mandate": sign(pm)}})
        if r.status_code != 200:
            log(f"[acp] payment not completed: {r.status_code} {r.text[:300]}")
            return None
        mr = r.json()["order"]["mandate_receipt"]
        got = ""
        if mr.get("unit"):
            got = f": {mr.get('name')}, grade {mr.get('grade')}"
            if mr.get("expires_at"):
                got += f", {(mr['expires_at'] - time.time()) / 3600:.1f} h of shelf life left"
            if mr.get("claim_honest") is False:
                got += f" (the shop claimed {mr.get('claim')})"
        log(f"[acp] paid {co['price']} through {base}, settled at ledger #{mr['ledger']}{got}")
        return mr
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log(f"[acp] payment failed: {type(e).__name__}: {e}")
        return None
    finally:
        c.close()


# --- acp.json tool buyer (week-08 optional HW): the buyer reads the shop's acp.json, makes tools, checks out with them

PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.:~-]+$")
JSON_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def schema_errors(schema, value, at="$"):
    """The subset of JSON Schema an acp.json needs: type, required, properties, additionalProperties, items, minItems,
    enum, minimum. Returns a list of problems (empty: valid)."""
    if not isinstance(schema, dict):
        return []
    errs = []
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        def is_type(t):
            if t == "integer":
                return type(value) is int
            if t == "number":
                return type(value) in (int, float)
            return t in JSON_TYPES and type(value) is JSON_TYPES[t]
        if not any(is_type(t) for t in types):
            return [f"{at} is {type(value).__name__}, not {'/'.join(types)}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{at} is not one of {schema['enum']}")
    if "minimum" in schema and type(value) in (int, float) and value < schema["minimum"]:
        errs.append(f"{at} is below {schema['minimum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        errs += [f"{at}.{k} is missing" for k in schema.get("required") or [] if k not in value]
        for k, v in value.items():
            if k in props:
                errs += schema_errors(props[k], v, f"{at}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{at}.{k} is not allowed")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{at} has fewer than {schema['minItems']} items")
        for i, v in enumerate(value):
            errs += schema_errors(schema.get("items"), v, f"{at}[{i}]")
    return errs


def input_schema(declared, path):
    """A tool's input as JSON Schema. The kit's acp.json writes input as {name: "short description"}; a shop may
    also give a JSON Schema ({"type": "object", ...}). Path parameters ({id}) become required string properties."""
    if isinstance(declared, dict) and declared.get("type") == "object":
        schema = json.loads(json.dumps(declared))
    else:
        declared = declared if isinstance(declared, dict) else {}
        schema = {"type": "object", "required": list(declared),
                  "properties": {k: {"description": str(v)} for k, v in declared.items()}}
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    for p in PATH_PARAM.findall(path):
        schema["properties"].setdefault(p, {"type": "string", "description": f"{p} in the path"})
        if p not in schema["required"]:
            schema["required"].append(p)
    return schema


class AcpTool:
    """One tool made from an acp.json entry: a definition a model (or the kit) can read, and the HTTP call behind it."""

    def __init__(self, base, entry):
        self.name, self.method = str(entry["name"]), str(entry.get("method", "POST")).upper()
        self.path = str(entry["path"])
        if (self.method not in ("GET", "POST") or not self.path.startswith("/") or "//" in self.path
                or ".." in self.path or "?" in self.path or "#" in self.path):
            raise ValueError(f"tool {self.name}: {self.method} {self.path} is not a path under the ACP endpoint")
        self.base = base
        self.params = PATH_PARAM.findall(self.path)
        self.schema = input_schema(entry.get("input_schema", entry.get("input")), self.path)
        self.description = entry.get("description") or f"{self.method} {self.path} on the shop's ACP endpoint"

    def spec(self):
        """Tool definition in the shape the course's model APIs take (name, description, input_schema)."""
        return {"name": self.name, "description": self.description, "input_schema": self.schema}

    def __call__(self, client, **args):
        errs = schema_errors(self.schema, args)
        if errs:
            raise ValueError(f"tool {self.name}: arguments do not fit its schema: {'; '.join(errs[:3])}")
        path = self.path
        for p in self.params:
            v = str(args.pop(p))
            if not SAFE_SEGMENT.match(v):
                raise ValueError(f"tool {self.name}: {p}={v!r} is not a safe path segment")
            path = path.replace("{" + p + "}", v)
        if self.method == "GET":
            return client.get(self.base + path)
        return client.post(self.base + path, json=args)


def acp_tools(card, shop_url):
    """The tools in a shop's acp.json, by name. Raises ValueError when the card cannot give a checkout."""
    base = str(card["endpoint"]).rstrip("/")
    if urlparse(base).hostname != urlparse(shop_url).hostname:
        raise ValueError(f"the ACP endpoint {base} is not on the shop's host")
    tools = {}
    for entry in card.get("tools") or []:
        t = AcpTool(base, entry)
        tools[t.name] = t
    return base, tools


def pick_tool(tools, name, method, tail):
    """The tool by its ACP name, or else the one whose method and path say the same thing."""
    if name in tools:
        return tools[name]
    return next((t for t in tools.values() if t.method == method and re.sub(PATH_PARAM, "{}", t.path).endswith(tail)), None)


class BeforeSession(Exception):
    """The tool checkout failed before a checkout session existed: the fixed acp_pay can still try."""


def acp_pay_tools(o, shop_url, shop_pub, receipt, buyer_key, accept=None, why=None):
    """acp_pay done with tools made from the shop's acp.json. Same checks as acp_pay (signed checkout that matches the
    receipt, total within the open payment mandate's limit). If it fails before a session exists, the fixed acp_pay
    runs instead; if it fails after, it never pays twice: one fixed complete on the same session at most.
    Kit v4: the order's acceptable SKUs and the buyer's accept policy are checked as acp_pay checks them, and the
    reason for not paying goes to `why` (payments.jsonl)."""
    try:
        return _acp_pay_tools(o, shop_url, shop_pub, receipt, buyer_key, accept, why)
    except BeforeSession as e:
        log(f"[acp-tools] {e}; falling back to the kit's fixed checkout")
        return acp_pay(o, shop_url, shop_pub, receipt, buyer_key, accept, why)


def _acp_pay_tools(o, shop_url, shop_pub, receipt, buyer_key, accept=None, why=None):
    def log(msg):
        globals()["log"](msg)
        if why is not None and msg.startswith("[acp-tools]") and " paid " not in msg and " tools from " not in msg:
            why.append(msg[12:])
    c = httpx.Client(timeout=60)
    sid = cancel = None
    try:
        try:
            r = c.get(f"{shop_url}/.well-known/acp.json")
            if r.status_code != 200:
                raise BeforeSession(f"{shop_url} serves no /.well-known/acp.json ({r.status_code})")
            base, tools = acp_tools(r.json(), shop_url)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise BeforeSession(f"cannot make tools from {shop_url}'s acp.json: {type(e).__name__}: {e}")
        create = pick_tool(tools, "create_checkout_session", "POST", "/checkout_sessions")
        get = pick_tool(tools, "get_checkout_session", "GET", "/checkout_sessions/{}")
        complete = pick_tool(tools, "complete_checkout_session", "POST", "/checkout_sessions/{}/complete")
        cancel = pick_tool(tools, "cancel_checkout_session", "POST", "/checkout_sessions/{}/cancel")
        if create is None:
            raise BeforeSession(f"acp.json has no checkout-creating tool (tools: {sorted(tools)})")
        log(f"[acp-tools] {len(tools)} tools from {base}/: {', '.join(sorted(tools))}")

        try:
            args = {"line_items": [{"id": receipt["item"], "quantity": 1}], "receipt": receipt}
            if o.get("acceptable"):
                args["acceptable"] = o["acceptable"]
            r = create(c, **args)
        except (httpx.HTTPError, ValueError) as e:
            raise BeforeSession(f"{create.name} failed: {type(e).__name__}: {e}")
        if r.status_code not in (200, 201):
            raise BeforeSession(f"{create.name} refused: {r.status_code} {r.text[:200]}")
        s = r.json()
        sid = s.get("id")

        # from here a session exists on the shop: the fixed acp_pay would only get "already has a checkout session"
        co = jwt_payload(s.get("checkout_jwt", ""), shop_pub)
        budget = next(x["max"] for x in o["mandates"]["payment"]["mandate"]["constraints"] if x["type"] == "payment.amount_range")
        item_ok = co and (co.get("item") == receipt["item"] or co.get("item") in (o.get("acceptable") or []))
        if not item_ok or (co.get("price"), co.get("task")) != (receipt["price"], receipt["task"]):
            log("[acp-tools] the checkout does not match the agreed receipt (or is not signed by the shop); not paying")
            return _cancel(c, cancel, sid)
        if co["price"] > budget:
            log(f"[acp-tools] checkout total {co['price']} is above the mandate's limit; not paying")
            return _cancel(c, cancel, sid)
        refuse = check_accept(o, co, accept) if o.get("acceptable") else None
        if refuse:
            log(f"[acp-tools] not paying: {refuse}")
            return _cancel(c, cancel, sid)
        if get is not None and sid:                     # read the session back before paying (status, same checkout)
            try:
                g = get(c, id=sid)
                # only a clear sign stops the payment: shops built without the kit name their statuses their own way
                # or leave the jwt out of GET, and the fixed acp_pay would have paid them
                gs = g.json() if g.status_code == 200 else {}
                if gs.get("status") in ("completed", "canceled") or gs.get("checkout_jwt") not in (None, s["checkout_jwt"]):
                    log(f"[acp-tools] {get.name} shows the session changed ({gs.get('status')}); not paying")
                    return _cancel(c, cancel, sid)
            except (httpx.HTTPError, ValueError) as e:
                log(f"[acp-tools] {get.name} failed ({type(e).__name__}: {e}); paying on the created session")

        h = b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
        opens = {k: hashlib.sha256(canon(v["mandate"])).hexdigest() for k, v in o["mandates"].items()}
        cm = {"vct": "mandate.checkout.1", "order": o["order"], "open_mandate": opens["checkout"],
              "checkout_jwt": s["checkout_jwt"], "checkout_hash": h}
        pm = {"vct": "mandate.payment.1", "order": o["order"], "open_mandate": opens["payment"], "payee": shop_url,
              "amount": co["price"], "currency": "credits", "checkout_hash": h}
        sign = lambda m: {"mandate": m, "sig": base64.b64encode(buyer_key.sign(canon(m))).decode()}
        payment_data = {"order": o["order"], "checkout_mandate": sign(cm), "payment_mandate": sign(pm)}
        try:
            if complete is None:
                raise ValueError("acp.json has no completing tool")
            r = complete(c, id=sid, payment_data=payment_data)
            how = complete.name
        except ValueError as e:                         # the tool refused our arguments: nothing was sent yet
            log(f"[acp-tools] {e}; completing with the kit's fixed request on the same session")
            r = c.post(f"{base}/checkout_sessions/{sid}/complete", json={"payment_data": payment_data})
            how = "fixed complete"
        if r.status_code != 200:
            log(f"[acp-tools] payment not completed ({how}): {r.status_code} {r.text[:300]}")
            return None
        mr = r.json()["order"]["mandate_receipt"]
        log(f"[acp-tools] paid {co['price']} with {create.name} -> {how}, settled at ledger #{mr['ledger']}")
        return mr
    except BeforeSession:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration) as e:
        log(f"[acp-tools] payment failed{f' on session {sid}' if sid else ''}: {type(e).__name__}: {e}")
        return None
    finally:
        c.close()


def _cancel(c, cancel, sid):
    if cancel is not None and sid:
        try:
            r = cancel(c, id=sid)
            log(f"[acp-tools] {cancel.name} {sid}: {r.status_code}")
        except (httpx.HTTPError, ValueError) as e:
            log(f"[acp-tools] {cancel.name} failed: {type(e).__name__}: {e}")
    return None


def cmd_acp_tools(a):
    """Prints the tools the buyer makes from a shop's acp.json (no checkout)."""
    shop = a.shop.rstrip("/")
    card = httpx.get(f"{shop}/.well-known/acp.json", timeout=30).json()
    base, tools = acp_tools(card, shop)
    print(json.dumps({"endpoint": base, "tools": [t.spec() for t in tools.values()]}, ensure_ascii=False, indent=1))


def receipt_from_task(task):
    """The receipt the shop put in the task's artifact, in the market's field names."""
    for art in task.get("artifacts") or []:
        for part in art.get("parts") or []:
            data = part.get("data")
            if isinstance(data, dict) and "price" in data:
                rec = {"shop": data.get("shop", data.get("seller")), "item": data.get("item"),
                       "price": data.get("price"), "task": data.get("task", task.get("id"))}
                if data.get("sig_seller"):
                    rec["sig_seller"] = data["sig_seller"]
                return rec
    return None


# --- market

class Market:
    def __init__(self, base, owner=None):
        self.base = base.rstrip("/")
        self.owner = owner
        self.c = httpx.Client(timeout=httpx.Timeout(30, connect=10), transport=httpx.HTTPTransport(retries=3))
        self.token = {}
        self.joined = {}                                # role -> (name, key, url), to join again after a reset

    def join(self, role, name, key, url=None, tries=6):
        """Join (again). Waits out a restarting market; a refusal (4xx) raises SystemExit with the market's reason."""
        ts = int(time.time())                           # proves the key: the market lets only its holder join again
        body = {"name": name, "role": role, "pubkey": pub_b64(key), "url": url, "ts": ts,
                "sig": base64.b64encode(key.sign(canon({"name": name, "role": role, "url": url, "ts": ts}))).decode()}
        if self.owner:
            body["owner"] = self.owner
        for i in range(tries):
            try:
                r = self.c.post(f"{self.base}/api/join", json=body)
                if r.status_code == 200:
                    self.token[role] = r.json()["token"]
                    self.joined[role] = (name, key, url)
                    return r.json()
                if r.status_code < 500 and r.status_code != 429:
                    break
            except RETRY as e:
                r = e
            log(f"[market] join as {role}: {getattr(r, 'status_code', type(r).__name__)}, trying again")
            time.sleep(min(10 * 2 ** i, 120))
        raise SystemExit(f"[market] join as {role} failed: {getattr(r, 'status_code', '')} {getattr(r, 'text', r)}")

    def call(self, method, path, role="buyer", **kw):
        return self.c.request(method, f"{self.base}{path}",
                              headers={"Authorization": f"Bearer {self.token[role]}"}, **kw)


def wait_card(base, seconds=60):
    for _ in range(seconds * 2):
        try:
            if httpx.get(base + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def open_tunnel(port):
    proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    found = []

    def read():                                     # reads until cloudflared exits, so its pipe never fills
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not found:
                found.append(m.group())
    threading.Thread(target=read, daemon=True).start()
    deadline = time.time() + 60
    while time.time() < deadline and not found and proc.poll() is None:
        time.sleep(0.2)
    if found:
        return proc, found[0]
    stop(proc)
    raise RuntimeError("cloudflared did not print a trycloudflare URL within 60 s")


# --- one order

def run_buyer(cmd, cwd):
    lines = []
    proc = start(cmd, cwd)
    t = pump(proc, "[buyer]", lines)
    try:
        proc.wait(timeout=BUYER_TIMEOUT)
    except subprocess.TimeoutExpired:
        log(f"[kit] buyer still running after {BUYER_TIMEOUT} s, stopped")
    finally:
        stop(proc)                                  # also on Ctrl-C, so no buyer is left running
    t.join(timeout=5)
    return lines


def last_json(lines):
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def post_result(market, body):
    """Retry only when the result surely did not arrive (no connection, or the market restarting behind its proxy)."""
    for i in range(5):
        try:
            r = market.call("POST", "/api/results", json=body)
            if r.status_code not in (502, 503, 504):
                return r
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            r = e
        log(f"[market] result not delivered ({getattr(r, 'status_code', type(r).__name__)}), trying again")
        time.sleep(5 * (i + 1))
    if isinstance(r, Exception):
        raise r
    return r


def handle_order(o, agent, d, market, proxy, own_shop, keys):
    rnd = o["round"]
    acceptable = o.get("acceptable") or []
    log(f"[order {o['order']}] {o['item']}, budget {o['budget']}, round {rnd}"
        + (f", use within {o['use_within_hours']} h" if o.get("use_within_hours") else ""))
    shops = [s for s in market.call("GET", "/api/shops").json()["shops"] if s["url"] != own_shop]
    if not shops:
        log("[kit] no shops in the market yet, waiting")
        time.sleep(15)                                  # the same order comes back on the next request
        return None
    pubkeys = {s["url"]: s["pubkey"] for s in shops}
    order_file = d / ".market" / "order.json"
    order_file.parent.mkdir(exist_ok=True)
    order_file.write_text(json.dumps({k: v for k, v in o.items() if k not in ("mandates", "nonce")},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    if acceptable:                                      # Round 3: what each shop has that fits this order
        names = catalog_names(market, d)
        for s in shops:
            s["offers"] = [{"sku": k, "name": names.get(k, k), "qty": n} for k, n in (s.get("stock") or {}).items()
                           if k in acceptable]
    accept = None

    if rnd >= 1 and "market_buyer" in agent:
        shops_file = d / ".market" / "shops.json"
        shops_file.write_text(json.dumps([{k: s[k] for k in ("name", "url", "reviews", "offers") if k in s} for s in shops],
                                         ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"[kit] {len(shops)} shops in {shops_file.relative_to(d)}, running market_buyer")
        lines = run_buyer(fill(agent["market_buyer"]["cmd"], shops=shops_file, item=o["item"], budget=o["budget"],
                               order=order_file), d)
        out = last_json(lines)
        if not out or "shop" not in out:
            log("[kit] market_buyer printed no result line {\"shop\": ..., \"receipt\": ..., \"stars\": ..., \"review\": ...}; "
                "closing the order as no deal")
            post_result(market, {"order": o["order"], "shop": "", "receipt": None})
            return None
        shop_url = str(out["shop"]).rstrip("/")
        receipt = out.get("receipt")
        if isinstance(receipt, dict) and "seller" in receipt and "shop" not in receipt:
            receipt["shop"] = receipt.pop("seller")
        stars, review, accept = out.get("stars"), out.get("review"), out.get("accept")
    else:
        stocked = [s for s in shops if s.get("offers")]
        shop = random.choice(stocked or shops)
        shop_url = shop["url"]
        proxy.target(shop_url)
        log(f"[kit] shop for this order: {shop['name']} {shop_url}")
        run_buyer(fill(agent["buyer"]["cmd"], url=proxy.public, item=o["item"], budget=o["budget"]), d)
        done = [t for t in proxy.seen if t.get("status", {}).get("state") == "TASK_STATE_COMPLETED"]
        receipt = receipt_from_task(done[-1]) if done else None
        stars, review = None, None
        log(f"[kit] seen through the kit: {len(proxy.seen)} task updates, "
            f"last state {proxy.seen[-1]['status']['state'] if proxy.seen else 'none'}")

    if isinstance(receipt, dict) and isinstance(receipt.get("price"), float) and receipt["price"].is_integer():
        receipt["price"] = int(receipt["price"])     # A2A data Parts carry numbers as doubles: 120 arrives as 120.0
    body = {"order": o["order"], "shop": shop_url, "receipt": receipt, "stars": stars, "review": review}
    if isinstance(receipt, dict):
        if not receipt.get("sig_seller"):
            log("[sign] receipt has no sig_seller")
        elif shop_url not in pubkeys:
            log(f"[sign] {shop_url} is not a registered shop")
        elif not verify_seller(pubkeys[shop_url], receipt):
            log("[sign] sig_seller does not verify with the shop's registered key")
        elif (receipt.get("item") != o["item"] and receipt.get("item") not in acceptable) \
                or not isinstance(receipt.get("price"), int) or receipt["price"] > o["budget"]:
            log(f"[sign] receipt {receipt.get('item')} at {receipt.get('price')} does not fit the order, not signing")
        else:
            signed = {k: receipt[k] for k in RECEIPT_KEYS}
            body["sig_buyer"] = base64.b64encode(keys["buyer"].sign(canon(dict(signed, order=o["order"])))).decode()
            log("[sign] sig_seller ok, sig_buyer added")
            if rnd >= 3 and o.get("mandates"):
                why = []
                mr = CHECKOUT(o, shop_url, pubkeys[shop_url], receipt, keys["buyer"], accept, why)
                body["mandate_receipt"] = mr
                with open(d / ".market" / "payments.jsonl", "a", encoding="utf-8") as f:   # the buyer reads what happened
                    f.write(json.dumps({"order": o["order"], "shop": shop_url, "item": receipt.get("item"),
                                        "price": receipt.get("price"), "paid": bool(mr),
                                        **({"ledger": mr.get("ledger")} if mr else {"reason": "; ".join(why) or "unknown"})},
                                       ensure_ascii=False) + "\n")
                if mr and "review" in agent:            # the buyer sees what it got, then reviews
                    deal_file = d / ".market" / "deal.json"
                    deal_file.write_text(json.dumps({"order": {k: v for k, v in o.items() if k not in ("mandates", "nonce")},
                                                     "receipt": receipt, "settled": mr}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
                    rv = last_json(run_buyer(fill(agent["review"]["cmd"], deal=deal_file), d)) or {}
                    if isinstance(rv.get("stars"), int):
                        body["stars"], body["review"] = rv["stars"], rv.get("review")
    r = post_result(market, body)
    if r.status_code != 200:
        log(f"[market] result refused: {r.status_code} {r.text}")
        return None
    res = r.json()
    failed = [k for k, v in res["checks"].items() if not v]
    deal = f"{receipt.get('item')} at {receipt.get('price')}" if isinstance(receipt, dict) else "no deal"
    log(f"[market] ledger #{res['ledger']}: {deal}, stars {body['stars']}, verified {res['verified']}"
        + (f" (failed: {', '.join(failed)})" if receipt and failed else "")
        + f", review {'accepted' if res['review_accepted'] else 'not shown'}")
    return res


# --- Round 3: .market/ files, restock, forum outbox

CATALOG = {}


def catalog_names(market, d):
    """sku -> name, from the market's catalog (fetched once, also written to .market/catalog.json)."""
    if not CATALOG:
        try:
            r = market.c.get(f"{market.base}/api/catalog")
            if r.status_code == 200:
                for x in r.json():
                    CATALOG[x["sku"]] = x
                (d / ".market").mkdir(exist_ok=True)
                (d / ".market" / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        except RETRY:
            pass
    return {k: v["name"] for k, v in CATALOG.items()}


def write_json(path, data):
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")    # one per thread: the restock and sale paths write too
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)                           # a reader never sees half a file


def default_restock(inv, demand):
    """The kit's restock when agent.json has none: when stock runs low, a few units of random SKUs in random
    categories, spending at most a tenth of the balance on each. It knows nothing about what sells. Not clever."""
    if len(inv["units"]) >= RESTOCK_LOW or not demand or (inv.get("balance") or 0) <= 0:
        return []
    picks = random.sample(demand, k=min(3, len(demand)))
    buy = []
    for p in picks:
        skus = [s for s, x in CATALOG.items() if x["category"] == p["category"]]
        if not skus:
            continue
        sku = random.choice(skus)
        qty = min(2, int(inv["balance"] * 0.1 / (CATALOG[sku]["list_price"] * 0.75)))
        if qty >= 1:
            buy.append({"sku": sku, "qty": qty})
    return buy


def sync_loop(a, agent, d, market, state):
    """Round 3: refresh .market/ (inventory and wallet for a shop, catalog, demand, forum), run the restock,
    and send what the agents wrote to .market/outbox.jsonl. Never raises."""
    md = d / ".market"
    md.mkdir(exist_ok=True)
    last_restock, last_forum, forum = 0, 0, []
    while True:
        time.sleep(SYNC_EVERY)
        try:
            catalog_names(market, d)
            shop = "shop" in market.token
            inv = None
            if shop:
                r = market.call("GET", "/api/inventory", "shop")
                if r.status_code == 200:
                    inv = r.json()
                    write_json(md / "inventory.json", inv)
                    state["round"] = inv.get("round", 0)
            if time.time() - last_forum > 30:
                last_forum = time.time()
                r = market.c.get(f"{market.base}/api/forum", params={"limit": 100})
                if r.status_code == 200:
                    forum = r.json()
                    write_json(md / "forum.json", forum)
                r = market.c.get(f"{market.base}/api/demand")
                if r.status_code == 200:
                    state["demand"] = r.json()
                    write_json(md / "demand.json", state["demand"])
            outbox(market, md)
            if "buyer" in market.token:
                broken_news(market, md, state)
            if shop and inv and inv.get("round", 0) >= ECON_ROUND and time.time() - last_restock > RESTOCK_EVERY \
                    and not (state.get("restocking") and state["restocking"].is_alive()):
                last_restock = time.time()                # its own thread: supply waits must not stall the files
                state["restocking"] = threading.Thread(target=restock, daemon=True,
                                                       args=(agent, d, market, md, inv, state.get("demand") or []))
                state["restocking"].start()
        except Exception as e:
            log(f"[kit] sync: {type(e).__name__}: {e}")


def refresh_inventory(market, md):
    """Rewrite .market/inventory.json now (after a supply or a sale), not at the next sync."""
    with contextlib.suppress(httpx.HTTPError, ValueError, OSError):
        r = market.call("GET", "/api/inventory", "shop")
        if r.status_code == 200:
            write_json(md / "inventory.json", r.json())


def bankrupt(market):
    r = market.call("POST", "/api/bankruptcy", "shop")
    if r.status_code == 200:
        j = r.json()
        log(f"[bankrupt] stock seized ({j['seized_units']} units), balance back to {j['balance']},"
            f" {j['left']} filings left; the {j['given']} given counts against profit")
    else:
        log(f"[bankrupt] refused: {r.status_code} {r.text[:200]}")
    return r.status_code == 200


def restock(agent, d, market, md, inv, demand):
    """The agent's restock command (it may also print "bankrupt": true), else the default. Without a command the
    kit files for bankruptcy by itself when the shop is below the line and has nothing left to sell."""
    stuck = (inv.get("balance") or 0) < inv.get("bankrupt_line", 0) and (inv.get("bankruptcies_left") or 0) > 0
    if "restock" in agent:
        out = last_json(run_buyer(fill(agent["restock"]["cmd"], state=md), d)) or {}
        if out.get("bankrupt") is True and stuck and bankrupt(market):
            return
        buy = out.get("buy") if isinstance(out.get("buy"), list) else []
    else:
        if stuck and not inv["units"] and bankrupt(market):
            return
        buy = default_restock(inv, demand)
    for b in buy[:10]:
        if not isinstance(b, dict):
            continue
        for _ in range(3):
            r = market.call("POST", "/api/supply", "shop", json={"sku": b.get("sku"), "qty": b.get("qty")})
            if r.status_code == 429:
                time.sleep(min(float(r.json().get("wait", 10)), 30))
                continue
            break
        if r.status_code == 200:
            j = r.json()
            refresh_inventory(market, md)
            log(f"[supply] {b.get('qty')} x {CATALOG.get(b.get('sku'), {}).get('name', b.get('sku'))} for {j['total']},"
                f" balance {j['balance']}")
        else:
            log(f"[supply] {b} refused: {r.status_code} {r.text[:200]}")
            if r.status_code == 402:
                break


def broken_news(market, md, state):
    """Round 3: goods this buyer bought that broke, one line each in .market/broken.jsonl: the item's name and when
    it broke, nothing else. Which purchase it was, and from which shop, is for the agent's own records to answer."""
    me = market.joined.get("buyer", (None,))[0]
    if "ledger_seq" not in state:                       # start from now; earlier news is in the ledger
        r = market.c.get(f"{market.base}/api/ledger", params={"after": -1})
        state["ledger_seq"] = r.json()[-1]["seq"] if r.status_code == 200 and r.json() else 0
        return
    while True:
        r = market.c.get(f"{market.base}/api/ledger", params={"after": state["ledger_seq"]})
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            return
        state["ledger_seq"] = rows[-1]["seq"]
        for e in rows:
            d = e["data"]
            if e["kind"] != "broken" or d.get("buyer") != me:
                continue
            news = {"name": CATALOG.get(d.get("item"), {}).get("name") or d.get("item"), "broke_at": int(e["ts"])}
            with open(md / "broken.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(news, ensure_ascii=False) + "\n")
            log(f"[broken] {news['name']} broke")
        if len(rows) < 500:
            return


def outbox(market, md):
    """Each line of .market/outbox.jsonl: {"post": text, "refs": [..], "thread": id, "as": "shop"|"buyer"}
    or {"vote": post id, "value": 1|-1, "as": ...}. Sent lines move to outbox.sent.jsonl with the market's answer."""
    box = md / "outbox.jsonl"
    if not box.exists() or box.stat().st_size == 0:
        return
    taken = md / "outbox.taking"
    os.replace(box, taken)
    keep, sent = [], []
    for line in taken.read_text(encoding="utf-8").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            sent.append({"line": line[:200], "error": "not JSON"})
            continue
        role = m.get("as") if m.get("as") in market.token else next(iter(market.token), None)
        if role is None:
            keep.append(line)
            continue
        if "vote" in m:
            r = market.call("POST", "/api/forum/vote", role, json={"post": m["vote"], "value": m.get("value")})
        else:
            body = {"text": m.get("post"), "refs": m.get("refs") or [], "thread": m.get("thread")}
            r = market.call("POST", "/api/forum", role, json=body)
            if r.status_code == 429:                    # one post per 5 minutes: keep it for later
                keep.append(line)
                continue
        sent.append({"sent": m, "status": r.status_code, "answer": r.text[:300]})
        log(f"[forum] {'vote' if 'vote' in m else 'post'} as {role}: {r.status_code}")
    with open(md / "outbox.sent.jsonl", "a", encoding="utf-8") as f:
        for x in sent:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    if keep:
        with open(box, "a", encoding="utf-8") as f:
            f.write("\n".join(keep) + "\n")
    taken.unlink()


# --- main

def cmd_join(a):
    global LOG_FILE
    d = Path(a.dir).resolve()
    agent = json.loads((d / "agent.json").read_text(encoding="utf-8"))
    (d / "logs").mkdir(exist_ok=True)
    LOG_FILE = d / "logs" / "market-kit.log"
    log(f"[kit] market_kit {KIT_VERSION} (week-08 acp.json tool buyer, checkout: {CHECKOUT.__name__}), {d}")
    ignore_pem(d)
    keys = {"shop": load_key(d / "shop_key.pem"), "buyer": load_key(d / "buyer_key.pem")}
    market = Market(a.market, a.owner)
    procs, own_shop, state = [], None, {}
    (d / ".market").mkdir(exist_ok=True)
    os.environ["MARKET_DIR"] = str(d / ".market")       # the seller and the buyer read inventory, catalog, forum here
    syncing = []
    try:
        if not a.buyer_only:
            if a.deployed:
                own_shop = a.deployed.rstrip("/")
            else:
                sport, pport = free_port(), a.port or free_port()
                procs.append(start(fill(agent["seller"]["cmd"], port=sport), d))
                pump(procs[-1], "[seller]")
                if not wait_card(f"http://127.0.0.1:{sport}"):
                    sys.exit("[kit] your seller did not serve /.well-known/agent-card.json within 60 s")
                proxy = Proxy(pport)
                proxy.target(f"http://127.0.0.1:{sport}")
                proxy.acp = AcpShop(keys["shop"], market, d / ".market")
                if a.tunnel:
                    try:
                        tp, url = open_tunnel(pport)
                    except RuntimeError as e:
                        sys.exit(f"[tunnel] {e}")
                    procs.append(tp)
                    proxy.public = url
                    log(f"[tunnel] {url}, waiting until it answers")
                    if not wait_card(url, 120):
                        sys.exit("[tunnel] the tunnel URL did not answer within 120 s; run the kit again")
                elif a.public_url:
                    proxy.public = a.public_url.rstrip("/")
                    log(f"[tunnel] {proxy.public} must forward to http://127.0.0.1:{pport}, waiting until it answers")
                    if not wait_card(proxy.public, 120):
                        sys.exit(f"[tunnel] {proxy.public} did not answer within 120 s; is your tunnel pointing at port {pport}?")
                else:
                    log("[kit] no --tunnel: only this computer can reach your shop")
                own_shop = proxy.public
                log(f"[shop] your seller on port {sport}; others reach it through the kit at {own_shop}")
            j = market.join("shop", a.name, keys["shop"], own_shop)
            log(f"[market] shop registered as {j['name']}, round {j['round']}")
            if not a.deployed:
                state["own_shop"] = own_shop
                threading.Thread(target=watchdog, args=(a, agent, d, market, keys, procs, proxy, sport, pport, state),
                                 daemon=True).start()
            syncing.append(threading.Thread(target=sync_loop, args=(a, agent, d, market, state), daemon=True))
            syncing[0].start()
        if a.shop_only:
            log("[kit] shop only; Ctrl-C to leave the market")
            while True:
                time.sleep(3600)
        j = market.join("buyer", a.name, keys["buyer"])
        log(f"[market] buyer registered as {j['name']}, round {j['round']}")
        if not syncing:                                 # buyer only: catalog, demand and forum, and the outbox
            threading.Thread(target=sync_loop, args=(a, agent, d, market, state), daemon=True).start()
        bproxy = Proxy(free_port(), same_host=True)
        n = 0
        while a.orders == 0 or n < a.orders:
            try:                                        # nothing in one order may end the kit
                r = market.call("POST", "/api/orders")
                if r.status_code == 429:
                    try:
                        wait = float(r.json().get("wait", 5))
                    except Exception:
                        wait = 5
                    time.sleep(min(max(wait, 1), 30))
                    continue
                if r.status_code == 401:                # the market forgot this buyer (e.g. a reset): join again
                    log("[market] buyer token refused, joining again")
                    try:
                        market.join("buyer", *market.joined["buyer"][:2])
                    except SystemExit as e:             # refused for good (e.g. no claim code): keep trying slowly
                        log(str(e))
                        time.sleep(300)
                    continue
                if r.status_code != 200:
                    log(f"[market] order failed: {r.status_code} {r.text[:200]}")
                    time.sleep(10)
                    continue
                handle_order(r.json(), agent, d, market, bproxy, state.get("own_shop", own_shop), keys)
                n += 1
            except Exception as e:
                log(f"[kit] order failed: {type(e).__name__}: {e}")
                time.sleep(15)
    except KeyboardInterrupt:
        log("[kit] leaving the market")
    finally:
        for p in procs:
            stop(p)


def card_ok(base):
    try:
        return httpx.get(base + "/.well-known/agent-card.json", timeout=10).status_code == 200
    except Exception:
        return False


def watchdog(a, agent, d, market, keys, procs, proxy, sport, pport, state, every=60):
    """Bring the shop back by itself: restart a crashed seller, and replace a dead quick tunnel
    (join again with the new URL; the market moves the shop's history to it)."""
    misses = 0
    while True:
        time.sleep(every)
        try:
            if procs[0].poll() is not None:
                log(f"[kit] your seller exited (code {procs[0].returncode}), starting it again")
                procs[0] = start(fill(agent["seller"]["cmd"], port=sport), d)
                pump(procs[0], "[seller]")
                wait_card(f"http://127.0.0.1:{sport}")
            if not a.tunnel:
                continue
            ok = procs[1].poll() is None and card_ok(proxy.public)
            misses = 0 if ok else misses + 1
            if misses < 3:
                continue
            log(f"[tunnel] {proxy.public} stopped answering, opening a new tunnel")
            stop(procs[1])
            procs[1], url = open_tunnel(pport)
            if not wait_card(url, 120):
                raise RuntimeError(f"the new tunnel {url} did not answer within 120 s")
            proxy.public = state["own_shop"] = url
            j = market.join("shop", a.name, keys["shop"], url)
            log(f"[market] shop registered again as {j['name']} at {url}")
            misses = 0
        except (Exception, SystemExit) as e:
            log(f"[kit] watchdog: {type(e).__name__}: {e}")


def cmd_starter_round3(a):
    """A Round 3 seller that sells from the kit's inventory, next to your files (nothing is overwritten)."""
    src, d = Path(__file__).resolve().parent / "starter_round3", Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    if (d / "seller_r3.py").exists():
        sys.exit(f"{d / 'seller_r3.py'} already exists; the starter does not overwrite files")
    (d / "seller_r3.py").write_text((src / "seller.py").read_text(encoding="utf-8"), encoding="utf-8")
    if not (d / "harness.py").exists():
        (d / "harness.py").write_text((src / "harness.py").read_text(encoding="utf-8"), encoding="utf-8")
    py = f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)
    print(f"seller_r3.py written to {d}. To use it, set the seller in agent.json to:")
    print(json.dumps({"seller": {"cmd": f"{py} seller_r3.py --port {{port}}"}}))


def cmd_starter(a):
    if a.round3:
        return cmd_starter_round3(a)
    src, d = Path(__file__).resolve().parent / "starter_week07", Path(a.dir)
    files = ["seller.py", "buyer.py", "harness.py", "agent.json"]
    taken = [f for f in files if (d / f).exists()]
    if taken:
        sys.exit(f"{d} already has {', '.join(taken)}; the starter does not overwrite files")
    d.mkdir(parents=True, exist_ok=True)
    for f in files[:3]:
        (d / f).write_text((src / f).read_text(encoding="utf-8"), encoding="utf-8")
    (d / "seller_r3.py").write_text((src.parent / "starter_round3" / "seller.py").read_text(encoding="utf-8"),
                                    encoding="utf-8")
    py = f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)
    (d / "agent.json").write_text(json.dumps({
        "harness": "starter: claude -p --model haiku (set HARNESS_CMD to use another harness)",
        "seller": {"cmd": f"{py} seller_r3.py --port {{port}}"},
        "buyer": {"cmd": f"{py} buyer.py --url {{url}} --item {{item}} --budget {{budget}}"},
    }, indent=2) + "\n", encoding="utf-8")
    print(f"starter written to {d}: {', '.join(files)}, seller_r3.py")
    print("agent.json runs seller_r3.py, which signs and sells from the Round 3 stock; seller.py is the week-07 "
          "starter (unsigned, LAB 2). There is no market_buyer yet (LAB 1)")


def utf8_everywhere():
    """Windows consoles and files default to a legacy code page; shop names and reviews are UTF-8."""
    os.environ.setdefault("PYTHONUTF8", "1")                    # the seller, the buyer and their harness calls
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    utf8_everywhere()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("join")
    j.add_argument("--market", required=True)
    j.add_argument("--name", required=True)
    j.add_argument("--dir", default=".")
    g = j.add_mutually_exclusive_group()
    g.add_argument("--tunnel", action="store_true")
    g.add_argument("--public-url")
    g.add_argument("--deployed")
    j.add_argument("--port", type=int, default=0, help="the kit's shop port, for your own tunnel with --public-url")
    j.add_argument("--buyer-only", action="store_true")
    j.add_argument("--shop-only", action="store_true")
    j.add_argument("--orders", type=int, default=0, help="stop after this many orders (0: keep going)")
    j.add_argument("--owner", default=os.environ.get("MARKET_OWNER"),
                   help="claim code from the market's join page; links your agents to your GitHub account")
    st = sub.add_parser("starter", help="write an unsigned week-06 shop and buyer into --dir")
    st.add_argument("--dir", default=".")
    j.add_argument("--fixed-checkout", action="store_true",
                   help="pay with the kit's fixed ACP checkout instead of tools made from the shop's acp.json")
    st.add_argument("--round3", action="store_true", help="write seller_r3.py, a seller that sells from the Round 3 stock")
    t = sub.add_parser("acp-tools", help="print the tools the buyer makes from a shop's acp.json")
    t.add_argument("--shop", required=True, help="the shop's card URL")
    a = ap.parse_args()
    if a.cmd == "starter":
        return cmd_starter(a)
    if a.cmd == "acp-tools":
        return cmd_acp_tools(a)
    global CHECKOUT
    CHECKOUT = acp_pay if a.fixed_checkout else acp_pay_tools
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))     # stop the seller and the tunnel on kill as well
    cmd_join(a)


if __name__ == "__main__":
    main()
