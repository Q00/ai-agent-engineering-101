"""POLICY §11: deterministic prices, mock buying, isolated state migration."""
import copy
import json
import socket
import unittest
from unittest.mock import patch

import restock
import seller
import store
from test_unified_buying_offline import BuyingFixture, NOW


class DisposalPricingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()
        patch.object(seller.time, "time", return_value=NOW).start()
        patch.object(seller, "clearance_factor", return_value=1.0).start()
        patch.object(seller, "get_sku_market_anchor", return_value=19_000).start()
        patch.object(seller, "load_catalog", return_value={}).start()

    def price(self, hours=72, grade="A", kind="food", age=0, hub=None):
        unit = {"sku": "food", "cost": 10_000, "list_price": 20_000, "kind": kind,
                "live_grade": grade, "hours_left": hours, "supplied_at": NOW-age*3600}
        return seller.compute_unit_pricing_and_terms(unit, hub or {})

    def assertPrices(self, result, expected):
        self.assertEqual(tuple(result[k] for k in ("open_ask", "r1_target", "r2_target", "hard_floor")), expected)

    def test_food_boundaries(self):
        for hours, expected in [
                (72.0, (19_000, 13_780, 11_260, 10_300)),
                (71.9, (16_000, 14_800, 10_000, 10_000)),
                (48.0, (16_000, 14_800, 10_000, 10_000)),
                (47.9, (14_400, 12_800, 8_000, 8_000)),
                (24.0, (14_400, 12_800, 8_000, 8_000)),
                (23.9, (13_000, 11_000, 5_500, 5_500)),
                (12.0, (13_000, 11_000, 5_500, 5_500)),
                (11.9, (10_000, 8_400, 3_500, 3_500))]:
            with self.subTest(hours=hours):
                self.assertPrices(self.price(hours), expected)

    def test_grade_c_order_and_b_normal_prices(self):
        self.assertPrices(self.price(72, "C"), (14_400, 12_800, 7_800, 7_800))
        self.assertPrices(self.price(72, "B"), (16_800, 15_200, 10_000, 10_000))
        for grade in ("A", "B", "C"):
            for hours in (71.9, 48.0):
                with self.subTest(grade=grade, hours=hours):
                    self.assertPrices(self.price(hours, grade), (16_000, 14_800, 10_000, 10_000))
            self.assertPrices(self.price(47.9, grade), (14_400, 12_800, 8_000, 8_000))

    def test_goods_age_targets_keep_protected_floor(self):
        # Nov 6 intentional correction: age markdowns no longer reset the reserve;
        # accrued storage/current expected contribution also protect fresh goods.
        for age, expected in [(7.9, (19_000, 12_700, 10_859, 10_859)),
                              (8, (17_600, 10_860, 10_860, 10_860)),
                              (11.9, (17_600, 10_876, 10_876, 10_876)),
                              (12, (16_400, 10_877, 10_877, 10_877))]:
            with self.subTest(age=age):
                self.assertPrices(self.price(kind="goods", age=age), expected)

    def test_clearance_still_applies_above_new_food_stage(self):
        with patch.object(seller, "clearance_factor", return_value=.5):
            self.assertPrices(self.price(48), (9_250, 8_650, 6_250, 6_250))

    def test_no_rebuy_does_not_discount_food_or_goods(self):
        hub = {"no_rebuy": {"food": NOW-1}, "no_rebuy_overflow": True}
        for kind in ("food", "goods"):
            for grade in ("A", "B", "C"):
                for hours in (72, 48, 24, 12, 4):
                    with self.subTest(kind=kind, grade=grade, hours=hours):
                        self.assertEqual(self.price(hours, grade, kind), self.price(hours, grade, kind, hub=hub))


class DisposalBuyingTests(BuyingFixture, unittest.TestCase):
    def test_new_food_179_excluded_180_allowed_hot_exception(self):
        self.assertEqual(restock.FOOD_MIN_SHELF_HOURS, 180)
        for sku, shelf, inquiries in [("short", 179, 2), ("allowed", 180, 2), ("hot", 12, 4)]:
            self.item(sku, kind="food", shelf=shelf)
            self.inquiry(sku, inquiries)
        self.assertEqual(self.buys(), {"hot": 4, "allowed": 2})
        self.assertEqual(self.diag()["excluded"]["food_safety"], 1)

    def test_legacy_hub_merge_preserves_values_and_restart(self):
        raw = {"dead_skus": {"old": NOW-10, "both": NOW-20},
               "no_rebuy": {"new": NOW-5, "both": NOW-15},
               "dead_detection_since": NOW-3600, "dead_skus_overflow": True}
        path = self.path / "legacy.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        hub = store.load_hub(path)
        self.assertEqual(hub["no_rebuy"], {"old": NOW-10, "new": NOW-5, "both": NOW-15})
        self.assertEqual(hub["no_rebuy_detection_since"], NOW-3600)
        self.assertTrue(hub["no_rebuy_overflow"])
        self.assertFalse(any(k.startswith("dead_") for k in hub))
        expected = copy.deepcopy(hub)
        store.migrate_no_rebuy(hub)
        self.assertEqual(hub, expected)
        store.save_hub(hub, path)
        self.assertEqual(store.load_hub(path)["no_rebuy"], expected["no_rebuy"])
        self.assertFalse(any(k.startswith("dead_") for k in json.loads(path.read_text(encoding="utf-8"))))

    def test_migration_clears_legacy_overflow_even_when_new_flag_is_true(self):
        hub = {"dead_skus_overflow": False, "no_rebuy_overflow": True}
        store.migrate_no_rebuy(hub)
        self.assertEqual(hub, {"no_rebuy_overflow": True})

    def test_direct_planner_migrates_and_reports_no_rebuy(self):
        sku = self.item()
        self.inquiry(sku, 4)
        self.hub["dead_skus"] = {sku: NOW-1}
        self.assertEqual(self.buys(), {})
        self.assertNotIn("dead_skus", self.hub)
        self.assertEqual(self.diag()["no_rebuy_total"], 1)
        self.assertEqual(self.diag()["no_rebuy_new"], 0)
        self.assertEqual(self.diag()["excluded"]["no_rebuy"], 1)
        self.assertFalse(any(k.startswith("dead_") for k in self.diag()))


if __name__ == "__main__":
    unittest.main()
