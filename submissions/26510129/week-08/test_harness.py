import unittest
from types import SimpleNamespace
from unittest.mock import patch

import harness


class HarnessTest(unittest.TestCase):
    def test_command_bounds_haiku_cost_and_customization(self):
        self.assertIn("haiku", harness.CMD)
        self.assertIn("low", harness.CMD)
        self.assertIn("0.005", harness.CMD)
        self.assertIn("--safe-mode", harness.CMD)
        self.assertIn("--no-session-persistence", harness.CMD)
        self.assertEqual(0, harness.RETRIES)

    @patch("harness.subprocess.run")
    def test_call_has_no_quota_and_adds_compact_system_rule(self, run):
        run.return_value = SimpleNamespace(
            stdout='{"action":"offer","price":12}', stderr="", returncode=0
        )
        for _ in range(3):
            self.assertEqual('{"action":"offer","price":12}', harness.call("rules", "state"))
        self.assertEqual(3, run.call_count)
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
