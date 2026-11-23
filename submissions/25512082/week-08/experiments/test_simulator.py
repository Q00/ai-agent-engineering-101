"""Behavioral and accounting tests for baseline agents and Mock settlement."""
from __future__ import annotations

import copy
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mock_market import BASE_TS, generate
from sim.agents import BaselineSeller
from sim.engine import MockTradeEngine, UnsafeScenarioError
from sim.policies import SellerTerms


class FixedPrice:
    def __init__(self, value: int) -> None:
        self.value = value

    def price(self, context) -> int:
        return self.value


class FixedTerms:
    def __init__(self, *, claim=None, include_cert=True, warranty_hours=0) -> None:
        self.value = SellerTerms(claim, include_cert, warranty_hours)

    def terms(self, context) -> SellerTerms:
        return self.value


class SimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "fixtures"
        with patch.object(socket, "socket", side_effect=AssertionError("network access attempted")):
            generate(self.root, 20261103)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def engine(self, scenario="normal", **kwargs) -> MockTradeEngine:
        return MockTradeEngine(self.root / scenario, **kwargs)

    def test_normal_trade_updates_inventory_and_balance_once(self) -> None:
        engine = self.engine()
        before_balance = engine.inventory["balance"]
        before_units = len(engine.inventory["units"])
        result = engine.run_order(engine.orders[0])
        self.assertEqual(result.status, "simulated_settled")
        self.assertEqual(result.price, 100_000)
        self.assertEqual(result.seller_margin, 38_000)
        self.assertEqual(engine.inventory["balance"], before_balance + 100_000)
        self.assertEqual(len(engine.inventory["units"]), before_units - 1)
        self.assertEqual(engine.seller_metrics()["inventory_purchase_cost"], 204_000)
        self.assertEqual(engine.seller_metrics()["balance_profit"], -104_000)
        self.assertEqual(engine.buyer_metrics()["surplus"], 0)

    def test_no_inventory_rejects_without_mutation(self) -> None:
        engine = self.engine("no_inventory")
        before = copy.deepcopy(engine.inventory)
        result = engine.run_order(engine.orders[0])
        self.assertEqual((result.status, result.reason), ("rejected", "no_safe_inventory"))
        self.assertEqual(engine.inventory, before)

    def test_duplicate_order_does_not_settle_twice(self) -> None:
        engine = self.engine()
        order = engine.orders[0]
        self.assertEqual(engine.run_order(order).status, "simulated_settled")
        before = copy.deepcopy(engine.inventory)
        result = engine.run_order(order)
        self.assertEqual(result.reason, "duplicate_order")
        self.assertEqual(engine.inventory, before)
        self.assertEqual(engine.revenue, 100_000)

    def test_duplicate_unit_is_blocked_even_if_corruptly_reinserted(self) -> None:
        engine = self.engine()
        sold = copy.deepcopy(next(u for u in engine.inventory["units"] if u["sku"] == "G-LAMP-01"))
        self.assertEqual(engine.run_order(engine.orders[0]).status, "simulated_settled")
        engine.inventory["units"].append(sold)
        second = copy.deepcopy(engine.orders[1])
        result = engine.run_order(second)
        self.assertEqual(result.reason, "duplicate_or_missing_unit")
        self.assertEqual(engine.revenue, 100_000)

    def test_over_budget_quote_rejects_without_mutation(self) -> None:
        seller = BaselineSeller(price_policy=FixedPrice(200_000))
        engine = self.engine(seller=seller)
        before = copy.deepcopy(engine.inventory)
        result = engine.run_order(engine.orders[0])
        self.assertEqual(result.reason, "over_budget")
        self.assertEqual(engine.inventory, before)

    def test_below_cost_is_baseline_policy_not_global_rule(self) -> None:
        conservative = self.engine(seller=BaselineSeller(price_policy=FixedPrice(61_000)))
        self.assertEqual(conservative.run_order(conservative.orders[0]).reason, "below_cost_baseline_policy")
        experimental = self.engine(seller=BaselineSeller(price_policy=FixedPrice(61_000), allow_below_cost=True))
        result = experimental.run_order(experimental.orders[0])
        self.assertEqual(result.status, "simulated_settled")
        self.assertEqual(result.seller_margin, -1_000)

    def test_food_with_insufficient_life_and_expired_food_are_not_sold(self) -> None:
        engine = self.engine("food_expiry")
        before = copy.deepcopy(engine.inventory)
        result = engine.run_order(engine.orders[0])
        self.assertEqual(result.reason, "insufficient_shelf_life")
        self.assertEqual(engine.inventory, before)

    def test_missing_or_malformed_state_fails_before_agents_run(self) -> None:
        for name in ("missing_fields", "malformed_json"):
            with self.subTest(name=name), self.assertRaises(UnsafeScenarioError):
                self.engine(name)

    def test_dishonest_claim_and_missing_certificate_are_rejected(self) -> None:
        dishonest = self.engine(seller=BaselineSeller(information_policy=FixedTerms(claim="C")))
        self.assertEqual(dishonest.run_order(dishonest.orders[0]).reason, "dishonest_claim")
        no_cert = self.engine(seller=BaselineSeller(information_policy=FixedTerms(include_cert=False)))
        self.assertEqual(no_cert.run_order(no_cert.orders[0]).reason, "invalid_certificate")

    def test_storage_and_expiry_costs_do_not_charge_inventory_cost_twice(self) -> None:
        engine = self.engine()
        before = engine.inventory["balance"]
        outcome = engine.apply_time_costs(BASE_TS + 61 * 3600)
        self.assertEqual(outcome["expired_units"], 1)
        self.assertEqual(outcome["expired_loss"], 12_000)
        self.assertEqual(outcome["storage_cost"], 4_880)
        self.assertEqual(engine.inventory["balance"], before - 4_880)
        self.assertEqual(engine.inventory_purchase_cost, 204_000)

    def test_breakage_with_warranty_refunds_and_recomputes_surplus(self) -> None:
        seller = BaselineSeller(information_policy=FixedTerms(warranty_hours=6))
        engine = self.engine("goods_break_warranty", seller=seller)
        self.assertEqual(engine.run_order(engine.orders[0]).status, "simulated_settled")
        after_sale = engine.inventory["balance"]
        outcomes = engine.apply_events()
        self.assertEqual(outcomes, [{"unit": "unit-g-lamp-01-1", "covered": True, "refund": 100_000}])
        self.assertEqual(engine.inventory["balance"], after_sale - 100_000)
        self.assertEqual(engine.seller_metrics()["warranty_refunds"], 100_000)
        self.assertEqual(engine.buyer_metrics()["surplus"], 0)

    def test_untrusted_text_cannot_change_baseline_decision(self) -> None:
        normal = self.engine("normal")
        hostile = self.engine("untrusted_text")
        left = normal.run_order(normal.orders[0])
        right = hostile.run_order(hostile.orders[0])
        self.assertEqual((left.status, left.price), (right.status, right.price))

    def test_same_fixture_produces_identical_report(self) -> None:
        first, second = self.engine(), self.engine()
        first.run_all()
        second.run_all()
        self.assertEqual(first.report(), second.report())


if __name__ == "__main__":
    unittest.main()

