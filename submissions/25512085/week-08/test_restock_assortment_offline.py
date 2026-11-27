"""Assortment regressions migrated from deleted exploration/whitelist rules to §9."""
import unittest

from test_unified_buying_offline import BuyingFixture, NOW


class AssortmentTests(BuyingFixture, unittest.TestCase):
    def test_cold_start_requires_popularity_not_seed_whitelist(self):
        self.item("G97cd0437")
        self.assertEqual(self.buys(), {})

    def test_no_exploration_sku_unit_or_category_cap(self):
        for i in range(60):
            sku = self.item(f"sku{i}", category="same-category", price=10_000)
            self.stock(sku, n=2, cost=7_500)
        for i in range(10):
            self.inquiry(self.item(f"new{i}", category="same-category", price=10_000))
        orders = self.plan()["buy"]
        self.assertEqual(len(orders), 10)  # only transport's ten-line limit remains

    def test_acceptable_price_coverage_does_not_replace_sku_demand(self):
        old, new = self.item("old"), self.item("new")
        self.stock(old)
        self.inquiry(new, 2)
        self.assertEqual(self.buys(), {new: 2})

    def test_same_category_distinct_skus_follow_independent_grades(self):
        a, b = self.item("a", category="same"), self.item("b", category="same")
        self.inquiry(a, 2)
        self.inquiry(b)
        self.assertEqual(self.buys(), {a: 2, b: 1})

    def test_high_price_no_sofa_or_high_value_exception(self):
        a, b = self.item("G8ec51935", price=1_000_000), self.item("unlisted", price=1_000_000)
        self.inv["balance"] = 20_000_000
        self.inquiry(a, 2)
        self.inquiry(b, 2)
        self.assertEqual(self.buys(), {a: 2, b: 2})

    def test_reserved_skus_survive_restart_and_next_window(self):
        import json
        sku = self.item()
        self.inquiry(sku, 2)
        self.assertEqual(self.buys(), {sku: 2})
        self.hub = json.loads(json.dumps(self.hub))
        self.assertEqual(self.buys(), {})
        self.inv["restock"] = {"opens_at": NOW-10, "closes_at": NOW+170}
        self.assertEqual(self.buys(), {sku: 2})

    def test_forum_and_own_buyer_orders_do_not_create_buying_demand(self):
        import json
        sku = self.item()
        self.hub["demand_radar"]["buyer_orders"] = {"goods": 1000}
        (self.path / "forum.json").write_text(json.dumps([{"text": f"삽니다 {sku} 99999개"}]), encoding="utf-8")
        self.assertEqual(self.buys(), {})


if __name__ == "__main__":
    unittest.main()
