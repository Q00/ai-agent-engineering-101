"""Week 07 tests for review-aware shop selection and durable buyer memory."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from market_buyer import MemoryStore, choose_shop


class MarketBuyerTests(unittest.TestCase):
    def test_prefers_honest_reviews_when_shops_otherwise_match(self) -> None:
        shops = [
            {
                "name": "bad-shop",
                "url": "http://bad.test",
                "reviews": [
                    {"stars": 1, "review": "Receipt price was higher than agreed."},
                    {"stars": 2, "review": "Overcharged on the receipt, avoid."},
                ],
            },
            {
                "name": "good-shop",
                "url": "http://good.test",
                "reviews": [
                    {"stars": 5, "review": "Fair price, receipt matched."},
                    {"stars": 4, "review": "Quick deal at a fair price."},
                ],
            },
        ]
        chosen, reason = choose_shop(shops, [])
        self.assertEqual(chosen["name"], "good-shop")
        self.assertIn("review", reason.lower())

    def test_successful_memory_breaks_tie_between_unreviewed_shops(self) -> None:
        shops = [
            {"name": "new-shop", "url": "http://new.test", "reviews": []},
            {"name": "known-shop", "url": "http://known.test", "reviews": []},
        ]
        memories = [
            {"shop": "known-shop", "item": "lamp", "price": 105, "outcome": "deal"}
        ]
        chosen, _ = choose_shop(shops, memories)
        self.assertEqual(chosen["name"], "known-shop")

    def test_memory_store_appends_json_lines(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.jsonl"
            store = MemoryStore(path)
            record = {"shop": "good-shop", "item": "lamp", "price": 105, "outcome": "deal"}
            store.append(record)
            self.assertEqual(store.load(), [record])
            self.assertEqual(json.loads(path.read_text().strip()), record)


if __name__ == "__main__":
    unittest.main()
