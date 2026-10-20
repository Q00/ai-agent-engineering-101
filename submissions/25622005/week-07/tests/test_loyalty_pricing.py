"""User-selected tiers, rounding, floor limits and completed-sale memory."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loyalty_pricing import customer_id_from, purchase_quote
from seller_memory import SellerMemory


class LoyaltyTests(unittest.TestCase):
    def test_price_tiers(self):
        expected = {"lamp": [100, 105, 110], "chair": [180, 190, 200],
                    "bookshelf": [260, 271, 285]}
        for item, prices in expected.items():
            self.assertEqual([purchase_quote(item, n)["price"] for n in range(3)], prices)
        self.assertTrue(purchase_quote("lamp", 0)["floor_applied"])
        self.assertTrue(purchase_quote("bookshelf", 0)["floor_applied"])
        self.assertFalse(purchase_quote("chair", 0)["floor_applied"])

    def test_customer_id_is_optional_and_bounded(self):
        self.assertEqual(customer_id_from({"customer_id": "buyer-82"}), "buyer-82")
        for value in [None, 123, "", "x" * 81, "../buyer", "buyer\n82"]:
            self.assertIsNone(customer_id_from({"customer_id": value}))

    def test_count_only_completed_distinct_tasks_across_items_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sales.jsonl"
            memory = SellerMemory(path)
            row = {"task_id": "one", "item": "lamp", "customer_id": "a", "outcome": "open"}
            memory.record(row)
            self.assertEqual(memory.completed_purchases("a"), 0)
            memory.record({**row, "outcome": "deal", "sale_price": 100})
            memory.record({**row, "outcome": "deal", "sale_price": 100})
            memory.record({**row, "task_id": "cancel", "outcome": "canceled"})
            memory.record({**row, "task_id": "two", "item": "chair", "outcome": "deal", "sale_price": 190})
            restored = SellerMemory(path)
            self.assertEqual(restored.completed_purchases("a"), 2)
            self.assertEqual(restored.completed_purchases("b"), 0)


if __name__ == "__main__":
    unittest.main()
