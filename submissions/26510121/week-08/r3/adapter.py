"""Local v4 integration. Shared course scripts are imported read-only."""
import asyncio
import base64
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import time
from starlette.responses import JSONResponse
from .state import ROOT, Journal, atomic_json, policy


def load_kit():
    source = ROOT.parents[2] / "scripts" / "market_kit.py"
    spec = importlib.util.spec_from_file_location("r3_course_kit", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.KIT_VERSION != "4":
        raise RuntimeError("This adapter is verified for course kit v4 only")
    return module


def install(kit, journal=None, config=None):
    j = journal or Journal()
    cfg = config or policy()
    original_accept = kit.check_accept
    original_call = kit.Market.call
    original_order = kit.handle_order
    original_news = kit.broken_news
    original_outbox = kit.outbox
    BaseShop = kit.AcpShop

    def strict_accept(order, co, accept):
        if order.get("acceptable"):
            if not isinstance(accept, dict) or not accept.get("expected_sku"):
                return "exact SKU expectation is required by the R3 adapter"
            if co.get("item") != accept["expected_sku"]:
                return "checkout SKU differs from the requested SKU"
            if order.get("kind") == "food":
                required = order.get("use_within_hours", 0)
                if accept.get("min_hours_left", -1) < required:
                    return "food acceptance is weaker than the order use horizon"
        return original_accept(order, co, accept)

    class StrictShop(BaseShop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.create_lock = asyncio.Lock()

        def expire_sessions(self):
            now = time.time()
            for session in self.sessions.values():
                if session["status"] == "ready_for_payment" and now - session.get("r3_created", now) > cfg["session_ttl_seconds"]:
                    session["status"] = "canceled"

        def pick_unit(self, rec, acceptable):
            self.expire_sessions()
            catalog = j.snapshot("catalog.json", {})
            if rec.get("item") in catalog:
                acceptable = [rec["item"]] if not acceptable or rec["item"] in acceptable else []
                if not acceptable:
                    return None, "requested SKU is outside the order"
            return super().pick_unit(rec, acceptable)

        async def create(self, body, public):
            async with self.create_lock:
                response = await super().create(body, public)
                for session in self.sessions.values():
                    session.setdefault("r3_created", time.time())
                return response

        async def handle(self, req, path, body, public):
            self.expire_sessions()
            return await super().handle(req, path, body, public)

    def market_call(self, method, path, role="buyer", **kwargs):
        response = original_call(self, method, path, role=role, **kwargs)
        if response.status_code != 200:
            return response
        data = response.json()
        if path == "/api/shops" and method == "GET":
            registry = {s["url"].rstrip("/"): {k: s.get(k) for k in ("name", "pubkey", "owner")} for s in data["shops"]}
            atomic_json(j.md / "registry.json", registry)
        elif path == "/api/inventory" and method == "GET":
            accounting = {k: data.get(k) for k in ("balance", "start", "injected", "bankruptcies_left")}
            if accounting != j.get("accounting_snapshot"):
                j.add("accounting_snapshot", accounting, role="shop")
                j.put("accounting_snapshot", accounting)
        elif path == "/api/settle" and method == "POST" and role == "shop":
            mr = data.get("mandate_receipt")
            if mr:
                token = kwargs.get("json", {}).get("checkout_jwt", "")
                try:
                    key = kit.load_key(ROOT / "shop_key.pem")
                    co = kit.jwt_payload(token, kit.pub_b64(key)) or {}
                    record = {"task": co.get("task"), "unit": mr.get("unit"), "sku": mr.get("sku", co.get("item")),
                              "price": co.get("price", 0), "warranty_hours": co.get("warranty_hours", 0), "paid_at": time.time()}
                    j.add("shop_settled", record, eid="settlement:" + str(data.get("ledger")), role="shop")
                    j.release(co.get("task"))
                except (ValueError, OSError, KeyError):
                    j.add("attribution_error", {"ledger": data.get("ledger")}, role="shop")
        elif path == "/api/results" and method == "POST":
            submitted = kwargs.get("json", {})
            j.add("market_result", {"order": submitted.get("order"), "verified": data.get("verified"),
                                    "checks": data.get("checks"), "ledger": data.get("ledger")},
                  eid="result:" + str(submitted.get("order")), role="buyer")
        return response

    def order_handler(order, *args, **kwargs):
        os.environ["R3_STRICT_ADAPTER"] = "1"
        if order.get("round", 0) < 3:
            kit.log("[r3] Waiting for Round 3; no legacy order executed")
            time.sleep(10)
            return None
        return original_order(order, *args, **kwargs)

    def news(market, md, state):
        original_news(market, md, state)
        seq = j.get("public_ledger_seq", -500)
        for _ in range(4):
            r = market.c.get(f"{market.base}/api/ledger", params={"after": seq})
            if r.status_code != 200:
                break
            rows = r.json()
            if not rows:
                break
            for event in rows:
                if event.get("kind") == "settle":
                    j.add("public_settle", event["data"], eid="public:" + str(event["seq"]), ts=event.get("ts"))
                elif event.get("kind") in {"refund", "broken"}:
                    j.add("public_" + event["kind"], event["data"], eid="public:" + str(event["seq"]), ts=event.get("ts"))
            seq = rows[-1]["seq"]
            j.put("public_ledger_seq", seq)
            if len(rows) < 500:
                break
        j.reconcile()

    def outbox(market, md):
        original_outbox(market, md)
        if time.time() - j.get("board_checked_at", 0) > 60:
            with contextlib.suppress(Exception):
                response = market.c.get(f"{market.base}/api/board", timeout=5)
                if response.status_code == 200:
                    board = response.json()
                    if board.get("round") == 3:
                        atomic_json(j.md / "board.json", board)
                    j.put("board_checked_at", time.time())
        for key, payload in j.pending_posts():
            role = payload.get("as", "buyer")
            if role not in market.token:
                continue
            r = market.call("POST", "/api/forum", role,
                            json={"text": payload["post"], "refs": payload.get("refs", []), "thread": payload.get("thread")})
            if r.status_code == 429:
                break
            j.add("forum_delivery", {"key": key, "status": r.status_code, "role": role})
            if r.status_code in (200, 201) or 400 <= r.status_code < 500:
                j.sent_post(key)

    kit.check_accept = strict_accept
    kit.AcpShop = StrictShop
    kit.Market.call = market_call
    kit.handle_order = order_handler
    kit.broken_news = news
    kit.outbox = outbox
    os.environ["R3_STRICT_ADAPTER"] = "1"
    return kit
