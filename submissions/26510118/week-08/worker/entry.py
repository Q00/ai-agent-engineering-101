"""Week 08 -- the shop and the buyer on Cloudflare Workers, with Round 3 payments.

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
    payments   Round 3 counts only settled deals, and the kit that settles them
               for other students does not run here, so acp.py does it: the
               shop serves /.well-known/acp.json and /acp/checkout_sessions and
               forwards completed checkouts to /api/settle under its own shop
               token (GET /shop/log lists the settlements); the buyer pays its
               Round 3 deals with closed mandates inside the open ones.
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

import acp
import buyer_core
import catalog
import market_anchor
import shop_core
from negotiation_memory import Journal
from shop_data import ASKS, EPISODES, ITEMS, LEVELS, MEMORY, ROUND3_ASKS

catalog.use(ITEMS)

STATE = {"input-required": "TASK_STATE_INPUT_REQUIRED", "completed": "TASK_STATE_COMPLETED",
         "rejected": "TASK_STATE_REJECTED"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}
KEEP_TASKS = 3600         # finished tasks stay readable (GetTask) this long
RECEIPT_KEYS = ("shop", "item", "price", "task")
WINDOW = 50               # seconds of order-taking per cron tick (the cron fires every minute)
# Said after every deal, because it is true here: buyers' review models repeat
# what a shop says (week 8, EP 6), and LLM buyers pick shops by receipts and
# signatures (99.3% of one buyer's reasons). No digits: buyers read numbers as prices.
DEAL_NOTE = (" The receipt is signed with this shop's key, and the shop serves an ACP checkout at "
             "/.well-known/acp.json, so the deal can settle at the market.")


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


def kv_table(sql):
    """A (k, v) table in an object's SQLite: kv(k) reads, kv(k, v) writes."""
    sql.exec("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")

    def kv(k: str, v: str | None = None):
        if v is not None:
            sql.exec("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", k, v)
            return v
        found = rows(sql.exec("SELECT v FROM kv WHERE k = ?", k))
        return found[0]["v"] if found else None
    return kv


# ------------------------------------------------------------ the market

class MarketClient:
    """The market's API in one role (buyer or shop). It joins the way kit v3
    does -- a signature over a fresh timestamp proves the key -- when the object
    has no token yet or the market refused it, and keeps the token in kv."""

    def __init__(self, env, kv, key, role: str, url: str | None = None):
        self.env, self.kv, self.key, self.role, self.url = env, kv, key, role, url
        self.base = env_str(env, "MARKET_URL").rstrip("/")

    async def join(self) -> str:
        body = acp.signed_join(self.key, env_str(self.env, "MARKET_NAME"), self.role, self.url,
                               env_str(self.env, "MARKET_OWNER") or None)
        r = await fetch(f"{self.base}/api/join", js({"method": "POST", "body": json.dumps(body),
                                                     "headers": {"Content-Type": "application/json"},
                                                     "signal": AbortSignal.timeout(30000)}))
        data = py(await r.json())
        if r.status != 200 or "token" not in data:
            raise RuntimeError(f"join as {self.role} failed: {r.status} {str(data)[:200]}")
        print(f"[market] {self.role} registered as {data.get('name')}, round {data.get('round')}", flush=True)
        return self.kv("token", data["token"])

    async def call(self, method: str, path: str, body=None):
        """(status, json) from the market, joining first or again when the token is missing or refused."""
        for attempt in (1, 2):
            token = self.kv("token") or await self.join()
            init = {"method": method, "signal": AbortSignal.timeout(30000),
                    "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}}
            if body is not None:
                init["body"] = json.dumps(body)
            r = await fetch(f"{self.base}{path}", js(init))
            if r.status in (401, 403) and attempt == 1:
                self.kv("token", "")
                continue
            try:
                return r.status, py(await r.json())
            except Exception:                           # noqa: BLE001  (not JSON)
                return r.status, {"text": str(await r.text())[:200]}
        return 401, {}


class FetchHTTP:
    """acp.pay's transport on fetch: (status, json or None)."""

    async def _go(self, url: str, init: dict):
        r = await fetch(url, js(dict(init, signal=AbortSignal.timeout(30000))))
        try:
            return r.status, py(await r.json())
        except Exception:                               # noqa: BLE001  (not JSON: the caller says why it stops)
            return r.status, None

    async def get(self, url: str):
        return await self._go(url, {"method": "GET"})

    async def post(self, url: str, body: dict):
        return await self._go(url, {"method": "POST", "body": json.dumps(body),
                                    "headers": {"Content-Type": "application/json"}})


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
        self.shop = shop_core.Shop(self.ask, journal, key=load_key(self.env, "SHOP_KEY"), asks=self.ask_for,
                                   market_mean=self.market_mean, levels=lambda i: tuple(LEVELS.get(i, ())),
                                   deal_note=DEAL_NOTE)
        self.open: dict[str, dict] = {}
        self.stats = {"t": 0.0, "items": {}, "round": None}
        # Round 3: checkout sessions live in memory like tasks; each settlement
        # (or refusal) is one stored row, the shop's half of market-kit.log.
        self.sql.exec("CREATE TABLE IF NOT EXISTS settles (t REAL, j TEXT)")
        self.public = env_str(self.env, "SHOP_URL").rstrip("/")
        self.checkout = self.client = None
        if self.shop.key is not None:
            self.client = MarketClient(self.env, kv_table(self.sql), self.shop.key, "shop", self.public)
            self.checkout = acp.Checkout(self.shop.key, lambda item: (catalog.by_id(item) or {}).get("reserve"),
                                         self.settle, log=lambda line: print(line, flush=True))

    async def ask(self, prompt: str) -> str:
        return await ask_model(self.env, prompt)

    async def market_mean(self, item_id: str):
        if time.time() - self.stats["t"] > market_anchor.TTL:
            self.stats["t"] = time.time()               # one try per TTL, even when it fails
            try:
                r = await fetch(market_anchor.STATS, js({"signal": AbortSignal.timeout(3000)}))
                data = py(await r.json())
                self.stats["items"] = market_anchor.items_from(data)
                if data.get("round") != self.stats.get("round"):
                    print(f"[seller] market round {data.get('round')}; first asks now "
                          f"{ {i: self._first_ask(i, data.get('round')) for i in ASKS} }", flush=True)
                self.stats["round"] = data.get("round")
            except Exception:                           # noqa: BLE001  (no anchor this time)
                pass
        return market_anchor.mean_of(self.stats["items"], item_id)

    @staticmethod
    def _first_ask(item_id: str, rnd):
        """asks.json's first ask, or from Round 3 on its "round3" one: an opening
        sale while buyers whose shops cannot settle look for new ones."""
        if (rnd or 0) >= 3 and item_id in ROUND3_ASKS:
            return ROUND3_ASKS[item_id]
        return ASKS.get(item_id)

    def ask_for(self, item_id: str):
        """The round comes with the stats the shop reads for its market anchor
        (every TTL), so Round 3 switches the asks without a deploy."""
        return self._first_ask(item_id, self.stats.get("round"))

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

    async def settle(self, payload: dict):
        return await self.client.call("POST", "/api/settle", payload)

    async def acp(self, method: str, path: str, body: str) -> str:
        """One ACP checkout call -> {"status", "body"} as JSON."""
        self._setup()
        if self.checkout is None:
            return json.dumps({"status": 503, "body": {"error": "The shop is not configured yet (no signing key)."}})
        status, out = await self.checkout.handle(method, path, body, self.public)
        rec = out.pop("settle", None) if isinstance(out, dict) else None
        if rec is not None:
            self.sql.exec("INSERT INTO settles (t, j) VALUES (?, ?)", time.time(), json.dumps(dict(rec, status=status)))
        return json.dumps({"status": status, "body": out})

    async def journal_dump(self, since: float, limit: int) -> str:
        """The negotiation journal from `since`, oldest first: what reprice_r3.py
        reprices from once the ledger stops showing budgets. Read from memory
        (the journal loads once per object start), so it costs no SQL rows.
        An episode holds the item, my asks, the buyer's offers, the outcome
        and the price; no reserve and no buyer names."""
        self._setup()
        eps = [e for e in self.shop.journal.episodes if e.get("ts", 0) >= since]
        return json.dumps({"total": len(self.shop.journal.episodes), "since": since,
                           "episodes": eps[:max(1, min(int(limit), 50000))]})

    async def settle_log(self, n: int, since: float | None = None) -> str:
        """Settlements newest first, in the kit's log words, with totals; with
        `since`, every one from that time on, oldest first (export_logs.py)."""
        self._setup()
        every = [dict(json.loads(r["j"]), t=r["t"]) for r in rows(self.sql.exec("SELECT t, j FROM settles ORDER BY t"))]
        for s in every:
            s["line"] = (f"[acp] settled {s['item']} at {s['amount']}, ledger #{s.get('ledger')}" if s["status"] == 200
                         else f"[acp] settle refused: {s.get('failed') or s['status']}")
        ok_ = [s for s in every if s["status"] == 200]
        refused: dict[str, int] = {}
        for s in every:
            for f in (s.get("failed") or []) if s["status"] != 200 else []:
                refused[f] = refused.get(f, 0) + 1
        recent = [s for s in every if s["t"] >= since] if since is not None else every[::-1][:max(1, min(int(n), 200))]
        return json.dumps({"settled": len(ok_), "refused": len(every) - len(ok_), "refused_checks": refused,
                           "credits": sum(s["amount"] for s in ok_), "recent": recent}, indent=1)


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
        self.sql.exec("CREATE TABLE IF NOT EXISTS orders (t REAL, j TEXT)")
        self.mem = {r["name"]: json.loads(r["j"]) for r in rows(self.sql.exec("SELECT name, j FROM memory"))}
        if not self.mem:                                # first start: the PC buyer's memory, Round 0 lessons included
            self.mem = json.loads(json.dumps(MEMORY))
            for name in self.mem:
                self._remember(name)
        self.key = load_key(self.env, "BUYER_KEY")
        self.client = MarketClient(self.env, kv_table(self.sql), self.key, "buyer") if self.key else None
        self.busy = False
        self.ready = True

    def _remember(self, name: str) -> None:
        self.sql.exec("INSERT OR REPLACE INTO memory (name, j) VALUES (?, ?)", name, json.dumps(self.mem[name]))

    async def ask(self, prompt: str) -> str:
        return await ask_model(self.env, prompt)

    async def market(self, method: str, path: str, body=None):
        return await self.client.call(method, path, body)

    def _verify_seller(self, pubkey_b64: str, receipt: dict) -> bool:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            body = {k: receipt.get(k) for k in RECEIPT_KEYS}
            Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(
                base64.b64decode(receipt["sig_seller"]), canon(body))
            return True
        except Exception:                               # noqa: BLE001
            return False

    @staticmethod
    def _normalized(receipt):
        """The receipt in the market's field names and types, in place."""
        if isinstance(receipt, dict) and "seller" in receipt and "shop" not in receipt:
            receipt["shop"] = receipt.pop("seller")
        if isinstance(receipt, dict) and isinstance(receipt.get("price"), float) and receipt["price"].is_integer():
            receipt["price"] = int(receipt["price"])    # data Parts carry numbers as doubles
        return receipt

    def _sign_buyer(self, o: dict, budget: int, shop_url: str, receipt, pubkeys: dict) -> str | None:
        """sig_buyer when the receipt is the registered shop's and fits the order, as the kit checks it."""
        say = buyer_core.log
        if not isinstance(receipt, dict):
            return None
        if not receipt.get("sig_seller"):
            say("[sign] receipt has no sig_seller")
        elif shop_url not in pubkeys:
            say(f"[sign] {shop_url} is not a registered shop")
        elif not self._verify_seller(pubkeys[shop_url], receipt):
            say("[sign] sig_seller does not verify with the shop's registered key")
        elif receipt.get("item") != o["item"] or not isinstance(receipt.get("price"), int) or receipt["price"] > budget:
            say(f"[sign] receipt {receipt.get('item')} at {receipt.get('price')} does not fit the order, not signing")
        else:
            signed = dict({k: receipt[k] for k in RECEIPT_KEYS}, order=o["order"])
            say("[sign] sig_seller ok, sig_buyer added")
            return base64.b64encode(self.key.sign(canon(signed))).decode()
        return None

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
        # Round 3: the budget is the open payment mandate's limit, the one the
        # market enforces at settlement; never more than the order says either.
        limit = acp.mandate_limit(o) if o.get("mandates") else None
        budget = o.get("budget") if o.get("budget") is not None else limit
        if limit is not None:
            budget = min(budget, limit)
        say(f"[order {o['order']}] {o['item']}, budget {budget}, round {o['round']}"
            + (f", mandate limit {limit}" if limit is not None else ""))
        status, data = await self.market("GET", "/api/shops")
        own = env_str(self.env, "SHOP_URL").rstrip("/")
        shops = [s for s in data.get("shops", []) if s["url"].rstrip("/") != own]
        pubkeys = {s["url"]: s["pubkey"] for s in shops}
        lst = [{"name": s["name"], "url": s["url"], "reviews": s["reviews"]} for s in shops]
        paid: dict = {}                                 # what the payment step adds to the result line

        async def settle(chosen: dict, r: dict):
            """The kit's checks and sig_buyer, then the Round 3 payment -- run
            inside shop_once, before the review, so the review can say a deal
            did not settle. -> (settled, why), (None, None) when nothing to pay."""
            shop_url, receipt = str(chosen["url"]).rstrip("/"), self._normalized(r.get("receipt"))
            sig = self._sign_buyer(o, budget, shop_url, receipt, pubkeys)
            if sig is None:
                return None, None
            paid["sig_buyer"] = sig
            if o["round"] < 3 or not o.get("mandates"):
                return None, None
            if r.get("honest") is False:                # never pay a receipt above the agreed price
                say("[acp] the receipt does not match the agreed price; not paying")
                return False, "wrong_receipt"
            mr, why = await acp.pay(FetchHTTP(), o, shop_url, pubkeys[shop_url], receipt, self.key, log=say)
            paid["mandate_receipt"] = mr
            return mr is not None, why

        out, changed = await buyer_core.shop_once(self.ask, FetchA2A(), lst, self.mem, o["item"], budget, settle)
        if changed:
            self._remember(changed)

        shop_url, receipt = str(out["shop"]).rstrip("/"), self._normalized(out.get("receipt"))
        body = dict({"order": o["order"], "shop": shop_url, "receipt": receipt,
                     "stars": out.get("stars"), "review": out.get("review")}, **paid)
        status, res = await self.market("POST", "/api/results", body)
        if status != 200:
            say(f"[market] result refused: {status} {str(res)[:200]}")
            res = {}
        deal = f"{receipt.get('item')} at {receipt.get('price')}" if isinstance(receipt, dict) else "no deal"
        if res:
            say(f"[market] ledger #{res.get('ledger')}: {deal}, stars {out.get('stars')}, verified {res.get('verified')}")
        self.sql.exec("INSERT INTO orders (t, j) VALUES (?, ?)", time.time(), json.dumps({
            "order": o["order"], "round": o["round"], "item": o["item"], "budget": budget, "shop": shop_url,
            "deal": deal, "settled": bool(body.get("mandate_receipt")),
            "stars": out.get("stars"), "ledger": res.get("ledger"), "verified": res.get("verified"),
            "log": [line for line in buyer_core._sink if ": stars " not in line][-60:]}))

    async def tick(self) -> str:
        """Take orders for about WINDOW seconds. A tick that finds the last one
        still running leaves it alone, so one order is never handled twice."""
        if env_str(self.env, "BUYER_ENABLED") != "1":
            return "disabled"                           # before _setup: a disabled buyer seeds nothing
        self._setup()
        if self.client is None:
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
            "round3_orders": sum(1 for _, o in every if (o.get("round") or 0) >= 3),
            "round3_deals": sum(1 for _, o in every if (o.get("round") or 0) >= 3 and o["deal"] != "no deal"),
            "settled": sum(1 for _, o in every if o.get("settled")),
        }
        recent = [{k: v for k, v in o.items() if k != "log"} for _, o in every[-20:][::-1]]
        return json.dumps({"enabled": env_str(self.env, "BUYER_ENABLED") == "1", "busy": self.busy,
                           "shops_remembered": len(self.mem), **totals, "recent": recent})

    async def order_log(self, n: int, since: float | None = None) -> str:
        """The last n orders with their log lines, newest first; with `since`,
        up to n orders from that time on, oldest first, each with its time --
        what export_logs.py pages through for the submission's log file."""
        self._setup()
        if since is None:
            return json.dumps([json.loads(r["j"]) for r in rows(
                self.sql.exec("SELECT j FROM orders ORDER BY t DESC LIMIT ?", max(1, min(int(n), 50))))], indent=1)
        return json.dumps([dict(json.loads(r["j"]), t=r["t"]) for r in rows(self.sql.exec(
            "SELECT t, j FROM orders WHERE t > ? ORDER BY t LIMIT ?", float(since), max(1, min(int(n), 200))))])


# ------------------------------------------------------------ the entry point

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        u = urlparse(request.url)
        shop = self.env.SHOP.get(self.env.SHOP.idFromName("shop"))
        if u.path == "/.well-known/acp.json":
            public = env_str(self.env, "SHOP_URL") or f"{u.scheme}://{u.netloc}"
            return Response(json.dumps(acp.descriptor(public)), headers={"content-type": "application/json"})
        if u.path.startswith("/acp/"):
            body = await request.text() if request.method == "POST" else ""
            out = json.loads(await shop.acp(request.method, u.path, body))
            return Response(json.dumps(out["body"]), status=out["status"], headers={"content-type": "application/json"})
        if request.method == "POST":
            return Response(await shop.rpc(await request.text()), headers={"content-type": "application/json"})
        if u.path == "/shop/log":
            q = parse_qs(u.query)
            n = q.get("n", ["20"])[0]
            try:
                since = float(q["since"][0]) if "since" in q else None
            except ValueError:
                return Response('{"error": "since is a number"}', status=400)
            return Response(await shop.settle_log(int(n) if n.isdigit() else 20, since),
                            headers={"content-type": "application/json"})
        if u.path == "/shop/journal":
            q = parse_qs(u.query)
            try:
                since, limit = float(q.get("since", ["0"])[0]), int(q.get("limit", ["50000"])[0])
            except ValueError:
                return Response('{"error": "since and limit are numbers"}', status=400)
            return Response(await shop.journal_dump(since, limit), headers={"content-type": "application/json"})
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
            q = parse_qs(u.query)
            n = q.get("n", ["5"])[0]
            try:
                since = float(q["since"][0]) if "since" in q else None
            except ValueError:
                return Response('{"error": "since is a number"}', status=400)
            stub = self.env.BUYER.get(self.env.BUYER.idFromName("buyer"))
            return Response(await stub.order_log(int(n) if n.isdigit() else 5, since),
                            headers={"content-type": "application/json"})
        if u.path == "/buyer/status":
            stub = self.env.BUYER.get(self.env.BUYER.idFromName("buyer"))
            return Response(await stub.status(), headers={"content-type": "application/json"})
        return Response(f"{shop_core.NAME}: A2A JSON-RPC at /, card at /.well-known/agent-card.json\n")

    async def scheduled(self, controller, env, ctx):
        stub = self.env.BUYER.get(self.env.BUYER.idFromName("buyer"))
        print(f"[cron] buyer tick: {await stub.tick()}", flush=True)
