"""One offline suite: real A2A/ACP code, local simulated settlement authority.

No production registration, order, mandate or settlement is requested.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_tools import audit_reciprocity
from payments import DiscoveredTools, SchemaAcpShop, pay, verify_checkout
from policy import RESERVES, load_policy
from seller import build
from signing import canon as receipt_canon
from vendor import market_kit_v3 as kit


class Fixture:
    def __init__(self, price=110, maximum=120, fault=None):
        self.shop, self.buyer, self.authority = (Ed25519PrivateKey.generate() for _ in range(3))
        self.url, self.calls, self.settlements, self.fault = "https://shop.test", [], 0, fault
        checkout = {"vct": "mandate.checkout.open.1", "constraints": [{"type": "checkout.line_items", "items": ["lamp"]}]}
        payment = {"vct": "mandate.payment.open.1", "constraints": [{"type": "payment.amount_range", "min": 0, "max": maximum}]}
        signed = lambda m: {"mandate": m, "sig": base64.b64encode(self.authority.sign(kit.canon(m))).decode()}
        self.order = {"order": "local-o-test", "item": "lamp", "budget": maximum,
                      "expires": time.time() + 60, "mandates": {"checkout": signed(checkout), "payment": signed(payment)}}
        self.receipt = {"shop": "Local Shop", "item": "lamp", "price": price, "task": "local-task"}
        self.receipt["sig_seller"] = base64.b64encode(self.shop.sign(receipt_canon(self.receipt))).decode()
        self.merchant = SchemaAcpShop(self.shop, self)
        self.client = httpx.Client(transport=httpx.MockTransport(self.route))

    def call(self, method, path, role, **kwargs):
        assert (method, path, role) == ("POST", "/api/settle", "shop")
        data = kwargs["json"]
        assert self.settlements == 0 and data["order"] == self.order["order"]
        assert self.order["expires"] > time.time()
        co = kit.jwt_payload(data["checkout_jwt"], kit.pub_b64(self.shop))
        assert co and co["price"] <= self.order["budget"] and co["item"] == self.order["item"]
        hashed = kit.b64url(hashlib.sha256(data["checkout_jwt"].encode()).digest())
        for field, open_name in (("checkout_mandate", "checkout"), ("payment_mandate", "payment")):
            closed = data[field]
            self.buyer.public_key().verify(base64.b64decode(closed["sig"]), kit.canon(closed["mandate"]))
            open_ = self.order["mandates"][open_name]
            self.authority.public_key().verify(base64.b64decode(open_["sig"]), kit.canon(open_["mandate"]))
            assert closed["mandate"]["open_mandate"] == hashlib.sha256(kit.canon(open_["mandate"])).hexdigest()
            assert closed["mandate"]["checkout_hash"] == hashed
            assert closed["mandate"]["order"] == self.order["order"]
        assert data["payment_mandate"]["mandate"]["amount"] == co["price"]
        assert data["payment_mandate"]["mandate"]["payee"] == self.url
        self.settlements += 1
        return httpx.Response(200, json={"settled": True, "ledger": 1,
            "mandate_receipt": {"ledger": 1, "local_fixture": True}})

    def route(self, request):
        self.calls.append((request.method, request.url.path))
        path = request.url.path.lstrip("/")
        if self.fault == "bad_total" and path.endswith("/complete"):
            raise AssertionError("unsafe complete must never be called")
        response = asyncio.run(self.merchant.handle(SimpleNamespace(method=request.method),
            path, request.content, self.url))
        if self.fault == "lost_response" and path.endswith("/complete"):
            raise httpx.ReadTimeout("simulated lost payment response")
        payload = json.loads(response.body)
        if self.fault == "bad_total" and request.method == "GET" and path.startswith("acp/"):
            payload["totals"][0]["amount"] += 1
        if self.fault == "forged" and request.method == "GET" and path.startswith("acp/"):
            payload["checkout_jwt"] = kit.jwt_sign(self.buyer, {"item": "lamp", "price": 1})
        return httpx.Response(response.status_code, json=payload)

    def pay(self, folder):
        return pay(self.order, self.url, kit.pub_b64(self.shop), self.receipt,
                   self.buyer, client=self.client, journal_dir=folder)


class CoreTests(unittest.TestCase):
    def test_normal_descriptor_tools_and_signed_mandates(self):
        f = Fixture()
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(f.pay(folder)["local_fixture"])
        self.assertEqual(f.settlements, 1)
        self.assertEqual([path for _, path in f.calls][-1].split("/")[-1], "complete")
        self.assertIn(("GET", "/.well-known/acp.json"), f.calls)

    def test_overspend_never_completes(self):
        f = Fixture(price=130, maximum=120)
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(f.pay(folder))
        self.assertEqual(f.settlements, 0)
        self.assertFalse(any(p.endswith("/complete") for _, p in f.calls))

    def test_forged_checkout_never_completes(self):
        f = Fixture(fault="forged")
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(f.pay(folder))
        self.assertEqual(f.settlements, 0)

    def test_displayed_total_mismatch_is_cancelled(self):
        f = Fixture(fault="bad_total")
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(f.pay(folder))
        self.assertEqual(f.settlements, 0)
        self.assertTrue(any(p.endswith("/cancel") for _, p in f.calls))

    def test_replay_returns_only_cached_receipt_without_second_payment(self):
        f = Fixture()
        with tempfile.TemporaryDirectory() as folder:
            first = f.pay(folder)
            calls = len(f.calls)
            self.assertEqual(first, f.pay(folder))
            self.assertEqual(calls, len(f.calls))
            f.receipt["price"] += 1
            self.assertIsNone(f.pay(folder))
        self.assertEqual(f.settlements, 1)

    def test_lost_complete_response_is_not_retried(self):
        f = Fixture(fault="lost_response")
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(f.pay(folder))
            self.assertIsNone(f.pay(folder))
        self.assertEqual(f.settlements, 1)
        self.assertEqual(sum(p.endswith("/complete") for _, p in f.calls), 1)

    def test_descriptor_cannot_redirect_to_another_host(self):
        f = Fixture()
        card = f.merchant.card(f.url)
        card["endpoint"] = "https://other.test/acp"
        with self.assertRaises(ValueError):
            DiscoveredTools(card, f.url, f.client)

    def test_quantity_and_expiry_constraints(self):
        f = Fixture()
        response = f.merchant.create({"line_items": [{"id": "lamp", "quantity": 2}], "receipt": f.receipt}, f.url)
        self.assertEqual(response.status_code, 400)
        f.order["expires"] = time.time() - 1
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(f.pay(folder))
        self.assertEqual(f.settlements, 0)

    def test_reciprocity_audit_is_symmetric_and_not_a_verdict(self):
        rows = [{"buyer_owner": "us", "shop_owner": "peer", "settled": True}] * 20
        rows += [{"buyer_owner": "peer", "shop_owner": "us", "settled": True}] * 20
        evidence = audit_reciprocity(rows)
        self.assertEqual(len(evidence["observed_pairs"]), 1)
        self.assertIn("not established", evidence["observed_pairs"][0]["interpretation"])

    def test_a2a_sale_reserve_and_terminal_state(self):
        async def run():
            # Patch only receipt signing to use an in-memory fixture key, not a
            # fake model. The real committed model policy executes unchanged.
            import seller
            key = Ed25519PrivateKey.generate()
            original = seller.sign_receipt
            def signing(receipt):
                return {**receipt, "sig_seller": base64.b64encode(key.sign(receipt_canon(receipt))).decode()}
            seller.sign_receipt = signing
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build(9999)), base_url="http://127.0.0.1:9999") as client:
                    async def send(text, task=None):
                        message = {"role": "ROLE_USER", "messageId": str(time.time_ns()), "parts": [{"text": text}]}
                        if task:
                            message["taskId"] = task
                        result = (await client.post("/", headers={"A2A-Version": "1.0"}, json={"jsonrpc": "2.0", "id": "local", "method": "SendMessage", "params": {"message": message}})).json()
                        return result
                    card = (await client.get("/.well-known/agent-card.json")).json()
                    self.assertEqual(card["supportedInterfaces"][0]["protocolVersion"], "1.0")
                    response = await send("I want to buy a lamp")
                    task = response["result"].get("task", response["result"])
                    tid = task["id"]
                    for offer in (60, 80, 100, 110, 120):
                        if task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                            break
                        self.assertGreaterEqual(task["status"]["message"]["metadata"]["ask"], 100)
                        response = await send(str(offer), tid)
                        task = response["result"].get("task", response["result"])
                    self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
                    rec = task["artifacts"][0]["parts"][0]["data"]
                    self.assertTrue(100 <= rec["price"] <= 120)
                    key.public_key().verify(base64.b64decode(rec["sig_seller"]), receipt_canon(rec))
                    self.assertIn("error", await send("120", tid))
            finally:
                seller.sign_receipt = original
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
