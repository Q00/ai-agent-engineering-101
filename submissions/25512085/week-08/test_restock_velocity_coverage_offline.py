"""Depth now follows 40-minute inquiries, not 20-minute sales or price bands."""
import unittest

from test_unified_buying_offline import BuyingFixture, NOW


class VelocityCoverageTests(BuyingFixture, unittest.TestCase):
    def test_three_own_sales_do_not_imply_four_units(self):
        sku = self.item()
        self.hub["own_sale_events"] = {str(i): {"sku": sku, "ts": NOW} for i in range(3)}
        self.assertEqual(self.buys(), {})

    def test_seven_inquiries_target_seven_and_large_counts_cap_at_eight(self):
        a, b = self.item("a"), self.item("b")
        self.inquiry(a, 7)
        self.inquiry(b, 100)
        self.assertEqual(self.buys(), {a: 7, b: 8})

    def test_old_inquiries_do_not_keep_base_three(self):
        sku = self.item()
        self.inquiry(sku, 20, age=2401)
        self.assertEqual(self.buys(), {})

    def test_expired_stock_does_not_satisfy_target(self):
        sku = self.item()
        self.stock(sku, age=1000, grade="expired")
        self.inquiry(sku, 2)
        self.assertEqual(self.buys(), {sku: 2})

    def test_same_price_band_does_not_hide_distinct_popular_sku(self):
        a, b = self.item("a"), self.item("b")
        self.stock(a)
        self.inquiry(b, 2)
        self.assertEqual(self.buys(), {b: 2})

    def test_food_has_no_global_twenty_unit_cap(self):
        a, b = self.item("a", kind="food"), self.item("b", kind="food")
        self.stock(a, n=20)
        self.inquiry(b, 4)
        self.assertEqual(self.buys(), {b: 4})

    def test_popular_partial_target_not_sales_multiplier(self):
        sku = self.item()
        self.stock(sku)
        self.inquiry(sku, 3, miss=False)
        self.assertEqual(self.buys(), {sku: 1})

    def test_transport_limits_remain_ten_lines_and_twenty_per_line(self):
        for i in range(15):
            self.inquiry(self.item(str(i)), 100)
        plan = self.plan()
        self.assertEqual(len(plan["buy"]), 10)
        self.assertTrue(all(1 <= b["qty"] <= 20 for b in plan["buy"]))


if __name__ == "__main__":
    unittest.main()
