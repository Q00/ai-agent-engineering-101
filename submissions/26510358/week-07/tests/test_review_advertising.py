"""TEST ONLY model/receipt fixtures: all outcomes produce factual one-line reviews."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_buyer
from test_seller_cancel import RecordingTrace


class TransactionReviewTests(unittest.TestCase):
    def run_case(self, *, result=None, error=None, name="TEST_ONLY_LOWER", stage="trade", item="lamp"):
        candidate = {"name": name, "url": "https://TEST-ONLY.invalid", "score": 5,
                     "reviews": [], "personal_memory": {}, "card_flags": [], "card": {}}
        if stage == "blocked":
            candidate["card_flags"] = ["review_manipulation"]
        memory = Mock()
        memory.ingest_kit_feedback.return_value = []
        memory.read.return_value = {}
        model = Mock()
        model.decide.return_value = {"action": "select_shop", "item_id": name, "reason": "TEST ONLY"}
        with patch.object(market_buyer, "rank_shops", return_value=[] if stage == "excluded" else [candidate]), \
                patch.object(market_buyer, "check_card", return_value=None if stage == "unavailable" else candidate), \
                patch.object(market_buyer, "Harness", return_value=model), \
                patch.object(market_buyer, "negotiate", return_value=deepcopy(result), side_effect=error) as trade:
            response = market_buyer.buy([candidate], item, 120, memory, RecordingTrace())
        if stage == "trade":
            trade.assert_called_once()
        else:
            trade.assert_not_called()
        for promotion in ("Ad (my shop)", "REVIEW10", "Ssadassa", "myeonggi", "10% off"):
            self.assertNotIn(promotion, response["review"])
        self.assertNotIn("\n", response["review"])
        self.assertNotIn("{buyer name}", response["review"])
        self.assertNotIn("registry verification delegated", response["review"])
        self.assertNotIn("seller signature present", response["review"])
        self.assertLessEqual(len(response["review"]), 400)
        return response

    def test_higher_and_lower_successful_shops_keep_transaction_rating_without_ads(self):
        result = {"status": "completed", "receipt": {"price": 100, "sig_seller": "TEST_ONLY_NO_REAL_SIGNATURE"}}
        for name in ("TEST_ONLY_HIGHER", "TEST_ONLY_LOWER"):
            with self.subTest(name=name):
                response = self.run_case(result=result, name=name)
                self.assertEqual(response["stars"], 5)
                self.assertEqual(response["receipt"], result["receipt"])
                self.assertEqual(response["review"],
                                 "Bought lamp for 100 credits (budget 120); the receipt matched our agreement.")
                self.assertNotIn("Simulation competition rating", response["review"])

    def test_no_deal_unsigned_disputed_and_error_reviews_preserve_actual_outcome(self):
        cases = [({"status": "no_deal", "receipt": None, "reason": "TEST ONLY budget too low"}, None, 3),
                 ({"status": "completed", "receipt": {"price": 100}}, None, 3),
                 ({"status": "disputed", "receipt": {"price": 130}, "issues": ["receipt_price_mismatch"]}, None, 1),
                 (None, RuntimeError("TEST ONLY network error " * 100), 2)]
        for result, error, stars in cases:
            with self.subTest(result=result, error=bool(error)):
                response = self.run_case(result=result, error=error)
                self.assertEqual(response["stars"], stars)
                self.assertEqual(response["receipt"], result["receipt"] if result else None)
                if error:
                    self.assertEqual(response["review"],
                                     "I could not confirm a completed lamp purchase.")
                    self.assertNotIn("TEST ONLY network error", response["review"])
                elif result["status"] == "no_deal":
                    self.assertEqual(response["review"], "No deal for lamp within my 120-credit budget.")
                elif result["status"] == "disputed":
                    self.assertIn("did not match our agreed purchase", response["review"])
                elif not result["receipt"].get("sig_seller"):
                    self.assertIn("lacked a seller signature", response["review"])

    def test_pretrade_excluded_unavailable_and_blocked_results_have_no_ads(self):
        for stage, stars in (("excluded", 1), ("unavailable", 2), ("blocked", 1)):
            with self.subTest(stage=stage):
                response = self.run_case(stage=stage)
                self.assertEqual(response["stars"], stars)
                self.assertIsNone(response["receipt"])

    def test_long_requested_item_stays_one_line_within_market_limit(self):
        result = {"status": "completed", "receipt": {"price": 100, "sig_seller": "TEST_ONLY"}}
        response = self.run_case(result=result, item="TEST ONLY unusually long item\n" * 30)
        self.assertEqual(len(response["review"]), 400)
