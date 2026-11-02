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


if __name__ == "__main__":
    unittest.main()
