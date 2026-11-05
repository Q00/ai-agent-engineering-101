"""Offline end to end: our seller.py behind the kit copy's Proxy and AcpShop, our market_buyer.py against it,
then the kit's acp_pay (check_accept + check_binding) and review.py on the resulting deal.json.

Everything runs on 127.0.0.1 with a temp R3_HOME: seller.py is a subprocess on a free port, the kit's Proxy
(with its AcpShop) runs in this process, the market is FakeMarket (inventory from a fixture, settle answers
with a mandate_receipt built from the checkout). No market, no model.
"""
import base64
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

W = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("market_kit_e2e", W / "market_kit.py")
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)
mk.print = lambda *a, **k: None                     # the kit's log() prints every line; keep test output readable

H = 3600
NOW = time.time()
CATALOG = {
    "G0000a001": {"sku": "G0000a001", "name": "Curtain A", "category": "가구>커튼", "list_price": 40000, "kind": "goods"},
    "G0000a002": {"sku": "G0000a002", "name": "Curtain B", "category": "가구>커튼", "list_price": 30000, "kind": "goods"},
    "G0000f001": {"sku": "G0000f001", "name": "Kimchi A", "category": "식품>김치", "list_price": 30000, "kind": "food",
                  "shelf_hours": 720},
    "G0000f002": {"sku": "G0000f002", "name": "Kimchi B", "category": "식품>김치", "list_price": 20000, "kind": "food",
                  "shelf_hours": 720},
}


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


class FakeResp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeMarket:
    """The two market calls AcpShop makes: GET /api/inventory and POST /api/settle."""

    def __init__(self, units):
        self.units, self.settles, self.lock = list(units), [], threading.Lock()

    def call(self, method, path, role="buyer", **kw):
        with self.lock:
            if path == "/api/inventory":
                return FakeResp(200, {"round": 3, "balance": 5000000, "units": [dict(u) for u in self.units]})
            if path == "/api/settle":
                body = kw.get("json") or {}
                co = json.loads(mk.unb64url(body["checkout_jwt"].split(".")[1]))
                self.settles.append(body)
                u = next(u for u in self.units if u["unit"] == co.get("unit"))
                self.units.remove(u)                    # sold: the next inventory no longer has it
                return FakeResp(200, {"ledger": 100 + len(self.settles), "mandate_receipt": {
                    "order": body.get("order"), "shop": co["shop_url"], "unit": u["unit"], "sku": u["sku"],
                    "name": u["name"], "grade": u["grade"], "claim": co.get("claim"),
                    "claim_honest": None if co.get("claim") is None else co["claim"] == u["grade"],
                    "expires_at": u["expires_at"], "warranty_hours": co.get("warranty_hours"),
                    "value": u["list_price"], "ledger": 100 + len(self.settles)}})
            return FakeResp(404, {"error": "unknown"})


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="r3-e2e-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", self.home / "strategy" / "SKILL.md")
        (self.home / "logs").mkdir()
        self.md = self.home / ".market"
        self.md.mkdir()
        self.market_key, self.shop_key, self.buyer_key = (Ed25519PrivateKey.generate() for _ in range(3))
        (self.home / "shop_key.pem").write_bytes(pem(self.shop_key))
        self.shop_pub = mk.pub_b64(self.shop_key)

        saved = (mk.LOG_FILE, dict(mk.CATALOG))
        mk.LOG_FILE = self.home / "logs" / "market-kit.log"
        mk.CATALOG.clear()
        mk.CATALOG.update(CATALOG)

        def restore():
            mk.LOG_FILE = saved[0]
            mk.CATALOG.clear()
            mk.CATALOG.update(saved[1])
        self.addCleanup(restore)
        (self.md / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")

    # --- fixture

    def unit(self, unit, sku, grade="A", hours=None):
        c = CATALOG[sku]
        exp = NOW + hours * H if hours else None
        body = {"unit": unit, "sku": sku, "grade": grade, "expires_at": exp}
        cert = {"cert": body, "sig": base64.b64encode(self.market_key.sign(mk.canon(body))).decode()}
        return {"unit": unit, "sku": sku, "name": c["name"], "category": c["category"], "list_price": c["list_price"],
                "kind": c["kind"], "cost": c["list_price"] // 2, "grade": grade, "supplied_at": NOW - H,
                "expires_at": exp, "cert": cert}

    def start(self, units):
        """FakeMarket with these units, inventory.json, seller.py on a free port, and the kit's Proxy + AcpShop."""
        self.market = FakeMarket(units)
        (self.md / "inventory.json").write_text(json.dumps(self.market.call("GET", "/api/inventory").json()),
                                                encoding="utf-8")
        sport = mk.free_port()
        env = dict(os.environ, R3_HOME=str(self.home), MARKET_DIR=str(self.md))
        self.seller = subprocess.Popen([sys.executable, "-B", str(W / "seller.py"), "--port", str(sport)],
                                       cwd=self.home, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop_seller)
        self.assertTrue(self.wait_card(f"http://127.0.0.1:{sport}"), "seller.py did not serve its card")
        self.proxy = mk.Proxy(mk.free_port())
        self.addCleanup(setattr, self.proxy.server, "should_exit", True)
        # no keep-alive to the seller: the server thread just stops, so a pooled socket would be left unclosed
        self.proxy.client = httpx.AsyncClient(timeout=240, limits=httpx.Limits(max_keepalive_connections=0))
        self.proxy.target(f"http://127.0.0.1:{sport}")
        self.acp = self.proxy.acp = mk.AcpShop(self.shop_key, self.market, self.md)

    def stop_seller(self):
        self.seller.terminate()
        out = self.seller.communicate(timeout=10)[0]
        self.assertEqual(out.decode(errors="replace"), "", "seller.py printed to stdout/stderr")

    @staticmethod
    def wait_card(base):
        for _ in range(200):
            try:
                if httpx.get(base + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        return False

    def order(self, oid, kind, acceptable, budget, uwh=None):
        o = {"order": oid, "item": "김치" if kind == "food" else "커튼",
             "category": "식품>김치" if kind == "food" else "가구>커튼", "budget": budget,
             "acceptable": acceptable, "kind": kind, "expires": int(NOW + 600), "round": 3,
             "market_pubkey": mk.pub_b64(self.market_key)}
        if uwh is not None:
            o["use_within_hours"] = uwh
            o["shelf_hours"] = 720
        o["mandates"] = {"checkout": {"mandate": {"vct": "mandate.checkout.open.1", "order": oid}},
                         "payment": {"mandate": {"vct": "mandate.payment.open.1", "order": oid, "constraints": [
                             {"type": "payment.amount_range", "min": 0, "max": budget}]}}}
        return o

    def run_market_buyer(self, o, skus):
        """Writes order.json / shops.json the way handle_order does and runs the hook. Returns its one line."""
        (self.md / "order.json").write_text(json.dumps({k: v for k, v in o.items() if k not in ("mandates", "nonce")},
                                                       ensure_ascii=False), encoding="utf-8")
        shops = [{"name": "psyoung82", "url": self.proxy.public, "reviews": [],
                  "offers": [{"sku": s, "name": CATALOG[s]["name"], "qty": 1} for s in skus]}]
        (self.md / "shops.json").write_text(json.dumps(shops, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, R3_HOME=str(self.home), MARKET_DIR=str(self.md))
        p = subprocess.run([sys.executable, "-B", str(W / "market_buyer.py"), "--shops", str(self.md / "shops.json"),
                            "--item", o["item"], "--budget", str(o["budget"]), "--order", str(self.md / "order.json")],
                           cwd=self.home, env=env, capture_output=True, text=True, timeout=120)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"market_buyer stdout: {p.stdout!r} stderr: {p.stderr!r}")
        out = json.loads(lines[0])
        self.assertTrue(out.get("receipt"), f"no deal: {out}; log: {self.hook_log('market_buyer')}")
        return out

    def hook_log(self, role):
        p = self.home / "logs" / "hooks" / f"{role}.log"
        return p.read_text(encoding="utf-8")[-2000:] if p.exists() else ""

    def kit_sign_checks(self, o, out):
        """handle_order's checks before it pays (kit L790-800)."""
        receipt = out["receipt"]
        self.assertEqual(out["shop"].rstrip("/"), self.proxy.public)
        self.assertTrue(mk.verify_seller(self.shop_pub, receipt), receipt)
        self.assertIn(receipt["item"], o["acceptable"])
        self.assertIs(type(receipt["price"]), int)
        self.assertLessEqual(receipt["price"], o["budget"])

    def pay(self, o, out):
        why = []
        mr = mk.acp_pay(o, self.proxy.public, self.shop_pub, out["receipt"], self.buyer_key, out.get("accept"), why)
        return mr, why

    def record_payment(self, o, out, mr, why):
        """The payments.jsonl row handle_order writes after acp_pay (kit L814-818)."""
        receipt = out["receipt"]
        with open(self.md / "payments.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"order": o["order"], "shop": out["shop"], "item": receipt.get("item"),
                                "price": receipt.get("price"), "paid": bool(mr),
                                **({"ledger": mr.get("ledger")} if mr else {"reason": "; ".join(why) or "unknown"})},
                               ensure_ascii=False) + "\n")

    def ingest_shop_stats(self):
        """memory/ingest.py over the kit files this run wrote (ledger stubbed empty), then query.shop_stats."""
        code = ("import json, sys, time; sys.path.insert(0, sys.argv[1]); from memory import ingest, query; "
                "ingest.fetch = lambda after, timeout: []; ingest.sync(time.time() + 10); "
                "print(json.dumps(query.shop_stats(query.load_summary(), sys.argv[2])))")
        p = subprocess.run([sys.executable, "-B", "-c", code, str(W), self.proxy.public], cwd=self.home,
                           env=dict(os.environ, R3_HOME=str(self.home)), capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.splitlines()[-1])

    def acp_rows(self):
        p = self.home / "logs" / "acp-sessions.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def session_for(self, task):
        return next(s for s in self.acp.sessions.values() if s["task"] == task)

    # --- tests

    def test_goods_deal_with_warranty_settles_and_is_reviewed(self):
        units = [self.unit("u-a1", "G0000a001"), self.unit("u-b1", "G0000a002")]
        self.start(units)
        o = self.order("o-e2e-goods", "goods", ["G0000a001", "G0000a002"], 45000)
        out = self.run_market_buyer(o, ["G0000a001", "G0000a002"])
        receipt = out["receipt"]
        self.kit_sign_checks(o, out)
        self.assertEqual(receipt["item"], "G0000a001")
        self.assertEqual(receipt["unit"], "u-a1")

        # the seller's own terms reached the kit through Proxy.observe
        terms = self.acp.terms[receipt["task"]]
        self.assertEqual((terms["unit"], terms["warranty_hours"], terms.get("claim")), ("u-a1", 6, "A"))
        self.assertIsNot(terms.get("cert"), False)      # the seller never withholds the certificate

        accept = out["accept"]
        self.assertEqual(accept["warranty_hours"], 6)
        self.assertEqual((accept["bind"]["sku"], accept["bind"]["unit"]), ("G0000a001", "u-a1"))

        mr, why = self.pay(o, out)
        self.assertIsNotNone(mr, why)
        self.assertEqual(why, [])
        # the checkout shipped the unit the seller named, with its terms and the market certificate
        s = self.session_for(receipt["task"])
        self.assertEqual(s["status"], "completed")
        self.assertEqual(s["fulfillment"]["unit"], "u-a1")
        self.assertEqual(s["fulfillment"]["warranty_hours"], 6)
        self.assertEqual(s["fulfillment"]["cert"], units[0]["cert"])
        co = mk.jwt_payload(s["checkout_jwt"], self.shop_pub)
        self.assertEqual((co["item"], co["unit"], co["price"]), ("G0000a001", "u-a1", receipt["price"]))
        self.assertIsNone(mk.check_binding(o, co, receipt, accept))
        self.assertIsNone(mk.check_accept(o, co, accept))
        self.assertEqual((mr["unit"], mr["sku"], mr["claim_honest"]), ("u-a1", "G0000a001", True))
        self.assertEqual(len(self.market.settles), 1)
        self.assertEqual([r["event"] for r in self.acp_rows()], ["create", "complete"])
        self.record_payment(o, out, mr, why)
        inv = json.loads((self.md / "inventory.json").read_text(encoding="utf-8"))   # refreshed after the sale
        self.assertEqual([u["unit"] for u in inv["units"]], ["u-b1"])

        # review.py on the deal.json the kit writes
        deal = self.md / "deal.json"
        deal.write_text(json.dumps({"order": {k: v for k, v in o.items() if k not in ("mandates", "nonce")},
                                    "receipt": receipt, "settled": mr}, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, R3_HOME=str(self.home))
        p = subprocess.run([sys.executable, "-B", str(W / "review.py"), "--deal", str(deal)], cwd=self.home, env=env,
                           capture_output=True, text=True, timeout=60)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, f"review stdout: {p.stdout!r} stderr: {p.stderr!r} {self.hook_log('review')}")
        rv = json.loads(lines[0])
        self.assertIsInstance(rv["stars"], int)
        self.assertEqual(rv["stars"], 5 if (40000 - receipt["price"]) / 40000 >= 0.20 else 4)
        self.assertIn("u-a1", rv["review"])
        self.assertTrue((self.home / "memory" / "deals" / "o-e2e-goods.json").exists())
        rows = [json.loads(x) for x in (self.home / "logs" / "decisions" / "review.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["choice"], {"stars": rv["stars"]})
        self.assertTrue(rows[-1]["memory_refs"])        # linked to market_buyer's order row
        st = self.ingest_shop_stats()
        self.assertEqual((st["attempts"], st["paid"], st["bind_mismatch"]), (1, 1, 0))

    def test_binding_refuses_other_sku_and_cancels(self):
        units = [self.unit("u-f1", "G0000f001", hours=700), self.unit("u-f2", "G0000f002", hours=600)]
        self.start(units)
        o = self.order("o-e2e-food", "food", ["G0000f001", "G0000f002"], 29000, uwh=10)
        out = self.run_market_buyer(o, ["G0000f001", "G0000f002"])
        receipt = out["receipt"]
        self.kit_sign_checks(o, out)
        self.assertEqual((receipt["item"], receipt["unit"]), ("G0000f001", "u-f1"))
        self.assertEqual(self.acp.terms[receipt["task"]]["unit"], "u-f1")
        self.assertEqual(out["accept"]["min_hours_left"], 10.2)
        self.assertEqual(out["accept"]["bind"]["sku"], "G0000f001")

        # The agreed unit sold elsewhere and the kit lost the seller's terms (e.g. it restarted): the kit's FEFO
        # fallback would now ship the other acceptable SKU. check_accept passes it (valid certificate, enough
        # hours); only the D12 binding stops the payment.
        self.acp.terms.pop(receipt["task"])
        self.market.units = [u for u in self.market.units if u["unit"] != "u-f1"]
        mr, why = self.pay(o, out)
        self.assertIsNone(mr)
        self.assertEqual(len(why), 1, why)
        self.assertTrue(why[0].startswith("not paying: binding: shipped SKU G0000f002, agreed G0000f001"), why)
        s = self.session_for(receipt["task"])
        self.assertEqual((s["status"], s["unit"]), ("canceled", "u-f2"))
        co = mk.jwt_payload(s["checkout_jwt"], self.shop_pub)
        self.assertIsNone(mk.check_accept(o, co, out["accept"]))
        self.assertEqual(self.market.settles, [])
        self.assertEqual([(r["event"], r["unit"]) for r in self.acp_rows()], [("create", "u-f2"), ("cancel", "u-f2")])
        # memory sees the refusal as a binding mismatch of this shop (D12 / L3 data path)
        self.record_payment(o, out, mr, why)
        st = self.ingest_shop_stats()
        self.assertEqual((st["attempts"], st["paid"], st["bind_mismatch"], st["refused"]), (1, 0, 1, {"binding": 1}))

    def test_food_deal_ships_named_unit(self):
        # two units of the agreed SKU: FEFO alone would ship u-f0 (expires first); the seller named u-f1
        # because u-f0 does not last the 10 + 1 h the buyer asked for
        units = [self.unit("u-f0", "G0000f001", hours=5), self.unit("u-f1", "G0000f001", hours=700)]
        self.start(units)
        o = self.order("o-e2e-food2", "food", ["G0000f001"], 29000, uwh=10)
        out = self.run_market_buyer(o, ["G0000f001"])
        self.kit_sign_checks(o, out)
        self.assertEqual(out["receipt"]["unit"], "u-f1")
        mr, why = self.pay(o, out)
        self.assertIsNotNone(mr, why)
        self.assertEqual(mr["unit"], "u-f1")
        self.assertEqual(self.session_for(out["receipt"]["task"])["fulfillment"]["unit"], "u-f1")


if __name__ == "__main__":
    unittest.main()
