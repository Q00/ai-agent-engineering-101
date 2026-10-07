"""Runner checks with actual MCP HTTP and scripted (ungraded) model responses."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from run_experiment import BASE, HEADER, completed_rows, load_scenarios, run_batch, run_episode
from test_negotiation_host import MarketFixture, ScriptedModel


class RunnerHTTPTests(MarketFixture, unittest.IsolatedAsyncioTestCase):
    async def test_eight_successful_moves_leave_episode_open(self):
        model = ScriptedModel([("propose", {"price": 130})] * 8)
        scenario = {"id": 1, "item": "bicycle", "reserve": 120, "budget": 150}
        result = await run_episode(scenario, "http://127.0.0.1:8001/mcp", self.admin,
                                   model, self.logs.append, self.transport)
        self.assertEqual((result["outcome"], result["turns"], result["tool_calls"]), ("open", 8, 16))
        self.assertEqual(result["correct"], 0)
        self.assertIsNone(result["price"])

    async def test_csv_log_and_resume_do_not_repeat_completed_episode(self):
        scenario = {"id": 1, "item": "bicycle", "reserve": 120, "budget": 150}
        model = ScriptedModel([("propose", {"price": 130}), ("accept_proposal", {})])
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            root = Path(directory)
            results, logs = root / "results.csv", root / "logs"
            failed = await run_batch([scenario], "prompt", 1, "http://127.0.0.1:8001/mcp",
                                     self.admin, results, logs, lambda _: model, self.transport)
            self.assertEqual(failed, 0)
            with results.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, HEADER)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row["outcome"], row["price"], row["turns"], row["tool_calls"]), ("deal", "130", "2", "4"))
            self.assertEqual((row["correct"], row["violation"]), ("1", "0"))
            self.assertEqual(completed_rows(results), {("1", "prompt", "1")})
            original_csv, original_log = results.read_bytes(), (logs / "prompt-01.txt").read_bytes()
            second = await run_batch([scenario], "prompt", 1, "http://127.0.0.1:8001/mcp",
                                     self.admin, results, logs, lambda _: self.fail("Model instantiated on resume"), self.transport)
            self.assertEqual(second, 0)
            self.assertEqual(results.read_bytes(), original_csv)
            self.assertEqual((logs / "prompt-01.txt").read_bytes(), original_log)
            for token in (self.admin, *self.app.state.market.parties):
                self.assertNotIn(token, original_csv.decode())
                self.assertNotIn(token, original_log.decode())

    async def test_crashed_episode_is_saved_and_secrets_are_redacted(self):
        admin = self.admin

        class CrashedModel:
            async def complete(self, *args):
                raise RuntimeError(f"simulated failure containing {admin}")

        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            root = Path(directory)
            scenario = {"id": 1, "item": "bicycle", "reserve": 120, "budget": 150}
            failed = await run_batch([scenario], "prompt", 2, "http://127.0.0.1:8001/mcp",
                                     admin, root / "results.csv", root / "logs",
                                     lambda _: CrashedModel(), self.transport)
            self.assertEqual(failed, 1)
            csv_text = (root / "results.csv").read_text(encoding="utf-8")
            row = list(csv.DictReader(csv_text.splitlines()))[0]
            self.assertEqual((row["outcome"], row["price"], row["correct"]), ("", "", ""))
            self.assertIn("crash: RuntimeError", row["note"])
            self.assertIn("[REDACTED]", row["note"])
            self.assertNotIn(admin, csv_text)
            self.assertNotIn(admin, (root / "logs" / "prompt-02.txt").read_text(encoding="utf-8"))
            self.assertIn(("2", "prompt", "1"), completed_rows(root / "results.csv"))

    async def test_unimplemented_conditions_fail_before_creating_files(self):
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "prompt only"):
                await run_batch([], "server_inject", 1, "http://127.0.0.1:8001/mcp", self.admin,
                                root / "results.csv", root / "logs")
            self.assertEqual(list(root.iterdir()), [])


class ScenarioTests(unittest.TestCase):
    def test_committed_scenarios_match_week04_and_have_both_cases(self):
        rows = load_scenarios(BASE / "scenarios.json")
        self.assertEqual(rows, json.loads((BASE.parent / "week-04" / "scenarios.json").read_text(encoding="utf-8")))
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
