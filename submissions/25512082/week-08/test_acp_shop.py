"""Kit-free ACP checkout: receipt binding, unit binding, settle forwarding (fake market, no network)."""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from starlette.testclient import TestClient

import acp_shop
from seller import SafeSeller, sign_receipt

UNIT = {"unit": "u-1", "sku": "G1", "name": "granola", "kind": "food", "grade": "A",
        "cost": 100, "list_price": 200, "supplied_at": 0, "expires_at": 9e12,
        "cert": {"cert": {"unit": "u-1", "sku": "G1"}, "sig": "x"}}


class FakeMarket:
    def __init__(self, settle_status=200):
        self.settle_status = settle_status
        self.settles = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/inventory":
            return httpx.Response(200, json={"round": 3, "units": [UNIT]})
        if request.url.path == "/api/settle":
            body = json.loads(request.content)
            self.settles.append(body)
            if self.settle_status != 200:
                return httpx.Response(self.settle_status, json={"settled": False, "checks": {"amount_in_range": False}})
            return httpx.Response(200, json={"settled": True, "ledger": 42,
                                             "mandate_receipt": {"unit": "u-1", "grade": "A", "name": "granola", "ledger": 42}})
        return httpx.Response(404)


class AcpShopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.key_path = self.tmp / "shop_key.pem"
        self.key = Ed25519PrivateKey.generate()
        self.key_path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
        acp_shop.LOG_PATH = self.tmp / "acp-shop.log"

    def shop(self, settle_status=200):
        fake = FakeMarket(settle_status)
        market = acp_shop.MarketClient("http://market", self.key, None,
                                       client=httpx.Client(transport=httpx.MockTransport(fake.handler)))
        market.token = "t"
        app, checkout = acp_shop.build_shop(1234, "http://shop.test", self.key_path, self.tmp,
                                            self.tmp / "ledger.json", market)
        receipt = sign_receipt({"shop": "S", "item": "G1", "price": 150, "task": "task-1", "unit": "u-1", "claim": "A"},
                               self.key_path)
        checkout.seller.issued["task-1"] = receipt
        return TestClient(app), checkout, fake, receipt

    def test_card_points_at_own_host(self):
        client, *_ = self.shop()
        card = client.get("/.well-known/acp.json").json()
        self.assertEqual(card["endpoint"], "http://shop.test/acp")
        self.assertEqual(card["capabilities"]["payment_methods"], ["ap2-mandate"])

    def test_create_signs_checkout_bound_to_receipt_and_unit(self):
        client, _, _, receipt = self.shop()
        r = client.post("/acp/checkout_sessions", json={"line_items": [{"id": "G1", "quantity": 1}], "receipt": receipt})
        self.assertEqual(r.status_code, 201)
        head, body, sig = r.json()["checkout_jwt"].split(".")
        Ed25519PublicKey.from_public_bytes(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).verify(
            base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), f"{head}.{body}".encode())
        co = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        self.assertEqual((co["price"], co["task"], co["unit"], co["claim"]), (150, "task-1", "u-1", "A"))

    def test_rejects_tampered_price_and_unknown_receipt(self):
        client, _, _, receipt = self.shop()
        items = [{"id": "G1", "quantity": 1}]
        self.assertEqual(client.post("/acp/checkout_sessions", json={"line_items": items, "receipt": {**receipt, "price": 1}}).status_code, 400)
        self.assertEqual(client.post("/acp/checkout_sessions", json={"line_items": items, "receipt": {**receipt, "task": "other"}}).status_code, 400)

    def test_one_session_per_receipt(self):
        client, _, _, receipt = self.shop()
        body = {"line_items": [{"id": "G1", "quantity": 1}], "receipt": receipt}
        self.assertEqual(client.post("/acp/checkout_sessions", json=body).status_code, 201)
        self.assertEqual(client.post("/acp/checkout_sessions", json=body).status_code, 409)

    def test_complete_forwards_mandates_to_settle_once(self):
        client, _, fake, receipt = self.shop()
        sid = client.post("/acp/checkout_sessions", json={"line_items": [{"id": "G1", "quantity": 1}], "receipt": receipt}).json()["id"]
        pay = {"payment_data": {"order": "o-1", "checkout_mandate": {"m": 1}, "payment_mandate": {"m": 2}}}
        r = client.post(f"/acp/checkout_sessions/{sid}/complete", json=pay)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "completed")
        self.assertEqual(fake.settles[0]["order"], "o-1")
        self.assertIn("checkout_jwt", fake.settles[0])
        self.assertEqual(client.post(f"/acp/checkout_sessions/{sid}/complete", json=pay).status_code, 409)
        self.assertEqual(len(fake.settles), 1)

    def test_market_refusal_is_402_and_session_stays_payable(self):
        client, checkout, _, receipt = self.shop(settle_status=400)
        sid = client.post("/acp/checkout_sessions", json={"line_items": [{"id": "G1", "quantity": 1}], "receipt": receipt}).json()["id"]
        r = client.post(f"/acp/checkout_sessions/{sid}/complete", json={"payment_data": {}})
        self.assertEqual(r.status_code, 402)
        self.assertEqual(checkout.sessions[sid]["status"], "ready_for_payment")

    def test_unit_not_in_stock_is_refused(self):
        client, checkout, _, _ = self.shop()
        other = sign_receipt({"shop": "S", "item": "G1", "price": 150, "task": "task-2", "unit": "u-gone"}, self.key_path)
        checkout.seller.issued["task-2"] = other
        r = client.post("/acp/checkout_sessions", json={"line_items": [{"id": "G1", "quantity": 1}], "receipt": other})
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
