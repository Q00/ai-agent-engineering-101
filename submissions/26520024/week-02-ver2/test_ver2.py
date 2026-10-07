"""Offline regression tests. Synthetic replies never enter benchmark logs."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import codex_backend as backend
import harness_plan_execute as plan
import harness_react as react
from harness_state import ObservationState
import run_ab
import tools_shared as shared

READ = ("", [{"name": "read_file", "args": {"path": "app.log"}}])
FINAL = ("Answer: 14:00", [])
PLAN = ('["Read the input", "Analyze and answer"]', [])


@contextlib.contextmanager
def fake_model(replies):
    pending = iter(replies)
    requests = []

    def complete(system, history, specs, enabled, model, on_usage):
        requests.append(copy.deepcopy((system, history, enabled)))
        on_usage(10, 2)
        return copy.deepcopy(next(pending))
    with patch.object(backend, "complete", side_effect=complete):
        yield requests


@contextlib.contextmanager
def temporary_cwd():
    old = Path.cwd()
    with tempfile.TemporaryDirectory() as folder:
        os.chdir(folder)
        try:
            yield Path(folder)
        finally:
            os.chdir(old)


class Ver2Tests(unittest.TestCase):
    def setUp(self):
        p = patch.object(shared, "PROVIDER", "codex")
        p.start()
        self.addCleanup(p.stop)

    def test_react_finishes_after_evidence(self):
        with fake_model([READ, FINAL]) as requests:
            answer, meter = react.run_react("test task", log=lambda x: None)
        self.assertEqual(answer, FINAL[0])
        self.assertEqual((meter.iters, meter.tokens, meter.interventions), (2, 24, 0))
        self.assertEqual(len(requests[1][1]), 1)
        payload = json.loads(requests[1][1][0]["content"])
        self.assertEqual(payload["task"], "test task")
        self.assertTrue(payload["observations"][0]["ok"])

    def test_react_rejects_unsupported_answer(self):
        with fake_model([FINAL, READ, FINAL]):
            _, meter = react.run_react("task", log=lambda x: None)
        self.assertEqual(meter.iters, 3)

    def test_react_error_then_recovery(self):
        bad = ("", [{"name": "read_file", "args": {"path": "TASK.md"}}])
        with fake_model([bad, READ, FINAL]) as requests:
            answer, meter = react.run_react("task", log=lambda x: None)
        self.assertEqual(answer, FINAL[0])
        self.assertEqual(meter.iters, 3)
        observations = json.loads(requests[1][1][0]["content"])["observations"]
        self.assertFalse(observations[0]["ok"])

    def test_react_global_cap(self):
        with fake_model([("not complete", [])] * 8):
            answer, meter = react.run_react("task", log=lambda x: None)
        self.assertIn("INCOMPLETE", answer)
        self.assertEqual(meter.iters, 8)

    def test_react_counts_approval_and_denial(self):
        for approved in (True, False):
            with fake_model([READ, FINAL]), patch.object(react, "IRREVERSIBLE", {"read_file"}), \
                    patch.object(react, "ask_human", return_value=approved):
                answer, meter = react.run_react("task", max_steps=2, log=lambda x: None)
            self.assertEqual(meter.interventions, 1)
            self.assertEqual(answer == FINAL[0], approved)

    def test_memory_is_bounded_without_summarizing_outputs(self):
        state = ObservationState(4)
        for i in range(6):
            state.observe([{"name": "read_file", "args": {}, "output": str(i), "ok": True}])
        self.assertEqual([r["output"] for r in state.records()], ["2", "3", "4", "5"])
        payload = json.loads(state.prompt("task"))
        self.assertEqual(set(payload), {"task", "observations"})

    def test_plan_early_final_avoids_extra_synthesis(self):
        with fake_model([PLAN, READ, FINAL]) as requests:
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertEqual((answer, meter.iters, replans), (FINAL[0], 3, 0))
        self.assertFalse(requests[0][2])
        self.assertTrue(requests[1][2])
        self.assertEqual(json.loads(requests[2][1][0]["content"])["current_step"], 1)

    def test_plan_invalid_initial_plan_has_one_repair(self):
        with fake_model([("invalid", []), PLAN, READ, FINAL]):
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertEqual((answer, meter.iters, replans), (FINAL[0], 4, 1))

    def test_plan_repair_failure_is_not_success(self):
        with fake_model([("invalid", []), ("still invalid", [])]):
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertTrue(answer.startswith("FAILED"))
        self.assertEqual((meter.iters, replans), (2, 1))

    def test_off_plan_replanning(self):
        with fake_model([PLAN, ("OFF_PLAN: need a different step", []), PLAN, READ, FINAL]):
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertEqual((answer, meter.iters, replans), (FINAL[0], 5, 1))

    def test_invalid_replan_fails(self):
        with fake_model([PLAN, ("OFF_PLAN: blocked", []), ("invalid", [])]):
            answer, meter, replans = plan.run_plan_execute("task", log=lambda x: None)
        self.assertTrue(answer.startswith("FAILED"))
        self.assertEqual((meter.iters, replans), (3, 1))

    def test_plan_strict_tool_round_cap(self):
        observed = []
        with fake_model([PLAN, READ, READ]):
            answer, meter, _ = plan.run_plan_execute("task", max_replan=0,
                max_tool_rounds=1, log=observed.append)
        self.assertTrue(answer.startswith("FAILED"))
        self.assertEqual(sum(s.startswith("  [tool]") for s in observed), 1)
        self.assertEqual(meter.iters, 3)

    def test_plan_global_cap_includes_planner(self):
        with fake_model([PLAN, READ]):
            answer, meter, _ = plan.run_plan_execute("task", max_steps=2, log=lambda x: None)
        self.assertIn("INCOMPLETE", answer)
        self.assertEqual(meter.iters, 2)

    def test_plan_requires_evidence(self):
        with fake_model([PLAN, FINAL, READ, FINAL]):
            answer, meter, _ = plan.run_plan_execute("task", log=lambda x: None)
        self.assertEqual((answer, meter.iters), (FINAL[0], 4))

    def test_plan_shape_validation(self):
        for text in ('[]', '[1]', '[""]', '["a","b","c","d"]', '{}', 'prose'):
            self.assertIsNone(plan.parse_plan(text))
        self.assertEqual(plan.parse_plan('```json\n["read"]\n```'), ["read"])

    def test_empty_and_pending_final_rejected(self):
        state = ObservationState()
        state.observe([{"ok": True}])
        self.assertFalse(state.final_answer(shared.Reply("Answer: ")))
        self.assertFalse(state.final_answer(shared.Reply("Answer: 14:00", [object()])))

    def test_inputs_and_schemas_unchanged(self):
        import ast
        root = Path(__file__).resolve().parent
        old = root.parent / "week-02"
        for name in ("TASK.md", "app.log", "codex_backend.py"):
            self.assertEqual((root / name).read_bytes(), (old / name).read_bytes())
        old_tree = ast.parse((old / "tools_shared.py").read_text())
        new_tree = ast.parse((root / "tools_shared.py").read_text())
        for name in ("read_file", "count_pattern", "input_path"):
            a = next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            b = next(n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertEqual(ast.dump(a), ast.dump(b))
        schema = next(n.value for n in old_tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "TOOL_SPECS" for t in n.targets))
        self.assertEqual(ast.literal_eval(schema), shared.TOOL_SPECS)

    def test_input_isolation_and_success_flag(self):
        for name in ("TASK.md", "../week-02/app.log", "/etc/passwd"):
            with self.assertRaises(ValueError):
                shared.read_file(name)
        chat = shared.Chat("system", shared.Meter())
        reply = shared.Reply("", [shared.ToolCall("a", "count_pattern", {"path": "app.log", "pattern": "["})])
        records = chat.run_tools(reply, log=lambda x: None)
        self.assertFalse(records[0]["ok"])

    def test_usage_counts_cache_once(self):
        meter = shared.Meter()
        event = {"type": "turn.completed", "usage": {"input_tokens": 100,
                 "cached_input_tokens": 80, "output_tokens": 7}}
        backend.record_events(json.dumps(event), meter.record_usage)
        self.assertEqual(meter.tokens, 107)

    def test_internal_actions_rejected(self):
        event = {"type": "item.completed", "item": {"type": "command_execution"}}
        with self.assertRaises(RuntimeError):
            backend.record_events(json.dumps(event), lambda a, b: None)

    def test_failed_model_call_keeps_attempt_count(self):
        meter = shared.Meter()
        with patch.object(backend, "complete", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                shared.Chat("system", meter).send()
        self.assertEqual(meter.iters, 1)
        self.assertFalse(meter.tokens_complete)

    def test_runner_keeps_crash_and_does_not_overwrite(self):
        def crash(task, log, meter):
            meter.iters = 1
            meter.tokens_complete = False
            raise RuntimeError("offline test failure")
        with temporary_cwd() as root, contextlib.redirect_stdout(io.StringIO()):
            (root / "logs").mkdir()
            row = run_ab.run_one(1, "react", crash, "task", "14:00", {})
            self.assertEqual(row[2:6], ["X", "", 1, 0])
            self.assertEqual(run_ab.next_run_number(), 2)
            with self.assertRaises(FileExistsError):
                run_ab.run_one(1, "react", crash, "task", "14:00", {})


if __name__ == "__main__":
    unittest.main()
