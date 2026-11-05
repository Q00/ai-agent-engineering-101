"""agy first, then codex, then rules: failures route correctly, and the permission surface stays closed."""

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from harness import HarnessError
import model_router as mr

ANSWER = {"action": "offer", "price": 90, "reason": "fine"}
QUOTA = {"weekly_remaining_percent": 90, "five_hour_remaining_percent": 90, "weekly_resets_at": "2026-11-11T12:00:00Z", "five_hour_resets_at": "2026-11-05T00:00:00Z"}


def stream(result=None, tools=()):
    lines = [json.dumps({"event": "init", "init": {"model": "m", "tools": ["search_web"]}})]
    for name, state in tools:
        lines.append(json.dumps({"event": "step_update", "step_update": {"step_type": "tool", "tool_name": name, "state": state, "input": "SENSITIVE"}}))
    if result is not None:
        lines.append(json.dumps({"event": "result", "result": result}))
    return "\n".join(lines).encode()


def success(answer=ANSWER, tools=(), **extra):
    return stream({"status": "SUCCESS", "response": json.dumps(answer), "structured_output": answer, "usage": {"total_tokens": 9000}, **extra}, tools)


class Base(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        printing = patch("builtins.print")
        printing.start()
        self.addCleanup(printing.stop)
        self.calls = []
        self.outcomes = []
        codex = patch.object(mr.harness, "run_harness", return_value={"action": "offer", "price": 70, "reason": "codex"})
        self.codex = codex.start()
        self.addCleanup(codex.stop)
        breaker = patch.object(mr.model_breaker, "check", return_value=(True, "ok"))
        self.breaker = breaker.start()
        self.addCleanup(breaker.stop)

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else SimpleNamespace(returncode=0, stdout=success(), stderr=b"")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def ask(self, quota=QUOTA):
        return mr.run_model("codex", "You are a buyer. 프롬프트", 60, "buyer", self.root, self.runner, lambda _t: dict(quota) if quota else None)


class Surface(unittest.TestCase):
    def test_command_has_the_tested_flags_and_nothing_that_grants_more(self):
        command = mr.build_agy_command("agy", "p", "gemini-3.8-flash-low", 70)
        for flag in ("--sandbox", "--disable-slash-commands", "--json-schema"):
            self.assertIn(flag, command)
        for flag in ("--dangerously-skip-permissions", "--add-dir", "--remote-agent", "--mode", "--project", "--continue"):
            self.assertNotIn(flag, command)
        self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
        self.assertEqual(json.loads(command[command.index("--settings") + 1]),
                         {"permissions": {"deny": ["read_file(*)", "write_file(*)", "command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]}})
        self.assertEqual(command[command.index("--model") + 1], "gemini-3.8-flash-low")
        self.assertEqual(command[command.index("--print") + 1], "p")

    def test_only_gemini_names_are_accepted(self):
        with TemporaryDirectory() as root:
            for name in ("claude-opus-5-5-low", "gpt-oss-120b-medium", "", 5):
                (Path(root) / "model_router.json").write_text(json.dumps({"agy_model": name}), encoding="utf-8")
                self.assertEqual(mr.load_config(root)["agy_model"], mr.DEFAULTS["agy_model"])

    def test_bad_config_keeps_defaults(self):
        with TemporaryDirectory() as root:
            for content in ("{broken", "[]", json.dumps({"min_remaining_percent": "x", "agy_enabled": "no"})):
                (Path(root) / "model_router.json").write_text(content, encoding="utf-8")
                settings = mr.load_config(root)
                self.assertEqual((settings["agy_enabled"], settings["min_remaining_percent"]), (True, 15))

    def test_quota_parse_keeps_only_the_gemini_group(self):
        output = {"command": {"data": {"groups": [
            {"name": "Claude and GPT models", "buckets": [{"window": "weekly", "remaining_fraction": .01, "reset_time": "x"}]},
            {"name": "Gemini Models", "buckets": [{"window": "weekly", "remaining_fraction": .9945, "reset_time": "a"}, {"window": "5h", "remaining_fraction": .5, "reset_time": "b"}]}]}}}
        self.assertEqual(mr.parse_quota(output), {"weekly_remaining_percent": 99.45, "weekly_resets_at": "a", "five_hour_remaining_percent": 50.0, "five_hour_resets_at": "b"})
        for broken in (None, {}, {"command": {"data": {"groups": []}}}, {"command": {"data": {"groups": [{"name": "Gemini Models", "buckets": [{"window": "weekly", "remaining_fraction": 1}]}]}}}):
            self.assertIsNone(mr.parse_quota(broken))

    def test_answers_are_data_only(self):
        self.assertEqual(mr.extract_answer({"structured_output": ANSWER}), ANSWER)
        self.assertEqual(mr.extract_answer({"response": "```json\n" + json.dumps(ANSWER) + "\n```"}), ANSWER)
        for bad in ({"response": ""}, {"response": "I will run a command"}, {"structured_output": {"action": "offer"}}, {"response": json.dumps(dict(ANSWER, extra=1))}):
            with self.assertRaises(ValueError):
                mr.extract_answer(bad)


class Routing(Base):
    def test_agy_answers_first_and_codex_is_untouched(self):
        self.assertEqual(self.ask(), ANSWER)
        self.codex.assert_not_called()
        self.assertEqual(mr.last_call(), {"provider": "agy", "model": mr.DEFAULTS["agy_model"], "route_reason": "agy_first"})

    def test_call_isolation(self):
        self.ask()
        command, kwargs = self.calls[0]
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["TEMP"], str(kwargs["cwd"]))
        self.assertEqual(kwargs["env"]["TMP"], str(kwargs["cwd"]))
        self.assertTrue(Path(kwargs["cwd"]).is_dir())
        self.assertEqual(list(Path(kwargs["cwd"]).iterdir()), [])

    def test_logs_name_the_real_model_and_keep_raw_output(self):
        self.ask()
        folder = next((self.root / "logs/harness").iterdir())
        self.assertTrue(folder.name.endswith("-agy"))
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["provider"], metadata["model"], metadata["status"], metadata["tokens"]), ("agy", mr.DEFAULTS["agy_model"], "ok", 9000))
        self.assertEqual(metadata["command"][metadata["command"].index("--model") + 1], mr.DEFAULTS["agy_model"])
        self.assertNotIn("프롬프트", json.dumps(metadata["command"], ensure_ascii=False))
        self.assertIn("프롬프트", (folder / "request.txt").read_text(encoding="utf-8"))
        self.assertIn("SUCCESS", (folder / "stdout.txt").read_text(encoding="utf-8"))

    def test_limit_failure_goes_to_codex_and_stops_repeating(self):
        self.outcomes = [SimpleNamespace(returncode=3, stdout=b"", stderr=b"AGY_ERROR: RESOURCE_EXHAUSTED quota")]
        self.assertEqual(self.ask()["reason"], "codex")
        self.assertEqual(mr.last_call()["provider"], "codex")
        self.assertEqual(mr.last_call()["route_reason"], "agy_limit_failure")
        self.ask()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.codex.call_count, 2)
        self.assertEqual(mr.read_state(self.root)["agy_off_reason"], "agy_limit_failure")

    def test_exhausted_window_holds_it_off_until_reset(self):
        self.outcomes = [SimpleNamespace(returncode=3, stdout=b"", stderr=b"quota")]
        spent = dict(QUOTA, five_hour_remaining_percent=0, five_hour_resets_at="2036-01-01T00:00:00Z")
        reads = iter([dict(QUOTA), spent])
        mr.run_model("codex", "prompt", 60, "buyer", self.root, self.runner, lambda _t: next(reads))
        self.assertGreater(mr.read_state(self.root)["agy_off_until"], mr._epoch("2035-12-31T00:00:00Z"))

    def test_agy_comes_back_after_the_off_period(self):
        self.outcomes = [SimpleNamespace(returncode=3, stdout=b"", stderr=b"quota")]
        self.ask()
        state = mr.read_state(self.root)
        state["agy_off_until"] = 1
        state["quota"] = dict(QUOTA, read_at=__import__("time").time())
        (self.root / ".market/model-router.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.ask(), ANSWER)
        self.assertEqual(mr.last_call()["provider"], "agy")

    def test_unknown_failure_shapes_go_to_codex_with_raw_output_saved(self):
        for outcome in (SimpleNamespace(returncode=1, stdout=b"not json at all", stderr=b"weird new failure"),
                        SimpleNamespace(returncode=0, stdout=stream({"status": "ERROR"}), stderr=b""),
                        SimpleNamespace(returncode=0, stdout=stream(None), stderr=b""),
                        SimpleNamespace(returncode=0, stdout=success(finished_reason="error"), stderr=b""),
                        FileNotFoundError("no agy")):
            with self.subTest(outcome=str(outcome)[:40]):
                self.setUp()
                self.outcomes = [outcome]
                self.assertEqual(self.ask()["reason"], "codex")
                self.assertEqual(mr.read_state(self.root)["agy_off_reason"][:4], "agy_")
                self.assertLess(mr.read_state(self.root)["agy_off_until"] - __import__("time").time(), 1000)
        folder = next((self.root / "logs/harness").iterdir())
        self.assertEqual(json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["status"], "failed")

    def test_failure_classes(self):
        self.assertEqual(mr.classify_failure("429 RESOURCE_EXHAUSTED"), "limit")
        self.assertEqual(mr.classify_failure("You are not logged in"), "service")
        self.assertEqual(mr.classify_failure("HTTP 503 unavailable"), "service")
        self.assertEqual(mr.classify_failure("strange"), "unknown")

    def test_timeout_stays_transient_and_spares_codex(self):
        self.outcomes = [subprocess.TimeoutExpired("agy", 60)]
        with self.assertRaises(HarnessError) as caught:
            self.ask()
        self.assertNotIsInstance(caught.exception.__cause__, ValueError)
        self.codex.assert_not_called()

    def test_print_timeout_result_is_transient(self):
        self.outcomes = [SimpleNamespace(returncode=0, stdout=stream({"status": "SUCCESS", "finished_reason": "print_timeout", "response": ""}), stderr=b"")]
        with self.assertRaises(HarnessError):
            self.ask()
        self.codex.assert_not_called()

    def test_bad_json_is_a_validation_error_so_the_caller_can_retry(self):
        self.outcomes = [SimpleNamespace(returncode=0, stdout=stream({"status": "SUCCESS", "response": ""}), stderr=b"denied")]
        with self.assertRaises(HarnessError) as caught:
            self.ask()
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.codex.assert_not_called()

    def test_repeated_passing_failures_move_on_then_a_success_resets(self):
        bad = SimpleNamespace(returncode=0, stdout=stream({"status": "SUCCESS", "response": ""}), stderr=b"")
        self.outcomes = [bad, SimpleNamespace(returncode=0, stdout=success(), stderr=b""), bad, bad]
        with self.assertRaises(HarnessError):
            self.ask()
        self.ask()
        self.assertEqual(mr.read_state(self.root)["agy_transient_failures"], 0)
        with self.assertRaises(HarnessError):
            self.ask()
        with self.assertRaises(HarnessError):
            self.ask()
        self.outcomes = [bad]
        self.assertEqual(self.ask()["reason"], "codex")
        self.assertEqual(mr.last_call()["route_reason"], "agy_repeat_failure")
        self.assertEqual(mr.read_state(self.root)["agy_off_reason"], "agy_repeat_failure")

    def test_low_or_unreadable_quota_skips_agy_without_a_call(self):
        for quota, reason in ((dict(QUOTA, five_hour_remaining_percent=14.9), "agy_five_hour_low"), (dict(QUOTA, weekly_remaining_percent=14.9), "agy_weekly_low"), (None, "agy_quota_unreadable")):
            with self.subTest(reason=reason):
                self.setUp()
                self.assertEqual(self.ask(quota)["reason"], "codex")
                self.assertEqual(self.calls, [])
                self.assertEqual(mr.last_call()["route_reason"], reason)

    def test_quota_is_read_at_most_once_per_interval_even_when_failing(self):
        reads = []
        reader = lambda _t: reads.append(1)
        for offset in (0, 10, 299, 300):
            mr.agy_status(self.root, "buyer", 1000 + offset, reader)
        self.assertEqual(len(reads), 2)

    def test_old_quota_turns_agy_off(self):
        self.assertTrue(mr.agy_status(self.root, "buyer", 1000, lambda _t: dict(QUOTA))[0])
        self.assertEqual(mr.agy_status(self.root, "buyer", 1000 + 1801, lambda _t: None), (False, "agy_quota_stale"))

    def test_both_off_raises_for_the_caller_to_use_rules(self):
        self.breaker.return_value = (False, "weekly_usage_high")
        self.outcomes = [SimpleNamespace(returncode=3, stdout=b"", stderr=b"quota")]
        with self.assertRaises(HarnessError) as caught:
            self.ask()
        self.assertIn("weekly_usage_high", str(caught.exception))
        self.codex.assert_not_called()
        self.assertIsNone(mr.last_call())

    def test_prompt_tells_the_model_not_to_use_tools(self):
        self.ask()
        command = self.calls[0][0]
        self.assertTrue(command[command.index("--print") + 1].endswith(mr.NO_TOOLS_NOTE))
        folder = next((self.root / "logs/harness").iterdir())
        self.assertTrue((folder / "request.txt").read_text(encoding="utf-8").endswith(mr.NO_TOOLS_NOTE))

    def test_search_web_is_recorded_and_its_answer_discarded(self):
        self.outcomes = [SimpleNamespace(returncode=0, stdout=success(tools=[("search_web", "DONE")]), stderr=b"")]
        self.assertEqual(self.ask(), mr.DISCARDED)
        self.codex.assert_not_called()
        self.assertEqual(mr.last_call(), {"provider": "agy", "model": mr.DEFAULTS["agy_model"], "route_reason": "agy_tool_use_discarded", "tools_used": ["search_web"]})
        self.assertTrue(mr.load_config(self.root)["agy_enabled"])
        folder = next((self.root / "logs/harness").iterdir())
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["tools_used"], metadata["answer_discarded"]), (["search_web"], True))
        self.assertFalse((self.root / "logs/model-router-alerts.log").exists())

    def test_refused_file_calls_discard_without_stopping(self):
        self.outcomes = [SimpleNamespace(returncode=0, stdout=success(tools=[("read_file", "ACTIVE"), ("read_file", "ERROR")]), stderr=b"")]
        self.assertEqual(self.ask(), mr.DISCARDED)
        self.assertTrue(mr.load_config(self.root)["agy_enabled"])

    def test_unexpected_tools_turn_agy_off_and_leave_a_notice(self):
        for name, state in (("schedule", "DONE"), ("send_message", "ERROR"), ("define_subagent", "DONE"), ("invoke_subagent", "ACTIVE"), ("manage_subagents", "DONE"),
                            ("manage_task", "DONE"), ("ask_question", "DONE"), ("brand_new_tool", "DONE"), ("read_file", "DONE"), ("run_command", "DONE")):
            with self.subTest(name=name, state=state):
                self.setUp()
                (self.root / "model_router.json").write_text(json.dumps({"agy_enabled": True, "agy_model": "gemini-4-argon-low"}), encoding="utf-8")
                self.outcomes = [SimpleNamespace(returncode=0, stdout=success(tools=[(name, state)]), stderr=b"")]
                self.assertEqual(self.ask(), mr.DISCARDED)
                self.assertFalse(mr.load_config(self.root)["agy_enabled"])
                self.assertEqual(mr.load_config(self.root)["agy_model"], "gemini-4-argon-low")
                self.assertEqual(mr.last_call()["route_reason"], "agy_unexpected_tool")
                alert = json.loads((self.root / "logs/model-router-alerts.log").read_text(encoding="utf-8"))
                self.assertEqual(alert["tools"], [name])
                self.assertEqual(self.ask()["reason"], "codex")
                self.assertEqual(len(self.calls), 1)

    def finish_run(self, answer=ANSWER, final=ANSWER, index=4, closing=True, extra=(), status="SUCCESS"):
        """The real shape of run 20261104T172538777345Z: finish reported as a tool, then the finish step at the same index."""
        lines = [{"event": "step_update", "step_update": {"step_index": 3, "state": "DONE", "step_type": "agent_response"}},
                 {"event": "step_update", "step_update": {"step_index": index, "state": "ACTIVE", "step_type": "tool", "tool_name": "finish",
                                                          "tool_info": {"name": "finish", "parameters": answer}}}]
        if closing:
            lines.append({"event": "step_update", "step_update": {"step_index": 4, "state": "DONE", "step_type": "finish"}})
        lines += [{"event": "step_update", "step_update": step} for step in extra]
        lines.append({"event": "result", "result": {"status": status, "response": json.dumps(final), "structured_output": final, "usage": {"total_tokens": 1}}})
        return "\n".join(json.dumps(line) for line in lines).encode()

    def enable(self):
        (self.root / "model_router.json").write_text(json.dumps({"agy_enabled": True, "agy_model": "gemini-4-argon-low"}), encoding="utf-8")

    def test_the_closing_finish_keeps_the_answer_and_raises_no_alert(self):
        self.enable()
        self.outcomes = [SimpleNamespace(returncode=0, stdout=self.finish_run(), stderr=b"")]
        self.assertEqual(self.ask(), ANSWER)
        self.assertEqual(mr.last_call()["route_reason"], "agy_first")
        self.assertTrue(mr.load_config(self.root)["agy_enabled"])
        self.assertFalse((self.root / "logs/model-router-alerts.log").exists())

    def test_a_finish_that_does_not_close_the_run_stays_an_unknown_tool(self):
        other = {"action": "offer", "price": 1, "reason": "x"}
        later = {"step_index": 5, "state": "DONE", "step_type": "agent_response"}
        cases = {"different_answer": self.finish_run(answer=other), "no_finish_step": self.finish_run(closing=False), "steps_after_it": self.finish_run(extra=[later]),
                 "earlier_step": self.finish_run(index=2), "failed_run": self.finish_run(status="ERROR")}
        for name, stdout in cases.items():
            with self.subTest(name):
                _, tools = mr.read_stream(stdout.decode())
                self.assertEqual(sorted(tools), ["finish"])
                self.assertEqual(mr.serious_tools(tools), ["finish"])

    def test_finish_with_another_tool_applies_the_other_tools_rule(self):
        other = {"step_index": 3, "state": "DONE", "step_type": "tool", "tool_name": "brand_new_tool"}
        _, tools = mr.read_stream(self.finish_run(extra=[other]).decode())
        self.assertEqual(sorted(tools), ["brand_new_tool"])

    def stored_stop(self, tools, at=1000.0):
        (self.root / ".market").mkdir(exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        moment = mr.datetime.fromtimestamp(at, mr.timezone.utc).isoformat()
        (self.root / "logs/model-router-alerts.log").write_text(json.dumps({"at": moment, "role": "buyer", "tools": tools}) + "\n", encoding="utf-8")
        (self.root / ".market/model-router.json").write_text(json.dumps({"agy_off_until": at + mr.STOP_SECONDS, "agy_off_reason": "agy_unexpected_tool"}), encoding="utf-8")

    def test_a_stop_from_finish_alone_is_lifted_without_touching_the_switch(self):
        (self.root / "model_router.json").write_text(json.dumps({"agy_enabled": False}), encoding="utf-8")
        self.stored_stop(["finish"])
        self.assertEqual(mr.agy_status(self.root, "buyer", 2000.0, lambda _t: dict(QUOTA)), (False, "agy_disabled_in_config"))
        state = mr.read_state(self.root)
        self.assertNotIn("agy_off_until", state)
        self.assertNotIn("agy_off_reason", state)
        self.assertFalse(mr.load_config(self.root)["agy_enabled"])
        self.assertFalse(json.loads((self.root / "model_router.json").read_text(encoding="utf-8"))["agy_enabled"])
        self.enable()
        self.assertEqual(mr.agy_status(self.root, "buyer", 2000.0, lambda _t: dict(QUOTA)), (True, "ok"))

    def test_a_stop_from_any_other_tool_is_kept(self):
        for tools in (["schedule"], ["finish", "schedule"], [], None):
            with self.subTest(tools=tools):
                self.setUp()
                self.enable()
                self.stored_stop(tools)
                self.assertEqual(mr.agy_status(self.root, "buyer", 2000.0, lambda _t: dict(QUOTA)), (False, "agy_unexpected_tool"))
                self.assertIn("agy_off_until", mr.read_state(self.root))

    def test_a_stop_without_matching_evidence_is_kept(self):
        self.enable()
        self.stored_stop(["finish"])
        state = mr.read_state(self.root)
        cases = {"other_alert_time": dict(state, agy_off_until=state["agy_off_until"] + 60), "other_reason": dict(state, agy_off_reason="agy_limit_failure")}
        for name, stored in cases.items():
            with self.subTest(name):
                (self.root / ".market/model-router.json").write_text(json.dumps(stored), encoding="utf-8")
                self.assertFalse(mr.agy_status(self.root, "buyer", 2000.0, lambda _t: dict(QUOTA))[0])
                self.assertIn("agy_off_until", mr.read_state(self.root))
        (self.root / "logs/model-router-alerts.log").unlink()
        (self.root / ".market/model-router.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(mr.agy_status(self.root, "buyer", 2000.0, lambda _t: dict(QUOTA))[0])
        self.assertIn("agy_off_until", mr.read_state(self.root))

    def test_tool_inputs_are_not_kept_in_metadata(self):
        self.outcomes = [SimpleNamespace(returncode=0, stdout=success(tools=[("search_web", "DONE")]), stderr=b"")]
        self.ask()
        folder = next((self.root / "logs/harness").iterdir())
        self.assertNotIn("SENSITIVE", (folder / "metadata.json").read_text(encoding="utf-8"))

    def test_argon_quota_uses_its_weekly_group_and_the_gemini_five_hour(self):
        output = {"command": {"data": {"groups": [
            {"name": "Gemini Models", "buckets": [{"window": "weekly", "remaining_fraction": .9, "reset_time": "a"}, {"window": "5h", "remaining_fraction": .8, "reset_time": "b"}]},
            {"name": "Gemini 4 Argon", "buckets": [{"window": "weekly", "remaining_fraction": .1, "reset_time": "c"}]}]}}}
        self.assertEqual(mr.parse_quota(output, "gemini-4-argon-low"), {"weekly_remaining_percent": 10.0, "weekly_resets_at": "c", "five_hour_remaining_percent": 80.0, "five_hour_resets_at": "b"})
        self.assertEqual(mr.parse_quota(output, "gemini-3.8-flash-low")["weekly_remaining_percent"], 90.0)
        self.assertIsNone(mr.parse_quota({"command": {"data": {"groups": output["command"]["data"]["groups"][:1]}}}, "gemini-4-argon-low"))

    def test_available_reports_any_usable_model(self):
        self.assertEqual(mr.available(self.root, "buyer", 1000, lambda _t: dict(QUOTA)), (True, "agy"))
        self.assertEqual(mr.available(self.root, "buyer", 5000, lambda _t: None)[0], True)
        self.breaker.return_value = (False, "usage_stale")
        allowed, reason = mr.available(self.root, "buyer", 9000, lambda _t: None)
        self.assertFalse(allowed)
        self.assertIn("usage_stale", reason)

    def test_damaged_state_does_not_break_anything(self):
        (self.root / ".market").mkdir()
        for content in ("{broken", "[]", json.dumps({"agy_off_until": "x", "quota": "y"})):
            with self.subTest(content=content):
                (self.root / ".market/model-router.json").write_text(content, encoding="utf-8")
                self.assertEqual(self.ask(), ANSWER)

    def test_unexpected_reader_failure_skips_agy_and_never_raises(self):
        def explode(_t):
            raise RuntimeError("boom")
        self.assertEqual(mr.agy_status(self.root, "buyer", 1000, explode), (False, "router_error"))

    def test_disabled_in_config_goes_straight_to_codex(self):
        (self.root / "model_router.json").write_text(json.dumps({"agy_enabled": False}), encoding="utf-8")
        self.assertEqual(self.ask()["reason"], "codex")
        self.assertEqual(self.calls, [])

    def test_oversized_prompt_goes_to_codex(self):
        with patch.dict(mr.DEFAULTS, {"max_prompt_chars": 5}):
            self.assertEqual(self.ask()["reason"], "codex")
        self.assertEqual(self.calls, [])

    def test_other_providers_pass_through(self):
        mr.run_model("claude", "p", 30, root=self.root, runner=self.runner)
        self.codex.assert_called_once()
        self.assertEqual(self.calls, [])

    def test_transitions_are_logged_once(self):
        self.ask()
        self.ask()
        self.outcomes = [SimpleNamespace(returncode=3, stdout=b"", stderr=b"quota")]
        self.ask()
        rows = [json.loads(line) for line in (self.root / "logs/model-router.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["agy"], r["reason"]) for r in rows], [("on", "ok"), ("off", "agy_limit_failure")])


if __name__ == "__main__":
    unittest.main()
