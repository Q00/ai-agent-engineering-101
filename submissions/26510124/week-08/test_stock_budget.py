"""Money limits must hold even with few expensive units or repeated buys."""
from copy import deepcopy
import unittest

from stock_budget import StockBudget, validate

CFG = {"max_run_cost_percent": 10, "max_sku_cost_percent": 10,
       "max_category_cost_percent": 25, "max_food_cost_percent": 10}


def item(uid="u1", sku="G00001", cost=300000, category="goods", kind="goods"):
    return {"unit": uid, "sku": sku, "cost": cost, "category": category, "kind": kind}


class StockBudgetTests(unittest.TestCase):
    def budget(self, units=(), balance=5000000):
        return StockBudget({"start": 5000000, "balance": balance, "units": list(units)}, CFG)

    def test_sku_cap_counts_existing_actual_and_planned_worst_cost(self):
        budget = self.budget([item()])
        offer = {"category": "goods", "kind": "goods", "worst_cost": 200000}
        self.assertIsNone(budget.reason("G00001", offer))
        budget.add("G00001", offer)
        self.assertEqual(budget.reason("G00001", {**offer, "worst_cost": 1}), "sku_cost_cap")
        self.assertEqual(budget.skus["G00001"], 500000)

    def test_category_cap_includes_other_skus(self):
        budget = self.budget([item(str(i), str(i), 400000) for i in range(3)])
        offer = {"category": "goods", "kind": "goods", "worst_cost": 50001}
        self.assertEqual(budget.reason("new", offer), "category_cost_cap")
        self.assertIsNone(budget.reason("new", {**offer, "category": "other"}))

    def test_food_cap_is_across_categories_not_just_one_sku(self):
        budget = self.budget([item(kind="food", category="fruit", cost=400000)])
        offer = {"category": "meat", "kind": "food", "worst_cost": 100001}
        self.assertEqual(budget.reason("F00002", offer), "food_cost_cap")
        self.assertIsNone(budget.reason("G00002", {**offer, "kind": "goods"}))

    def test_run_limit_does_not_grow_with_current_wallet(self):
        budget = self.budget(balance=20000000)
        offer = {"category": "goods", "kind": "goods", "worst_cost": 250000}
        budget.add("G00001", offer)
        budget.add("G00002", offer)
        self.assertEqual(budget.reason("G00003", {**offer, "worst_cost": 1}), "run_cost_cap")
        self.assertEqual(budget.summary()["planned_cost"], 500000)

    def test_unknown_costs_duplicates_and_invalid_config_fail_closed(self):
        for units in ([item(), item()], [item(cost=True)], [item(cost=0)], [item(kind="unknown")]):
            with self.subTest(units=units), self.assertRaises(ValueError):
                self.budget(units)
        for cfg in ({**CFG, "max_food_cost_percent": False},
                    {**CFG, "max_sku_cost_percent": 30}, {}):
            with self.assertRaises(ValueError):
                validate(cfg)

    def test_held_units_are_never_modified_or_sold_by_budgeting(self):
        units = [item(cost=600000)]
        before = deepcopy(units)
        budget = self.budget(units)
        self.assertEqual(budget.reason("G00001", {"category": "goods", "kind": "goods", "worst_cost": 1}),
                         "sku_cost_cap")
        self.assertEqual(units, before)

    def test_secondary_cap_only_applies_after_explicit_focus_gate(self):
        cfg = {**CFG, "max_run_cost_percent": 44, "max_sku_cost_percent": 26,
               "max_category_cost_percent": 60, "focused_goods": {
                   "enabled": True, "secondary_skus": ["G03d0199f"], "max_secondary_sku_cost_percent": 40}}
        budget = StockBudget({"start": 5000000, "units": []}, cfg)
        offer = {"category": "goods", "kind": "goods", "worst_cost": 1812376}
        self.assertEqual(budget.reason("G03d0199f", offer), "sku_cost_cap")
        self.assertIsNone(budget.reason("G03d0199f", {**offer, "secondary_eligible": True}))
        self.assertEqual(budget.reason("other", {**offer, "secondary_eligible": True}), "sku_cost_cap")
