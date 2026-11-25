"""Public quote interoperability without real model calls or market trades."""
import json
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from seller_agent import DEFAULT, build
from shop_policy import quote_fields, selling_terms, policy_at
import test_closing as fixtures
from test_inventory import unit


class SellerQuoteContractTests(unittest.TestCase):
    setUp = fixtures.ClosingTests.setUp
    seller = fixtures.ClosingTests.seller
    receipt = fixtures.ClosingTests.receipt

    def facts(self, task):
        return next(p["data"] for p in task["status"]["message"]["parts"] if "data" in p)

    def test_quote_metadata_certificate_and_fast_accept_match(self):
        product = unit()
        product["secret_internal"] = "not public"
        seller, key = self.seller(product)
        with patch("seller_r3.inventory_decision", return_value=fixtures.counter(90000)) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = fixtures.send(client, "G00001")
            facts = self.facts(first)
            self.assertEqual(facts["cert"], product["cert"])
            self.assertEqual((facts["ask"], facts["list_price"], facts["grade"], facts["warranty_hours"]),
                             (90000, 100000, "B", 0))
            self.assertEqual(first["status"]["message"]["metadata"],
                             {"ask": 90000, "unit": "u1", "sku": "G00001", "final": False})
            self.assertNotIn("cost", json.dumps(facts))
            self.assertNotIn("secret_internal", json.dumps(first))
            self.assertFalse(first.get("artifacts"))
            done = fixtures.send(client, str(int(facts["ask"])), first["id"])
        self.receipt(done, key, 90000)
        self.assertEqual(model.call_count, 1)

    def test_food_publication_uses_market_time_and_never_promises_receipt_grade(self):
        market_now = time.time() - 780
        product = unit(kind="food", grade="C", expires_at=market_now + 36000)
        product["cert"]["cert"].update(grade=None, expires_at=product["expires_at"], kind="food")
        seller, key = self.seller(product)
        clock = {"status": "synced", "now": market_now, "uncertainty_seconds": 2}
        with patch("seller_r3.schedule_now", return_value=clock), \
                patch("seller_r3.inventory_decision", return_value=fixtures.counter(90000)), \
                TestClient(build(DEFAULT, seller)) as client:
            first = fixtures.send(client, "G00001")
            facts = self.facts(first)
            self.assertEqual(facts["hours_left"], 9.99)
            self.assertEqual(facts["grade_basis"], "inventory_snapshot")
            self.assertEqual(facts["quoted_at"], market_now)
            self.assertIn("9.99 h remaining", json.dumps(first))
            done = fixtures.send(client, "90000", first["id"])
        receipt = self.receipt(done, key, 90000)
        self.assertNotIn("claim", receipt)
        self.assertNotIn("warranty_hours", receipt)

    def test_untrusted_clock_or_wrong_certificate_is_not_fabricated(self):
        product = unit(kind="food", expires_at=time.time() + 3600)
        terms = selling_terms(product, policy_at())
        for clock in ({}, {"status": "stale", "now": 0, "uncertainty_seconds": 1},
                      {"status": "synced", "now": 0, "uncertainty_seconds": 7}):
            facts = quote_fields(product, 90000, terms, clock=clock)
            self.assertIsNone(facts["hours_left"])
            self.assertEqual(facts["expires_at"], product["expires_at"])
        for cert in (None, {"cert": {"unit": "other", "sku": product["sku"]}, "sig": "x"}):
            facts = quote_fields({**product, "cert": cert}, 90000, terms)
            self.assertIsNone(facts["cert"])

    def test_final_quote_and_card_describe_supported_contract(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[fixtures.counter(90000),
                   fixtures.counter(85000), fixtures.counter(80000)]), \
                TestClient(build(DEFAULT, seller)) as client:
            card = client.get("/.well-known/agent-card.json").json()
            self.assertIn("list_price", card["description"])
            self.assertIn("certificate", card["skills"][0]["tags"])
            self.assertNotIn("lamp", json.dumps(card["skills"][0]["examples"]))
            first = fixtures.send(client, "G00001")
            for offer in (60000, 61000, 62000):
                final = fixtures.send(client, str(offer), first["id"])
            facts = self.facts(final)
            self.assertTrue(facts["final"])
            self.assertEqual(facts["ask"], 73500)
            self.assertEqual(final["status"]["message"]["metadata"]["ask"], facts["ask"])
            self.assertFalse(final.get("artifacts"))
