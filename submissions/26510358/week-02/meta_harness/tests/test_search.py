import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meta_harness import __main__ as cli
from meta_harness.clients import Budget, BudgetExceeded, Completion, specs
from meta_harness.demo import DemoClient
from meta_harness.evaluation import suite
from meta_harness.search import ask, optimize


def demo_clients(budget, emit):
    models = specs()
    return {role: DemoClient(models[name], budget, emit) for role, name in
            {"proposer": "gpt", "reviewer": "gemini", "refiner": "solar", "executor": "gpt"}.items()}


class SearchTests(unittest.TestCase):
    def test_truncated_valid_json_is_still_rejected_and_failed_search_is_explicit(self):
        from unittest.mock import Mock
        reviewer = Mock()
        reviewer.complete.return_value = Completion(
            {"content": '{"issues": [], "recommendation": "Keep all history."}'}, 10, 20, 0.0, "length")
        with self.assertRaisesRegex(ValueError, "Incomplete reviewer response"):
            ask(reviewer, "reviewer", {}, "Review")
        clients = demo_clients(Budget(), lambda *a, **k: None)
        clients["reviewer"] = reviewer
        result = optimize(clients, *suite(), 1, 1, lambda *a, **k: None)
        self.assertEqual(result["status"], "search_failed")
        self.assertFalse(result["history"][0]["accepted"])

    def test_end_to_end_demo_and_heldout_is_never_search_feedback(self):
        events = []
        emit = lambda kind, **data: events.append({"event": kind, **data})
        training, heldout = suite()
        result = optimize(demo_clients(Budget(), emit), training, heldout, 1, 1, emit)
        self.assertEqual(result["status"], "provisional_improvement")
        roles = [e["role"] for e in events if e["event"] == "demo_response" and e["role"] != "executor"]
        self.assertEqual(roles, ["proposer", "reviewer", "refiner"])
        for event in events:
            if event["event"] == "demo_response" and event["role"] != "executor":
                prompt = json.dumps(event["messages"])
                for case in heldout:
                    self.assertNotIn(case.name, prompt)
        start = next(i for i, event in enumerate(events) if event["event"] == "heldout_start")
        self.assertFalse(any(e.get("role") in roles for e in events[start:]))

    def test_heldout_rejection_retains_original_policy(self):
        from meta_harness.contracts import Policy, gate
        from dataclasses import asdict
        training, heldout = suite()
        # First gate accepts training; the independent heldout gate rejects.
        with patch("meta_harness.search.gate", side_effect=[(True, "training_pass"), (False, "heldout_regression")]):
            result = optimize(demo_clients(Budget(), lambda *a, **k: None),
                              training, heldout, 1, 1, lambda *a, **k: None)
        self.assertEqual(result["status"], "baseline_retained")
        self.assertEqual(result["selected_policy"], asdict(Policy()))

    def test_budget_abort_keeps_partial_evidence_and_never_selects_a_policy(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "ROOT", Path(temp)), \
             patch("sys.argv", ["meta_harness", "--demo", "--max-calls", "1"]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 1)
            artifact = next(Path(temp).rglob("result.json"))
            result = json.loads(artifact.read_text())
            self.assertEqual(result["status"], "aborted")
            self.assertNotIn("selected_policy", result)
            self.assertEqual(result["total_model_call_attempts"], 1)
            events = artifact.with_name("events.jsonl").read_text()
            self.assertIn('"event": "partial_case"', events)

    def test_missing_provider_stops_before_any_call_or_artifact(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "ROOT", Path(temp)), \
             patch.dict("os.environ", {}, clear=True), patch("sys.argv", ["meta_harness", "--run"]), \
             patch.object(cli, "APIClient") as client, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 1)
            client.assert_not_called()
            self.assertFalse((Path(temp) / "logs").exists())


if __name__ == "__main__":
    unittest.main()
