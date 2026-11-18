#!/usr/bin/env python3
"""Local test of the acp.json tool buyer in market_kit.py (week-08 optional HW). No market, no network.

    python test_acp_tools.py

Serves the kit's AcpShop on 127.0.0.1 with a fake market whose /api/settle checks the buyer's signatures and the open
payment mandate's limit, then runs acp_pay_tools against it in each case below. Counts settle calls so a case that
must not pay (or must not pay twice) shows it.
"""
import base64
import json
import socket
import sys
import threading
import time

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import market_kit as mk
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

LINES = []
mk.log = lambda line="": LINES.append(line)


class FakeMarket:
    """/api/settle: buyer signatures on both closed mandates, amount within the open payment mandate's max."""

    def __init__(self, buyer_pub, budget, refuse=False):
        self.buyer_pub, self.budget, self.refuse, self.settles = buyer_pub, budget, refuse, 0

    def call(self, method, path, role="buyer", **kw):
        if (method, path) == ("GET", "/api/inventory"):    # kit v4 picks a unit; before Round 3 there is no stock
            return httpx.Response(200, json={"round": 2, "units": []})
        assert (method, path, role) == ("POST", "/api/settle", "shop")
        self.settles += 1
        b = kw["json"]
        checks = {}
        for k in ("checkout_mandate", "payment_mandate"):
            try:
                self.buyer_pub.verify(base64.b64decode(b[k]["sig"]), mk.canon(b[k]["mandate"]))
                checks[k] = True
            except Exception:
                checks[k] = False
        checks["amount"] = b["payment_mandate"]["mandate"]["amount"] <= self.budget
        checks["market"] = not self.refuse
        if not all(checks.values()):
            return httpx.Response(402, json={"settled": False, "checks": checks})
        return httpx.Response(200, json={"settled": True, "ledger": 7, "mandate_receipt": {"ledger": 7, "order": b["order"]}})


class OldCard(mk.AcpShop):
    """acp.json as kit v3 (and our Worker) writes it: inputs as short description strings."""

    def card(self, public):
        tools = [("create_checkout_session", "POST", "/checkout_sessions",
                  {"line_items": "[{id: item, quantity: 1}]", "receipt": "the receipt the shop signed over A2A"}),
                 ("get_checkout_session", "GET", "/checkout_sessions/{id}", {}),
                 ("complete_checkout_session", "POST", "/checkout_sessions/{id}/complete",
                  {"payment_data": "{order, checkout_mandate, payment_mandate}"}),
                 ("cancel_checkout_session", "POST", "/checkout_sessions/{id}/cancel", {})]
        return {"protocol": "acp", "version": "2026-04-17", "endpoint": f"{public}/acp",
                "tools": [{"name": n, "method": m, "path": p, "input": i} for n, m, p, i in tools]}


class NoTools(mk.AcpShop):
    def card(self, public):
        return {k: v for k, v in super().card(public).items() if k != "tools"}


class OddComplete(mk.AcpShop):
    """complete's schema asks for a field the buyer does not have: the tool refuses before sending anything."""

    def card(self, public):
        c = super().card(public)
        for t in c["tools"]:
            if t["name"] == "complete_checkout_session":
                t["input"]["required"].append("loyalty_code")
        return c


class TerseGet(mk.AcpShop):
    """A shop built without the kit: its GET gives its own status word and no checkout_jwt. The fixed acp_pay never
    reads GET and would pay it, so the tool buyer must not refuse it either."""

    async def handle(self, req, path, body, public):
        r = await super().handle(req, path, body, public)
        if req.method == "GET" and "/checkout_sessions/" in path:
            return JSONResponse({"id": json.loads(r.body)["id"], "status": "open"})
        return r


class OtherHost(mk.AcpShop):
    def card(self, public):
        return dict(super().card(public), endpoint=public.replace("127.0.0.1", "localhost") + "/acp")


def serve(shop_holder):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    public = f"http://127.0.0.1:{port}"

    async def handle(req):
        path = req.path_params["path"]
        return await shop_holder[0].handle(req, path, await req.body(), public)

    app = Starlette(routes=[Route("/{path:path}", handle, methods=["GET", "POST"])])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return public


def main():
    shop_key, buyer_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    shop_pub = mk.pub_b64(shop_key)
    holder = [None]
    url = serve(holder)
    results = []

    def case(name, cls, price=120, budget=150, tamper=False, refuse=False, expect_paid=True, expect_settles=1,
             expect_in_log=()):
        market = FakeMarket(buyer_key.public_key(), budget, refuse)
        holder[0] = shop = cls(shop_key, market)
        task = f"t-{name}"
        rec = {"shop": "test-shop", "item": "lamp", "price": price, "task": task}
        rec["sig_seller"] = base64.b64encode(shop_key.sign(mk.canon(rec))).decode()
        if tamper:
            rec["price"] = price - 30                   # the buyer edits the signed price
        o = {"order": f"o-{name}", "mandates": {
            "checkout": {"mandate": {"vct": "mandate.checkout.open.1", "item": "lamp"}},
            "payment": {"mandate": {"vct": "mandate.payment.open.1",
                                    "constraints": [{"type": "payment.amount_range", "max": budget}]}}}}
        LINES.clear()
        mr = mk.acp_pay_tools(o, url, shop_pub, rec, buyer_key)
        states = [s["status"] for s in shop.sessions.values()]
        ok = (bool(mr) == expect_paid and market.settles == expect_settles
              and all(any(x in line for line in LINES) for x in expect_in_log))
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: paid={bool(mr)} settles={market.settles} sessions={states}")
        for line in LINES:
            print(f"        {line}")

    case("schema-card", mk.AcpShop, expect_in_log=["[acp-tools] paid 120 with create_checkout_session -> complete_checkout_session"])
    case("old-string-card", OldCard, expect_in_log=["[acp-tools] paid 120"])
    case("no-tools-fallback", NoTools, expect_in_log=["falling back", "[acp] paid 120"])
    case("over-mandate-limit", mk.AcpShop, price=200, budget=150, expect_paid=False, expect_settles=0,
         expect_in_log=["above the mandate's limit", "cancel_checkout_session"])
    case("odd-complete-schema", OddComplete, expect_in_log=["loyalty_code is missing", "fixed complete"])
    case("market-refuses-once", mk.AcpShop, refuse=True, expect_paid=False, expect_settles=1,
         expect_in_log=["payment not completed"])
    case("tampered-receipt", mk.AcpShop, tamper=True, expect_paid=False, expect_settles=0,
         expect_in_log=["falling back", "[acp] checkout refused"])
    case("terse-get", TerseGet, expect_in_log=["[acp-tools] paid 120"])
    case("endpoint-other-host", OtherHost, expect_paid=False, expect_settles=0,
         expect_in_log=["not on the shop's host"])
    # blocked buyers (11/16): the order's buyer from the ledger cache; a missing list or unknown order lets the sale go
    import tempfile
    from pathlib import Path
    real = mk.BLOCKED_BUYERS
    mk.BLOCKED_BUYERS = Path(tempfile.mkdtemp(prefix="blk-")) / "b.json"
    mk._ORDER_POLL["started"] = True                    # no ledger reader in the test
    mk.ORDER_BUYER.update({"o-bad": "rater", "o-good": "fair"})
    try:
        none_file = mk.blocked_buyer(None, "o-bad", wait=0)
        mk.BLOCKED_BUYERS.write_text('{"buyers": ["rater"]}')
        ok = (none_file is None and mk.blocked_buyer(None, "o-bad", wait=0) == "rater"
              and mk.blocked_buyer(None, "o-good", wait=0) is None and mk.blocked_buyer(None, "o-unknown", wait=0) is None)
        print(("PASS" if ok else "FAIL") + " blocked-buyers: listed buyer refused, others and unknown orders pass")
        results.append(ok)
    finally:
        mk.BLOCKED_BUYERS = real
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
