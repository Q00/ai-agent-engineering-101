import json
import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

from meta_harness.clients import APIClient, Budget, BudgetExceeded, Completion, ModelSpec
from meta_harness.contracts import Policy, gate
from meta_harness.evaluation import run_case, suite, tool_output


def action(name="read_file", args=None, call_id="read-1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(args or {"path": "app.log"})}}]}


class Scripted:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.requests = []

    def complete(self, messages, **kwargs):
        self.requests.append(messages)
        return Completion(next(self.messages), 10, 2, 0.0)


class CoreTests(unittest.TestCase):
    def test_candidate_cannot_change_oracle_or_use_boolean_step_limit(self):
        for extra in ({"expected": "14:00"}, {"max_steps": True}, {"max_steps": 1000}):
            with self.assertRaises(ValueError):
                Policy.parse({**asdict(Policy()), **extra})

    def test_gate_checks_each_case_not_only_total_accuracy(self):
        old = [dict(case="a", repeat=0, success=True, tokens=100),
               dict(case="b", repeat=0, success=False, tokens=100)]
        swapped = [dict(case="a", repeat=0, success=False, tokens=1),
                   dict(case="b", repeat=0, success=True, tokens=1)]
        self.assertFalse(gate(old, swapped)[0])
        self.assertFalse(gate(old, old[:1])[0])
        self.assertFalse(gate(old, [{**r, "tokens": None} for r in old])[0])
        self.assertTrue(gate(old, [{**r, "tokens": 80} for r in old])[0])

    def test_fixtures_exercise_truncation_and_ties(self):
        training, heldout = suite()
        self.assertEqual([c.expected for c in training], ["14:00", "21:00", "07:00"])
        self.assertEqual([c.expected for c in heldout], ["06:00", "19:00"])
        self.assertGreater(len(training[1].text), 4000)
        self.assertGreater(len(heldout[1].text), 4000)

    def test_no_local_file_is_read_by_an_executor_tool(self):
        case = suite()[0][0]
        for path in (".env", "../app.log", "/etc/passwd"):
            with self.assertRaises(ValueError):
                tool_output(case, action(args={"path": path})["tool_calls"][0])

    def test_correct_guess_without_evidence_fails(self):
        client = Scripted([{"role": "assistant", "content": "Answer: 14:00"}])
        result = run_case(Policy(), suite()[0][0], 0, client, lambda *a, **k: None)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "no_tool_evidence")

    def test_context_trimming_preserves_complete_tool_pairs(self):
        client = Scripted([action(call_id="one"), action(call_id="two"),
                           {"role": "assistant", "content": "Answer: 14:00"}])
        result = run_case(Policy(history_turns=1), suite()[0][0], 0,
                          client, lambda *a, **k: None)
        self.assertTrue(result["success"])
        last = client.requests[-1]
        self.assertEqual([m["role"] for m in last], ["system", "user", "assistant", "tool"])
        self.assertEqual(last[-1]["tool_call_id"], last[-2]["tool_calls"][0]["id"])
        self.assertEqual(last[-1]["tool_call_id"], "two")

    def test_failure_retains_trace_and_partial_work(self):
        client = Scripted([action(args={"path": ".env"})])
        result = run_case(Policy(error_recovery="stop"), suite()[0][0], 0,
                          client, lambda *a, **k: None)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "tool_error")
        self.assertEqual(result["tokens"], 12)
        self.assertIn("error:", result["trace"][-1]["content"])

    def test_budget_is_global_and_does_not_grant_an_extra_call(self):
        budget = Budget(limit=1)
        budget.claim()
        with self.assertRaises(BudgetExceeded):
            budget.claim()
        self.assertEqual(budget.calls, 1)

    def test_clients_keep_provider_keys_separate_and_mask_errors(self):
        events = []
        with patch.dict("os.environ", {"FIRST_KEY": "test-first", "SECOND_KEY": "test-second"}), \
             patch("openai.OpenAI") as constructor:
            first = APIClient(ModelSpec("openai", "model-a", "FIRST_KEY", "https://api.openai.com/v1", "none"),
                              Budget(), lambda *a, **k: events.append((a, k)))
            second = APIClient(ModelSpec("google", "model-b", "SECOND_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/"),
                               Budget(), lambda *a, **k: events.append((a, k)))
            self.assertEqual(constructor.call_args_list[0].kwargs["api_key"], "test-first")
            self.assertEqual(constructor.call_args_list[1].kwargs["api_key"], "test-second")
            constructor.return_value.chat.completions.create.side_effect = RuntimeError("test-first test-second")
            with self.assertRaisesRegex(RuntimeError, "openai request failed: RuntimeError"):
                first.complete([{"role": "user", "content": "test"}])
            with self.assertRaises(RuntimeError):
                second.complete([{"role": "user", "content": "test"}])
            calls = constructor.return_value.chat.completions.create.call_args_list
            self.assertEqual(calls[0].kwargs["reasoning_effort"], "none")
            self.assertNotIn("reasoning_effort", calls[1].kwargs)
        serialized = json.dumps(events)
        self.assertNotIn("test-first", serialized)
        self.assertNotIn("test-second", serialized)


if __name__ == "__main__":
    unittest.main()
