import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from feedback import atomic_json
from buyer_policy import buying_terms
from market_buyer import (BuyerError, acceptance, choose_shop, last_asking_price,
                          stocked_shops, trade_and_record)
from payment_memory import recent_jsonl, reconcile
from review_settled import review
from seller_agent import DEFAULT, build
from seller_r3 import InventorySeller
from test_inventory import unit
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent


class BuyerContractTests(unittest.TestCase):
    def test_quote_parser_does_not_accept_sku_or_warranty_as_price(self):
        self.assertEqual(last_asking_price("G00001 asking 85,000. Warranty 6 hours."), 85000)
        self.assertEqual(last_asking_price("Counteroffer: 85000."), 85000)
        self.assertIsNone(last_asking_price("G00001; warranty 6 hours."))
        self.assertIsNone(last_asking_price("List price: 100000; asking price: 85000"))

    def test_only_stock_for_current_order_reaches_model(self):
        shops = [{"name": "empty", "offers": []}, {"name": "stocked", "url": "https://shop.invalid", "offers": [
            {"sku": "G00001", "name": "의자", "qty": 1}, {"sku": "G00002", "qty": 3}]}]
        filtered = stocked_shops(shops, {"acceptable": ["G00001"]})
        self.assertEqual(len(filtered), 1)
        def model(_, ctx, validate, **kwargs):
            self.assertEqual(ctx["candidates"][0]["offers"][0]["sku"], "G00001")
            return validate('{"name":"stocked","sku":"G00001","reason":"available"}')
        with patch("market_buyer.decide", side_effect=model):
            selected, _ = choose_shop(filtered, [], item="의자", budget=90000, deadline=time.monotonic()+10)
        self.assertEqual(selected["selected_sku"], "G00001")
        with self.assertRaises(BuyerError):
            stocked_shops(shops, {"acceptable": ["G99999"]})

    def test_food_policy_matches_real_v4_certificate_checker(self):
        key = Ed25519PrivateKey.generate()
        body = {"unit": "u1", "sku": "G00001", "grade": "A", "expires_at": time.time() + 7200}
        cert = {"cert": body, "sig": base64.b64encode(key.sign(kit.canon(body))).decode()}
        order = {"kind": "food", "use_within_hours": 2, "market_pubkey": kit.pub_b64(key)}
        checkout = {"unit": "u1", "item": "G00001", "cert": cert}
        self.assertIn("shelf life", kit.check_accept(order, checkout, acceptance(order)))
        order["use_within_hours"] = .5
        self.assertIsNone(kit.check_accept(order, checkout, acceptance(order)))
        self.assertIn("no certificate", kit.check_accept(order, {"unit": "u1", "item": "G00001"}, acceptance(order)))

    def test_agreement_is_pending_until_matching_payment(self):
        record = {"name": "shop", "url": "https://shop.invalid", "round": 3, "order": "o1",
                  "item": "G00001", "price": 85000, "result": "receipt_issued"}
        payment = {"shop": record["url"], "order": "o1", "item": "G00001", "price": 85000,
                   "paid": True, "ledger": 17}
        self.assertEqual(reconcile([record], [])[0]["result"], "receipt_issued")
        self.assertEqual(reconcile([record], [payment])[0]["result"], "settled")
        self.assertEqual(record["result"], "receipt_issued")
        refused = {**payment, "paid": False, "reason": "no certificate"}
        self.assertEqual(reconcile([record], [refused])[0]["result"], "payment_refused")
        self.assertEqual(reconcile([record], [{**payment, "price": 1}])[0]["result"], "receipt_issued")

    def test_settled_review_does_not_expose_budget(self):
        deal = {"order": {"order": "o1", "budget": 123456}, "receipt": {"price": 85000},
                "settled": {"sku": "G00001", "grade": "B", "ledger": 17, "claim_honest": True}}
        self.assertNotIn("123456", review(deal)["review"])
        deal["settled"]["claim_honest"] = False
        self.assertEqual(review(deal)["stars"], 1)

    def test_buyer_and_inventory_seller_trade_without_model_or_network(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            folder = Path(d)
            atomic_json(folder / "inventory.json", {"round": 3, "units": [unit(cost=50000, grade="A")]})
            with patch("seller_agent.load_shop_key", return_value=Ed25519PrivateKey.generate()):
                seller = InventorySeller({**DEFAULT, "name": "shop", "market_dir": folder})
            seller.events = folder / "seller.jsonl"
            with patch("seller_r3.inventory_decision", side_effect=[
                    {"action": "counter", "price": 65000}, {"action": "accept", "price": 60000}]), \
                    patch("market_buyer.buyer_decision", return_value={"action": "offer", "price": 60000, "reason": "test"}), \
                    TestClient(build(DEFAULT, seller)) as client:
                result = trade_and_record({"name": "shop", "url": "http://testserver"}, item="G00001", budget=99000,
                                          memory_path=folder / "memory.jsonl", reason="fixture", seconds=10, client=client,
                                          order={"order": "o1", "round": 3, "kind": "goods"},
                                          policy=buying_terms(99000, product={"list_price": 100000},
                                                             order={"round": 3, "kind": "goods"}))
            self.assertIsNotNone(result["receipt"])
            self.assertEqual(result["receipt"]["unit"], "u1")
            self.assertEqual(result["accept"], {"cert": True, "grades": ["A"], "warranty_hours": 6})
            record = recent_jsonl(folder / "memory.jsonl")[0]
            self.assertEqual(record["result"], "receipt_issued")
            self.assertNotIn("99000", result["review"])

    def test_memory_reader_ignores_partial_append(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            p = Path(d) / "memory.jsonl"
            p.write_text('{"result":"receipt_issued"}\n{"unfinished"')
            self.assertEqual(len(recent_jsonl(p)), 1)
