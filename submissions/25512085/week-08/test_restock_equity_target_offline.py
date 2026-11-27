"""Equity regressions: market ratio and formula replace tiered/capped stock paths."""
import unittest
from unittest.mock import patch

import restock
import store
from test_unified_buying_offline import BuyingFixture, NOW


class EquityTargetTests(BuyingFixture, unittest.TestCase):
    def test_cash_rich_still_uses_market_ratio_not_point_seventy_five(self):
        self.inv["balance"] = 100_000_000
        for i in range(501):
            self.item(str(i))
        self.plan()
        d = self.diag()
        self.assertEqual(d["R"], .4)
        self.assertEqual(d["W"], int(40_000_000/restock.FILL_WINDOWS))

    def test_store_has_cash_limit_not_max_stock_cap(self):
        sku = self.item()
        self.stock(sku, 600, cost=15_000)
        cs = store.update_capital_stage(self.hub, self.inv)
        self.assertNotIn("max_stock_cap", cs)
        self.assertEqual(cs["cash_floor"], 1_590_000)
        self.assertEqual(cs["available_budget"], 4_410_000)

    def test_zero_deficit_still_allows_soldout_popular_restoration(self):
        held, soldout = self.item("held", price=20_000_000), self.item("soldout")
        self.stock(held, cost=15_000_000)
        self.inquiry(soldout, 2)
        self.assertEqual(self.buys(), {soldout: 2})
        self.assertEqual(self.diag()["deficit"], 0)
        self.assertEqual(self.diag()["W"], 30_000)

    def test_proven_history_does_not_grant_depth(self):
        sku = self.item()
        self.hub["restock_proven_skus"] = [sku]
        self.hub["own_sale_events"] = {"1": {"sku": sku, "ts": NOW}}
        self.assertEqual(self.buys(), {})

    def test_started_window_cap_does_not_increase_with_ratio(self):
        sku = self.item()
        self.inquiry(sku, 2)
        self.plan()
        first = self.state()["window_cap"]
        with patch.object(restock, "resolve_market_ratio", return_value=(.6, {"source": "mock", "default": False})):
            self.plan()
        self.assertEqual(self.state()["window_cap"], first)

    def test_cash_floor_is_hard_limit_despite_large_restoration(self):
        self.inv["balance"] = 600_000
        self.inquiry(self.item(), 8)
        self.assertEqual(self.buys(), {})
        self.assertEqual(self.diag()["W"], 0)

    def test_recovery_mode_does_not_change_budget_or_output_contract(self):
        import copy
        sku = self.item()
        self.inquiry(sku, 4)
        base = copy.deepcopy(self.hub)
        expected = self.plan()
        self.hub = base
        self.hub["adaptive_strategy"] = {"last_decision": NOW, "mode": "recover", "exploration_factor": .5}
        self.assertEqual(self.plan(), expected)
        self.assertEqual(set(expected), {"buy"})


if __name__ == "__main__":
    unittest.main()
