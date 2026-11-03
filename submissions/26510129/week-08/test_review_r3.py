import tempfile
import unittest
from pathlib import Path

import review_r3


class SettledReviewTest(unittest.TestCase):
    def deal(self, **settled):
        base = {"name": "bed", "grade": "A", "claim_honest": True, "value": 1000, "ledger": 12}
        base.update(settled)
        return {"order": {"kind": "goods"}, "receipt": {"item": "B1", "price": 800}, "settled": base}

    def test_good_value_and_honest_claim_get_five(self):
        stars, _ = review_r3.build_review(self.deal())
        self.assertEqual(stars, 5)

    def test_false_claim_gets_one(self):
        stars, text = review_r3.build_review(self.deal(claim_honest=False))
        self.assertEqual(stars, 1)
        self.assertIn("false", text)

    def test_zero_value_gets_one(self):
        stars, _ = review_r3.build_review(self.deal(value=0))
        self.assertEqual(stars, 1)

    def test_forum_post_has_reference_and_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertTrue(review_r3.maybe_post(state, self.deal(), now=1000))
            self.assertFalse(review_r3.maybe_post(state, self.deal(), now=1100))
            line = (state / "outbox.jsonl").read_text(encoding="utf-8")
            self.assertIn('"refs": [12]', line)
            self.assertIn('"as": "buyer"', line)


if __name__ == "__main__":
    unittest.main()
