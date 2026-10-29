"""Buyer integration guards with fixture decisions, never live purchases."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

WEEK08 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEEK08.parent / "week-07"))
import market_buyer
from harness import HarnessThrottleError
from market_memory import MarketMemory, rank_shops


class BuyerProtection(unittest.TestCase):
    def setUp(self):
        private = WEEK08 / ".market"
        private.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=private)
        self.addCleanup(self.temp.cleanup)
        self.memory = MarketMemory(Path(self.temp.name) / "memory.json")
        self.shop = {"name": "fixture", "url": "https://fixture.test", "reviews": [],
                     "registry_owner": "another-owner", "registry_owner_verified": True,
                     "card": {}, "card_flags": [], "personal_memory": {}, "score": 4}

    def test_owner_alias_is_excluded_before_discovery_or_model_call(self):
        own = {**self.shop, "registry_owner": "SUNGMYEONGGI"}
        with patch.object(market_buyer, "check_card") as card, patch.object(market_buyer, "Harness") as harness:
            result = market_buyer.buy([own], "lamp", 120, self.memory, Mock())
        card.assert_not_called()
        harness.assert_not_called()
        self.assertEqual(result, {"shop": "", "receipt": None, "stars": None, "review": ""})

    def test_local_provider_failure_cannot_lower_public_or_personal_shop_rating(self):
        model = Mock()
        model.decide.return_value = {"action": "select_shop", "item_id": "fixture", "reason": "TEST ONLY"}
        with patch.object(market_buyer, "check_card", return_value=self.shop), \
                patch.object(market_buyer, "Harness", return_value=model), \
                patch.object(market_buyer, "negotiate", side_effect=HarnessThrottleError("429 TEST ONLY")):
            result = market_buyer.buy([self.shop], "lamp", 120, self.memory, Mock())
        self.assertIsNone(result["stars"])
        self.assertEqual(result["review"], "")
        recent = self.memory.read()["shops"]["fixture"]["recent"][-1]
        self.assertEqual(recent["status"], "local_error")
        self.assertFalse(recent["penalize_shop"])
        self.assertEqual(rank_shops([self.shop], self.memory.read(), "lamp", 120)[0]["score"], 4)

    def test_repeated_reviews_by_one_owner_contribute_one_observation(self):
        review = {"stars": 5, "author": "reviewer", "registry_author_owner": "owner", "review": "TEST ONLY"}
        one = rank_shops([{**self.shop, "reviews": [review]}], {}, "lamp", 120)[0]
        many = rank_shops([{**self.shop, "reviews": [review] * 20}], {}, "lamp", 120)[0]
        self.assertEqual((one["score"], many["score"]), (one["score"], one["score"]))
        self.assertEqual(many["review_sample_size"], 1)

    def test_round3_ranking_does_not_promote_old_round2_counts(self):
        memory = {"shops": {"legacy": {"verified_completions": 9999,
                    "verified_by_item": {"lamp": {"completed": 9999}}},
                  "settled": {"round3_settlements": 1, "settled_by_item": {"lamp": {"completed": 1}}}}}
        ranked = rank_shops([{"name": "legacy", "current_round": 3},
                             {"name": "settled", "current_round": 3}], memory, "lamp", 120)
        self.assertEqual(ranked[0]["name"], "settled")

    def test_selection_window_is_bounded_and_does_not_blacklist_a_legitimate_regular(self):
        for _ in range(70):
            self.memory.visit("fixture", item="lamp", reason="TEST ONLY comparable purchase", owner="owner")
        data = self.memory.read()
        self.assertEqual(len(data["selection_window"]), 60)
        self.assertNotIn("avoid_until", data["shops"]["fixture"])


if __name__ == "__main__":
    unittest.main()
