"""The separate buyer process must never select its own registered shop."""

import unittest
from unittest.mock import patch

from market_buyer import choose_shop


class SelfShopExclusionTest(unittest.TestCase):
    def test_model_never_receives_own_shop(self):
        shops = [
            {"name": "outseop-26510122", "url": "https://changing-tunnel.example", "reviews": []},
            {"name": "other", "url": "https://other.example", "reviews": []},
        ]
        with patch("market_buyer.decide_json", return_value={
            "shop_id": "0", "trustworthiness": "uncertain",
            "feasibility": "uncertain", "reason": "Only other shop is eligible.",
        }) as decide, patch("market_buyer.trace"):
            chosen, _ = choose_shop(shops, "chair", 225, {"shops": {}}, "outseop-26510122")
        self.assertEqual(chosen["name"], "other")
        self.assertEqual([s["name"] for s in decide.call_args.args[1]["candidate_shops"]], ["other"])

    def test_no_alternative_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no other shops"):
            choose_shop([{"name": "outseop-26510122", "url": "https://self.example"}],
                        "chair", 225, {"shops": {}}, "outseop-26510122")


if __name__ == "__main__":
    unittest.main()
