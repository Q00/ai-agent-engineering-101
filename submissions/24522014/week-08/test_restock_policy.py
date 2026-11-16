import unittest
from datetime import datetime
from unittest.mock import patch
from restock_policy import plan


class RestockLimits(unittest.TestCase):
    def setUp(self):
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": []}
        self.catalog = {"A": {"category": "food", "kind": "food", "list_price": 100000},
                        "B": {"category": "food", "kind": "food", "list_price": 100000},
                        "C": {"category": "goods", "kind": "goods", "list_price": 100000}}
        self.demand = [{"category": "food"}, {"category": "goods"}]

    def test_observed_sale_priority_and_spending_bound(self):
        buys, explanation = plan(self.inv, self.catalog, self.demand, {"C": 2})
        self.assertEqual(buys[0]["sku"], "C")
        self.assertLessEqual(explanation["estimated_max_cost"], explanation["budget"])
        self.assertEqual(len(buys), 1)

    def test_round_and_cash_guards(self):
        self.assertEqual(plan({**self.inv, "round": 2}, self.catalog, self.demand, {})[0], [])
        self.assertEqual(plan({**self.inv, "balance": 900000}, self.catalog, self.demand, {})[0], [])

    def test_food_and_unit_caps(self):
        units = [{"sku": str(i), "kind": "food", "grade": "A"} for i in range(2)]
        # Two unproven lots now exhaust exploration; proven goods may still replenish.
        self.assertEqual(plan({**self.inv, "units": units}, self.catalog, self.demand, {})[0], [])
        buys, _ = plan({**self.inv, "units": units}, self.catalog, self.demand, {"C": 1})
        self.assertEqual(buys, [{"sku": "C", "qty": 1}])
        self.assertEqual(plan({**self.inv, "units": units * 4}, self.catalog, self.demand, {})[0], [])

    def test_unseen_products_rotate(self):
        for value in self.catalog.values():
            value["kind"] = "goods"
        a = plan(self.inv, self.catalog, self.demand, {}, 0)[0]
        b = plan(self.inv, self.catalog, self.demand, {}, 1)[0]
        self.assertNotEqual(a[0]["sku"], b[0]["sku"])


class RoundEndPurchaseStop(unittest.TestCase):
    """The user's 11-16 choice ("2시간 전까지 구매, 정리 판매 끔 (권장)"): buying stops 2 h before round_end, not 24 h."""

    END = datetime.fromisoformat("2026-11-17T23:59:00+09:00").timestamp()

    def reason(self, hours_left, stop=None):
        import restock_policy
        from r3_policy import load_policy
        policy = load_policy()
        if stop is None:
            policy["selling"].pop("round_end_purchase_stop_hours", None)
        else:
            policy["selling"]["round_end_purchase_stop_hours"] = stop
        from strategy_policy import BASE
        inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": []}
        runtime = {"version": "t", "controls": dict(BASE)}
        with patch.object(restock_policy, "load_policy", return_value=policy):
            return plan(inv, {}, [], {}, now=self.END - hours_left * 3600, runtime=runtime)[1].get("reason")

    def test_stop_at_two_hours(self):
        self.assertEqual(self.reason(3, 2), "bounded_experiment")
        self.assertEqual(self.reason(2.01, 2), "bounded_experiment")
        self.assertEqual(self.reason(2, 2), "round_end_purchase_stop")
        self.assertEqual(self.reason(0.5, 2), "round_end_purchase_stop")

    def test_old_24_hours_when_missing_or_bad(self):
        for stop in (None, "2", -1, 25, float("inf"), True):
            with self.subTest(stop=stop):
                self.assertEqual(self.reason(3, stop), "round_end_purchase_stop")
                self.assertEqual(self.reason(23.9, stop), "round_end_purchase_stop")
                self.assertEqual(self.reason(24.1, stop), "bounded_experiment")

    def test_zero_buys_until_the_end(self):
        self.assertEqual(self.reason(0.01, 0), "bounded_experiment")
        self.assertEqual(self.reason(0, 0), "round_end_purchase_stop")


if __name__ == "__main__":
    unittest.main()
