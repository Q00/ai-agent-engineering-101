"""acp.py against the kit's own Round 3 code, on this computer.

The kit (scripts/market_kit.py, KIT_VERSION 3) is the reference: other
students' buyers pay with its acp_pay and their shops check out with its
AcpShop. So the test runs both directions over real HTTP:

    kit buyer  (acp_pay)  ->  this shop's checkout (acp.Checkout)
    this buyer (acp.pay)  ->  the kit shop's checkout (AcpShop)

and both settle at a stand-in market that runs the 13 checks the lecture's
TRY IT lists, rebuilt from their names (the real market stays the judge: a
live settle is the only full proof). Then the refusals: a forged checkout, a
total over the mandate's limit, a foreign receipt, a price under the reserve,
and a second settle of one order.

    python acp_test.py          (writes logs/acp-test.log)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import acp
import buyer_core
import catalog
import shop_core

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("market_kit", HERE.parents[2] / "scripts" / "market_kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

LOG = HERE / "logs" / "acp-test.log"
lines: list[str] = []
results: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)
    lines.append(line)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    say(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))


# ------------------------------------------------------------ a stand-in market

class Market:
    """Issues Round 3 orders with open mandates and settles them with the
    lecture's 13 checks. One settlement per order."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.orders, self.settled, self.ledger = {}, set(), 100

    def order(self, item: str, budget: int) -> dict:
        oid = f"o-{len(self.orders):04d}"
        exp = int(time.time()) + 300
        m = {"checkout": {"vct": "mandate.checkout.open.1", "order": oid,
                          "constraints": [{"type": "checkout.line_items", "items": [{"id": item, "quantity": 1}]}]},
             "payment": {"vct": "mandate.payment.open.1", "order": oid,
                         "constraints": [{"type": "payment.amount_range", "min": 0, "max": budget, "currency": "credits"},
                                         {"type": "payment.execution_date", "before": exp}]}}
        signed = {k: {"mandate": v, "sig": base64.b64encode(self.key.sign(acp.canon(v))).decode()} for k, v in m.items()}
        o = {"order": oid, "item": item, "budget": budget, "round": 3, "expires": exp, "mandates": signed}
        self.orders[oid] = o
        return o

    def settle(self, body: dict, shop_url: str, shop_pub: str, buyer_pub: str) -> tuple[int, dict]:
        o = self.orders.get(body.get("order")) or {}
        cm = (body.get("checkout_mandate") or {}).get("mandate") or {}
        pm = (body.get("payment_mandate") or {}).get("mandate") or {}
        jwt = body.get("checkout_jwt") or ""
        co = acp.jwt_payload(jwt, shop_pub) or {}
        h = acp.b64url(hashlib.sha256(jwt.encode()).digest())
        opens = {k: hashlib.sha256(acp.canon(v["mandate"])).hexdigest() for k, v in (o.get("mandates") or {}).items()}
        limit = acp.mandate_limit(o) if o else None

        def signed(part) -> bool:
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                Ed25519PublicKey.from_public_bytes(base64.b64decode(buyer_pub)).verify(
                    base64.b64decode(part["sig"]), acp.canon(part["mandate"]))
                return True
            except Exception:                           # noqa: BLE001
                return False

        checks = {
            "round_3_order": o.get("round") == 3,
            "not_expired": bool(o) and time.time() < o["expires"],
            "not_settled_before": body.get("order") not in self.settled,
            "checkout_signed_by_shop": bool(co),
            "checkout_matches_order": bool(co) and co.get("item") == o.get("item"),
            "checkout_mandate_signed_by_buyer": signed(body.get("checkout_mandate") or {}),
            "payment_mandate_signed_by_buyer": signed(body.get("payment_mandate") or {}),
            "closed_mandates_types": (cm.get("vct"), pm.get("vct")) == ("mandate.checkout.1", "mandate.payment.1"),
            "bound_to_open_mandates": bool(opens) and (cm.get("open_mandate"), pm.get("open_mandate"))
                                      == (opens.get("checkout"), opens.get("payment")),
            "bound_to_this_checkout": cm.get("checkout_jwt") == jwt and cm.get("checkout_hash") == h == pm.get("checkout_hash"),
            "payee_is_this_shop": pm.get("payee") == shop_url,
            "amount_is_checkout_total": bool(co) and pm.get("amount") == co.get("price"),
            "amount_in_range": limit is not None and isinstance(pm.get("amount"), int) and 0 <= pm["amount"] <= limit,
        }
        if not all(checks.values()):
            return 400, {"settled": False, "checks": checks}
        self.settled.add(body["order"])
        self.ledger += 1
        return 200, {"settled": True, "ledger": self.ledger, "checks": checks,
                     "mandate_receipt": {"order": body["order"], "amount": pm["amount"], "ledger": self.ledger}}


class KitMarket:
    """What the kit's AcpShop calls: market.call(method, path, role, json=...)."""

    class Resp:
        def __init__(self, status, data):
            self.status_code, self._data = status, data
            self.headers = {"content-type": "application/json"}

        def json(self):
            return self._data

        @property
        def text(self):
            return json.dumps(self._data)

    def __init__(self, market, shop_url, shop_pub, buyer_pub):
        self.market, self.shop_url, self.shop_pub, self.buyer_pub = market, shop_url, shop_pub, buyer_pub

    def call(self, method, path, role="buyer", **kw):
        return self.Resp(*self.market.settle(kw["json"], self.shop_url, self.shop_pub, self.buyer_pub))


# ------------------------------------------------------------ HTTP plumbing

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app) -> str:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


class AsyncHTTP:
    """acp.pay's transport on the PC: (status, json or None)."""

    def __init__(self):
        self.c = httpx.AsyncClient(timeout=30)

    async def _out(self, r):
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None

    async def get(self, url):
        return await self._out(await self.c.get(url))

    async def post(self, url, body):
        return await self._out(await self.c.post(url, json=body))


def ours_app(checkout: acp.Checkout, public: dict) -> Starlette:
    async def route(request):
        status, out = await checkout.handle(request.method, request.url.path,
                                            (await request.body()).decode(), public["url"])
        return JSONResponse(out, status_code=status)
    return Starlette(routes=[Route("/{p:path}", route, methods=["GET", "POST"])])


def kit_app(shop, public: dict) -> Starlette:
    async def route(request):
        return await shop.handle(request, request.url.path.lstrip("/"), await request.body(), public["url"])
    return Starlette(routes=[Route("/{p:path}", route, methods=["GET", "POST"])])


# ------------------------------------------------------------ the runs

async def main() -> int:
    catalog.use([it for it in json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
                 if it["id"] in ("lamp", "chair", "bookshelf")])   # the Worker's catalogue (build.py)
    reserve_of = lambda item: (catalog.by_id(item) or {}).get("reserve")  # noqa: E731
    market = Market()
    shop_key, buyer_key, other_key = (Ed25519PrivateKey.generate() for _ in range(3))
    shop_pub, buyer_pub = acp.pub_b64(shop_key), acp.pub_b64(buyer_key)
    http = AsyncHTTP()

    say("== same bytes as the kit")
    payload = {"id": "cs_1", "item": "lamp", "price": 104, "task": "t-1", "shop": "Kiro Furniture"}
    check("canon", acp.canon(payload) == kit.canon(payload))
    check("checkout_jwt", acp.jwt_sign(shop_key, payload) == kit.jwt_sign(shop_key, payload),
          "Ed25519 is deterministic, so equal tokens mean equal bytes")
    check("descriptor", acp.descriptor("https://x") == kit.AcpShop(shop_key, None).card("https://x"))
    check("join body", set(acp.signed_join(shop_key, "n", "shop", "https://x", "c")) ==
          {"name", "role", "pubkey", "url", "ts", "sig", "owner"})

    # this shop, served the way the Worker serves it
    ours_pub = {"url": ""}
    async def ours_settle(body):
        return market.settle(body, ours_pub["url"], shop_pub, buyer_pub)
    checkout = acp.Checkout(shop_key, reserve_of, ours_settle, log=lambda l: say("    shop: " + l))
    ours_pub["url"] = serve(ours_app(checkout, ours_pub))

    say("\n== kit buyer (acp_pay) -> this shop's checkout")
    kit.log = lambda line="": say("    kit: " + line)
    o = market.order("lamp", 125)
    rec = shop_core.sign_receipt(shop_key, "Kiro Furniture", "lamp", 104, "task-a")
    mr = await asyncio.to_thread(kit.acp_pay, o, ours_pub["url"], shop_pub, rec, buyer_key)
    check("settled", bool(mr), f"mandate receipt {mr}")
    o2 = market.order("chair", 205)
    rec2 = shop_core.sign_receipt(shop_key, "Kiro Furniture", "chair", 191, "task-b")
    mr2 = await asyncio.to_thread(kit.acp_pay, o2, ours_pub["url"], shop_pub, rec2, buyer_key)
    check("settled a second item", bool(mr2))

    say("\n== the shop forgets its sessions between create and complete (an object restart)")
    class Forgetful(AsyncHTTP):
        async def post(self, url, body):
            out = await super().post(url, body)
            if url.endswith("/checkout_sessions"):
                checkout.sessions.clear()
            return out
    fh = Forgetful()
    o8 = market.order("bookshelf", 325)
    rec8 = shop_core.sign_receipt(shop_key, "Kiro Furniture", "bookshelf", 276, "task-h")
    mr8, why8 = await acp.pay(fh, o8, ours_pub["url"], shop_pub, rec8, buyer_key, log=lambda l: say("    us: " + l))
    check("settled anyway, from the checkout_jwt in the closed mandate", bool(mr8), str(why8 or mr8))
    forged = {"payment_data": {"checkout_mandate": {"mandate": {"checkout_jwt": acp.jwt_sign(
        other_key, {"id": "cs_forged", "item": "lamp", "price": 100, "task": "t", "iat": int(time.time())})}}}}
    st, out = await checkout.handle("POST", "/acp/checkout_sessions/cs_forged/complete", json.dumps(forged),
                                    ours_pub["url"])
    check("a session token signed by another key is not restored", st == 404, out.get("error", ""))
    await fh.c.aclose()

    say("\n== this buyer (acp.pay) -> the kit shop's checkout")
    kit_pub = {"url": ""}
    kit_shop = kit.AcpShop(other_key, None)
    kit_pub["url"] = serve(kit_app(kit_shop, kit_pub))
    kit_shop.market = KitMarket(market, kit_pub["url"], acp.pub_b64(other_key), buyer_pub)
    o3 = market.order("lamp", 125)
    rec3 = shop_core.sign_receipt(other_key, "Kit Shop", "lamp", 110, "task-c")
    mr3, why3 = await acp.pay(http, o3, kit_pub["url"], acp.pub_b64(other_key), rec3, buyer_key,
                              log=lambda l: say("    us: " + l))
    check("settled", bool(mr3) and why3 is None, f"mandate receipt {mr3}")

    say("\n== refusals")
    # the buyer: a checkout above the open mandate's limit is never paid
    o4 = market.order("lamp", 100)
    rec4 = shop_core.sign_receipt(other_key, "Kit Shop", "lamp", 110, "task-d")
    mr4, why4 = await acp.pay(http, o4, kit_pub["url"], acp.pub_b64(other_key), rec4, buyer_key,
                              log=lambda l: say("    us: " + l))
    check("buyer: total over the mandate's limit is not paid", mr4 is None and why4 == "over_limit", why4)
    # the buyer: a checkout signed by another key than the shop's registered one
    o5 = market.order("lamp", 125)
    rec5 = shop_core.sign_receipt(other_key, "Kit Shop", "lamp", 110, "task-e")
    mr5, why5 = await acp.pay(http, o5, kit_pub["url"], shop_pub, rec5, buyer_key, log=lambda l: say("    us: " + l))
    check("buyer: forged checkout (wrong shop key) is not paid", mr5 is None and why5 == "checkout_mismatch", why5)
    # the buyer: a shop with no checkout at all (what this Worker served before week 8)
    bare = serve(Starlette(routes=[Route("/{p:path}", lambda request: JSONResponse("A2A only"), methods=["GET"])]))
    mr7, why7 = await acp.pay(http, market.order("lamp", 125), bare, shop_pub, rec5, buyer_key,
                              log=lambda l: say("    us: " + l))
    check("buyer: a shop without a checkout is reported as the shop's fault", mr7 is None and why7 in acp.SHOP_FAULT, why7)
    # the shop: a receipt another shop signed
    st, out = await checkout.handle("POST", "/acp/checkout_sessions", json.dumps(
        {"line_items": [{"id": "lamp", "quantity": 1}], "receipt": rec3}), ours_pub["url"])
    check("shop: a receipt it did not sign gets no checkout", st == 400, out.get("error", ""))
    # the shop: its own signature on a price under the reserve
    low = shop_core.sign_receipt(shop_key, "Kiro Furniture", "lamp", 90, "task-f")
    st, out = await checkout.handle("POST", "/acp/checkout_sessions", json.dumps(
        {"line_items": [{"id": "lamp", "quantity": 1}], "receipt": low}), ours_pub["url"])
    check("shop: a price under the reserve gets no checkout", st == 400, out.get("error", ""))
    # the shop: one receipt, one checkout (while the object holds its sessions; after a restart a
    # second session could open, and it would still sell at the agreed price, never below the reserve)
    dup = json.dumps({"line_items": [{"id": "lamp", "quantity": 1}],
                      "receipt": shop_core.sign_receipt(shop_key, "Kiro Furniture", "lamp", 104, "task-dup")})
    await checkout.handle("POST", "/acp/checkout_sessions", dup, ours_pub["url"])
    st, out = await checkout.handle("POST", "/acp/checkout_sessions", dup, ours_pub["url"])
    check("shop: a receipt already checked out gets no second session", st == 409, out.get("error", ""))
    # the market: a second settle of a settled order (the shop reports the refusal as 402)
    again = shop_core.sign_receipt(shop_key, "Kiro Furniture", "lamp", 104, "task-g")
    mr6 = await asyncio.to_thread(kit.acp_pay, o, ours_pub["url"], shop_pub, again, buyer_key)
    check("market: the same order cannot settle twice", mr6 is None)

    say("\n== the buyer's review and memory after a deal that did not settle")
    async def model(prompt):                            # the harness, pinned: a happy 4-star review
        return json.dumps({"stars": 4, "review": "Bought the lamp at 100 of 125, receipt matched."})
    deal = {"deal": True, "honest": True, "price": 100, "budget": 125, "rounds": 2, "item": "lamp",
            "receipt": rec, "error": None, "refused": False, "fp": "f"}
    s0, r0 = await buyer_core.make_review(model, dict(deal))
    s1, r1 = await buyer_core.make_review(model, dict(deal, settled=False, unsettled_why="no_checkout"))
    s2, r2 = await buyer_core.make_review(model, dict(deal, settled=False, unsettled_why="not_completed"))
    say(f"    round 2 (nothing to pay): {s0} | {r0}")
    say(f"    no checkout:              {s1} | {r1}")
    say(f"    market refused:           {s2} | {r2}")
    check("review: Round 2 deals are untouched", s0 >= 4 and "settle" not in r0)
    check("review: the shop's fault caps the stars and says why",
          s1 <= buyer_core.UNSETTLED_STARS and "no ACP checkout" in r1)
    check("review: an unclear refusal is told, not blamed", s2 == s0 and "did not settle" in r2)
    mem: dict = {}
    for _ in range(2):
        buyer_core.remember(mem, "no-checkout-shop", "f", "https://x", dict(deal, settled=False,
                            unsettled_why="no_checkout"), s1)
    why = buyer_core.why_bad(4.8, mem["no-checkout-shop"])
    check("memory: two unsettled deals in the last five and the shop is avoided", bool(why), why or "")

    await http.c.aclose()
    passed = sum(ok for _, ok, _ in results)
    say(f"\n{passed}/{len(results)} passed -> {'PASS' if passed == len(results) else 'FAIL'}")
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
