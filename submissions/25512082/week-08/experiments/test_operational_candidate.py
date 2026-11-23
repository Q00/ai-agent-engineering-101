from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from operational_candidate.buyer import BOriginalClient, CandidateBuyerError, offer_for
from operational_candidate.safe_restock import decision as safe_restock
from operational_candidate.seller import NAME, canon, find_unit, sign_receipt, terms


class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code, self._payload = status, payload
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeMarket:
    def __init__(self, inventory: dict, mode: str = "ok"):
        self.inventory, self.settle_payload, self.mode = inventory, None, mode

    def call(self, method, path, role, json=None):
        if method == "GET" and path == "/api/inventory":
            if self.mode == "inventory_error": return FakeResponse(503, {"error": "offline"})
            return FakeResponse(200, self.inventory)
        if method == "POST" and path == "/api/settle":
            self.settle_payload = json
            if self.mode == "timeout": raise TimeoutError("local fake timeout")
            if self.mode == "missing": return FakeResponse(200, {})
            if self.mode in {"amount_mismatch", "over_budget", "same_owner"}:
                return FakeResponse(402, {"checks": {self.mode: False}})
            return FakeResponse(200, {"mandate_receipt": {"unit": "u1", "name": "X"}, "ledger": 1})
        return FakeResponse(404, {"error": "unexpected"})


def make_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return key


class CandidateCoreTests(unittest.TestCase):
    def test_real_unit_selection_and_expired_rejection(self):
        inventory = {"round": 3, "units": [
            {"sku": "X", "unit": "expired", "name": "Thing", "category": "goods>X", "grade": "expired",
             "cost": 90, "list_price": 150, "expires_at": 1, "supplied_at": 1},
            {"sku": "X", "unit": "u1", "name": "Thing", "category": "goods>X", "grade": "A",
             "cost": 100, "list_price": 150, "expires_at": None, "supplied_at": 2}]}
        self.assertEqual(find_unit("I want X", inventory, now=10)["unit"], "u1")
        inventory["units"] = inventory["units"][:1]
        self.assertIsNone(find_unit("I want X", inventory, now=10))

    def test_b_safe_terms_never_go_below_cost(self):
        ask, floor = terms({"cost": 100, "list_price": 110})
        self.assertGreaterEqual(floor, 100)
        self.assertGreaterEqual(ask, floor)

    def test_signed_receipt_verifies_and_tamper_fails(self):
        from scripts.market_kit import pub_b64, verify_seller
        with tempfile.TemporaryDirectory() as temporary:
            key = make_key(Path(temporary) / "test.pem")
            receipt = sign_receipt({"shop": NAME, "item": "X", "price": 113, "task": "t1", "unit": "u1"},
                                   Path(temporary) / "test.pem")
            self.assertTrue(verify_seller(pub_b64(key), receipt))
            self.assertFalse(verify_seller(pub_b64(key), {**receipt, "price": 114}))

    def test_buyer_executes_a2a_jsonrpc_and_budget_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            key_path = Path(temporary) / "test.pem"; key = make_key(key_path)
            calls = []
            def handler(request: httpx.Request) -> httpx.Response:
                if request.method == "GET":
                    return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0", "url": "http://seller.test/"}]})
                body = json.loads(request.content); calls.append(body)
                if len(calls) == 1:
                    task = {"id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                            "message": {"parts": [{"text": "X asking price: 123"}]}}}
                else:
                    price = int(body["params"]["message"]["parts"][0]["text"])
                    receipt = sign_receipt({"shop": NAME, "item": "X", "price": price, "task": "t1", "unit": "u1"}, key_path)
                    task = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                            "artifacts": [{"parts": [{"data": receipt}]}]}
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"task": task}})
            with httpx.Client(transport=httpx.MockTransport(handler)) as http:
                receipt = BOriginalClient("http://seller.test", http).buy("X", 150, 140)
            self.assertLessEqual(receipt["price"], 140)
            self.assertEqual(calls[0]["method"], "SendMessage")
            from scripts.market_kit import pub_b64, verify_seller
            self.assertTrue(verify_seller(pub_b64(key), receipt))

    def test_safe_restock_is_exact_and_purchase_free(self):
        self.assertEqual(safe_restock(), {"buy": []})


class OfficialAcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from scripts.market_kit import AcpShop
        self.temporary = tempfile.TemporaryDirectory(); root = Path(self.temporary.name)
        self.shop_key = make_key(root / "shop.pem")
        self.market_key = Ed25519PrivateKey.generate()
        body = {"unit": "u1", "sku": "X", "grade": "A", "expires_at": time.time() + 23 * 3600}
        cert = {"cert": body, "sig": base64.b64encode(self.market_key.sign(canon(body))).decode()}
        self.inventory = {"round": 3, "units": [{"sku": "X", "unit": "u1", "grade": "A",
            "expires_at": body["expires_at"], "supplied_at": 1, "cert": cert}]}
        self.market = FakeMarket(self.inventory)
        self.acp = AcpShop(self.shop_key, self.market)
        self.receipt = sign_receipt({"shop": NAME, "item": "X", "price": 113, "task": "t1", "unit": "u1"},
                                    root / "shop.pem")
        self.acp.terms["t1"] = self.receipt

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def test_acp_checkout_duplicate_and_bad_signature(self):
        body = {"line_items": [{"id": "X", "quantity": 1}], "acceptable": ["X"], "receipt": self.receipt}
        async def immediate(func, *args, **kwargs): return func(*args, **kwargs)
        with patch("scripts.market_kit.asyncio.to_thread", new=immediate):
            first = await self.acp.create(body, "http://shop.test")
            self.assertEqual(first.status_code, 201)
            duplicate = await self.acp.create(body, "http://shop.test")
            self.assertEqual(duplicate.status_code, 409)
            bad = await self.acp.create({**body, "receipt": {**self.receipt, "price": 114}}, "http://shop.test")
            self.assertEqual(bad.status_code, 400)

    async def test_official_certificate_accept_and_mock_ap2_forwarding(self):
        from scripts.market_kit import check_accept, pub_b64
        body = {"line_items": [{"id": "X", "quantity": 1}], "acceptable": ["X"], "receipt": self.receipt}
        async def immediate(func, *args, **kwargs): return func(*args, **kwargs)
        with patch("scripts.market_kit.asyncio.to_thread", new=immediate):
            response = await self.acp.create(body, "http://shop.test")
        session = json.loads(response.body)
        # Decode is unnecessary here: the same official certificate fields are checked directly.
        checkout = {"unit": "u1", "item": "X", "cert": self.inventory["units"][0]["cert"]}
        order = {"market_pubkey": pub_b64(self.market_key), "kind": "food", "shelf_hours": 24}
        self.assertIsNone(check_accept(order, checkout, {"cert": True, "grades": ["A"], "min_hours_left": 1}))
        class Request: method = "POST"
        payment = {"payment_data": {"order": "o1", "checkout_mandate": {"test": True},
                                     "payment_mandate": {"test": True}}}
        with patch("scripts.market_kit.asyncio.to_thread", new=immediate):
            completed = await self.acp.handle(Request(), f"/checkout_sessions/{session['id']}/complete",
                                              json.dumps(payment).encode(), "http://shop.test")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(self.market.settle_payload["order"], "o1")

    async def _complete_with_mode(self, mode: str):
        self.market.mode = mode
        body = {"line_items": [{"id": "X", "quantity": 1}], "acceptable": ["X"], "receipt": self.receipt}
        async def immediate(func, *args, **kwargs): return func(*args, **kwargs)
        with patch("scripts.market_kit.asyncio.to_thread", new=immediate):
            response = await self.acp.create(body, "http://shop.test")
            session = json.loads(response.body)
            class Request: method = "POST"
            payment = {"payment_data": {"order": "o1", "checkout_mandate": {"test": True},
                                         "payment_mandate": {"test": True}}}
            return await self.acp.handle(Request(), f"/checkout_sessions/{session['id']}/complete",
                                         json.dumps(payment).encode(), "http://shop.test"), session, Request, payment, immediate

    async def test_mock_market_rejects_amount_budget_and_same_owner(self):
        for mode in ("amount_mismatch", "over_budget", "same_owner"):
            with self.subTest(mode=mode):
                self.acp.sessions.clear()
                # Fresh task/session for each market-side rejection.
                self.receipt = {**self.receipt, "task": "t-" + mode}
                root = Path(self.temporary.name)
                self.receipt = sign_receipt({k: self.receipt[k] for k in ("shop", "item", "price", "task", "unit")},
                                            root / "shop.pem")
                self.acp.terms[self.receipt["task"]] = self.receipt
                response, *_ = await self._complete_with_mode(mode)
                self.assertEqual(response.status_code, 402)

    async def test_acp_inventory_error_and_duplicate_completion(self):
        self.market.mode = "inventory_error"
        body = {"line_items": [{"id": "X", "quantity": 1}], "acceptable": ["X"], "receipt": self.receipt}
        async def immediate(func, *args, **kwargs): return func(*args, **kwargs)
        with patch("scripts.market_kit.asyncio.to_thread", new=immediate):
            failed = await self.acp.create(body, "http://shop.test")
        self.assertEqual(failed.status_code, 409)
        self.market.mode = "ok"
        response, session, Request, payment, immediate = await self._complete_with_mode("ok")
        self.assertEqual(response.status_code, 200)
        with patch("scripts.market_kit.asyncio.to_thread", new=immediate):
            duplicate = await self.acp.handle(Request(), f"/checkout_sessions/{session['id']}/complete",
                                              json.dumps(payment).encode(), "http://shop.test")
        self.assertEqual(duplicate.status_code, 409)

    async def test_timeout_and_missing_success_fields_are_not_silently_accepted(self):
        with self.assertRaises(TimeoutError):
            await self._complete_with_mode("timeout")
        # A fresh signed task is needed after the timed-out session reserved the first task.
        self.acp.sessions.clear()
        root = Path(self.temporary.name)
        self.receipt = sign_receipt({"shop": NAME, "item": "X", "price": 113, "task": "t-missing", "unit": "u1"},
                                    root / "shop.pem")
        self.acp.terms["t-missing"] = self.receipt
        with self.assertRaises(KeyError):
            await self._complete_with_mode("missing")


if __name__ == "__main__":
    unittest.main()
