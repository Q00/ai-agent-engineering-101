import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import harness


class HarnessTest(unittest.TestCase):
    def test_command_bounds_haiku_cost_and_customization(self):
        self.assertIn("haiku", harness.CMD)
        self.assertIn("low", harness.CMD)
        self.assertIn("0.002", harness.CMD)
        self.assertIn("--safe-mode", harness.CMD)
        self.assertIn("--no-session-persistence", harness.CMD)
        self.assertEqual(0, harness.RETRIES)

    def test_shared_quota_stops_at_daily_limit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"MARKET_DIR": directory, "MARKET_LLM_DAILY_LIMIT": "2"}, clear=True
        ):
            now = datetime(2026, 11, 3, 12, tzinfo=ZoneInfo("Asia/Seoul"))
            self.assertEqual((1, 2), harness._claim_daily_quota(now))
            self.assertEqual((2, 2), harness._claim_daily_quota(now))
            self.assertIsNone(harness._claim_daily_quota(now))

    def test_quota_resets_on_next_kst_day(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"MARKET_DIR": directory, "MARKET_LLM_DAILY_LIMIT": "1"}, clear=True
        ):
            day1 = datetime(2026, 11, 3, 23, tzinfo=ZoneInfo("Asia/Seoul"))
            day2 = datetime(2026, 11, 4, 0, tzinfo=ZoneInfo("Asia/Seoul"))
            self.assertEqual((1, 1), harness._claim_daily_quota(day1))
            self.assertEqual((1, 1), harness._claim_daily_quota(day2))

    @patch("harness.subprocess.run")
    def test_call_uses_one_quota_slot_and_compact_system_rule(self, run):
        run.return_value = SimpleNamespace(
            stdout='{"action":"offer","price":12}', stderr="", returncode=0
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"MARKET_DIR": directory, "MARKET_LLM_DAILY_LIMIT": "1"}, clear=True
        ):
            self.assertEqual('{"action":"offer","price":12}', harness.call("rules", "state"))
            self.assertEqual("", harness.call("rules", "state"))
        self.assertEqual(1, run.call_count)
        command = run.call_args.args[0]
        system = command[command.index("--system-prompt") + 1]
        self.assertIn("under 120 characters", system)

    def test_parse_ignores_markdown_and_finds_json(self):
        text = '```json\n{"action":"offer","price":12}\n```'
        self.assertEqual({"action": "offer", "price": 12}, harness.parse(text))

    @patch("harness.call", return_value='{"action":"offer","price":12.6}')
    def test_decide_normalizes_price_without_retry(self, call):
        self.assertEqual({"action": "offer", "price": 13}, harness.decide("rules", "state"))
        self.assertEqual(1, call.call_count)


if __name__ == "__main__":
    unittest.main()
