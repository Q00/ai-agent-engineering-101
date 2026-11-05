"""Forced closing decisions use no model and retain the real receipt guards."""
import json
from unittest.mock import patch
import unittest

from starlette.testclient import TestClient

from inventory import StockError
from receipt_leases import LeaseError, ReceiptLeaseStore
from seller_agent import DEFAULT, build
from test_adaptive_seller import send
import test_closing as fixtures
from test_inventory import unit

counter = fixtures.counter


class SellerFastCloseTests(unittest.TestCase):
    setUp = fixtures.ClosingTests.setUp
    seller = fixtures.ClosingTests.seller
    receipt = fixtures.ClosingTests.receipt

    def events(self, seller):
        return [json.loads(line) for line in seller.events.read_text().splitlines()]

    def test_forced_close_accepts_exact_bid_and_persists_signed_receipt_lease(self):
        ReceiptLeaseStore(self.root).initialize()
        with patch.dict("os.environ", {"MARKET_RECEIPT_GUARD": "1"}):
            seller, key = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(80000)]) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            send(client, "71000", first["id"])
            done = send(client, "72000", first["id"])
        receipt = self.receipt(done, key, 72000)
        self.assertEqual(model.call_count, 2)  # Opening and first negotiation still use Haiku.
        event = self.events(seller)[-1]
        self.assertEqual((event["stage"], event["active_floor"]), ("close", 71400))
        self.assertEqual(event["decision_source"], "closing_floor_accepted")
        self.assertEqual(event["discount_reason"], "improving_close_offer")
        lease = json.loads((self.root / "receipt-leases.json").read_text())["leases"][first["id"]]
        self.assertEqual(lease["status"], "issued")  # A receipt is not a settled sale.
        self.assertEqual(lease["receipt"]["sig_seller"], receipt["sig_seller"])
        self.assertEqual(seller.inventory.holds, {"u1": first["id"]})

    def test_final_offer_accepts_above_floor_below_previous_ask_without_model(self):
        seller, key = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(85000),
                counter(80000)]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for offer in (60000, 60000, 74000):
                done = send(client, str(offer), first["id"])
        self.receipt(done, key, 74000)
        self.assertEqual(model.call_count, 3)
        event = self.events(seller)[-1]
        self.assertEqual((event["stage"], event["decision_source"]),
                         ("final_offer", "closing_floor_accepted"))

    def test_below_floor_final_quote_then_rejection_skips_both_model_calls(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(85000),
                counter(80000)]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for offer in (60000, 61000, 62000):
                final = send(client, str(offer), first["id"])
            self.assertEqual(final["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertIn("Asking price: 73500.", json.dumps(final))
            self.assertFalse(final.get("artifacts"))
            self.assertEqual(self.events(seller)[-1]["decision_source"], "final_floor_quoted")
            done = send(client, "73499", first["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_REJECTED")
        self.assertFalse(done.get("artifacts"))
        self.assertEqual(model.call_count, 3)
        self.assertFalse(seller.inventory.active)
        self.assertFalse(seller.inventory.holds)

    def test_a_grade_does_not_receive_b_discount_and_keeps_warranty(self):
        seller, key = self.seller(unit(grade="A"))
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(85000),
                counter(80000)]) as model, TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            for offer in (71000, 72000, 72000):
                final = send(client, str(offer), first["id"])
            self.assertEqual(final["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertIn("Asking price: 77000.", json.dumps(final))
            done = send(client, "77000", first["id"])
        receipt = self.receipt(done, key, 77000)
        self.assertEqual((receipt["claim"], receipt["warranty_hours"]), ("A", 6))
        self.assertEqual(model.call_count, 3)
        self.assertIsNone(self.events(seller)[-1]["discount_reason"])

    def test_fast_close_rechecks_inventory_before_publishing(self):
        seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(80000)]) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            send(client, "71000", first["id"])
            product = seller.inventory.current(first["id"])
            with patch.object(seller.inventory, "current", side_effect=[product, StockError("Unit left inventory")]):
                failed = send(client, "72000", first["id"])
        self.assertEqual(failed["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(failed.get("artifacts"))
        self.assertEqual(model.call_count, 2)
        self.assertFalse(seller.inventory.active)
        self.assertFalse(seller.inventory.holds)

    def test_fast_close_cannot_publish_when_receipt_authorization_cannot_persist(self):
        ReceiptLeaseStore(self.root).initialize()
        with patch.dict("os.environ", {"MARKET_RECEIPT_GUARD": "1"}):
            seller, _ = self.seller()
        with patch("seller_r3.inventory_decision", side_effect=[counter(90000), counter(80000)]) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            send(client, "71000", first["id"])
            with patch.object(seller.inventory.leases, "issue", side_effect=LeaseError("Disk failure")) as issue:
                failed = send(client, "72000", first["id"])
        self.assertEqual(failed["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(failed.get("artifacts"))
        self.assertEqual(model.call_count, 2)
        issue.assert_called_once()
        self.assertFalse(seller.inventory.holds)
        self.assertFalse(json.loads((self.root / "receipt-leases.json").read_text())["leases"])
