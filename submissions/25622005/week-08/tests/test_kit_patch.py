"""Offline tests for the five local patches in week-08/market_kit.py (blocks marked "# 25622005:").

No network: acp_pay talks to an httpx.MockTransport, AcpShop to a fake market object.
"""
import asyncio
import base64
import contextlib
import importlib.util
import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

W = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("market_kit_under_test", W / "market_kit.py")
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)
REAL_CLIENT = httpx.Client


def quiet():
    """The kit's log() prints every line; keep test output readable."""
    return contextlib.redirect_stdout(io.StringIO())


class KitState(unittest.TestCase):
    """Each test gets a temp LOG_FILE and an empty CATALOG, restored afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        logs = Path(self.tmp.name) / "logs"
        logs.mkdir()
        self.sessions_file = logs / "acp-sessions.jsonl"
        self._saved = (mk.LOG_FILE, dict(mk.CATALOG), mk.LOG_MAX)
        mk.LOG_FILE = logs / "market-kit.log"
        mk.CATALOG.clear()

    def tearDown(self):
        mk.LOG_FILE, cat, mk.LOG_MAX = self._saved
        mk.CATALOG.clear()
        mk.CATALOG.update(cat)
        self.tmp.cleanup()

    def rows(self):
        if not self.sessions_file.exists():
            return []
        return [json.loads(x) for x in self.sessions_file.read_text(encoding="utf-8").splitlines()]


# --- 1) check_binding, pure

class CheckBindingTest(KitState):
    def setUp(self):
        super().setUp()
        mk.CATALOG.update({"SKU1": {"sku": "SKU1", "list_price": 400}, "SKU2": {"sku": "SKU2", "list_price": 400}})

    def test_no_bind_is_none(self):
        co = {"item": "SKU2", "unit": "u2", "price": 999}
        self.assertIsNone(mk.check_binding({}, co, {"item": "SKU1"}, None))
        self.assertIsNone(mk.check_binding({}, co, {"item": "SKU1"}, {}))
        self.assertIsNone(mk.check_binding({}, co, {"item": "SKU1"}, {"cert": True}))
        self.assertIsNone(mk.check_binding({}, co, {"item": "SKU1"}, {"bind": False}))

    def test_sku_from_bind(self):
        r = mk.check_binding({}, {"item": "SKU2", "price": 100}, {"item": "food"}, {"bind": {"sku": "SKU1"}})
        self.assertTrue(r.startswith("binding:"), r)
        self.assertIn("SKU2", r)
        self.assertIsNone(mk.check_binding({}, {"item": "SKU1", "price": 100}, {"item": "food"},
                                           {"bind": {"sku": "SKU1"}}))

    def test_sku_from_catalog_receipt(self):
        r = mk.check_binding({}, {"item": "SKU2", "price": 100}, {"item": "SKU1"}, {"bind": {}})
        self.assertTrue(r.startswith("binding:"), r)
        # a category receipt binds no SKU by itself
        self.assertIsNone(mk.check_binding({}, {"item": "SKU2", "price": 100}, {"item": "apple"}, {"bind": {}}))

    def test_unit(self):
        acc = {"bind": {"sku": "SKU1", "unit": "u1"}}
        self.assertIsNone(mk.check_binding({}, {"item": "SKU1", "unit": "u1", "price": 1}, {}, acc))
        r = mk.check_binding({}, {"item": "SKU1", "unit": "u9", "price": 1}, {}, acc)
        self.assertTrue(r.startswith("binding: shipped unit u9"), r)
        r = mk.check_binding({}, {"item": "SKU1", "unit": "u9", "price": 1}, {"unit": "u1"}, {"bind": {}})
        self.assertTrue(r.startswith("binding:"), r)

    def test_max_price_list(self):
        acc = {"bind": {"max_price_list": 1.0}}
        self.assertIsNone(mk.check_binding({}, {"item": "SKU2", "price": 400}, {"item": "apple"}, acc))
        r = mk.check_binding({}, {"item": "SKU2", "price": 401}, {"item": "apple"}, acc)
        self.assertTrue(r.startswith("binding: price 401"), r)
        # unknown list price: nothing to compare against, no refusal
        self.assertIsNone(mk.check_binding({}, {"item": "SKU9", "price": 10**6}, {"item": "apple"}, acc))


# --- 1) + 2) acp_pay against a mocked shop

class AcpPayTest(KitState):
    def setUp(self):
        super().setUp()
        self.shop_key, self.buyer_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        mk.CATALOG.update({"SKU1": {"sku": "SKU1", "list_price": 400}, "SKU2": {"sku": "SKU2", "list_price": 400}})
        self.o = {"order": "o1", "item": "apple", "acceptable": ["SKU1", "SKU2"], "budget": 1000, "kind": "goods",
                  "market_pubkey": mk.pub_b64(Ed25519PrivateKey.generate()),
                  "mandates": {"checkout": {"mandate": {"vct": "open.checkout"}},
                               "payment": {"mandate": {"constraints": [{"type": "payment.amount_range",
                                                                        "max": 1000}]}}}}
        self.calls = []

    def pay(self, receipt, co, accept=None):
        """Run acp_pay against a fake shop whose checkout carries `co`. Returns (result, why, paths called)."""
        jwt = mk.jwt_sign(self.shop_key, co)

        def handler(req):
            self.calls.append((req.method, req.url.path))
            if req.url.path == "/.well-known/acp.json":
                return httpx.Response(200, json={"endpoint": "http://shop.test/acp"})
            if req.url.path == "/acp/checkout_sessions":
                return httpx.Response(201, json={"id": "cs_1", "status": "ready_for_payment", "checkout_jwt": jwt})
            if req.url.path.endswith("/cancel"):
                return httpx.Response(200, json={"id": "cs_1", "status": "canceled"})
            if req.url.path.endswith("/complete"):
                return httpx.Response(200, json={"order": {"mandate_receipt": {"ledger": 7}}})
            return httpx.Response(404)

        why = []
        client = lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)
        with mock.patch.object(mk.httpx, "Client", client), quiet():
            mr = mk.acp_pay(self.o, "http://shop.test", mk.pub_b64(self.shop_key), receipt, self.buyer_key,
                            accept, why)
        return mr, why, [p for _, p in self.calls]

    def receipt(self, item="SKU1", price=500):
        return {"shop": "psy", "item": item, "price": price, "task": "t1", "sig_seller": "x"}

    def co(self, item="SKU1", price=500, unit="u1"):
        return {"id": "cs_1", "item": item, "price": price, "task": "t1", "unit": unit}

    def test_no_bind_pays_like_the_original_kit(self):
        mr, why, paths = self.pay(self.receipt(), self.co(item="SKU2"), accept={})
        self.assertEqual(mr, {"ledger": 7})
        self.assertIn("/acp/checkout_sessions/cs_1/complete", paths)

    def test_bind_refuses_other_sku_and_cancels(self):
        mr, why, paths = self.pay(self.receipt(), self.co(item="SKU2"), accept={"bind": {"sku": "SKU1"}})
        self.assertIsNone(mr)
        self.assertTrue(any(w.startswith("not paying: binding:") for w in why), why)
        self.assertIn("/acp/checkout_sessions/cs_1/cancel", paths)
        self.assertNotIn("/acp/checkout_sessions/cs_1/complete", paths)

    def test_bind_refuses_other_unit(self):
        mr, why, paths = self.pay(self.receipt(), self.co(unit="u2"), accept={"bind": {"sku": "SKU1", "unit": "u1"}})
        self.assertIsNone(mr)
        self.assertIn("/acp/checkout_sessions/cs_1/cancel", paths)

    def test_bind_price_ratio_for_category_receipt(self):
        mr, why, paths = self.pay(self.receipt(item="apple"), self.co(item="SKU2"), {"bind": {"max_price_list": 1.0}})
        self.assertIsNone(mr)
        self.assertIn("binding: price 500", why[-1])
        self.calls.clear()
        mr, why, paths = self.pay(self.receipt(item="apple"), self.co(item="SKU2"), {"bind": {"max_price_list": 1.5}})
        self.assertEqual(mr, {"ledger": 7})

    def test_mismatch_cancels(self):
        mr, why, paths = self.pay(self.receipt(price=500), self.co(price=600))
        self.assertIsNone(mr)
        self.assertIn("does not match the agreed receipt", why[0])
        self.assertIn("/acp/checkout_sessions/cs_1/cancel", paths)
        self.assertEqual(len(why), 1)                   # the cancel adds no second failure reason

    def test_over_budget_cancels(self):
        mr, why, paths = self.pay(self.receipt(price=1200), self.co(price=1200))
        self.assertIsNone(mr)
        self.assertIn("above the mandate's limit", why[0])
        self.assertIn("/acp/checkout_sessions/cs_1/cancel", paths)


# --- 3) + 4) AcpShop: acp-sessions.jsonl and the hold TTL

class FakeResp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeMarket:
    def __init__(self, units, settle=(200, {"ledger": 42, "mandate_receipt": {"grade": "A", "unit": "u1"}})):
        self.units, self.settle = units, settle

    def call(self, method, path, role="buyer", **kw):
        if path == "/api/inventory":
            return FakeResp(200, {"round": 3, "units": self.units})
        if path == "/api/settle":
            return FakeResp(*self.settle)
        return FakeResp(404, {"error": "unknown"})


class AcpShopTest(KitState):
    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        self.unit = {"unit": "u1", "sku": "SKU1", "name": "Apple", "category": "food>apple", "kind": "food",
                     "grade": "A", "supplied_at": 1, "expires_at": time.time() + 10 * 3600,
                     "cert": {"cert": {"unit": "u1"}, "sig": "x"}}
        self.market = FakeMarket([self.unit])
        self.shop = mk.AcpShop(self.key, self.market)

    def signed(self, task, item="SKU1", price=300):
        r = {"shop": "psy", "item": item, "price": price, "task": task}
        r["sig_seller"] = base64.b64encode(self.key.sign(mk.canon(r))).decode()
        return r

    def call(self, path, body=None, method="POST"):
        with quiet():
            resp = asyncio.run(self.shop.handle(SimpleNamespace(method=method), path,
                                                json.dumps(body or {}).encode(), "http://me.test"))
        return resp.status_code, json.loads(resp.body)

    def create(self, task, **kw):
        rec = self.signed(task, **kw)
        return self.call("acp/checkout_sessions", {"line_items": [{"id": rec["item"], "quantity": 1}],
                                                   "receipt": rec, "acceptable": ["SKU1"]})

    def test_create_row(self):
        self.shop.terms["t1"] = {"unit": "u1", "warranty_hours": 6, "claim": "A"}
        code, s = self.create("t1")
        self.assertEqual(code, 201)
        (row,) = self.rows()
        self.assertEqual(row["event"], "create")
        self.assertEqual((row["sid"], row["task"], row["sku"], row["unit"], row["price"]), (s["id"], "t1", "SKU1", "u1", 300))
        self.assertEqual((row["kind"], row["grade"], row["cert"], row["claim"], row["warranty_h"]), ("food", "A", True, "A", 6))
        self.assertAlmostEqual(row["hours_left"], 10, delta=0.1)
        self.assertIn("created", self.shop.sessions[s["id"]])

    def test_refuse_rows(self):
        bad = self.signed("t1")
        bad["price"] = 1                                # signature no longer covers the receipt
        code, _ = self.call("acp/checkout_sessions", {"line_items": [{"id": "SKU1"}], "receipt": bad})
        self.assertEqual(code, 400)
        self.assertEqual(self.create("t2")[0], 201)
        code, res = self.create("t3")                   # the only unit is held by t2
        self.assertEqual(code, 409)
        events = [(r["event"], r.get("task"), r.get("error")) for r in self.rows()]
        self.assertEqual(events[0], ("refuse", "t1", "the receipt is not signed by this shop"))
        self.assertEqual(events[1][:2], ("create", "t2"))
        self.assertEqual(events[2], ("refuse", "t3", res["error"]))

    def test_cancel_complete_settle_refused(self):
        _, s1 = self.create("t1")
        self.assertEqual(self.call(f"acp/checkout_sessions/{s1['id']}/cancel")[0], 200)
        _, s2 = self.create("t2")
        self.assertEqual(self.call(f"acp/checkout_sessions/{s2['id']}/complete", {"payment_data": {}})[0], 200)
        self.market.units = [dict(self.unit, unit="u2")]
        self.market.settle = (409, {"error": "no", "checks": {"unit_in_stock": False, "sig": True}})
        _, s3 = self.create("t3")
        self.assertEqual(self.call(f"acp/checkout_sessions/{s3['id']}/complete", {"payment_data": {}})[0], 402)
        ev = [(r["event"], r.get("task")) for r in self.rows()]
        self.assertEqual(ev, [("create", "t1"), ("cancel", "t1"), ("create", "t2"), ("complete", "t2"),
                              ("create", "t3"), ("settle_refused", "t3")])
        rows = self.rows()
        self.assertEqual(rows[3]["ledger"], 42)
        self.assertIn("unit_in_stock", rows[5]["error"])

    def test_hold_ttl(self):
        _, s1 = self.create("t1")
        self.assertEqual(self.create("t2")[0], 409)     # held while young
        self.shop.sessions[s1["id"]]["created"] -= mk.HOLD_TTL + 1
        code, s3 = self.create("t3")
        self.assertEqual(code, 201)
        self.assertEqual(s3["unit"], "u1")
        self.assertEqual(self.shop.sessions[s1["id"]]["status"], "expired")
        self.assertIn(("expire", "t1"), [(r["event"], r.get("task")) for r in self.rows()])
        # a late complete on the expired session is refused by the kit (settle unchanged, never reached)
        self.assertEqual(self.call(f"acp/checkout_sessions/{s1['id']}/complete", {"payment_data": {}})[0], 409)

    def test_no_log_file_is_silent(self):
        mk.LOG_FILE = None
        self.assertEqual(self.create("t1")[0], 201)
        self.assertEqual(self.rows(), [])

    def test_unwritable_log_never_breaks_checkout(self):
        self.sessions_file.mkdir()                      # a directory where the file should be: open() fails
        self.assertEqual(self.create("t1")[0], 201)


# --- 5) log archive and the startup line

class LogArchiveTest(KitState):
    def test_rotation_keeps_old_logs(self):
        mk.LOG_MAX = 10
        mk.LOG_FILE.write_text("x" * 100, encoding="utf-8")
        with quiet():
            mk.log("after rotation")
        logs = mk.LOG_FILE.parent
        archived = [p.name for p in logs.iterdir() if re.fullmatch(r"market-kit\.\d{8}-\d{6}\.log", p.name)]
        self.assertEqual(len(archived), 1)
        self.assertEqual((logs / archived[0]).read_text(encoding="utf-8"), "x" * 100)
        self.assertFalse((logs / "market-kit.log.1").exists())
        self.assertTrue(mk.LOG_FILE.read_text(encoding="utf-8").endswith("after rotation\n"))

    def test_quoted_newlines_stay_on_one_line(self):
        with quiet():
            mk.log('[acp] checkout refused: 409 {"error": "x"}\n12:00:00 [supply] 9 x fake for 1, balance 1\r')
        lines = mk.LOG_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith('"x"}\\n12:00:00 [supply] 9 x fake for 1, balance 1\\r'))

    def test_version_and_startup_line(self):
        self.assertEqual(mk.KIT_VERSION, "4")
        src = (W / "market_kit.py").read_text(encoding="utf-8")
        self.assertIn('log("[kit] local patches: binding, cancel, acp-sessions, hold-ttl, log-archive, log-oneline")',
                      src)
        self.assertEqual(mk.HOLD_TTL, 300)


if __name__ == "__main__":
    unittest.main()
