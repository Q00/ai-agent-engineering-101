"""Week 07 -- the shop and the buyer on Cloudflare Workers.

The decisions are shop_core and buyer_core, the modules the market test checks
on the PC (build.py copies them in). This file is what only Cloudflare has:

    harness    an HTTP model. The OpenAI API when the OPENAI_API_KEY secret
               and OPENAI_MODEL are set; Workers AI (CF_MODEL) when they are
               not or the call fails; and "" when both fail, which the cores
               answer with their rules. The reserve and the budget are code
               either way.
    shop       one Durable Object, "shop", holds every open negotiation and
               the journal in its SQLite storage. Consecutive messages of a
               task can arrive at different Cloudflare machines; they all meet
               the same object, so a deal cannot be half-remembered. A2A is
               JSON-RPC by hand -- SendMessage, GetTask, CancelTask -- in the
               JSON shapes the a2a SDK produces on the PC (captured and compared).
    buyer      what the kit does for the buyer, without the kit: every minute a
               cron trigger wakes the "buyer" Durable Object, which joins the
               market, takes orders for about 50 s, runs buyer_core on each,
               checks the shop's signature, adds sig_buyer and posts the result.
               Its memory lives in the object's storage.
    keys       secrets: SHOP_KEY and BUYER_KEY hold the .pem files whose public
               halves the kit registered; MARKET_OWNER is the GitHub claim code.
    catalogue  the market's three items only (shop_data, built from catalog.json).
"""
import asyncio
import base64
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from js import AbortSignal, Object, fetch
from pyodide.ffi import to_js
from workers import DurableObject, Response, WorkerEntrypoint

import buyer_core
import catalog
import market_anchor
import shop_core
from negotiation_memory import Journal
from shop_data import ASKS, EPISODES, ITEMS, MEMORY

catalog.use(ITEMS)

STATE = {"input-required": "TASK_STATE_INPUT_REQUIRED", "completed": "TASK_STATE_COMPLETED",
         "rejected": "TASK_STATE_REJECTED"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}
KEEP_TASKS = 3600         # finished tasks stay readable (GetTask) this long
RECEIPT_KEYS = ("shop", "item", "price", "task")
WINDOW = 50               # seconds of order-taking per cron tick (the cron fires every minute)


def js(obj):
    return to_js(obj, dict_converter=Object.fromEntries)


def py(obj):
    """A JS value as Python; the SDK sometimes converts already."""
    return obj.to_py() if hasattr(obj, "to_py") else obj


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ok(rid, result) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result})


def err(rid, code: int, message: str, reason: str | None = None) -> str:
    e = {"code": code, "message": message}
    if reason:
        e["data"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason,
                      "domain": "a2a-protocol.org", "metadata": {}}]
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": e})


def rows(cursor) -> list[dict]:
    """A SQL cursor's rows as dicts, whether the runtime hands back JS objects or Python ones."""
    return [py(r) for r in py(cursor.toArray())]


def env_str(env, name: str) -> str:
    v = getattr(env, name, None)
    return v if isinstance(v, str) else ""


def load_key(env, name: str):
    from cryptography.hazmat.primitives import serialization
    pem = env_str(env, name)
    return serialization.load_pem_private_key(pem.encode(), password=None) if pem else None


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------ the harness

async def ask_model(env, prompt: str) -> str:
    """One prompt in, one line out; "" when every model failed."""
    t0 = time.time()
    if env_str(env, "OPENAI_API_KEY") and env_str(env, "OPENAI_MODEL"):
        try:
            body = {"model": env_str(env, "OPENAI_MODEL"),
                    "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 400}
            body.update(json.loads(env_str(env, "OPENAI_EXTRA") or "{}"))
            r = await fetch("https://api.openai.com/v1/chat/completions", js({
                "method": "POST", "body": json.dumps(body), "signal": AbortSignal.timeout(20000),
                "headers": {"Authorization": f"Bearer {env_str(env, 'OPENAI_API_KEY')}",
                            "Content-Type": "application/json"}}))
            data = py(await r.json())
            out = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            if out:
                print(f"[harness] openai {time.time() - t0:.1f}s -> {out.strip()[:60]!r}", flush=True)
                return out.strip()
            print(f"[harness] openai gave nothing: {str(data.get('error') or data)[:160]}", flush=True)
        except Exception as e:                          # noqa: BLE001
            print(f"[harness] openai failed: {type(e).__name__}: {e}"[:200], flush=True)
    try:
        res = py(await env.AI.run(env_str(env, "CF_MODEL"), js(
            {"messages": [{"role": "user", "content": prompt}], "max_tokens": 160})))
        out = res.get("response") if isinstance(res, dict) else None
        print(f"[harness] workers-ai {time.time() - t0:.1f}s -> {str(out)[:60]!r}", flush=True)
        return "" if out is None else str(out).strip()
    except Exception as e:                              # noqa: BLE001  (quota spent, outage -> rules)
        print(f"[harness] workers-ai failed: {type(e).__name__}: {e}"[:200], flush=True)
        return ""


# ------------------------------------------------------------ the shop

class Shop(DurableObject):
    """Every negotiation of the shop, in one place."""

    def _setup(self) -> None:
        if getattr(self, "shop", None):
            return
        self.sql = self.ctx.storage.sql
        self.sql.exec("CREATE TABLE IF NOT EXISTS journal (j TEXT)")
        # Tasks live in this object's memory, not in SQL: one object serves
        # every message, so memory is already consistent, and writing every
        # message (and sweeping the table on every request) would spend the
        # free plan's daily rows in hours. Only the journal is stored.
        self.sql.exec("DROP TABLE IF EXISTS tasks")
        self.tasks: dict[str, dict] = {}                # task id -> {"task", "t"}; deals are in self.open
        episodes = [json.loads(r["j"]) for r in rows(self.sql.exec("SELECT j FROM journal"))]
        if not episodes:                                # first start: carry the PC's memory over
            for ep in EPISODES:
                self.sql.exec("INSERT INTO journal (j) VALUES (?)", json.dumps(ep))
            episodes = list(EPISODES)
        journal = Journal(episodes, lambda ep: self.sql.exec("INSERT INTO journal (j) VALUES (?)", json.dumps(ep)))
        self.shop = shop_core.Shop(self.ask, journal, key=load_key(self.env, "SHOP_KEY"), asks=ASKS.get,
                                   market_mean=self.market_mean)
        self.open: dict[str, dict] = {}
        self.stats = {"t": 0.0, "items": {}}

    async def ask(self, prompt: str) -> str:
        return await ask_model(self.env, prompt)

    async def market_mean(self, item_id: str):
        if time.time() - self.stats["t"] > market_anchor.TTL:
            self.stats["t"] = time.time()               # one try per TTL, even when it fails
            try:
                r = await fetch(market_anchor.STATS, js({"signal": AbortSignal.timeout(3000)}))
                self.stats["items"] = market_anchor.items_from(py(await r.json()))
            except Exception:                           # noqa: BLE001  (no anchor this time)
                pass
        return market_anchor.mean_of(self.stats["items"], item_id)

    def _save(self, task: dict, deal: dict | None) -> None:
        self.tasks[task["id"]] = {"task": task, "t": time.time()}

    def _load(self, tid: str):
        row = self.tasks.get(tid)
        return (row["task"], self.open.get(tid)) if row else (None, None)

    def _sweep(self) -> None:
        for tid in self.shop.stale(self.open):
            self.shop.walked(self.open.pop(tid))
        cutoff = time.time() - KEEP_TASKS
        for tid in [t for t, row in self.tasks.items() if row["t"] < cutoff]:
            del self.tasks[tid]

    async def rpc(self, body: str) -> str:
        self._setup()
        try:
            req = json.loads(body)
        except ValueError:
            return err(None, -32700, "Parse error")
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        self._sweep()
        if method == "SendMessage":
            return await self._send(rid, params)
        if method in ("GetTask", "CancelTask"):
            tid = params.get("id") or str(params.get("name", "")).split("/")[-1]
            task, deal = self._load(tid)
            if task is None:
                return err(rid, -32001, f"Task {tid} not found", "TASK_NOT_FOUND")
            if method == "CancelTask":
                if task["status"]["state"] in TERMINAL:
                    return err(rid, -32002, f"Task {tid} is in terminal state: {task['status']['state']}",
                               "TASK_NOT_CANCELABLE")
                if deal:
                    self.shop.walked(deal)
                self.open.pop(tid, None)
                if task["status"].get("message"):
                    task["history"].append(task["status"]["message"])
                task["status"] = {"state": "TASK_STATE_CANCELED", "timestamp": now()}
                self._save(task, None)
            return ok(rid, task)
        return err(rid, -32601, f"Method not found: {method}")

    async def _send(self, rid, params: dict) -> str:
        if self.shop.key is None:
            return err(rid, -32603, "The shop is not configured yet (no signing key).")
        m = params.get("message") or {}
        text = " ".join(p["text"] for p in m.get("parts") or [] if isinstance(p.get("text"), str))
        tid = m.get("taskId")
        if tid:
            task, deal = self._load(tid)
            if task is None:
                return err(rid, -32001, f"Task {tid} not found", "TASK_NOT_FOUND")
            if task["status"]["state"] in TERMINAL:
                return err(rid, -32004, f"Task {tid} is in terminal state: {task['status']['state']}",
                           "UNSUPPORTED_OPERATION")
        else:
            tid, deal = str(uuid.uuid4()), None
            task = {"id": tid, "contextId": m.get("contextId") or str(uuid.uuid4()), "status": {}, "history": []}
        if task["status"].get("message"):                # the previous answer becomes history
            task["history"].append(task["status"]["message"])
        task["history"].append({"messageId": m.get("messageId") or str(uuid.uuid4()),
                                "contextId": task["contextId"], "taskId": tid, "role": "ROLE_USER",
                                "parts": [{"text": text}]})

        reply, deal = await self.shop.handle(tid, text, deal)

        answer = {"messageId": str(uuid.uuid4()), "contextId": task["contextId"], "taskId": tid,
                  "role": "ROLE_AGENT", "parts": [{"text": reply["text"]}]}
        if reply.get("ask") is not None:
            answer["metadata"] = {"ask": int(reply["ask"])}
        task["status"] = {"state": STATE[reply["state"]], "message": answer, "timestamp": now()}
        if reply.get("receipt"):
            task["artifacts"] = [{"artifactId": str(uuid.uuid4()), "name": "receipt",
                                  "parts": [{"data": reply["receipt"]}]}]
        if deal is None:
            self.open.pop(tid, None)
        else:
            self.open[tid] = deal
        self._save(task, deal)
        return ok(rid, {"task": task})


# ------------------------------------------------------------ the buyer

class FetchA2A:
    """The A2A 1.0 JSON-RPC client buyer_core trades through, on fetch: the
    same calls and the same log lines as buyer.A2A on the PC."""

    def __init__(self):
        self.url = None

    async def open(self, base: str) -> dict:
        r = await fetch(base.rstrip("/") + "/.well-known/agent-card.json", js({"signal": AbortSignal.timeout(30000)}))
        card = py(await r.json())
        ifaces = card.get("supportedInterfaces") or [{}]
        iface = next((i for i in ifaces if i.get("protocolBinding") == "JSONRPC"), ifaces[0])
        self.url = iface.get("url") or base
        buyer_core.log(f"[buyer] card: {card.get('name')!r} via {iface.get('protocolBinding')} {self.url}")
        return card

    async def rpc(self, method: str, params: dict):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = await fetch(self.url, js({"method": "POST", "body": json.dumps(body),
                                      "headers": {"Content-Type": "application/json", "A2A-Version": "1.0"},
                                      "signal": AbortSignal.timeout(60000)}))
        data = py(await r.json())
        if "error" in data:
            buyer_core.log(f"[buyer] <- error {data['error'].get('code')} {str(data['error'].get('message'))[:120]}")
            return None
        return data["result"].get("task", data["result"])

    async def send(self, text: str, task_id: str | None = None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        buyer_core.log(f"[buyer] -> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        t = await self.rpc("SendMessage", {"message": msg})
        if t:
            buyer_core.log(f"[buyer] <- {buyer_core.state(t)} | {buyer_core.message_text(t)[:120]}")
        return t

    async def cancel(self, task_id: str):
        try:
            await self.rpc("CancelTask", {"name": f"tasks/{task_id}", "id": task_id})
        except Exception as e:                          # noqa: BLE001
            buyer_core.log(f"[buyer] cancel failed, stopping anyway: {e}")


class Buyer(DurableObject):
    """The kit's order loop for the buyer, and the buyer's memory."""

    def _setup(self) -> None:
        if getattr(self, "ready", False):
            return
        self.sql = self.ctx.storage.sql
        self.sql.exec("CREATE TABLE IF NOT EXISTS memory (name TEXT PRIMARY KEY, j TEXT)")
        self.sql.exec("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self.sql.exec("CREATE TABLE IF NOT EXISTS orders (t REAL, j TEXT)")
        self.mem = {r["name"]: json.loads(r["j"]) for r in rows(self.sql.exec("SELECT name, j FROM memory"))}
        if not self.mem:                                # first start: the PC buyer's memory, Round 0 lessons included
            self.mem = json.loads(json.dumps(MEMORY))
            for name in self.mem:
                self._remember(name)
        self.key = load_key(self.env, "BUYER_KEY")
        self.base = env_str(self.env, "MARKET_URL").rstrip("/")
        self.busy = False
        self.ready = True

    def _remember(self, name: str) -> None:
        self.sql.exec("INSERT OR REPLACE INTO memory (name, j) VALUES (?, ?)", name, json.dumps(self.mem[name]))

    def _kv(self, k: str, v: str | None = None):
        if v is not None:
            self.sql.exec("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", k, v)
            return v
        found = rows(self.sql.exec("SELECT v FROM kv WHERE k = ?", k))
        return found[0]["v"] if found else None

    async def ask(self, prompt: str) -> str:
        return await ask_model(self.env, prompt)

    async def join(self) -> str:
        from cryptography.hazmat.primitives import serialization
        pub = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        body = {"name": env_str(self.env, "MARKET_NAME"), "role": "buyer", "pubkey": base64.b64encode(pub).decode(),
                "url": None, "owner": env_str(self.env, "MARKET_OWNER") or None}
        r = await fetch(f"{self.base}/api/join", js({"method": "POST", "body": json.dumps(body),
                                                     "headers": {"Content-Type": "application/json"},
                                                     "signal": AbortSignal.timeout(30000)}))
        data = py(await r.json())
        if r.status != 200 or "token" not in data:
            raise RuntimeError(f"join as buyer failed: {r.status} {str(data)[:200]}")
        print(f"[market] buyer registered as {data.get('name')}, round {data.get('round')}", flush=True)
        return self._kv("token", data["token"])

    async def market(self, method: str, path: str, body=None):
        """(status, json) from the market, joining first or again when the token is missing or refused."""
        for attempt in (1, 2):
            token = self._kv("token") or await self.join()
            init = {"method": method, "signal": AbortSignal.timeout(30000),
                    "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}}
            if body is not None:
                init["body"] = json.dumps(body)
            r = await fetch(f"{self.base}{path}", js(init))
            if r.status in (401, 403) and attempt == 1:
                self._kv("token", "")
                continue
            try:
                return r.status, py(await r.json())
            except Exception:                           # noqa: BLE001  (not JSON)
                return r.status, {"text": str(await r.text())[:200]}
        return 401, {}

    def _verify_seller(self, pubkey_b64: str, receipt: dict) -> bool:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            body = {k: receipt.get(k) for k in RECEIPT_KEYS}
            Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(
                base64.b64decode(receipt["sig_seller"]), canon(body))
            return True
        except Exception:                               # noqa: BLE001
            return False

    async def handle(self, o: dict) -> None:
        """One order, as the kit's handle_order does it for Round 1 and later. Its
        log lines are kept with the order (GET /buyer/log), because a cron
        run's lines do not reach `wrangler tail` -- they stand in for the kit's
        market-kit.log."""
        buyer_core._sink = []
        try:
            await self._handle(o)
        finally:
            buyer_core._sink = None

    async def _handle(self, o: dict) -> None:
        say = buyer_core.log
        say(f"[order {o['order']}] {o['item']}, budget {o['budget']}, round {o['round']}")
        status, data = await self.market("GET", "/api/shops")
        own = env_str(self.env, "SHOP_URL").rstrip("/")
        shops = [s for s in data.get("shops", []) if s["url"].rstrip("/") != own]
        pubkeys = {s["url"]: s["pubkey"] for s in shops}
        lst = [{"name": s["name"], "url": s["url"], "reviews": s["reviews"]} for s in shops]
        out, changed = await buyer_core.shop_once(self.ask, FetchA2A(), lst, self.mem, o["item"], o["budget"])
        if changed:
            self._remember(changed)

        shop_url, receipt = str(out["shop"]).rstrip("/"), out.get("receipt")
        if isinstance(receipt, dict) and "seller" in receipt and "shop" not in receipt:
            receipt["shop"] = receipt.pop("seller")
        if isinstance(receipt, dict) and isinstance(receipt.get("price"), float) and receipt["price"].is_integer():
            receipt["price"] = int(receipt["price"])    # data Parts carry numbers as doubles
        body = {"order": o["order"], "shop": shop_url, "receipt": receipt,
                "stars": out.get("stars"), "review": out.get("review")}
        if isinstance(receipt, dict):
            if not receipt.get("sig_seller"):
                say("[sign] receipt has no sig_seller")
            elif shop_url not in pubkeys:
                say(f"[sign] {shop_url} is not a registered shop")
            elif not self._verify_seller(pubkeys[shop_url], receipt):
                say("[sign] sig_seller does not verify with the shop's registered key")
            elif receipt.get("item") != o["item"] or not isinstance(receipt.get("price"), int) \
                    or receipt["price"] > o["budget"]:
                say(f"[sign] receipt {receipt.get('item')} at {receipt.get('price')} does not fit the order, "
                    f"not signing")
            else:
                signed = dict({k: receipt[k] for k in RECEIPT_KEYS}, order=o["order"])
                body["sig_buyer"] = base64.b64encode(self.key.sign(canon(signed))).decode()
                say("[sign] sig_seller ok, sig_buyer added")
        status, res = await self.market("POST", "/api/results", body)
        if status != 200:
            say(f"[market] result refused: {status} {str(res)[:200]}")
            res = {}
        deal = f"{receipt.get('item')} at {receipt.get('price')}" if isinstance(receipt, dict) else "no deal"
        if res:
            say(f"[market] ledger #{res.get('ledger')}: {deal}, stars {out.get('stars')}, verified {res.get('verified')}")
        self.sql.exec("INSERT INTO orders (t, j) VALUES (?, ?)", time.time(), json.dumps({
            "order": o["order"], "item": o["item"], "budget": o["budget"], "shop": shop_url, "deal": deal,
            "stars": out.get("stars"), "ledger": res.get("ledger"), "verified": res.get("verified"),
            "log": [line for line in buyer_core._sink if ": stars " not in line][-60:]}))

    async def tick(self) -> str:
        """Take orders for about WINDOW seconds. A tick that finds the last one
        still running leaves it alone, so one order is never handled twice."""
        if env_str(self.env, "BUYER_ENABLED") != "1":
            return "disabled"                           # before _setup: a disabled buyer seeds nothing
        self._setup()
        if self.key is None:
            return "no BUYER_KEY"
        if self.busy:
            return "busy"
        self.busy, done, end = True, 0, time.time() + WINDOW
        try:
            while time.time() < end:
                status, o = await self.market("POST", "/api/orders")
                if status == 429:
                    wait = min(float(o.get("wait", 5)), 30.0)
                    if time.time() + wait >= end:
                        break
                    await asyncio.sleep(wait)
                    continue
                if status != 200:
                    print(f"[market] order failed: {status} {str(o)[:200]}", flush=True)
                    break
                try:
                    await self.handle(o)
                except Exception as e:                  # noqa: BLE001
                    print(f"[kit] order failed: {type(e).__name__}: {e}"[:200], flush=True)
                done += 1
        finally:
            self.busy = False
        return f"{done} order(s)"

    async def status(self) -> str:
        if env_str(self.env, "BUYER_ENABLED") != "1":
            return json.dumps({"enabled": False})
        self._setup()
        every = [(r["t"], json.loads(r["j"])) for r in rows(self.sql.exec("SELECT t, j FROM orders ORDER BY t"))]
        floors = buyer_core.FLOORS
        feasible = [o for _, o in every if o["budget"] >= floors.get(o["item"], 0)]
        logged = [o for _, o in every if o.get("log")]
        totals = {
            "orders": len(every), "since": every[0][0] if every else None, "until": every[-1][0] if every else None,
            "deals": sum(1 for _, o in every if o["deal"] != "no deal"),
            "verified": sum(1 for _, o in every if o.get("verified")),
            "feasible": len(feasible), "feasible_deals": sum(1 for o in feasible if o["deal"] != "no deal"),
            "logged": len(logged),
            "refused": sum(1 for o in logged if any("held" in l and "leaving" in l for l in o["log"])),
            "broke_off": sum(1 for o in logged if any("broke off" in l for l in o["log"])),
        }
        recent = [{k: v for k, v in o.items() if k != "log"} for _, o in every[-20:][::-1]]
        return json.dumps({"enabled": env_str(self.env, "BUYER_ENABLED") == "1", "busy": self.busy,
                           "shops_remembered": len(self.mem), **totals, "recent": recent})

    async def order_log(self, n: int) -> str:
        """The last n orders with their log lines, newest first."""
        self._setup()
        return json.dumps([json.loads(r["j"]) for r in rows(
            self.sql.exec("SELECT j FROM orders ORDER BY t DESC LIMIT ?", max(1, min(int(n), 50))))], indent=1)


# ------------------------------------------------------------ the entry point

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        u = urlparse(request.url)
        if request.method == "POST":
            stub = self.env.SHOP.get(self.env.SHOP.idFromName("shop"))
            return Response(await stub.rpc(await request.text()), headers={"content-type": "application/json"})
        if u.path == "/.well-known/agent-card.json":
            description, skill = shop_core.card_text(shop_core.NAME)
            card = {
                "name": shop_core.NAME, "description": description, "version": "1.1.0",
                "supportedInterfaces": [{"url": f"{u.scheme}://{u.netloc}/", "protocolBinding": "JSONRPC",
                                         "protocolVersion": "1.0"}],
                "capabilities": {"streaming": False},
                "defaultInputModes": ["text/plain"], "defaultOutputModes": ["application/json", "text/plain"],
                "skills": [{"id": "negotiate", "name": "Find an item and negotiate a purchase",
                            "description": skill,
                            "tags": ["shopping", "negotiation", "furniture"] + catalog.categories(),
                            "examples": ["I want to buy a lamp", "90"]}],
            }
            return Response(json.dumps(card), headers={"content-type": "application/json"})
        if u.path == "/buyer/log":
            n = parse_qs(u.query).get("n", ["5"])[0]
            stub = self.env.BUYER.get(self.env.BUYER.idFromName("buyer"))
            return Response(await stub.order_log(int(n) if n.isdigit() else 5),
                            headers={"content-type": "application/json"})
        if u.path == "/buyer/status":
            stub = self.env.BUYER.get(self.env.BUYER.idFromName("buyer"))
            return Response(await stub.status(), headers={"content-type": "application/json"})
        return Response(f"{shop_core.NAME}: A2A JSON-RPC at /, card at /.well-known/agent-card.json\n")

    async def scheduled(self, controller, env, ctx):
        stub = self.env.BUYER.get(self.env.BUYER.idFromName("buyer"))
        print(f"[cron] buyer tick: {await stub.tick()}", flush=True)
