import argparse
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from claude_model import ClaudeModel, MODEL, cli_environment, parse_result
from run_agent import OPEN_AT, market_args


class ModelTests(unittest.TestCase):
    def result(self):
        return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                "permission_denials": [], "modelUsage": {MODEL: {"outputTokens": 10, "canonicalModel": MODEL}},
                "result": json.dumps({"thought": "fixture", "action": "sleep", "args": {"seconds": 30}})}

    def test_decision_and_fenced_json(self):
        result = self.result()
        self.assertEqual(json.loads(parse_result(json.dumps(result)))["action"], "sleep")
        result["result"] = "```json\n" + result["result"] + "\n```"
        self.assertEqual(json.loads(parse_result(json.dumps(result)))["action"], "sleep")

    def test_model_fallback_and_tool_use_fail_closed(self):
        for change in ({"modelUsage": {"haiku": {"outputTokens": 1}}},
                       {"permission_denials": [{"tool": "Bash"}]}, {"num_turns": 2},
                       {"is_error": True}, {"deferred_tool_use": {}},
                       {"result": '{"action":"sleep","args":[]}'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_result(json.dumps(dict(self.result(), **change)))

    def test_transport_disables_cli_tools_and_api_billing(self):
        with patch("claude_model.shutil.which", return_value="/test/claude"):
            model = ClaudeModel({"llm": {"backend": "claude-cli", "model": MODEL}})
        command = model.command()
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--max-turns") + 1], "1")
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("--dangerously-skip-permissions", command)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-only", "ANTHROPIC_BASE_URL": "https://invalid.example"}):
            env = cli_environment()
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_market_is_gated_until_open_and_uses_only_round4(self):
        args = argparse.Namespace(name="fixture", app_tunnel=False)
        with self.assertRaisesRegex(ValueError, "opens"):
            market_args(args, OPEN_AT - timedelta(seconds=1))
        command = market_args(args, OPEN_AT)
        self.assertIn("https://market.ourolabs.ai", command)
        self.assertTrue(command[command.index("--dir") + 1].endswith("/round-4"))
        self.assertNotIn("--owner", command)


if __name__ == "__main__":
    unittest.main()
