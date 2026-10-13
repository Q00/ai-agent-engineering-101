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
