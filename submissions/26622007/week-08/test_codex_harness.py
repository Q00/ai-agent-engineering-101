"""HARNESS=codex: `codex exec` with the schema, no tools, no keys, a slot limit over processes, and a failure that the caller
sees as HarnessError (its rule fallback), never a quiet switch to OpenRouter. The codex here is a fake script.

    python -m unittest test_codex_harness -v        (from submissions/26622007/week-08)
"""
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import agent_loop            # noqa: E402
import harness               # noqa: E402
import scorecard             # noqa: E402

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a"], "properties": {"a": {"type": "integer"}}}
FINAL = {"type": "object", "additionalProperties": False, "required": ["x"], "properties": {"x": {"type": "integer"}}}

# Each run pops the first entry of plan.json: {"answer": str or None, "rc": int, "events": [...], "sleep": s}.
FAKE = textwrap.dedent("""\
    #!{python}
    import json, os, sys, time
    d = {d!r}
    argv = sys.argv[1:]
    with open(os.path.join(d, "plan.json")) as f:
        plan = json.load(f)
    step = plan.pop(0)
    with open(os.path.join(d, "plan.json"), "w") as f:
        json.dump(plan, f)
    with open(argv[argv.index("--output-schema") + 1]) as f:
        schema = json.load(f)
    with open(os.path.join(d, "runs.jsonl"), "a") as f:
        f.write(json.dumps({{"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "stdin": sys.stdin.read(),
                            "schema": schema, "listing": os.listdir(".")}}) + "\\n")
    time.sleep(step.get("sleep", 0))
    for ev in step.get("events", []):
        print(json.dumps(ev))
    if step.get("answer") is not None:
        with open(argv[argv.index("-o") + 1], "w") as f:
            f.write(step["answer"])
        print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": step["answer"]}}}}))
        print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 17000, "cached_input_tokens": 11000, "output_tokens": 9}}}}))
    sys.exit(step.get("rc", 0))
    """)


class CodexHarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.md = os.path.join(self.d, "market")
        os.mkdir(self.md)
        self.codex = os.path.join(self.d, "codex")
        with open(self.codex, "w") as f:
            f.write(FAKE.format(python=sys.executable, d=self.d))
        os.chmod(self.codex, os.stat(self.codex).st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"HARNESS_CODEX": self.codex, "MARKET_DIR": self.md,
                                                "OPENROUTER_API_KEY": "sk-or-test-secret", "MARKET_OWNER": "claim-secret"})
        self.env.start()
        self.cwd_root = os.path.join(os.path.realpath(self.d), "cwd")
        self.patches = [mock.patch.object(harness, "BACKEND", "codex"), mock.patch.object(harness, "_codex_path", []),
                        mock.patch.object(harness, "CODEX_CWD_ROOT", self.cwd_root)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def plan(self, *steps):
        with open(os.path.join(self.d, "plan.json"), "w") as f:
            json.dump(list(steps), f)

    def runs(self):
        with open(os.path.join(self.d, "runs.jsonl")) as f:
            return [json.loads(line) for line in f]

    def lines(self, name):
        p = os.path.join(self.md, name)
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(line) for line in f]

    def test_one_call_passes_the_schema_and_turns_everything_else_off(self):
        self.plan({"answer": '{"a": 3}'})
        self.assertEqual(harness.ask("the offer is 80", SCHEMA, "You are a shop owner.", tag="seller-harness"), {"a": 3})
        run, = self.runs()
        argv = run["argv"]
        self.assertEqual(argv[:3], ["exec", "-m", "gpt-6-luna"])
        self.assertIn('model_reasoning_effort="low"', argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json"):
            self.assertIn(flag, argv)
        off = [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
        self.assertIn("shell_tool", off)
        self.assertIn("unified_exec", off)
        self.assertEqual(run["schema"], SCHEMA)
        self.assertIn("developer_instructions=" + json.dumps("You are a shop owner." + harness.CODEX_NO_TOOLS), argv)
        self.assertEqual(run["stdin"], "the offer is 80")
        self.assertEqual(argv[-1], "-")
        self.assertEqual(run["listing"], [])                                   # an empty folder: no AGENTS.md, no repo
        self.assertEqual(os.path.realpath(argv[argv.index("-C") + 1]), os.path.realpath(run["cwd"]))
        self.assertNotIn("OPENROUTER_API_KEY", run["env"])                     # no key reaches the model's process
        self.assertNotIn("MARKET_OWNER", run["env"])
        self.assertNotIn("MARKET_DIR", run["env"])
        rec, = self.lines("llm_calls.jsonl")
        self.assertEqual((rec["backend"], rec["tag"], rec["ok"], rec["prompt_tokens"], rec["completion_tokens"]),
                         ("codex", "seller-harness", True, 17000, 9))

    def test_a_system_prompt_with_quotes_and_lines_stays_one_toml_string(self):
        system = 'Say "no".\nLine two \\ and 한국어.'
        self.plan({"answer": '{"a": 1}'})
        harness.ask("p", SCHEMA, system)
        arg = next(a for a in self.runs()[0]["argv"] if a.startswith("developer_instructions="))
        import tomllib
        self.assertEqual(tomllib.loads(arg)["developer_instructions"], system + harness.CODEX_NO_TOOLS)

    def test_an_invalid_answer_is_asked_again_then_fails_to_the_caller(self):
        self.plan({"answer": '{"a": "x"}'}, {"answer": '{"a": 2}'})
        self.assertEqual(harness.ask("p", SCHEMA, "s"), {"a": 2})
        self.plan({"answer": "not json"}, {"answer": '{"b": 1}'})
        with self.assertRaises(harness.HarnessError):
            harness.ask("p", SCHEMA, "s", tag="buyer-agent")
        fails = self.lines(os.path.join("measure", "llm_codex_fail.jsonl"))
        self.assertEqual([f["kind"] for f in fails], ["invalid", "invalid", "invalid"])

    def test_a_usage_limit_is_not_retried_and_never_goes_to_openrouter(self):
        self.plan({"rc": 1, "events": [{"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}]},
                  {"answer": '{"a": 9}'})
        with mock.patch.object(harness, "_ask_openrouter", side_effect=AssertionError("no OpenRouter")), \
                self.assertRaises(harness.HarnessError) as e:
            harness.ask("p", SCHEMA, "s", tag="restock-agent")
        self.assertIn("usage limit", str(e.exception))
        self.assertEqual(len(self.runs()), 1)
        fail, = self.lines(os.path.join("measure", "llm_codex_fail.jsonl"))
        self.assertEqual((fail["kind"], fail["tag"]), ("refused", "restock-agent"))

    def test_a_plain_crash_is_tried_once_more(self):
        self.plan({"rc": 1}, {"answer": '{"a": 6}'})
        self.assertEqual(harness.ask("p", SCHEMA, "s"), {"a": 6})
        self.assertEqual(len(self.runs()), 2)

    def test_a_failed_turn_fails_even_with_exit_0_and_an_answer(self):
        self.plan({"events": [{"type": "turn.failed", "error": {"message": "stream closed"}}], "answer": '{"a": 1}'},
                  {"events": [{"type": "turn.failed", "error": {"message": "stream closed"}}], "answer": '{"a": 1}'})
        with self.assertRaises(harness.HarnessError):
            harness.ask("p", SCHEMA, "s")

    def test_a_tool_run_means_no_answer_is_used(self):
        self.plan({"events": [{"type": "item.completed", "item": {"type": "command_execution", "command": "env"}}],
                   "answer": '{"a": 1}'}, {"answer": '{"a": 1}'})
        with self.assertRaises(harness.HarnessError) as e:
            harness.ask("p", SCHEMA, "s")
        self.assertIn("command_execution", str(e.exception))
        self.assertEqual(len(self.runs()), 1)
        self.plan({"events": [{"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent"}}],
                   "answer": '{"a": 1}'})
        with self.assertRaises(harness.HarnessError) as e:     # a kind not named anywhere here counts too
            harness.ask("p", SCHEMA, "s")
        self.assertIn("collab_tool_call:spawn_agent", str(e.exception))

    def test_an_os_error_reaches_the_caller_as_harness_error(self):
        with mock.patch.object(harness.tempfile, "TemporaryDirectory", side_effect=OSError("disk full")), \
                self.assertRaises(harness.HarnessError):
            harness.ask("p", SCHEMA, "s")

    def test_final_errors_are_matched_on_words_not_digits_inside_ids(self):
        self.assertTrue(harness.CODEX_FINAL.search("usage_limit_reached"))
        self.assertTrue(harness.CODEX_FINAL.search("HTTP 429 Too Many Requests"))
        self.assertFalse(harness.CODEX_FINAL.search("stream error req_8429f1 after 4291ms"))

    def test_a_timeout_kills_the_child_codex_starts_too(self):
        pidfile = os.path.join(self.d, "grandchild.pid")
        with open(self.codex, "w") as f:      # node starting the native binary: here a shell starting a sleep
            f.write(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n")
        with mock.patch.object(harness, "CODEX_TIMEOUT", 1), self.assertRaises(harness.HarnessError):
            harness.ask("p", SCHEMA, "s")
        with open(pidfile) as f:
            pid = int(f.read())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_an_error_event_before_a_good_answer_is_no_failure(self):
        self.plan({"events": [{"type": "error", "message": "Reconnecting... 1/5"}], "answer": '{"a": 4}'})
        self.assertEqual(harness.ask("p", SCHEMA, "s"), {"a": 4})

    def test_a_caller_limit_replaces_codex_timeout(self):
        self.plan({"sleep": 5, "answer": '{"a": 1}'}, {"answer": '{"a": 1}'})
        started = time.time()
        with self.assertRaises(harness.HarnessError) as e:
            harness.ask("p", SCHEMA, "s", tag="buyer-choose", timeout=1)
        self.assertIn("timeout after 1s", str(e.exception))
        self.assertLess(time.time() - started, 4)

    def test_a_timeout_fails_once_without_a_second_wait(self):
        self.plan({"sleep": 5, "answer": '{"a": 1}'}, {"answer": '{"a": 1}'})
        with mock.patch.object(harness, "CODEX_TIMEOUT", 1), self.assertRaises(harness.HarnessError) as e:
            harness.ask("p", SCHEMA, "s")
        self.assertIn("timeout", str(e.exception))
        self.assertEqual(len(self.runs()), 1)

    def test_all_slots_busy_waits_then_fails_and_a_freed_slot_is_reused(self):
        with mock.patch.object(harness, "CODEX_CONCURRENCY", 1), mock.patch.object(harness, "CODEX_QUEUE_WAIT", 0.3):
            with harness._Slot("a"):
                started = time.time()
                with self.assertRaises(harness.HarnessError):
                    with harness._Slot("b"):
                        pass
                self.assertGreaterEqual(time.time() - started, 0.3)
            with harness._Slot("c") as s:
                self.assertIsNotNone(s.fd)
        self.assertEqual([f["kind"] for f in self.lines(os.path.join("measure", "llm_codex_fail.jsonl"))], ["busy"])

    def test_the_tool_loop_runs_through_codex_and_cites_its_calls(self):
        step = {"reason": "c0 shows it", "evidence": ["c0"], "action": "finish", "tool": "", "args_json": "", "final": {"x": 5}}
        self.plan({"answer": json.dumps(step)})
        with tempfile.TemporaryDirectory() as md:
            self.assertEqual(agent_loop.decide("g", FINAL, md, "restock", {"c0": "{}"}), {"x": 5})
        schema = self.runs()[0]["schema"]
        self.assertEqual(schema["properties"]["final"]["anyOf"][0], FINAL)
        self.assertEqual(schema["properties"]["evidence"]["items"]["enum"], ["c0"])     # only ids, not "c0: what it shows"

    def test_evidence_may_cite_each_call_once_it_happened(self):
        call = {"reason": "look", "evidence": [], "action": "call_tool", "tool": "t", "args_json": "{}", "final": None}
        done = {"reason": "c1", "evidence": ["c1"], "action": "finish", "tool": "", "args_json": "", "final": {"x": 1}}
        self.plan({"answer": json.dumps(call)}, {"answer": json.dumps(done)})
        with tempfile.TemporaryDirectory() as md, mock.patch.object(agent_loop.agent_tools, "discover", lambda md=None: {"t": "a tool"}), \
                mock.patch.object(agent_loop.agent_tools, "run", lambda *a: '{"ok": 1}'):
            self.assertEqual(agent_loop.decide("g", FINAL, md, "buyer", {"c0": "{}"}), {"x": 1})
        self.assertEqual([r["schema"]["properties"]["evidence"]["items"]["enum"] for r in self.runs()], [["c0"], ["c0", "c1"]])

    def test_the_scorecard_counts_calls_by_backend(self):
        self.plan({"answer": '{"a": 1}'})
        harness.ask("p", SCHEMA, "s")
        with open(os.path.join(self.md, "llm_calls.jsonl"), "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "tag": "t", "ok": True}) + "\n")   # an older line, OpenRouter
        self.assertEqual(scorecard.llm_last_hour(self.md)["backends"], {"codex": 1, "openrouter": 1})

    def test_cached_tokens_are_recorded_with_every_other_field(self):
        self.plan({"answer": '{"a": 1}'})
        harness.ask("p", SCHEMA, "s", tag="seller-harness")
        rec, = self.lines("llm_calls.jsonl")
        self.assertEqual(set(rec), {"ts", "tag", "backend", "chars", "seconds", "ok", "prompt_tokens", "completion_tokens",
                                    "cached_tokens", "cost"})
        self.assertEqual((rec["prompt_tokens"], rec["cached_tokens"], rec["completion_tokens"]), (17000, 11000, 9))
        harness.record_call("or", "p", "s", 1.0, True, {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 64}})
        self.assertEqual(self.lines("llm_calls.jsonl")[-1]["cached_tokens"], 64)    # OpenRouter's place for it
        self.assertEqual(scorecard.llm_last_hour(self.md)["cache"],
                         {"calls": 2, "prompt_tokens": 17100, "cached_tokens": 11064, "ratio": 0.647})

    def test_each_tag_keeps_one_empty_cwd_and_a_file_in_it_falls_back_to_scratch(self):
        self.plan({"answer": '{"a": 1}'}, {"answer": '{"a": 2}'}, {"answer": '{"a": 3}'}, {"answer": '{"a": 4}'})
        harness.ask("p", SCHEMA, "s", tag="seller-harness")
        harness.ask("q", SCHEMA, "s", tag="seller-harness")
        harness.ask("r", SCHEMA, "s", tag="buyer/agent")
        a, b, c = [os.path.realpath(r["cwd"]) for r in self.runs()]
        fixed = os.path.join(self.cwd_root, "t_seller-harness")
        self.assertEqual((a, b), (fixed, fixed))
        self.assertEqual(c, os.path.join(self.cwd_root, "t_buyer_agent"))
        self.assertEqual(os.listdir(fixed), [])                                 # nothing is left there
        open(os.path.join(fixed, "AGENTS.md"), "w").close()
        harness.ask("s", SCHEMA, "s", tag="seller-harness")
        last = self.runs()[-1]
        self.assertNotEqual(os.path.realpath(last["cwd"]), fixed)
        self.assertEqual(last["listing"], [])
        self.assertEqual(self.lines("measure/llm_codex_cwd.jsonl")[-1]["kind"], "scratch")

    def test_a_cwd_under_a_repository_is_never_used(self):
        """codex puts every AGENTS.md from the cwd up to the repo root into the prompt."""
        self.plan({"answer": '{"a": 1}'}, {"answer": '{"a": 2}'})
        open(os.path.join(self.d, "AGENTS.md"), "w").close()                  # above cwd_root
        harness.ask("p", SCHEMA, "s", tag="seller-harness")
        self.assertFalse(os.path.realpath(self.runs()[-1]["cwd"]).startswith(self.cwd_root))
        os.remove(os.path.join(self.d, "AGENTS.md"))
        os.mkdir(os.path.join(self.cwd_root, ".git"))
        harness.ask("p", SCHEMA, "s", tag="seller-harness")
        self.assertFalse(os.path.realpath(self.runs()[-1]["cwd"]).startswith(self.cwd_root))
        self.assertTrue(harness._under_project(os.path.join(HERE, ".market")))   # the kit's own MARKET_DIR
        os.rmdir(os.path.join(self.cwd_root, ".git"))
        self.assertEqual(os.path.basename(harness.codex_workdir("..", os.path.join(self.d, "s"))), "t___")


if __name__ == "__main__":
    unittest.main()
