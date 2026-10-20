"""Model-free unit tests for the Codex subprocess boundary."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import harness


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = harness.SCHEMA_DIR / "seller_decision.json"

    def mock_process(self, result=None, *, returncode=0, invalid_json=None, stderr=None):
        process = MagicMock()
        process.pid = 12345
        process.returncode = returncode

        def communicate(*, input, timeout):
            args = self.popen.call_args.args[0]
            output = Path(args[args.index("--output-last-message") + 1])
            if invalid_json is not None:
                output.write_text(invalid_json, encoding="utf-8")
            elif result is not None:
                output.write_text(json.dumps(result), encoding="utf-8")
            return (None, stderr)

        process.communicate.side_effect = communicate
        self.popen.return_value = process
        return process

    def run_with_popen(self, result=None, *, returncode=0, invalid_json=None, stderr=None):
        with patch("harness.subprocess.Popen") as popen:
            self.popen = popen
            process = self.mock_process(result, returncode=returncode, invalid_json=invalid_json, stderr=stderr)
            value = harness.run_decision("decide", self.schema)
            command = popen.call_args.args[0]
            cwd = Path(popen.call_args.kwargs["cwd"])
            return value, command, cwd, process

    def test_command_is_isolated_fixed_and_single_call(self):
        result = {"action": "counter", "price": 120, "reason": "continue"}
        value, command, cwd, _ = self.run_with_popen(result)
        self.assertEqual(result, value)
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--model") + 1], "gpt-6.1-sol")
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertEqual(command[-1], "-")
        self.assertIs(self.popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(self.popen.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertFalse(self.popen.call_args.kwargs["shell"])
        self.assertTrue(self.popen.call_args.kwargs["start_new_session"])
        self.popen.assert_called_once()
        self.assertFalse(cwd.exists())

    def test_schema_and_output_paths_are_inside_temporary_directory(self):
        result = {"action": "ask", "price": 130, "reason": "opening"}
        _, command, cwd, _ = self.run_with_popen(result)
        schema = Path(command[command.index("--output-schema") + 1])
        output = Path(command[command.index("--output-last-message") + 1])
        self.assertEqual(schema.parent, cwd)
        self.assertEqual(output.parent, cwd)
        self.assertFalse(schema.exists())
        self.assertFalse(output.exists())

    def test_invalid_json_is_rejected(self):
        with patch("harness.subprocess.Popen") as popen:
            self.popen = popen
            self.mock_process(invalid_json="not json")
            with self.assertRaisesRegex(harness.HarnessError, "invalid JSON"):
                harness.run_decision("decide", self.schema)
            popen.assert_called_once()

    def test_schema_violation_is_rejected(self):
        bad = {"action": "discount", "price": 1, "reason": "invalid action"}
        with patch("harness.subprocess.Popen") as popen:
            self.popen = popen
            self.mock_process(bad)
            with self.assertRaisesRegex(harness.HarnessError, "schema validation"):
                harness.run_decision("decide", self.schema)

    def test_action_price_violation_is_rejected(self):
        bad = {"action": "reject", "price": 100, "reason": "no deal"}
        with patch("harness.subprocess.Popen") as popen:
            self.popen = popen
            self.mock_process(bad)
            with self.assertRaisesRegex(harness.HarnessError, "action-price"):
                harness.run_decision("decide", self.schema)

    def test_nonzero_exit_is_generic_and_does_not_retry(self):
        with patch("harness.subprocess.Popen") as popen:
            self.popen = popen
            process = MagicMock(pid=12345, returncode=7)
            process.communicate.return_value = (None, "unclassified failure")
            popen.return_value = process
            with self.assertRaisesRegex(harness.HarnessError, "status 7") as caught:
                harness.run_decision("decide", self.schema)
            self.assertNotIn("secret", str(caught.exception).lower())
            popen.assert_called_once()

    def test_stderr_failures_are_safely_classified_without_retry(self):
        cases = (
            ("unknown model requested", harness.ErrorType.MODEL_NOT_FOUND),
            ("you do not have access to this model", harness.ErrorType.MODEL_ACCESS_DENIED),
            ("authentication failed: sk-example-secret", harness.ErrorType.AUTHENTICATION_ERROR),
            ("unexpected argument '--bad'", harness.ErrorType.INVALID_CLI_OPTION),
            ("invalid JSON schema near token sk-example-secret", harness.ErrorType.SCHEMA_ERROR),
            ("network error: failed to connect", harness.ErrorType.NETWORK_ERROR),
            ("sandbox permission denied at /secret", harness.ErrorType.SANDBOX_PERMISSION_ERROR),
            ("completely novel sk-example-secret failure", harness.ErrorType.UNKNOWN_ERROR),
        )
        for stderr, expected in cases:
            with self.subTest(expected=expected), patch("harness.subprocess.Popen") as popen:
                process = MagicMock(pid=12345, returncode=1)
                process.communicate.return_value = (None, stderr)
                popen.return_value = process
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.run_decision("decide", self.schema)
                self.assertEqual(caught.exception.error_type, expected)
                self.assertEqual(caught.exception.exit_code, 1)
                self.assertNotIn("sk-example-secret", str(caught.exception))
                self.assertNotIn("/secret", str(caught.exception))
                popen.assert_called_once()

    def test_transport_failure_phrases_are_network_errors(self):
        messages = (
            "error sending request to service",
            "failed to send request to service",
            "stream disconnected before completion",
            "connect failed while opening transport",
            "request timed out while waiting for response",
        )
        for stderr in messages:
            with self.subTest(stderr=stderr), patch("harness.subprocess.Popen") as popen:
                process = MagicMock(pid=12345, returncode=1)
                process.communicate.return_value = (None, stderr)
                popen.return_value = process
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.run_decision("decide", self.schema)
                self.assertEqual(caught.exception.error_type, harness.ErrorType.NETWORK_ERROR)
                self.assertEqual(caught.exception.exit_code, 1)
                popen.assert_called_once()

    def test_ambiguous_request_failed_remains_unknown(self):
        with patch("harness.subprocess.Popen") as popen:
            process = MagicMock(pid=12345, returncode=1)
            process.communicate.return_value = (None, "request failed")
            popen.return_value = process
            with self.assertRaises(harness.HarnessError) as caught:
                harness.run_decision("decide", self.schema)
            self.assertEqual(caught.exception.error_type, harness.ErrorType.UNKNOWN_ERROR)
            popen.assert_called_once()

    def test_request_failed_requires_network_context(self):
        messages = (
            "HTTP request failed",
            "request failed because the proxy connection closed",
            "request failed during TLS handshake",
        )
        for stderr in messages:
            with self.subTest(stderr=stderr), patch("harness.subprocess.Popen") as popen:
                process = MagicMock(pid=12345, returncode=1)
                process.communicate.return_value = (None, stderr)
                popen.return_value = process
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.run_decision("decide", self.schema)
                self.assertEqual(caught.exception.error_type, harness.ErrorType.NETWORK_ERROR)
                popen.assert_called_once()

    def test_specific_errors_take_priority_over_transport_text(self):
        cases = (
            ("status 401: error sending request", harness.ErrorType.AUTHENTICATION_ERROR),
            ("model access denied; connect failed", harness.ErrorType.MODEL_ACCESS_DENIED),
            ("unexpected argument; request timed out", harness.ErrorType.INVALID_CLI_OPTION),
        )
        for stderr, expected in cases:
            with self.subTest(expected=expected), patch("harness.subprocess.Popen") as popen:
                process = MagicMock(pid=12345, returncode=1)
                process.communicate.return_value = (None, stderr)
                popen.return_value = process
                with self.assertRaises(harness.HarnessError) as caught:
                    harness.run_decision("decide", self.schema)
                self.assertEqual(caught.exception.error_type, expected)
                popen.assert_called_once()

    def test_timeout_stops_process_group_without_retry(self):
        with patch("harness.subprocess.Popen") as popen, patch("harness.os.killpg") as killpg:
            process = MagicMock(pid=12345)
            process.communicate.side_effect = subprocess.TimeoutExpired("codex", 1)
            process.wait.return_value = 0
            popen.return_value = process
            with self.assertRaises(harness.HarnessError) as caught:
                harness.run_decision(
                    "decide",
                    self.schema,
                    harness.HarnessConfig(timeout_seconds=1),
                )
            self.assertEqual(caught.exception.error_type, harness.ErrorType.TIMEOUT)
            self.assertIsNone(caught.exception.exit_code)
            killpg.assert_called_once_with(12345, harness.signal.SIGTERM)
            popen.assert_called_once()

    def test_external_interrupt_stops_process_group(self):
        with patch("harness.subprocess.Popen") as popen, patch("harness.os.killpg") as killpg:
            process = MagicMock(pid=12345)
            process.communicate.side_effect = KeyboardInterrupt()
            process.wait.return_value = 0
            popen.return_value = process
            with self.assertRaises(KeyboardInterrupt):
                harness.run_decision("decide", self.schema)
            killpg.assert_called_once_with(12345, harness.signal.SIGTERM)
            popen.assert_called_once()

    def test_missing_output_is_rejected(self):
        with patch("harness.subprocess.Popen") as popen:
            process = MagicMock(pid=12345, returncode=0)
            process.communicate.return_value = (None, None)
            popen.return_value = process
            with self.assertRaisesRegex(harness.HarnessError, "output file"):
                harness.run_decision("decide", self.schema)

    def test_seller_reserve_violation_is_rejected(self):
        context = harness.SellerDecisionContext("lamp", 100, "open", 80)
        result = {"action": "counter", "price": 90, "reason": "continue"}
        with patch("harness.run_decision", return_value=result) as run:
            with self.assertRaisesRegex(harness.HarnessError, "reserve"):
                harness.decide_seller(context)
            run.assert_called_once()

    def test_buyer_budget_violation_is_rejected(self):
        context = harness.BuyerDecisionContext("lamp", 120, 130, "task-1", 110)
        result = {"action": "offer", "price": 111, "reason": "continue"}
        with patch("harness.run_decision", return_value=result) as run:
            with self.assertRaisesRegex(harness.HarnessError, "budget"):
                harness.decide_buyer(context)
            run.assert_called_once()

    def test_valid_buyer_json_uses_buyer_schema(self):
        context = harness.BuyerDecisionContext("lamp", 120, 130, "task-1", 120)
        result = {"action": "offer", "price": 110, "reason": "continue"}
        with patch("harness.run_decision", return_value=result) as run:
            self.assertEqual(result, harness.decide_buyer(context))
            schema_path = run.call_args.args[1]
            self.assertEqual(schema_path.name, "buyer_decision.json")

    def test_general_prompts_do_not_add_diagnostic_round_policy(self):
        seller_context = harness.SellerDecisionContext("lamp", 100, "opening", None)
        buyer_context = harness.BuyerDecisionContext("lamp", 120, 150, "task-1", 120)
        with patch("harness.run_decision", return_value={
            "action": "ask", "price": 150, "reason": "open",
        }) as run:
            harness.decide_seller(seller_context)
        seller_prompt = run.call_args.args[0]
        with patch("harness.run_decision", return_value={
            "action": "offer", "price": 90, "reason": "offer",
        }) as run:
            harness.decide_buyer(buyer_context)
        buyer_prompt = run.call_args.args[0]
        for prompt in (seller_prompt, buyer_prompt):
            self.assertNotIn("remaining_buyer_decisions", prompt)
            self.assertNotIn("final", prompt.lower())

    def test_final_diagnostic_prompts_preserve_model_choice_and_safety_limits(self):
        seller_context = harness.SellerDecisionContext(
            "lamp", 100, "negotiating", 110,
            remaining_buyer_decisions=0, is_final_decision=True,
        )
        buyer_context = harness.BuyerDecisionContext(
            "lamp", 120, 130, "task-1", 120,
            remaining_buyer_decisions=0, is_final_decision=True,
        )
        with patch("harness.run_decision", return_value={
            "action": "counter", "price": 120, "reason": "counter",
        }) as run:
            harness.decide_seller(seller_context)
        seller_prompt = run.call_args.args[0]
        self.assertIn("final seller decision opportunity", seller_prompt)
        self.assertIn("actively consider accepting", seller_prompt)
        self.assertIn("still choose", seller_prompt)
        self.assertIn("Never ask or accept below the reserve", seller_prompt)

        with patch("harness.run_decision", return_value={
            "action": "offer", "price": 120, "reason": "final offer",
        }) as run:
            harness.decide_buyer(buyer_context)
        buyer_prompt = run.call_args.args[0]
        self.assertIn("final buyer decision", buyer_prompt)
        self.assertIn("chance of completing the deal", buyer_prompt)
        self.assertIn("still choose", buyer_prompt)
        self.assertIn("never exceeding the remaining budget", buyer_prompt)


if __name__ == "__main__":
    unittest.main()
