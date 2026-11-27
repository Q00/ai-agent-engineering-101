"""Presence-only signature fixtures; no real checkout or cryptographic claim."""
import json
import unittest
from unittest.mock import patch

import restock
import store
import test_buyer_retry_offline as retry
import test_restock_assortment_offline as assortment


class SignatureAuditTests(unittest.TestCase):
    setUp = retry.BuyerRetryTests.setUp
    run_market = retry.BuyerRetryTests.run_market
    assert_single_success = retry.BuyerRetryTests.assert_single_success

    def test_unsigned_retries_without_success_memory(self):
        run = self.run_market(["unsigned", "valid"])
        self.assert_single_success(run)
        self.assertEqual(run.sends, [0, 1])
        self.assertIn("missing sig_seller", run.diagnostic)
        self.assertNotIn("Ed25519 receipt verified", run.result["review"])

    def test_all_unsigned_leave_no_pending(self):
        run = self.run_market(["unsigned"] * 3)
        self.assertIsNone(run.result["receipt"])
        self.assertFalse(self.hub.get("pending_deals"))


class DemandAuditTests(unittest.TestCase):
    setUp = assortment.AssortmentTests.setUp
    item = assortment.AssortmentTests.item
    stock = assortment.AssortmentTests.stock
    plan = assortment.AssortmentTests.plan

    def test_lowercase_sku_and_raw_leaf_map_once_to_full_category(self):
        self.item("G12345678", "goods>침구")
        radar = self.hub["demand_radar"]
        radar["sku_inquiries"] = {"g12345678": [{"ts": assortment.NOW, "miss": True}]*2}
        radar["missed_wants"] = {"침구": 1000}  # category totals no longer imply SKU demand
        self.assertEqual(self.plan()["buy"], [{"sku": "G12345678", "qty": 2}])

    def test_missed_sku_price_gap_restores_one_not_stocked_depth(self):
        self.item("G12345678", "goods>침구", 120_000)
        self.item("G87654321", "goods>침구", 20_000)
        self.stock("G87654321")
        radar = self.hub["demand_radar"]
        radar["sku_inquiries"] = {"g12345678": [{"ts": assortment.NOW, "miss": True}]}
        self.assertEqual(self.plan()["buy"], [{"sku": "G12345678", "qty": 1}])
        self.assertEqual(self.plan()["buy"], [])
        path = self.path / "isolated-hub.json"
        with patch.object(store.time, "time", return_value=assortment.NOW):
            store.record_inbound_inquiry("g12345678", False, path,
                                         "G12345678 G12345678")
        recorded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(recorded["demand_radar"]["missed_ts"]["g12345678"],
                         [assortment.NOW])


if __name__ == "__main__":
    unittest.main()
