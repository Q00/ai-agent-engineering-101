"""Certified usable C food, unchanged goods limits, and clock-safe review facts."""
import base64
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import buyer_policy
import review_settled
from vendor import market_kit_v4 as kit


class FoodPolicyTests(unittest.TestCase):
    def test_real_certificate_checker_allows_c_only_with_sufficient_actual_lifetime(self):
        key = Ed25519PrivateKey.generate()
        order = {"kind": "food", "use_within_hours": 2, "shelf_hours": 24,
                 "market_pubkey": kit.pub_b64(key)}
        accept = buyer_policy.checkout_policy(order)
        self.assertEqual(accept, {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 3})
        for remaining, allowed in ((4, True), (3, True), (2.99, False), (0, False), (-1, False)):
            body = {"unit": "u1", "sku": "F00001", "grade": "A", "expires_at": 10000 + remaining * 3600}
            cert = {"cert": body, "sig": base64.b64encode(key.sign(kit.canon(body))).decode()}
            checkout = {"unit": "u1", "item": "F00001", "cert": cert}
            # Initial A in the certificate cannot bypass elapsed-lifetime C or
            # the required use window. This is the actual pinned kit checker.
            with self.subTest(remaining=remaining), patch.object(kit.time, "time", return_value=10000):
                self.assertEqual(kit.check_accept(order, checkout, accept) is None, allowed)
        self.assertIsNotNone(kit.check_accept(order, {"item": "F00001"}, accept))
        cert["cert"]["expires_at"] += 100000
        self.assertIsNotNone(kit.check_accept(order, checkout, accept))

    def test_goods_and_historical_food_policy_are_unchanged(self):
        config = buyer_policy.load_policy()
        self.assertEqual(buyer_policy.checkout_policy({"kind": "goods"}, config),
                         {"cert": True, "grades": ["A", "B"], "warranty_hours": 3})
        config.pop("food_grades")
        self.assertEqual(buyer_policy.checkout_policy({"kind": "food", "use_within_hours": 2}, config)["grades"],
                         ["A", "B"])
        for invalid in (None, [], "ABC", ["D"], [True]):
            with self.subTest(invalid=invalid), patch.object(buyer_policy, "read_json",
                                                           return_value={**config, "food_grades": invalid}):
                with self.assertRaises(ValueError):
                    buyer_policy.load_policy()

    def test_food_keeps_price_caps_and_pinned_preferences(self):
        terms = buyer_policy.buying_terms(80000, product={"list_price": 100000},
                    order={"round": 3, "kind": "food", "use_within_hours": 4})
        self.assertEqual(terms["price_limit"], 72000)
        self.assertEqual(terms["max_decisions"], 4)
        self.assertEqual(terms["accept"]["min_hours_left"], 5)
        self.assertEqual(terms["review_preferences"]["grades"], ["A"])


class FoodReviewClockTests(unittest.TestCase):
    def setUp(self):
        self.terms = buyer_policy.buying_terms(100000, product={"list_price": 100000},
                    order={"round": 3, "kind": "food", "use_within_hours": 2})
        self.deal = {"order": {"order": "o1", "round": 3, "kind": "food"},
                     "receipt": {"item": "F00001", "task": "t1", "price": 50000},
                     "settled": {"ledger": 17, "sku": "F00001", "grade": "C",
                                 "expires_at": 10000 + 3.1 * 3600, "value": 100000,
                                 "claim_honest": None, "shop": "https://fixture.invalid"}}
        self.clock = {"status": "synced", "now": 10000, "uncertainty_seconds": 2}

    def test_fast_host_clock_does_not_invent_short_life_or_dishonesty(self):
        result = review_settled.review(self.deal, terms=self.terms, now=10780, market_clock=self.clock)
        self.assertEqual(result["reviewed_at"], 10780)
        self.assertEqual(result["market_clock"], self.clock)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["preference_shortfalls"], ["preferred_grade_not_met"])
        self.assertEqual(result["stars"], 3)
        self.assertNotIn("did not meet", result["review"])

    def test_unknown_clock_and_boundary_are_uncertainty_not_shop_faults(self):
        for clock in ({"status": "stale", "now": 10780},
                      {**self.clock, "now": float("nan")},
                      {**self.clock, "uncertainty_seconds": 7}):
            with self.subTest(clock=clock):
                result = review_settled.review(self.deal, terms=self.terms, now=10780, market_clock=clock)
                self.assertEqual(result["issues"], [])
                self.assertIn("expiry_clock_unavailable", result["reasons"])
                self.assertEqual(result["market_clock"]["status"], "unavailable")
        self.deal["settled"]["expires_at"] = 10000 + 3 * 3600 - 1
        result = review_settled.review(self.deal, terms=self.terms, market_clock=self.clock)
        self.assertIn("expiry_clock_boundary", result["reasons"])
        self.assertEqual(result["issues"], [])

    def test_actual_short_life_expiry_and_false_claim_remain_issues(self):
        for changes, issue in (({"expires_at": 10000 + 2.9 * 3600}, "short_shelf_life_at_review"),
                               ({"expires_at": 9990}, "expired_at_review"),
                               ({"claim_honest": False}, "claim_mismatch")):
            deal = deepcopy(self.deal)
            deal["settled"].update(changes)
            with self.subTest(issue=issue):
                result = review_settled.review(deal, terms=self.terms, market_clock=self.clock)
                self.assertIn(issue, result["issues"])
                self.assertLessEqual(result["stars"], 2)

    def test_hook_saves_clock_snapshot_without_new_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "deal.json"
            path.write_text(json.dumps(self.deal))
            with patch.object(review_settled, "ROOT", root), \
                    patch.object(review_settled.sys, "argv", ["review", "--deal", str(path)]), \
                    patch.object(review_settled, "purchase_terms", return_value=self.terms), \
                    patch.object(review_settled, "schedule_now", return_value=self.clock) as clock, \
                    patch.object(review_settled, "queue_confirmed_deal", return_value={"status": "queued"}), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                review_settled.main()
            clock.assert_called_once_with(root / ".market", 3)
            saved = json.loads((root / "logs/settled-deals.jsonl").read_text())
            self.assertEqual(saved["assessment"]["market_clock"], self.clock)
            self.assertNotIn("purchase_policy", saved)
            self.assertNotIn("budget", saved)


if __name__ == "__main__":
    unittest.main()
