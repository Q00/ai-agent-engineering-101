"""Offline safety and economic tests. Do not confuse with actual Round 3 settlements."""
import unittest
from unittest.mock import patch

from round3_policy import (
    MIN_CASH, buyer_target, demand_weights, find_unit, plan_restock,
    reputation_risk, reserve_for_unit, safe_accept_policy, suggested_ask,
)


def product(sku, price, cat="furniture", kind="goods"):
    return {"sku": sku, "list_price": price, "category": cat, "kind": kind}


class MoneySafetyTests(unittest.TestCase):
    def test_never_sell_under_cost(self):
        u = {"sku": "X", "cost": 700, "list_price": 1000, "supplied_at": 1}
        floor = reserve_for_unit(u)
        self.assertGreaterEqual(floor, 700)
        self.assertGreaterEqual(suggested_ask(u), floor)

    def test_discount_offer_never_over_budget(self):
        for b in (1, 90, 100, 1000):
            self.assertLessEqual(buyer_target(1000, b), b)

    def test_buyer_favors_price_reduction(self):
        self.assertLessEqual(buyer_target(1000, 1000), 840)

    def test_history_deception_is_hard_block(self):
        self.assertEqual(
            reputation_risk({"name": "spoof", "reviews": []},
                            {"spoof": {"injection_attempts": 1}}),
            float("inf"))

    def test_food_accepts_only_sufficient_shelf_life(self):
        a = safe_accept_policy({"kind": "food", "use_within_hours": 8})
        self.assertGreaterEqual(a["min_hours_left"], 8)
        self.assertTrue(a["cert"])

    def test_food_c_grade_requires_certified_shelf_life(self):
        policy = safe_accept_policy({"kind": "food", "use_within_hours": 72})
        self.assertTrue(policy["cert"])
        self.assertIn("C", policy["grades"])
        self.assertGreaterEqual(policy["min_hours_left"], 72)

    def test_restock_is_limited_to_two_low_inventory_items(self):
        inv = {"round": 3, "balance": 5_000_000, "units": []}
        products = {f"G{i}": product(f"G{i}", 12000) for i in range(25)}
        bought = plan_restock(inv, products, [{"category": "furniture", "count": 20}])
        self.assertLessEqual(len(bought), 2)

    def test_nonfood_warranty_is_opt_in(self):
        a = safe_accept_policy({"kind": "goods"})
        self.assertNotIn("warranty_hours", a)
        with patch.dict("os.environ", {"AGENT_MIN_WARRANTY_HOURS": "6"}):
            a = safe_accept_policy({"kind": "goods"})
        self.assertEqual(a["warranty_hours"], 6)

    def test_do_not_restock_while_cash_near_minimum(self):
        inv = {"round": 3, "balance": MIN_CASH, "units": []}
        planned = plan_restock(inv, {"S1": product("S1", 10000)},
                               [{"category": "furniture", "count": 100}])
        self.assertEqual(planned, [])

    def test_diversified_restock_and_cash_limit(self):
        inv = {"round": 3, "balance": 5_000_000, "units": []}
        products = {f"S{i}": product(f"S{i}", 30_000+i*1000) for i in range(12)}
        buy = plan_restock(inv, products,
                           [{"category": "furniture", "count": 7}])
        self.assertTrue(0 < len(buy) <= 2)
        self.assertTrue(all(x["qty"] == 1 for x in buy))
        estimated_max_cost = sum(products[x["sku"]]["list_price"]*.90 for x in buy)
        self.assertLessEqual(estimated_max_cost, inv["balance"]*.12)

    def test_no_unobserved_demand_means_no_stock(self):
        inv = {"round": 3, "balance": 5_000_000, "units": []}
        self.assertEqual(plan_restock(inv, {"S": product("S", 2000)}, []), [])

    def test_normalize_demand_list(self):
        self.assertEqual(demand_weights([{"category": "furniture", "count": 9}])["furniture"], 9)


if __name__ == "__main__":
    unittest.main()
