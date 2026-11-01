import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from feedback import atomic_json
from inventory import Inventory, StockError
from seller_agent import DEFAULT, build
from seller_r3 import InventorySeller
from test_adaptive_seller import send
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent


def unit(**changes):
    return {"unit": "u1", "sku": "G00001", "name": "테스트 의자", "category": "가구>의자",
            "kind": "goods", "cost": 70000, "list_price": 100000, "grade": "B",
            "supplied_at": time.time() - 100, "expires_at": None,
            "cert": {"cert": {"unit": "u1", "sku": "G00001", "grade": "B"}, "sig": "fixture"}, **changes}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write([unit()])

    def write(self, units, round=3):
        atomic_json(self.root / "inventory.json", {"round": round, "units": units})

    def test_unknown_explicit_sku_does_not_sell_different_item(self):
        stock = Inventory(self.root)
        with self.assertRaises(StockError):
            stock.reserve("a", "의자 (G00099)")
        self.assertEqual(stock.reserve("a", "G00001")["unit"], "u1")
        with self.assertRaises(StockError):
            stock.reserve("b", "G00001")

    def test_receipt_hold_survives_restart_until_inventory_removes_it(self):
        stock = Inventory(self.root)
        stock.reserve("a", "G00001")
        stock.issue("a")
        restarted = Inventory(self.root)
        with self.assertRaises(StockError):
            restarted.reserve("b", "G00001")
        self.write([])
        restarted.available()
        self.assertEqual(restarted.holds, {})

    def test_expired_and_stale_inventory_never_sells(self):
        self.write([unit(kind="food", expires_at=time.time() - 1)])
        with self.assertRaises(StockError):
            Inventory(self.root).reserve("a", "G00001")
        self.write([unit()])
        old = time.time() - 60
        os.utime(self.root / "inventory.json", (old, old))
        with self.assertRaises(StockError):
            Inventory(self.root).reserve("b", "G00001")

    def test_earliest_expiry_then_release(self):
        self.write([unit(unit="later", kind="food", expires_at=time.time() + 500),
                    unit(unit="sooner", kind="food", expires_at=time.time() + 100)])
        stock = Inventory(self.root)
        self.assertEqual(stock.reserve("a", "G00001")["unit"], "sooner")
        stock.release("a")
        self.assertEqual(stock.reserve("b", "G00001")["unit"], "sooner")

    def seller(self):
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.load_shop_key", return_value=key):
            seller = InventorySeller({**DEFAULT, "name": "dyishappy", "market_dir": self.root})
        seller.events = self.root / "events.jsonl"
        return seller, key

    def test_real_a2a_receipt_opens_official_v4_checkout(self):
        seller, key = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[
                {"action": "counter", "price": 90000}, {"action": "accept", "price": 85000}]), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "I want G00001")
            competing = send(client, "I want G00001")
            self.assertEqual(competing["status"]["state"], "TASK_STATE_REJECTED")
            done = send(client, "85000", first["id"])
        rec = done["artifacts"][0]["parts"][0]["data"]
        rec["price"] = int(rec["price"])  # kit normalizes A2A integer doubles
        self.assertEqual(rec["unit"], "u1")
        self.assertTrue(kit.verify_seller(kit.pub_b64(key), rec))
        class Market:
            def call(_, *args, **kwargs):
                return type("Response", (), {"status_code": 200, "json": lambda _: {"round": 3, "units": [unit()]}})()
        checkout = kit.AcpShop(key, Market())
        checkout.terms[rec["task"]] = rec
        # Tampering with buyer-side unsigned terms cannot change seller terms.
        response = asyncio.run(checkout.create({"receipt": {**rec, "unit": "forged", "claim": "A"},
                                                "line_items": [{"id": rec["item"], "quantity": 1}]},
                                               "https://fixture.invalid"))
        self.assertEqual(response.status_code, 201)
        co = json.loads(response.body)
        self.assertEqual(co["fulfillment"]["unit"], "u1")
        self.assertEqual(co["fulfillment"]["claim"], "B")
        self.assertEqual(json.loads(seller.events.read_text())["outcome"], "receipt_issued")

    def test_expiry_during_model_call_prevents_receipt(self):
        seller, _ = self.seller()
        def model(*_):
            self.write([unit(kind="food", expires_at=time.time() - 1)])
            return {"action": "counter", "price": 90000}
        with patch("seller_r3.inventory_decision", side_effect=model), TestClient(build(DEFAULT, seller)) as client:
            done = send(client, "G00001")
        self.assertEqual(done["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(done.get("artifacts"))
        self.assertFalse(seller.inventory.active)

    def test_wrong_amount_and_below_cost_cannot_issue_receipt(self):
        for choice in ({"action": "accept", "price": 69000}, {"action": "accept", "price": 85001}):
            seller, _ = self.seller()
            with patch("seller_r3.inventory_decision", side_effect=[{"action": "counter", "price": 90000}, choice]), \
                    TestClient(build(DEFAULT, seller)) as client:
                first = send(client, "G00001")
                done = send(client, "85000", first["id"])
            self.assertEqual(done["status"]["state"], "TASK_STATE_FAILED")
            self.assertFalse(done.get("artifacts"))
