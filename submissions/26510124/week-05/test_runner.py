"""Offline checks of append-only recovery, provenance, locking and batch resumption."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import run_experiment as runner


class DurableResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()

    def tearDown(self):
        self.console.__exit__(None, None, None)
        self.temp.cleanup()

    def capture(self, events):
        path = self.root / "logs" / "prompt_inject-run-01.jsonl"
        log = runner.JsonlLogger(path)
        try:
            for event in events:
                log(event)
        finally:
            log.close()
        return path

    def row(self):
        row = dict.fromkeys(runner.HEADER, "")
        row.update(run=1, condition="prompt_inject", scenario="one", outcome="no_deal", note="host=fake;model=fake")
        return row

    def test_completed_log_result_recovers_csv_once(self):
        row = self.row()
        self.capture([{"event": "episode_result", "row": row}])
        recovered = runner.recover_results(self.root, "host=fake;model=fake")
        self.assertEqual(len(recovered), 1)
        before = (self.root / "results.csv").read_bytes()
        runner.recover_results(self.root, "host=fake;model=fake")
        self.assertEqual(before, (self.root / "results.csv").read_bytes())

    def test_interrupted_episode_retains_logs_and_blank_crash_row(self):
        path = self.capture([{"event": "episode_start", "run": 1, "condition": "prompt_inject", "scenario": "one"},
                             {"event": "model_result", "message": "unfinished finding"}])
        original = path.read_bytes()
        with path.open("ab") as handle:
            handle.write(b'{"event": "model_')
        interrupted = path.read_bytes()
        rows = runner.recover_results(self.root, "host=fake;model=fake")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "")
        self.assertEqual(rows[0]["attempted_violations"], "")
        self.assertIn("InterruptedEpisode", rows[0]["note"])
        self.assertTrue(path.read_bytes().startswith(interrupted))
        self.assertTrue(interrupted.startswith(original))
        self.assertEqual(len(runner.recover_results(self.root, "host=fake;model=fake")), 1)

    def test_csv_log_conflict_rejected(self):
        row = self.row()
        runner.write_row(self.root / "results.csv", row)
        self.capture([{"event": "episode_result", "row": {**row, "outcome": "deal"}}])
        with self.assertRaisesRegex(ValueError, "CSV and log disagree"):
            runner.recover_results(self.root, "host=fake;model=fake")

    def test_corrupt_middle_log_is_not_ignored(self):
        path = self.root / "bad.jsonl"
        path.write_text('{"event":"one"}\ninvalid\n{"event":"two"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid log line"):
            runner.read_events(path)

    def test_changed_configuration_rejected(self):
        first = runner.ensure_manifest(self.root, {"temperature": 0.2}, "commit-one")
        self.assertEqual(first, runner.ensure_manifest(self.root, {"temperature": 0.2}, "commit-two"))
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            runner.ensure_manifest(self.root, {"temperature": 0.3}, "commit-two")

    def test_exclusive_writer(self):
        with runner.single_writer(self.root):
            with self.assertRaisesRegex(RuntimeError, "another runner"):
                with runner.single_writer(self.root):
                    self.fail("a second writer acquired the lock")

    def test_redaction_applies_to_nested_events(self):
        path = self.root / "capture.jsonl"
        log = runner.JsonlLogger(path, ["private-token"])
        try:
            log({"event": "error", "nested": ["private-token", {"header": "Bearer arbitrary-value"}]})
        finally:
            log.close()
        content = path.read_text()
        self.assertNotIn("private-token", content)
        self.assertNotIn("arbitrary-value", content)

    def test_condition_rotation_preserves_every_condition(self):
        conditions = list(runner.CONDITIONS)
        orders = [runner.condition_order(conditions, repeat) for repeat in range(1, 5)]
        self.assertEqual({order[0] for order in orders}, set(conditions))
        self.assertTrue(all(set(order) == set(conditions) for order in orders))

    def test_provider_configuration_failure_stops_but_transient_failure_does_not(self):
        from host import ModelCallError

        self.assertTrue(runner.fatal_configuration_error(ModelCallError("AuthenticationError", 401)))
        nested = ExceptionGroup("MCP context", [ModelCallError("BadRequestError", 400)])
        self.assertTrue(runner.fatal_configuration_error(nested))
        self.assertIn("status=400", runner.error_summary(nested))
        self.assertFalse(runner.fatal_configuration_error(ModelCallError("RateLimitError", 429)))
        self.assertFalse(runner.fatal_configuration_error(ModelCallError("InternalServerError", 500)))


class BatchResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_crash_retained_and_rerun_makes_no_new_calls(self):
        """Use a fake admin/host only; never contact a model provider."""
        class Backend:
            def __init__(self, **config):
                self.config = config

            def configuration(self):
                return self.config

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Admin:
            async def request(self, method, path, json=None):
                if path == "/admin/negotiations":
                    self.scenario = json["scenario"]
                    return Response({"negotiation_id": self.scenario["id"], "buyer_token": "private-buyer", "seller_token": "private-seller"})
                done = path.endswith("/finish_turn")
                return Response({"status": "no_deal" if done else "open", "price": None, "whose_turn": "buyer",
                                 "metrics": {"correct": int(self.scenario["reserve"] > self.scenario["budget"]),
                                             "violation": 0, "attempted_violations": 0, "refused_calls": 0,
                                             "turns": 0, "tool_calls": 0, "refusals_recovered": 0}})

        server_starts = []

        @contextlib.asynccontextmanager
        async def server_factory(*args):
            server_starts.append(True)
            yield "http://offline.invalid/mcp", Admin()

        calls = []

        async def turn_runner(**kwargs):
            calls.append(kwargs["negotiation_id"])
            self.assertNotIn("condition", kwargs)
            self.assertNotIn("scenario", kwargs)
            if len(calls) == 1:
                raise RuntimeError("scripted failure with private-buyer")
            return {"moved": False, "model_calls": 0, "tool_calls": 0, "reason": "offline test"}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            args = runner.build_parser().parse_args(["--output-dir", directory, "--runs", "1", "--conditions", "prompt_inject"])
            dependencies = {"backend_factory": Backend, "turn_runner": turn_runner,
                            "server_factory": server_factory, "require_committed": False}
            rows = await runner.run_batch(args, **dependencies)
            self.assertEqual(len(rows), 4)
            self.assertEqual(runner.load_scenarios(Path(directory) / "scenarios.json"),
                             runner.load_scenarios(args.scenarios))
            self.assertEqual(len(calls), 4)
            self.assertEqual(rows[0]["outcome"], "")
            self.assertIn("scripted failure", rows[0]["note"])
            self.assertNotIn("private-buyer", rows[0]["note"])
            before = (Path(directory) / "results.csv").read_bytes()
            await runner.run_batch(args, **dependencies)
            self.assertEqual(len(server_starts), 1)
            self.assertEqual(len(calls), 4)
            self.assertEqual(before, (Path(directory) / "results.csv").read_bytes())


if __name__ == "__main__":
    unittest.main()
