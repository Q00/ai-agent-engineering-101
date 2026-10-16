"""Opening-quote validation tests; all harness answers here are synthetic fixtures."""

import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from harness import HarnessError
import seller


def ask(price):
    return {"action": "ask", "price": price, "reason": "local test fixture"}


class OpeningChecks(unittest.TestCase):
    def setUp(self):
        self.root = seller.ROOT / "logs/step3-tests" / uuid4().hex
        (self.root / "prompts").mkdir(parents=True)
        (self.root / "prompts/seller.txt").write_text(
            (seller.ROOT / "prompts/seller.txt").read_text(encoding="utf-8"), encoding="utf-8")

    def call_with(self, responses):
        with patch.object(seller, "ROOT", self.root), patch.object(
                seller, "run_harness", side_effect=responses) as runner:
            value = seller.opening_decision("claude", "lamp", "I want a lamp", "local-fixture-task")
        return value, runner

    def test_reject_under_reserve_and_wrong_action(self):
        for response in (ask(99), ask(True), {"action": "reject", "price": None, "reason": "test"}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                seller.validate_opening(response, 100)

    def test_valid_price_is_not_replaced(self):
        value, runner = self.call_with([ask(147)])
        self.assertEqual(value, ask(147))
        self.assertEqual(runner.call_count, 1)

    def test_retry_constraint_violation_once(self):
        value, runner = self.call_with([ask(99), ask(139)])
        self.assertEqual(value["price"], 139)
        self.assertEqual(runner.call_count, 2)
        self.assertIn("at least the reserve 100", runner.call_args.args[1])

    def test_two_invalid_decisions_fail_without_fallback(self):
        with self.assertRaises(HarnessError):
            self.call_with([ask(99), ask(98)])
        record = json.loads(next((self.root / "logs/seller").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual(record["attempts"][-1]["response"]["price"], 98)

    def test_process_failure_is_not_retried(self):
        with patch.object(seller, "ROOT", self.root), patch.object(
                seller, "run_harness", side_effect=HarnessError("fixture process failure")) as runner:
            with self.assertRaises(HarnessError):
                seller.opening_decision("claude", "lamp", "lamp", "local-fixture-task")
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
