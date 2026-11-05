"""Closing-policy behavior through real A2A, with synthetic inventory/models."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from feedback import atomic_json
from harness import HarnessError
from seller_agent import DEFAULT, build
from seller_r3 import InventorySeller, inventory_decision
from shop_policy import closing_context, policy_at, selling_terms
from test_adaptive_seller import send
from test_inventory import unit
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent


def counter(price):
    return {"action": "counter", "price": price, "reason": "fixture quote"}


class ClosingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def seller(self, product=None):
        atomic_json(self.root / "inventory.json", {"round": 3, "units": [product or unit()]})
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.load_shop_key", return_value=key):
            seller = InventorySeller({**DEFAULT, "name": "ours", "market_dir": self.root})
        seller.events = self.root / "events.jsonl"
        return seller, key

    def receipt(self, done, key, price):
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        rec = done["artifacts"][0]["parts"][0]["data"]
        rec["price"] = int(rec["price"])
        self.assertEqual(rec["price"], price)
        self.assertTrue(kit.verify_seller(kit.pub_b64(key), rec))
        return rec

    def test_second_improving_offer_closes_b_grade_at_72000(self):
        seller, key = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(80000),
                    {"action": "accept", "price": 72000}]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            send(client, "71000", first["id"])
            done = send(client, "72000", first["id"])
        self.receipt(done, key, 72000)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args.args[0]["sales_terms"]["price_floor"], 73500)
        event = json.loads(seller.events.read_text().splitlines()[-1])
        self.assertEqual((event["stage"], event["active_floor"]), ("close", 71400))
        self.assertEqual(event["decision_source"], "closing_floor_accepted")
        self.assertEqual(event["discount_reason"], "improving_close_offer")
        self.assertEqual(event["buyer_offers"], 2)
        self.assertEqual(event["outcome"], "receipt_issued")  # not settled revenue

    def test_extra_margin_discount_is_not_available_on_first_offer(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000),
                    {"action": "accept", "price": 72000}]), TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            done = send(client, "72000", first["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(done.get("artifacts"))
        self.assertFalse(seller.inventory.active)

    def test_final_quote_has_an_acceptance_turn_without_another_model_call(self):
        seller, key = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(85000),
                    counter(80000), counter(73500)]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for price in (60000, 61000, 62000):
                final = send(client, str(price), first["id"])
            self.assertEqual(final["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertIn("Final offer", json.dumps(final))
            self.assertNotIn("70000", json.dumps(final))
            self.assertNotIn("71400", json.dumps(final))
            done = send(client, "73500", first["id"])
        self.receipt(done, key, 73500)
        self.assertEqual(model.call_count, 3)
        event = json.loads(seller.events.read_text().splitlines()[-1])
        self.assertEqual(event["decision_source"], "quoted_price_accepted")
        self.assertEqual(event["buyer_offers"], 4)
        self.assertFalse(seller.r3)

    def test_below_final_offer_ends_without_model_or_receipt_and_releases_unit(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(85000),
                    counter(80000), counter(73500)]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for price in (60000, 61000, 62000, 72000):
                done = send(client, str(price), first["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_REJECTED")
        self.assertFalse(done.get("artifacts"))
        self.assertEqual(model.call_count, 3)
        self.assertEqual(seller.inventory.reserve("new-buyer", "G00001")["unit"], "u1")

    def test_agreeing_an_existing_quote_skips_model_and_keeps_exact_authorized_price(self):
        for offer in (90000, 91000):
            with self.subTest(offer=offer):
                seller, key = self.seller()
                with patch("seller_r3.inventory_decision", return_value=counter(90000)) as model, \
                        TestClient(build(DEFAULT, seller)) as client:
                    first = send(client, "G00001")
                    done = send(client, str(offer), first["id"])
                self.receipt(done, key, offer)
                self.assertEqual(model.call_count, 1)
                # Reset fixture-only receipt hold between independent cases.
                seller.inventory.hold_path.unlink()

    def test_final_quote_for_aged_b_stock_uses_71400_without_loss(self):
        seller, key = self.seller(unit(supplied_at=time.time() - 90000))
        with patch("seller_r3.inventory_decision", side_effect=[counter(75000), counter(74500),
                    counter(74000), counter(71400)]), TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for price in (60000, 60000, 60000):
                send(client, str(price), first["id"])
            done = send(client, "71400", first["id"])
        self.receipt(done, key, 71400)

    def test_a_warranty_and_floor_stay_pinned_when_policy_changes_mid_deal(self):
        seller, key = self.seller(unit(grade="A", supplied_at=time.time() - 90000))
        with patch("seller_r3.inventory_decision", side_effect=[counter(79300), counter(79000),
                    counter(78500), counter(77000)]), TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            seller.policy["closing"]["grade_margin_percent"]["A"] = 0
            seller.policy["closing"]["final_offer"] = 5
            seller.policy["goods_warranty_hours"]["A"] = 0
            for price in (60000, 61000, 62000):
                final = send(client, str(price), first["id"])
            self.assertIn("Final offer", json.dumps(final))
            done = send(client, "77000", first["id"])
        self.assertEqual(self.receipt(done, key, 77000)["warranty_hours"], 6)

    def test_fast_accept_still_rejects_stale_or_expired_inventory(self):
        seller, _ = self.seller(unit(kind="food", expires_at=time.time() + 10800))
        with patch("seller_r3.inventory_decision", return_value=counter(90000)) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            atomic_json(self.root / "inventory.json", {"round": 3,
                        "units": [unit(kind="food", expires_at=time.time() - 1)]})
            done = send(client, "90000", first["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_REJECTED")
        self.assertFalse(done.get("artifacts"))
        self.assertEqual(model.call_count, 1)

    def test_repeated_or_token_improvements_do_not_unlock_discount(self):
        product = unit()
        for offers in ([72000, 72000], [72000, 72001, 72002], [72000, 60000, 72001]):
            deal = {"terms": selling_terms(product, policy_at()), "offers": offers}
            plan = closing_context(product, deal, time.time())
            self.assertEqual(plan["price_floor"], 73500)
            self.assertIsNone(plan["discount_reason"])

    def test_progress_must_also_close_the_gap_and_unlock_is_monotonic(self):
        product = unit()
        deal = {"terms": selling_terms(product, policy_at()), "offers": [60000, 61000]}
        self.assertEqual(closing_context(product, deal, time.time())["price_floor"], 73500)
        deal["offers"] = [70000, 71000, 72000]
        deal["negotiation"] = closing_context(product, deal, time.time())
        self.assertEqual(deal["negotiation"]["price_floor"], 71400)
        deal["offers"].append(60000)
        self.assertEqual(closing_context(product, deal, time.time())["price_floor"], 71400)

    def test_only_verified_time_urgency_unlocks_discount(self):
        for changes, expected in (({"kind": "food", "expires_at": time.time() + 3600}, 71400),
                                  ({"kind": "food", "expires_at": time.time() + 9000}, 73500),
                                  ({"supplied_at": "yesterday"}, 73500),
                                  ({"supplied_at": float("nan")}, 73500)):
            product = unit(**changes)
            deal = {"terms": selling_terms(product, policy_at()), "offers": [60000, 60000]}
            self.assertEqual(closing_context(product, deal, time.time())["price_floor"], expected)

    def test_bad_closing_settings_fail_instead_of_weakening_floors(self):
        for change in ({"grade_margin_percent": {"A": 10, "B": -1, "C": 0}},
                       {"grade_margin_percent": {"A": 11, "B": 2, "C": 0}},
                       {"start_offer": True}, {"start_offer": 3, "final_offer": 2},
                       {"final_offer": 100}, {"min_improvement_percent": 0}):
            cfg = policy_at()
            cfg["closing"].update(change)
            atomic_json(self.root / "shop-policy.json", cfg)
            with self.assertRaises(ValueError):
                policy_at(self.root)

    def test_model_validation_enforces_closing_policy_and_uses_active_floor(self):
        product = unit()
        deal = {"terms": selling_terms(product, policy_at()), "offers": [71000, 72000]}
        plan = closing_context(product, deal, time.time())
        enriched = {**product, "sales_terms": deal["terms"], "negotiation": plan}
        def model(prompt, context, validate, **_):
            self.assertEqual(context["price_floor"], 71400)
            self.assertEqual(context["sales_terms"]["price_floor"], 73500)
            for choice in (counter(76000), {"action": "reject", "reason": "low price"},
                           {"action": "accept", "price": 71400, "reason": "wrong amount"}):
                with self.assertRaises(HarnessError):
                    validate(json.dumps(choice))
            return validate('{"action":"accept","price":72000,"reason":"close sale"}')
        with patch("seller_r3.decide", side_effect=model):
            self.assertEqual(inventory_decision(enriched, 72000, [], 80000)["action"], "accept")

    def test_model_first_negotiation_never_signs_below_floor_or_raises_quote(self):
        for bad in ({"action": "accept", "price": 71000}, counter(76000)):
            seller, _ = self.seller(unit(supplied_at=time.time() - 90000))
            with patch("seller_r3.inventory_decision", side_effect=[counter(75000), bad]), \
                    TestClient(build(DEFAULT, seller)) as client:
                first = send(client, "G00001")
                done = send(client, "71000", first["id"])
            self.assertEqual(done["status"]["state"], "TASK_STATE_FAILED")
            self.assertFalse(done.get("artifacts"))
            self.assertFalse(seller.inventory.active)

    def test_non_price_message_does_not_consume_final_acceptance_chance(self):
        seller, key = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(85000),
                    counter(80000), counter(73500)]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for price in (60000, 60000, 60000):
                send(client, str(price), first["id"])
            reminder = send(client, "Can you do better?", first["id"])
            self.assertIn("73500", json.dumps(reminder))
            done = send(client, "73500", first["id"])
        self.receipt(done, key, 73500)
        self.assertEqual(model.call_count, 3)
