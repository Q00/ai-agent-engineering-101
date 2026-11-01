from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buyer import BuyerOutcome
from market_buyer import Shop, choose_shop, run, state_dir_for


class FakeBuyer:
    def __init__(self, url: str) -> None:
        self.url = url
        self.last_offer = 100

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def buy(self, item: str, budget: int) -> BuyerOutcome:
        receipt = {"shop": "Good", "item": item, "price": 100, "task": "task-1", "sig_seller": "signature"}
        return BuyerOutcome("deal", "task-1", 100, receipt)


class MarketBuyerTests(unittest.TestCase):
    def test_bad_reviews_are_avoided(self):
        bad = Shop("Bad", "http://bad.test", ({"stars": 1, "review": "Receipt overcharged, avoid."},))
        good = Shop("Good", "http://good.test", ({"stars": 5, "review": "Fair price, receipt matched."},))
        self.assertEqual(choose_shop((bad, good), {"shops": {}}).shop.name, "Good")

    def test_test_and_runtime_state_are_separate(self):
        self.assertEqual(state_dir_for(Path(".market/test-shops-buyer_deal-1.json")), Path(".market/test-state"))
        self.assertEqual(state_dir_for(Path(".market/shops.json")), Path(".market/runtime-state"))

    def test_actual_receipt_is_returned_and_memory_uses_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shops = root / "shops.json"
            shops.write_text(json.dumps([{"name": "Good", "url": "http://good.test", "reviews": []}]))
            state = root / "state"
            with patch.dict(os.environ, {"AX_STATE_DIR": str(state)}):
                result = run(shops, "lamp", 120, buyer_factory=FakeBuyer)
            self.assertEqual(result["receipt"]["task"], "task-1")
            memory = json.loads((state / "buyer-memory.json").read_text())
            self.assertEqual(memory["shops"]["Good"]["deals"], 1)


if __name__ == "__main__":
    unittest.main()
