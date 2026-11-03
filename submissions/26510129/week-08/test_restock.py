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

    def test_forum_requires_ledger_reference(self):
        forum = [{"text": "kimchi wanted", "refs": []}, {"text": "kimchi bought", "refs": [10]}]
        self.assertEqual(restock.forum_scores(forum, ["food>kimchi"])["food>kimchi"], 1)


if __name__ == "__main__":
    unittest.main()
