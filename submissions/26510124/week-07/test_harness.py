"""Offline boundary checks, including the first real Claude response failure."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import harness


class OpeningResponseTests(unittest.TestCase):
    def test_first_recorded_response_keeps_the_models_price(self):
        log = (harness.ROOT / "logs" / "opening-c6021350801d472e8daec0dddfdb5162.log").read_text()
        raw = log.split("STDOUT:\n", 1)[1].split("\nSTDERR:\n", 1)[0]
        self.assertEqual(harness.parse_opening(raw, 100)["price"], 125)

    def test_plain_json_for_each_reserve(self):
        for reserve, price in [(100, 120), (180, 210), (260, 300)]:
            with self.subTest(reserve=reserve):
                reply = json.dumps({"price": price, "reason": "Room to negotiate"})
                self.assertEqual(harness.parse_opening(reply, reserve)["price"], price)

    def test_invalid_prices_are_rejected_not_replaced(self):
        for price in [True, "120", 99, -1, 100.5, float("nan"), float("inf")]:
            with self.subTest(price=price):
                with self.assertRaises(harness.HarnessError):
                    harness.parse_opening(json.dumps({"price": price, "reason": "test"}), 100)

    def test_unusable_responses_are_rejected(self):
        for reply in ["", "not JSON", "[]", "{}", '{"price": 120}',
                      'Here is the price: {"price": 120, "reason": "test"}']:
            with self.subTest(reply=reply):
                with self.assertRaises(harness.HarnessError):
                    harness.parse_opening(reply, 100)

    def test_seller_limits_apply_to_counteroffers_and_acceptance(self):
        for action, price, offer in [("counter", 99, 80), ("accept", 120, 110), ("accept", 90, 90)]:
            with self.subTest(action=action, price=price), self.assertRaises(harness.HarnessError):
                harness.parse_seller(json.dumps({"action": action, "price": price, "reason": "test"}), 100, offer)
        accepted = harness.parse_seller('{"action":"accept","price":110,"reason":"agreement"}', 100, 110)
        self.assertEqual(accepted["price"], 110)

    def test_buyer_never_returns_over_budget_or_invalid_actions(self):
        for action, price in [("offer", 121), ("accept", True), ("offer", -1), ("skip", None)]:
            with self.subTest(action=action, price=price), self.assertRaises(harness.HarnessError):
                harness.parse_buyer(json.dumps({"action": action, "price": price, "reason": "test"}), 120)
        accepted = harness.parse_buyer('{"action":"offer","price":120,"reason":"within budget"}', 120)
        self.assertEqual(accepted["price"], 120)

    def test_invalid_output_is_reasked_and_each_attempt_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "prompts").mkdir()
            (root / "prompts/seller_opening.txt").write_text("Choose a price.")
            replies = [
                subprocess.CompletedProcess(harness.COMMAND, 0, '{"price":90,"reason":"bad"}', ""),
                subprocess.CompletedProcess(harness.COMMAND, 0, '{"price":145,"reason":"fixed"}', ""),
            ]
            with patch.object(harness, "ROOT", root), patch.object(harness.subprocess, "run", side_effect=replies) as run:
                self.assertEqual(harness.opening_price("lamp", 100, "I want a lamp"), 145)
                self.assertEqual(run.call_count, 2)
                self.assertIn("failed validation", run.call_args_list[1].kwargs["input"])
            logs = list((root / "logs").glob("opening-*.log"))
            self.assertEqual(len(logs), 2)
            self.assertTrue(any("VALIDATION ERROR:" in log.read_text() for log in logs))

    def test_execution_failures_preserve_a_log_and_do_not_return_a_price(self):
        failures = [
            FileNotFoundError("test: claude missing"),
            subprocess.TimeoutExpired(harness.COMMAND, 90, output=b"partial reply"),
            subprocess.CompletedProcess(harness.COMMAND, 1, "", "test: login required"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "prompts").mkdir()
                (root / "prompts/seller_opening.txt").write_text("Choose a price.")
                mock_args = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(harness, "ROOT", root), patch.object(harness.subprocess, "run", **mock_args):
                    with self.assertRaises(harness.HarnessError):
                        harness.opening_price("lamp", 100, "I want a lamp")
                logs = list((root / "logs").glob("opening-*.log"))
                self.assertEqual(len(logs), 1)
                self.assertIn("PROMPT:", logs[0].read_text())


if __name__ == "__main__":
    unittest.main()
