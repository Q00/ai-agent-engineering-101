import copy
import unittest
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import review_policy
from review_policy import evaluate


class ReviewPolicy(unittest.TestCase):
    def setUp(self):
        self.deal = {"order": {"order": "o1", "item": "category", "acceptable": ["G1"], "budget": 100, "kind": "goods"},
                     "receipt": {"item": "G1", "price": 90, "task": "t1", "sig_seller": "fixture"},
                     "settled": {"ledger": 10, "shop": "https://shop.test", "sku": "G1", "grade": "A", "value": 120}}

    def test_matching_settlement_is_five(self):
        self.assertEqual(evaluate(self.deal)["stars"], 5)

    def test_false_explicit_claim_is_two(self):
        self.deal["settled"].update(claim="A", grade="B", claim_honest=False)
        self.assertEqual(evaluate(self.deal)["stars"], 2)

    def test_unknown_claim_or_grade_is_neutral(self):
        self.deal["settled"].update(claim="A")
        self.assertEqual(evaluate(self.deal)["stars"], 3)

    def test_wrong_sku_or_price_is_not_success(self):
        other = copy.deepcopy(self.deal)
        other["settled"]["sku"] = "G2"
        self.assertEqual(evaluate(other)["stars"], 3)
        self.deal["receipt"]["price"] = 101
        self.assertEqual(evaluate(self.deal)["stars"], 3)

    def test_food_review_uses_current_time_and_margin(self):
        self.deal["order"].update(kind="food", use_within_hours=3)
        self.deal["settled"]["expires_at"] = 20000
        self.assertEqual(evaluate(self.deal, now=0)["stars"], 5)
        self.assertEqual(evaluate(self.deal, now=3000)["stars"], 3)

    def test_missing_settlement_is_neutral(self):
        self.deal["settled"] = {}
        self.assertEqual(evaluate(self.deal)["stars"], 3)

    def test_claim_mismatch_requires_matching_transaction(self):
        self.deal["settled"].update(claim="A", grade="B", claim_honest=False, sku="other")
        self.assertEqual(evaluate(self.deal)["stars"], 3)

    def test_repeated_hook_preserves_first_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deal = root / "deal.json"
            deal.write_text(json.dumps(self.deal), encoding="utf8")
            with patch.object(review_policy, "ROOT", root), patch("sys.argv", ["review", "--deal", str(deal)]):
                with redirect_stdout(io.StringIO()) as first:
                    review_policy.main()
                self.deal["settled"]["value"] = 0
                deal.write_text(json.dumps(self.deal), encoding="utf8")
                with redirect_stdout(io.StringIO()) as second:
                    review_policy.main()
            self.assertEqual(first.getvalue(), second.getvalue())
            self.assertEqual(len(list((root / "logs/settlement-reviews").glob("*.json"))), 1)


class BuyerNoteFromReview(unittest.TestCase):
    """The review hook also runs the buyer's market-note pass (the user's 11-14 choice); the review never depends on it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".market").mkdir()
        self.md = self.root / ".market"
        self.on = {"buyer_notes_from_review": True}

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, limits, run=None, now=1000.0):
        import forum_buyer_posts
        run = run or (lambda md, at: "queued")
        with patch.object(review_policy, "posting_limits", return_value=limits), \
                patch.object(forum_buyer_posts, "run", side_effect=run) as called:
            return review_policy.buyer_note(self.md, now), called

    def test_off_unless_the_switch_is_true(self):
        for limits in ({}, {"buyer_notes_from_review": False}, {"buyer_notes_from_review": "true"}):
            with self.subTest(limits=limits):
                result, called = self.note(limits)
                self.assertEqual(result, "off")
                called.assert_not_called()

    def test_on_runs_the_pass_with_the_same_clock_and_returns_its_outcome(self):
        result, called = self.note(self.on, run=lambda md, at: "rate_limited")
        self.assertEqual(result, "rate_limited")
        called.assert_called_once_with(self.md, 1000.0)

    def test_busy_forum_lock_skips_the_pass(self):
        import strategy_controller
        with patch.object(strategy_controller, "acquire", return_value=None):
            result, called = self.note(self.on)
        self.assertEqual(result, "busy")
        called.assert_not_called()

    def test_any_error_or_print_stays_out_of_the_result_line(self):
        def noisy(md, at):
            print('{"stars": 1, "review": "not ours"}')
            raise RuntimeError("boom")
        with redirect_stdout(io.StringIO()) as out:
            result, _ = self.note(self.on, run=noisy)
        self.assertEqual(result, "note_error: RuntimeError")
        self.assertEqual(out.getvalue(), "")

    def test_review_still_goes_out_and_the_note_outcome_is_logged(self):
        import forum_buyer_posts
        deal = self.root / "deal.json"
        deal.write_text(json.dumps({
            "order": {"order": "o1", "item": "category", "acceptable": ["G1"], "budget": 100, "kind": "goods"},
            "receipt": {"item": "G1", "price": 90, "task": "t1", "sig_seller": "fixture"},
            "settled": {"ledger": 10, "shop": "https://shop.test", "sku": "G1", "grade": "A", "value": 120}}), encoding="utf8")
        with patch.object(review_policy, "ROOT", self.root), patch("sys.argv", ["review", "--deal", str(deal)]), \
                patch.object(review_policy, "posting_limits", return_value=self.on), \
                patch.object(forum_buyer_posts, "run", side_effect=RuntimeError("boom")):
            with redirect_stdout(io.StringIO()) as out:
                review_policy.main()
        lines = [line for line in out.getvalue().splitlines() if line.strip()]
        self.assertEqual(json.loads(lines[-1])["stars"], 5)
        logged = [json.loads(p.read_text(encoding="utf8")) for p in (self.root / "logs/forum-policy").glob("*.json")]
        self.assertEqual([row["buyer_note"] for row in logged], ["note_error: RuntimeError"])

    def test_shipped_switch_is_on_and_the_limits_stay_valid(self):
        import forum_policy
        shipped = json.loads((forum_policy.ROOT / "forum_posting.json").read_text(encoding="utf-8"))
        self.assertIs(shipped["buyer_notes_from_review"], True)
        self.assertTrue(forum_policy.valid_limits(shipped))
        self.assertEqual((shipped["buyer_daily_limit"], shipped["buyer_min_interval_seconds"]), (288, 300))


if __name__ == "__main__":
    unittest.main()
