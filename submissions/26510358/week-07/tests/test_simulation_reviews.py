"""TEST ONLY board/receipt/model fixtures; no public reviews or purchases."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_buyer
from simulation_reviews import SimulationReviewPolicy
from test_seller_cancel import RecordingTrace

ROOT = Path(__file__).resolve().parents[1]


def board():
    return {"round": 2, "current": 2, "shops": [
        {"name": "TEST_ONLY_TOP", "deals": 60, "stars": 4},
        {"name": "TEST_ONLY_SECOND", "deals": 50, "stars": 4},
        {"name": "TEST_ONLY_THIRD", "deals": 40, "stars": 4},
        {"name": "TEST_ONLY_HIGHER", "deals": 30, "stars": 4},
        {"name": "myeonggi", "deals": 20, "stars": 4},
        {"name": "TEST_ONLY_LOWER", "deals": 10, "stars": 4}]}


class SimulationReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "TEST_ONLY_POLICY.json"
        self.rule = json.loads((ROOT / "data/review-policy.json").read_text())
        self.rule["enabled"] = True
        self.path.write_text(json.dumps(self.rule))
        self.trace = RecordingTrace()

    def policy(self, fetch=board):
        return SimulationReviewPolicy(self.trace, self.path, fetch_board=fetch)

    def test_higher_top_three_get_one_other_higher_two_lower_keeps_rating(self):
        policy = self.policy()
        for shop, expected in (("TEST_ONLY_TOP", 1), ("TEST_ONLY_SECOND", 1),
                               ("TEST_ONLY_THIRD", 1), ("TEST_ONLY_HIGHER", 2),
                               ("TEST_ONLY_LOWER", 5), ("myeonggi", 5)):
            with self.subTest(shop=shop):
                stars, note = policy.evaluate(shop, 5)
                self.assertEqual(stars, expected)
                if expected < 5:
                    self.assertIn("Simulation competition rating", note)
                    self.assertIn("not a product-quality finding", note)
                else:
                    self.assertEqual(note, "")

    def test_invalid_unavailable_or_wrong_round_standings_do_not_change_rating(self):
        for malformed in (None, {**board(), "round": 1}, {**board(), "current": 3},
                          {**board(), "shops": []}, {**board(), "shops": [None]},
                          {**board(), "shops": board()["shops"] * 2},
                          {**board(), "shops": [{"name": "bad", "deals": True, "stars": 5}]}):
            with self.subTest(board=malformed):
                self.assertEqual(self.policy(lambda: malformed).evaluate("TEST_ONLY_TOP", 5), (5, ""))
        def unavailable():
            raise OSError("TEST ONLY unavailable board")
        self.assertEqual(self.policy(unavailable).evaluate("TEST_ONLY_TOP", 5), (5, ""))
        self.assertEqual(self.policy().evaluate("TEST_ONLY_MISSING", 5), (5, ""))

    def test_disabled_policy_has_no_network_and_simulation_scope_is_required(self):
        self.rule["enabled"] = False
        self.path.write_text(json.dumps(self.rule))
        fetch = Mock(side_effect=AssertionError("TEST ONLY disabled policy must not fetch"))
        self.assertEqual(self.policy(fetch).evaluate("TEST_ONLY_TOP", 5), (5, ""))
        fetch.assert_not_called()
        self.rule["enabled"] = True
        self.rule["market_url"] = "https://TEST-ONLY-REAL-STORE.invalid"
        self.path.write_text(json.dumps(self.rule))
        with self.assertRaises(ValueError):
            self.policy()

    def test_ties_use_same_stars_tiebreak_and_stable_order_as_board(self):
        tied = board()
        tied["shops"] = [{"name": "myeonggi", "deals": 10, "stars": 4},
                         {"name": "TEST_ONLY_TIED", "deals": 10, "stars": 4},
                         {"name": "TEST_ONLY_BETTER", "deals": 10, "stars": 5}]
        policy = self.policy(lambda: tied)
        self.assertEqual(policy.evaluate("TEST_ONLY_BETTER", 5)[0], 1)
        self.assertEqual(policy.evaluate("TEST_ONLY_TIED", 5), (5, ""))

    def run_buyer(self, *, result, review_policy):
        candidate = {"name": "TEST_ONLY_TOP", "url": "https://TEST-ONLY.invalid", "score": 5,
                     "reviews": [], "personal_memory": {}, "card_flags": [], "card": {}}
        memory = Mock()
        memory.ingest_kit_feedback.return_value = []
        memory.read.return_value = {}
        model = Mock()
        model.decide.return_value = {"action": "select_shop", "item_id": candidate["name"], "reason": "TEST ONLY"}
        with patch.object(market_buyer, "rank_shops", return_value=[candidate]), \
                patch.object(market_buyer, "check_card", side_effect=lambda shop: shop), \
                patch.object(market_buyer, "Harness", return_value=model), \
                patch.object(market_buyer, "negotiate", return_value=deepcopy(result)) as trade:
            response = market_buyer.buy([candidate], "lamp", 120, memory, self.trace, review_policy=review_policy)
        trade.assert_called_once()
        return response, memory

    def test_one_completed_trade_keeps_receipt_and_facts_while_rating_changes(self):
        result = {"status": "completed", "receipt": {"price": 100, "sig_seller": "TEST_ONLY_NO_REAL_SIGNATURE"}}
        response, memory = self.run_buyer(result=result, review_policy=self.policy())
        self.assertEqual(response["receipt"], result["receipt"])
        self.assertEqual(response["stars"], 1)
        self.assertIn("Bought lamp at 100", response["review"])
        self.assertIn("Simulation competition rating", response["review"])
        self.assertIn("Ad (my shop)", response["review"])
        self.assertLessEqual(len(response["review"]), 400, "Public review must retain complete referral terms")
        self.assertTrue(response["review"].endswith("Acquisition prices excluded."))
        memory.record.assert_called_once()
        self.assertEqual(memory.record.call_args.kwargs["status"], "completed")
        self.assertEqual(memory.record.call_args.kwargs["issues"], [])

    def test_no_deal_or_unsigned_receipt_never_invokes_strategic_policy(self):
        policy = Mock()
        policy.evaluate.side_effect = AssertionError("TEST ONLY must not rate without a signed completed trade")
        for result, expected in (({"status": "no_deal", "receipt": None}, 3),
                                 ({"status": "completed", "receipt": {"price": 100}}, 3)):
            response, _ = self.run_buyer(result=result, review_policy=policy)
            self.assertEqual(response["stars"], expected)
            self.assertNotIn("Simulation competition rating", response["review"])
        policy.evaluate.assert_not_called()

    def test_review_policy_failure_cannot_discard_a_completed_receipt(self):
        result = {"status": "completed", "receipt": {"price": 100, "sig_seller": "TEST_ONLY_NO_REAL_SIGNATURE"}}
        policy = Mock()
        policy.evaluate.side_effect = RuntimeError("TEST ONLY unexpected policy failure")
        response, _ = self.run_buyer(result=result, review_policy=policy)
        self.assertEqual(response["receipt"], result["receipt"])
        self.assertEqual(response["stars"], 5)
        policy.evaluate.side_effect = None
        policy.evaluate.return_value = (False, None)
        response, _ = self.run_buyer(result=result, review_policy=policy)
        self.assertEqual(response["receipt"], result["receipt"])
        self.assertEqual(response["stars"], 5)
