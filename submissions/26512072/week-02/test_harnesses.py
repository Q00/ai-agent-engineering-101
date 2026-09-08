"""Offline regression checks. Fake API replies never become experiment rows."""
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import OpenAI

import harness_react
import run_ab
import summarize_results
import tools_shared as shared
from harness_plan_execute import run_plan_execute
from harness_react import run_react

ROOT = Path(__file__).resolve().parent


def tool(name, arguments):
    return {"role": "assistant", "content": "Thought: inspect the evidence.",
            "tool_calls": [{"id": "test-call", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def text(content):
    return {"role": "assistant", "content": content}


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.previous_directory = Path.cwd()
        self.directory = tempfile.TemporaryDirectory(prefix=".test-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.assertTrue(Path(self.directory.name).resolve().is_relative_to(ROOT))
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous_directory)
        Path("app.log").write_bytes((ROOT / "app.log").read_bytes())
        Path("TASK.md").write_bytes((ROOT / "TASK.md").read_bytes())
        Path("logs").mkdir()
        self.events = []
        self.requests = []

    def api(self, replies):
        pending = iter(replies)

        def handle(request):
            self.assertEqual(request.url.host, "openrouter.ai")
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            body = json.loads(request.content)
            self.requests.append(body)
            self.assertEqual(body["model"], shared.MODEL)
            self.assertEqual(body["temperature"], shared.TEMPERATURE)
            self.assertEqual(body["max_tokens"], shared.MAX_TOKENS)
            item = next(pending)
            if isinstance(item, int):
                return httpx.Response(item, json={"error": {"message": "offline test failure"}})
            return httpx.Response(200, json={
                "id": "offline-test", "created": 0, "object": "chat.completion",
                "model": shared.MODEL,
                "choices": [{"index": 0, "message": item,
                             "finish_reason": "tool_calls" if item.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            })

        client = OpenAI(api_key="offline-test-credential", base_url=shared.BASE_URL, max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        self.addCleanup(client.close)
        mocked = patch.object(shared, "_client", client)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_react_tool_results_and_full_observations(self):
        self.api([tool("read_file", {"path": "app.log"}),
                  tool("count_pattern", {"path": "app.log", "pattern": r"14:\d+:\d+ ERROR"}),
                  text("Answer: 14:00")])
        answer, meter = run_react("Inspect app.log", log=self.events.append)
        self.assertEqual(answer, "Answer: 14:00")
        self.assertEqual((meter.tokens, meter.iters, meter.interventions), (54, 3, 0))
        observation = "[Observation]\n" + Path("app.log").read_text(encoding="utf-8")[:4000]
        self.assertIn(observation, self.events)
        self.assertEqual(self.requests[-1]["messages"][-1]["content"], "6")

    def test_plan_counts_planning_execution_and_summary(self):
        self.api([text('["Read the file", "Count errors"]'),
                  tool("read_file", {"path": "app.log"}), text("Read complete"),
                  tool("count_pattern", {"path": "app.log", "pattern": "ERROR"}),
                  text("Count complete"), text("Answer: 14:00")])
        answer, meter, replans = run_plan_execute("Inspect app.log", log=self.events.append)
        self.assertEqual((answer, meter.iters, meter.tokens, replans), ("Answer: 14:00", 6, 108, 0))
        self.assertNotIn("tools", self.requests[0])
        self.assertEqual(self.requests[1]["tools"][0]["function"]["name"], "read_file")

    def test_off_plan_stops_after_one_replan(self):
        self.api([text('["Read missing file"]'), text("OFF_PLAN: file is missing"),
                  text('["Try another file"]'), text("OFF_PLAN: also missing")])
        answer, meter, replans = run_plan_execute("Inspect app.log", log=self.events.append)
        self.assertIn("replan budget exhausted", answer)
        self.assertEqual((replans, meter.iters), (1, 4))

    def test_empty_plan_is_a_failure(self):
        self.api([text("[]")])
        answer, meter, replans = run_plan_execute("Inspect app.log", log=self.events.append)
        self.assertEqual((answer, meter.iters, replans), ("plan parse failed", 1, 0))

    def test_both_harnesses_obey_total_budget(self):
        for name, fn, replies in (
            ("react", run_react, [tool("read_file", {"path": "app.log"})]),
            ("plan_exec", run_plan_execute, [text('["Read file"]')]),
        ):
            with self.subTest(harness=name):
                self.api(replies)
                with contextlib.redirect_stdout(io.StringIO()):
                    row = run_ab.run_one(1, name, fn, "Inspect app.log", "14:00", {"max_steps": 1})
                self.assertEqual(row[2:6], ["X", 18, 1, 0])

    def test_api_failure_preserves_usage_and_raw_log(self):
        self.api([tool("read_file", {"path": "app.log"}), 429])
        with contextlib.redirect_stdout(io.StringIO()):
            row = run_ab.run_one(1, "react", run_react, "Inspect app.log", "14:00", {"max_steps": 16})
        self.assertEqual(row[2:6], ["X", 18, 2, 0])
        self.assertIn("RateLimitError", row[-1])
        capture = Path("logs/react-01.txt").read_text(encoding="utf-8")
        self.assertNotIn("offline-test-credential", capture)
        self.assertIn("[Observation]\n" + Path("app.log").read_text(encoding="utf-8")[:4000], capture)
        self.assertEqual(run_ab.next_run_number(), 2)
        with self.assertRaises(FileExistsError):
            run_ab.run_one(1, "react", run_react, "task", "14:00", {"max_steps": 16})

    def test_approvals_and_denials_are_both_interventions(self):
        for approval in (True, False):
            with self.subTest(approval=approval):
                self.api([tool("read_file", {"path": "app.log"}), text("Answer: complete")])
                with patch.object(harness_react, "IRREVERSIBLE", {"read_file"}), \
                        patch.object(harness_react, "ask_human", return_value=approval):
                    _, meter = run_react("Inspect file", log=self.events.append)
                self.assertEqual(meter.interventions, 1)

    def test_missing_key_is_preflight_only(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(run_ab, "git", return_value=""):
            self.assertTrue(any("not set" in problem for problem in run_ab.preflight()))
        self.assertFalse(Path("results.csv").exists())
        self.assertFalse(any(Path("logs").iterdir()))

    def test_tools_only_read_the_public_experiment_input(self):
        for path in ("TASK.md", "../app.log", str(ROOT / "tools_shared.py")):
            with self.subTest(path=path):
                self.assertTrue(shared.read_file(path).startswith("denied:"))
                self.assertTrue(shared.count_pattern(path, "ERROR").startswith("denied:"))
        self.assertEqual(shared.count_pattern("app.log", r"14:\d+:\d+ ERROR"), "6")

    def test_summary_includes_failures_and_rejects_mixed_conditions(self):
        config = {key: "offline-fixture" for key in summarize_results.CONTROLS}
        with Path("results.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(run_ab.HEADER)
            for run_no, tokens in enumerate((10, 20, 30), start=1):
                writer.writerow([run_no, "react", "X" if run_no == 1 else "O", tokens, 2, 0, "offline test"])
                Path(f"logs/react-{run_no:02d}.txt").write_text(
                    "[conditions] " + json.dumps(config) + "\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            summarize_results.summarize(Path.cwd())
        self.assertIn("2/3 (66.7%)", output.getvalue())
        self.assertIn("20.00 / 100.00", output.getvalue())
        config["model"] = "different-offline-fixture"
        # This is a temporary unit-test fixture, never a real experiment log.
        Path("logs/react-03.txt").write_text("[conditions] " + json.dumps(config) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conditions differ"):
            summarize_results.summarize(Path.cwd())


if __name__ == "__main__":
    unittest.main()
