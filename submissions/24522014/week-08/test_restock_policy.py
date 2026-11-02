import unittest
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
        buys, _ = plan({**self.inv, "units": units}, self.catalog, self.demand, {})
        self.assertEqual(buys, [{"sku": "C", "qty": 1}])
        self.assertEqual(plan({**self.inv, "units": units * 4}, self.catalog, self.demand, {})[0], [])

    def test_unseen_products_rotate(self):
        for value in self.catalog.values():
            value["kind"] = "goods"
        a = plan(self.inv, self.catalog, self.demand, {}, 0)[0]
        b = plan(self.inv, self.catalog, self.demand, {}, 1)[0]
        self.assertNotEqual(a[0]["sku"], b[0]["sku"])


if __name__ == "__main__":
    unittest.main()
