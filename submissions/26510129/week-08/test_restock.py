import unittest

import restock


class RestockMemoryTest(unittest.TestCase):
    def test_expired_food_is_waste_not_demand(self):
        previous = {"units": [
            {"unit": "expired", "sku": "F1", "category": "food>kimchi", "kind": "food", "expires_at": 90},
            {"unit": "sold", "sku": "G1", "category": "home>bed", "kind": "goods", "expires_at": None},
        ]}
        memory = restock.update_memory(previous, [], now=100)
        self.assertEqual(memory["category_waste"]["food>kimchi"], 1)
        self.assertNotIn("food>kimchi", memory["category_depletion"])
        self.assertEqual(memory["category_depletion"]["home>bed"], 1)

    def test_depletion_outweighs_unseen_exploration(self):
        inv = {"balance": 5_000_000, "units": []}
        catalog = {
            "K1": {"category": "food>kimchi", "kind": "food", "list_price": 10_000},
            "B1": {"category": "home>bed", "kind": "goods", "list_price": 100_000},
        }
        demand = [{"category": "food>kimchi"}, {"category": "home>bed"}]
        memory = {"category_depletion": {"home>bed": 3}, "sku_depletion": {"B1": 2}}
        buys = restock.plan(inv, catalog, demand, [], memory)
        self.assertEqual(buys[0], {"sku": "B1", "qty": 2})

    def test_plan_respects_line_and_spend_caps(self):
        inv = {"balance": 100_000, "units": []}
        catalog = {
            f"S{n}": {"category": f"c>{n}", "kind": "goods", "list_price": 100_000}
            for n in range(20)
        }
        demand = [{"category": f"c>{n}"} for n in range(20)]
        buys = restock.plan(inv, catalog, demand, [], {})
        self.assertLessEqual(len(buys), restock.MAX_LINES)
        self.assertEqual(buys, [])  # even one estimated lot exceeds 12% of this balance

    def test_ledger_sales_raise_a_category(self):
        inv = {"balance": 5_000_000, "units": []}
        catalog = {
            "A1": {"category": "c>a", "kind": "goods", "list_price": 100_000},
            "B1": {"category": "c>b", "kind": "goods", "list_price": 100_000},
        }
        demand = [{"category": "c>a"}, {"category": "c>b"}]
        stats = {"categories": {"c>b": {"n": 20, "median_ratio": 0.9}}, "skus": {"B1": {"n": 20, "median": 90_000}}}
        self.assertEqual("A1", restock.plan(inv, catalog, demand, [], {})[0]["sku"])          # alphabetical without data
        self.assertEqual("B1", restock.plan(inv, catalog, demand, [], {}, stats)[0]["sku"])   # the market says b sells

    def test_market_price_below_expected_cost_skips_the_sku(self):
        inv = {"balance": 5_000_000, "units": []}
        catalog = {
            "S1": {"category": "c>a", "kind": "goods", "list_price": 100_000},
            "S2": {"category": "c>a", "kind": "goods", "list_price": 100_000},
        }
        demand = [{"category": "c>a"}]
        stats = {"skus": {"S1": {"n": 5, "median": 40_000}, "S2": {"n": 3, "median": 80_000}}, "categories": {}}
        report = {}
        buys = restock.plan(inv, catalog, demand, [], {}, stats, report)
        self.assertEqual("S2", buys[0]["sku"])
        self.assertEqual([{"sku": "S1", "market": 40_000, "settles": 5, "expected_cost": 60_000}], report["skipped_below_cost"])
        stats["skus"]["S2"]["median"] = 50_000
        self.assertEqual([], restock.plan(inv, catalog, demand, [], {}, stats))   # nothing in the category sells above cost

    def test_forum_requires_ledger_reference(self):
        forum = [{"text": "kimchi wanted", "refs": []}, {"text": "kimchi bought", "refs": [10]}]
        self.assertEqual(restock.forum_scores(forum, ["food>kimchi"])["food>kimchi"], 1)


if __name__ == "__main__":
    unittest.main()
