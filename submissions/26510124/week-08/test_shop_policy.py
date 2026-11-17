"""User policy, real A2A quotes and official checkout; no live model or trades."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from feedback import atomic_json
from harness import HarnessError
from seller_agent import DEFAULT, build
from seller_r3 import InventorySeller, inventory_decision
from shop_policy import policy_at, quote_copy, selling_terms
from test_adaptive_seller import send
from test_inventory import unit
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent


class ShopPolicyTests(unittest.TestCase):
    def setUp(self):
        from test_market_fixture import normal_market_horizon
        normal_market_horizon(self)
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def seller(self, product):
        atomic_json(self.root / "inventory.json", {"round": 3, "units": [product]})
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.load_shop_key", return_value=key):
            seller = InventorySeller({**DEFAULT, "name": "ours", "market_dir": self.root})
        seller.events = self.root / "events.jsonl"
        return seller, key

    def test_grade_floors_round_up_and_only_a_goods_get_six_hours(self):
        policy = policy_at()
        for grade, floor, warranty in (("A", 112, 6), ("B", 107, 0), ("C", 101, 0)):
            terms = selling_terms(unit(grade=grade, cost=101), policy)
            self.assertEqual(terms["price_floor"], floor)
            self.assertEqual(terms["warranty_hours"], warranty)
        self.assertEqual(selling_terms(unit(grade="A", kind="food"), policy)["warranty_hours"], 0)

    def test_invalid_policy_fails_instead_of_weakening_bounds(self):
        for field, value in (("grade_margin_percent", {"A": 0, "B": 5, "C": 0}),
                             ("goods_warranty_hours", {"A": 999, "B": 0, "C": 0})):
            cfg = policy_at()
            cfg[field] = value
            atomic_json(self.root / "shop-policy.json", cfg)
            with self.assertRaises(ValueError):
                policy_at(self.root)

    def test_model_cannot_undercut_grade_floor_to_follow_advisor(self):
        def fake(prompt, context, validate, **kw):
            self.assertEqual(context["price_floor"], 77000)
            self.assertEqual(context["sales_terms"]["warranty_hours"], 6)
            return validate('{"action":"counter","price":76000,"reason":"discount"}')
        with patch("seller_r3.decide", side_effect=fake), self.assertRaises(HarnessError):
            inventory_decision(unit(grade="A"), None, [], None,
                               {"price_bias_percent": -5, "focus": "value", "pace": "quick"})

    def test_runtime_rejects_above_cost_but_below_grade_floor(self):
        seller, _ = self.seller(unit(grade="A"))
        with patch("seller_r3.inventory_decision", side_effect=[
                {"action": "counter", "price": 90000}, {"action": "accept", "price": 76000}]), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            result = send(client, "76000", first["id"])
        self.assertEqual(result["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(result.get("artifacts"))

    def test_promised_warranty_is_pinned_and_enters_official_checkout(self):
        product = unit(grade="A")
        seller, key = self.seller(product)
        with patch("seller_r3.inventory_decision", side_effect=[
                {"action": "counter", "price": 90000}, {"action": "accept", "price": 85000}]), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            text = json.dumps(first, ensure_ascii=False)
            self.assertIn("6-hour breakage warranty", text)
            self.assertIn("Grade A", text)
            seller.policy["goods_warranty_hours"]["A"] = 0
            done = send(client, "85000", first["id"])
        rec = done["artifacts"][0]["parts"][0]["data"]
        rec["price"] = int(rec["price"])
        self.assertEqual(rec["warranty_hours"], 6)
        self.assertTrue(kit.verify_seller(kit.pub_b64(key), rec))
        market = Mock()
        market.call.return_value.status_code = 200
        market.call.return_value.json.return_value = {"round": 3, "units": [product]}
        checkout = kit.AcpShop(key, market)
        checkout.terms[rec["task"]] = rec
        response = asyncio.run(checkout.create({"receipt": {**rec, "warranty_hours": 999},
                    "line_items": [{"id": rec["item"], "quantity": 1}]}, "https://fixture.invalid"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.body)["fulfillment"]["warranty_hours"], 6)

    def test_food_never_gets_goods_guarantee_or_fixed_grade_claim(self):
        product = unit(grade="A", kind="food", expires_at=time.time() + 10800)
        seller, _ = self.seller(product)
        with patch("seller_r3.inventory_decision", side_effect=[
                {"action": "counter", "price": 90000}, {"action": "accept", "price": 85000}]), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            done = send(client, "85000", first["id"])
        rec = done["artifacts"][0]["parts"][0]["data"]
        self.assertNotIn("warranty_hours", rec)
        self.assertNotIn("claim", rec)
        self.assertIn("Food expiry", json.dumps(first))

    def test_b_trial_quote_and_receipt_keep_coverage_after_policy_changes(self):
        product = unit(grade="B")
        seller, key = self.seller(product)
        now = time.time()
        sold = now - 7 * 3600
        atomic_json(self.root / "sales-performance.json", {
            "schema": 1, "round": 3, "updated_at": now, "sales": [
                {"ledger": i+1, "unit": f"old-{i}", "sku": product["sku"], "category": product["category"],
                 "kind": "goods", "grade": "B", "cost": 70000, "price": 100000,
                 "receipt_time": sold-1, "settled_at": sold, "observed_at": sold,
                 "supplied_at": sold-3600, "warranty_hours": 0} for i in range(5)]})
        with patch("seller_r3.inventory_decision", side_effect=[
                {"action": "counter", "price": 90000}, {"action": "accept", "price": 85000}]), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            self.assertIn("6-hour breakage warranty", json.dumps(first))
            self.assertGreater(seller.r3[first["id"]]["terms"]["closing_price_floor"], 80000)
            seller.policy["b_warranty_trial"]["enabled"] = False
            done = send(client, "85000", first["id"])
        receipt = done["artifacts"][0]["parts"][0]["data"]
        receipt["price"] = int(receipt["price"])
        self.assertEqual(receipt["warranty_hours"], 6)
        self.assertTrue(kit.verify_seller(kit.pub_b64(key), receipt))

    def test_focus_changes_real_card_and_quotes_without_free_text_or_cost_leaks(self):
        product = unit()
        seller, _ = self.seller(product)
        seller.advisor = Mock()
        original_policy = deepcopy(seller.policy)
        with TestClient(build(DEFAULT, seller)) as client:
            seller.advisor.for_product.return_value = {"focus": "value", "reason": "FAKE FREE ITEMS"}
            value = client.get("/.well-known/agent-card.json")
            seller.advisor.for_product.return_value = {"focus": "quality"}
            quality = client.get("/.well-known/agent-card.json", headers={"If-None-Match": value.headers["etag"]})
        self.assertEqual(quality.status_code, 200)
        self.assertNotEqual(value.headers["etag"], quality.headers["etag"])
        self.assertIn("Compare", value.json()["description"])
        self.assertIn("condition", quality.json()["description"])
        self.assertNotIn("FAKE", value.text)
        self.assertEqual(seller.policy, original_policy)
        text = quote_copy(product, 85000, selling_terms(product, seller.policy), {"focus": "quality"})
        self.assertIn("damaged packaging", text)
        self.assertNotIn("70000", text)
        self.assertTrue(text.endswith("Asking price: 85000."))
