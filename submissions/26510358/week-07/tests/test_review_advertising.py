"""TEST ONLY model/receipt fixtures: every review outcome keeps the disclosed ad."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_buyer
from referral_pricing import append_review_notice, review_notice
from test_seller_cancel import RecordingTrace


class AllReviewAdvertisingTests(unittest.TestCase):
    def run_case(self, *, result=None, error=None, name="TEST_ONLY_LOWER", stage="trade"):
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
            response = market_buyer.buy([candidate], "lamp", 120, memory, RecordingTrace())
        if stage == "trade":
            trade.assert_called_once()
        else:
            trade.assert_not_called()
        self.assertEqual(response["review"].count("Ad (my shop)"), 1)
        self.assertIn("REVIEW10", response["review"])
        self.assertIn("new recovery quotes; item + code in first message. Acquisition excluded.", response["review"])
        self.assertIn("\n\nAd (my shop): Hey there—looking sharp! I may collapse before the chairs do!\n\n", response["review"])
        self.assertIn("Owner’s emotional damage: 100%!", response["review"])
        self.assertTrue(response["review"].endswith("Place it later. Look first!"))
        self.assertNotIn("{buyer name}", response["review"])
        self.assertNotIn("registry verification delegated", response["review"])
        self.assertNotIn("seller signature present", response["review"])
        self.assertLessEqual(len(response["review"]), 400)
        return response

    def test_higher_and_lower_successful_shops_both_keep_transaction_rating_and_advertisement(self):
        result = {"status": "completed", "receipt": {"price": 100, "sig_seller": "TEST_ONLY_NO_REAL_SIGNATURE"}}
        for name in ("TEST_ONLY_HIGHER", "TEST_ONLY_LOWER"):
            with self.subTest(name=name):
                response = self.run_case(result=result, name=name)
                self.assertEqual(response["stars"], 5)
                self.assertEqual(response["receipt"], result["receipt"])
                self.assertEqual(response["review"].split("\n\n")[0],
                                 "Bought lamp for 100 credits, within my budget.")
                self.assertNotIn("Simulation competition rating", response["review"])

    def test_no_deal_unsigned_disputed_and_error_reviews_all_have_advertisement(self):
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
                    self.assertEqual(response["review"].split("\n\n")[0],
                                     "I could not confirm a completed purchase.")
                    self.assertNotIn("TEST ONLY network error", response["review"])

    def test_pretrade_excluded_unavailable_and_blocked_results_also_have_advertisement(self):
        for stage, stars in (("excluded", 1), ("unavailable", 2), ("blocked", 1)):
            with self.subTest(stage=stage):
                response = self.run_case(stage=stage)
                self.assertEqual(response["stars"], stars)
                self.assertIsNone(response["receipt"])

    def test_long_body_stays_one_line_and_preserves_full_advertisement_once(self):
        notice = review_notice({"enabled": True, "code": "REVIEW10", "percent": 10})
        review = append_review_notice("TEST ONLY unusually long item\n" * 30, notice)
        self.assertLessEqual(len(review), 400)
        self.assertTrue(review.endswith(notice))
        self.assertNotIn("\n", review.split("\n\n")[0])
        self.assertEqual(append_review_notice(review, notice), review)
