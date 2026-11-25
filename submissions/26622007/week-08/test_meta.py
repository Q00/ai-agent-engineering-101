"""The Luna agent loop and the meta-harness. The fake codex is a script that edits a throwaway git repository.

    python -m unittest test_meta -v        (from submissions/26622007/week-08)
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import datetime
import textwrap
import time
import unittest
from unittest import mock

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import agent_loop            # noqa: E402
import agent_tools           # noqa: E402
import forum                 # noqa: E402
import harness               # noqa: E402
import market_model as mm    # noqa: E402
import meta_harness as mh    # noqa: E402
import restock               # noqa: E402

CATALOG = {"F1": {"name": "포기김치", "kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720}}
FINAL = restock.FINAL


class ScorecardRefreshTest(unittest.TestCase):
    def test_next_cycle_discards_stale_buyer_formula(self):
        original = mh.scorecard.OBJECTIVE
        try:
            mh.scorecard.OBJECTIVE = {"buyers": [(('surplus',), 0.5)]}
            refreshed = mh.refresh_scorecard()
            self.assertEqual(refreshed.OBJECTIVE["buyers"][0], (("saved",), 0.5))
        finally:
            mh.scorecard.OBJECTIVE = original


def step(action="finish", tool="", args="", evidence=(), final=None):
    return {"reason": "r", "evidence": list(evidence), "action": action, "tool": tool, "args_json": args, "final": final}


def script(*steps):
    seen = []

    def ask(prompt, schema, system, tag=""):
        seen.append(prompt)
        return steps[min(len(seen) - 1, len(steps) - 1)]
    return ask, seen


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        with open(os.path.join(self.md, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(CATALOG, f, ensure_ascii=False)
        self.final = {"why": "w", "buy": [{"sku": "F1", "qty": 2}]}

    def decide(self, *steps, **kw):
        ask, seen = script(*steps)
        return agent_loop.decide("goal", FINAL, self.md, "restock", ask_fn=ask, **kw), seen

    def test_calls_the_tool_it_chose_and_records_what_it_cited(self):
        out, seen = self.decide(step("call_tool", "catalog_search", '{"query": "김치"}'),
                                step(evidence=["c0"], final=self.final))
        self.assertEqual(out, self.final)
        self.assertIn("F1", seen[1])
        with open(os.path.join(self.md, "decisions.jsonl"), encoding="utf-8") as f:
            d = json.loads(f.readline())
        self.assertEqual((d["evidence"], d["calls"][0]["tool"]), (["c0"], "catalog_search"))
        with open(os.path.join(self.md, "tool_calls.jsonl"), encoding="utf-8") as f:
            self.assertTrue(json.loads(f.readline())["ok"])

    def test_a_decision_without_evidence_is_sent_back(self):
        out, seen = self.decide(step(final=self.final), step(evidence=["c9"], final=self.final),
                                step("call_tool", "catalog_search", "{}"), step(evidence=["c0"], final=self.final))
        self.assertEqual(out, self.final)
        self.assertIn("refused", seen[1])
        self.assertIn("refused", seen[2])

    def test_no_evidenced_decision_means_none(self):
        self.assertIsNone(self.decide(step(final=self.final), max_steps=3)[0])
        self.assertFalse(os.path.exists(os.path.join(self.md, "decisions.jsonl")))

    def test_a_given_proposal_can_be_cited(self):
        ask, _ = script(step(evidence=["c0"], final=self.final))
        self.assertEqual(agent_loop.decide("g", FINAL, self.md, "restock", {"c0": "{}"}, ask), self.final)

    def test_bad_tool_calls_are_told_not_fatal(self):
        out, seen = self.decide(step("call_tool", "", "{}"), step("call_tool", "catalog_search", "not json"),
                                step("call_tool", "catalog_search", "{}"), step(evidence=["c0"], final=self.final))
        self.assertEqual(out, self.final)
        self.assertIn("no tool named", seen[1])
        self.assertIn("not JSON", seen[2])

    def test_a_new_tool_is_found_without_a_restart(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(agent_tools, "TOOLS", t):
            self.assertEqual(agent_tools.discover(), {})
            os.makedirs(os.path.join(t, "fresh"))
            with open(os.path.join(t, "fresh", "manifest.json"), "w") as f:
                json.dump({"purpose": "p"}, f)
            with open(os.path.join(t, "fresh", "tool.py"), "w") as f:
                f.write('import json,sys;print(json.dumps({"got": json.load(sys.stdin)}))')
            self.assertEqual(list(agent_tools.discover()), ["fresh"])
            self.assertEqual(json.loads(agent_tools.run("fresh", {"a": 1}, self.md)), {"got": {"a": 1}})

    def test_a_crashing_tool_answers_with_the_error(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "bad"))
            with open(os.path.join(t, "bad", "tool.py"), "w") as f:
                f.write("raise ValueError('boom')")
            self.assertIn("boom", agent_tools.run("bad", {}, self.md, folder=t))

    def test_seed_tools_run(self):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"balance": 5, "units": [{"unit": "u", "category": "c", "cost": 3, "expires_at": 1}]}, f)
        for name in agent_tools.discover(os.path.join(HERE, "tools")):
            out = json.loads(agent_tools.run(name, {}, self.md, folder=os.path.join(HERE, "tools")))
            self.assertFalse(isinstance(out, dict) and "error" in out, (name, out))

    def test_research_pack_reports_missing_forum_snapshot(self):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"units": []}, f)
        out = json.loads(agent_tools.run("auto_market_research_pack", {}, self.md, folder=os.path.join(HERE, "tools")))
        self.assertFalse(out["forum_snapshot_available"])
        self.assertEqual(out["forum_posts_recent"], [])
        self.assertTrue(out["catalog_matches"])
        with open(os.path.join(self.md, "forum.json"), "w") as f:
            json.dump([{"text": "hello"}], f)
        out = json.loads(agent_tools.run("auto_market_research_pack", {}, self.md, folder=os.path.join(HERE, "tools")))
        self.assertTrue(out["forum_snapshot_available"])
        self.assertEqual(out["forum_posts_recent"], [{"text": "hello"}])

    def test_every_step_reaches_the_wire_with_a_strict_schema(self):
        bodies = []

        def api(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(step(evidence=["c0"], final=self.final))}}]})
        real = httpx.Client
        with mock.patch.object(harness.httpx, "Client", lambda **k: real(transport=httpx.MockTransport(api), **k)), \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "x"}):
            out = agent_loop.decide("g", FINAL, self.md, "restock", {"c0": "{}"}, harness._ask_openrouter)
        self.assertEqual(out, self.final)
        rf = bodies[0]["response_format"]
        self.assertEqual((rf["type"], rf["json_schema"]["strict"]), ("json_schema", True))

    def test_schema_is_what_strict_mode_asks_for(self):
        def check(s):
            if s.get("type") == "object":
                self.assertFalse(s["additionalProperties"])
                self.assertEqual(sorted(s["required"]), sorted(s["properties"]))
                for p in s["properties"].values():
                    check(p)
            for a in s.get("anyOf", []):
                check(a)
            if s.get("items"):
                check(s["items"])
        check(agent_loop.step_schema(["t"], FINAL))
        check(mh.schema_for("terra"))
        check(mh.schema_for("astra"))


class RestockAgentTest(unittest.TestCase):
    CFG = {**restock.DEFAULTS, "round_ends_at": None, "max_lines": 2}

    def turn(self, d, final, cat=None):
        inv = {"balance": 5000000, "units": []}
        calls = []

        def decide(goal, schema, md, agent, given):
            calls.append(given)
            return final
        out = restock.agent_turn(d, inv, cat or {f"S{i}": CATALOG["F1"] for i in range(5)}, [], self.CFG, None, decide)
        return out, calls

    def test_the_queue_goes_out_in_lots_and_the_next_decision_waits(self):
        lines = [{"sku": f"S{i}", "qty": 1} for i in range(3)]
        with tempfile.TemporaryDirectory() as d:
            out, calls = self.turn(d, {"buy": lines, "why": ""})
            self.assertEqual((out, len(calls)), (lines[:2], 1))
            self.assertIn("c0", calls[0])
            out, calls = self.turn(d, {"buy": [], "why": ""})
            self.assertEqual((out, calls), (lines[2:], []))

    def test_a_decision_made_ahead_keeps_its_lines_for_the_window(self):
        # 11/07 07:44 window: the 20-min decision fell on the window's first run, ~30 s of LLM before the first supply call
        # (first line +18 s after it opened, in 3 of 6 windows 06:21-08:05); outside the window it decides, sends nothing
        lines = [{"sku": f"S{i}", "qty": 1} for i in range(3)]
        inv, cat, calls = {"balance": 5000000, "units": []}, {f"S{i}": CATALOG["F1"] for i in range(5)}, []

        def decide(goal, schema, md, agent, given):
            calls.append(given)
            return {"buy": lines, "why": ""}
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(restock.agent_turn(d, inv, cat, [], self.CFG, None, decide, send=False))
            self.assertEqual(len(calls), 1)
            self.assertIsNone(restock.agent_turn(d, inv, cat, [], self.CFG, None, decide, send=False))
            self.assertEqual(len(calls), 1)                       # not due again
            self.assertEqual(restock.agent_turn(d, inv, cat, [], self.CFG, None, decide), lines[:2])
            self.assertEqual(len(calls), 1)                       # the window run sends the queue, no new decision

    def test_a_decision_ahead_only_in_the_last_minutes_before_the_window(self):
        # independent review 11/07 08:3x: deciding on every closed run moved the decision to just after a window closed
        # (~17 min before its lines go out, last window's lines still pending) and retried a failing agent each minute
        now = 1000000
        closed = lambda opens: {"restock": {"open": False, "opens_at": now + opens, "closes_at": now + opens + 180}}
        self.assertFalse(restock.ahead_due(closed(17 * 60), now))
        self.assertFalse(restock.ahead_due(closed(restock.AHEAD_SECONDS + 1), now))
        self.assertTrue(restock.ahead_due(closed(restock.AHEAD_SECONDS), now))
        self.assertTrue(restock.ahead_due(closed(70), now))
        self.assertFalse(restock.ahead_due({"restock": {"open": True, "closes_at": now + 60}}, now))
        self.assertFalse(restock.ahead_due({}, now))

    def test_in_the_window_a_decision_waits_for_due_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "restock_queue.json"), "w") as f:
                json.dump({"at": time.time() - 1.5 * self.CFG["agent_every_minutes"] * 60, "lines": []}, f)
            calls = []
            decide = lambda *a: calls.append(a) or {"buy": [], "why": ""}
            inv, cat = {"balance": 5000000, "units": []}, {"S0": CATALOG["F1"]}
            restock.agent_turn(d, inv, cat, [], self.CFG, None, decide, due_minutes=2 * self.CFG["agent_every_minutes"])
            self.assertEqual(calls, [])                           # 1.5 intervals old: not yet due inside the window
            restock.agent_turn(d, inv, cat, [], self.CFG, None, decide)   # the plain interval: due
            self.assertEqual(len(calls), 1)

    def test_no_decision_leaves_it_to_the_formula(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(self.turn(d, None)[0])

    def test_unknown_skus_and_bad_lots_do_not_go_out(self):
        got = restock.clean_lines([{"sku": "F1", "qty": 2}, {"sku": "F1", "qty": 3}, {"sku": "X", "qty": 1}, {"sku": "F2", "qty": 21}],
                                  {"F1": {}, "F2": {}}, 10)
        self.assertEqual(got, [{"sku": "F1", "qty": 2}])

    def test_my_receipts_identify_my_shop_when_the_tunnel_url_changes(self):
        rows = [{"k": "settle", "seq": 1, "ts": 1800000000, "buyer": "b", "order": "o", "sku": "F1", "amount": 1, "shop": "https://new.trycloudflare.com"},
                {"k": "result", "seq": 2, "order": "o", "shop": "https://new.trycloudflare.com", "seller": "Nocha Furniture"}]
        m = mm.estimate(rows, CATALOG, (), mm.my_shops(), 1800000000, {"min_settles": 1})
        self.assertEqual(m["skus"]["F1"]["my_share"], 1.0)
        self.assertEqual(m["skus"]["F1"]["sellers"], 0)


FAKE = """\
#!/usr/bin/env python3
import json, os, sys
a = sys.argv
out = a[a.index("-o") + 1]
os.chdir(a[a.index("-C") + 1])
mode = os.environ["FAKE_MODE"]
for p, text in json.loads(os.environ.get("FAKE_SIDE") or "{}").items():   # another job writes while the model runs
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    open(p, "w").write(text)
if mode == "refused":
    print("The 'gpt-6.1-terra' model is not supported", file=sys.stderr)
    sys.exit(1)
if mode == "edit-then-die":
    open("changed.py", "w").write("x = 1\\n")
    sys.exit(2)
check = {"current": "lean", "gap_cause": "unclear", "keep_or_shift": "keep", "reason": "r"}
action = "code"
if mode in ("watch", "shift", "shift-no-doc", "strategy-doc"):
    check["keep_or_shift"] = {"watch": "watch", "strategy-doc": "keep"}.get(mode, "shift")
    action = "strategy_shift" if mode.startswith("shift") else "none"
    if mode == "shift":
        open("meta/strategy.md", "a").write("- shifted\\n")
    if mode == "strategy-doc":
        open("meta/strategy.md", "w").write("prompt\\n- shifted\\n")
elif mode not in ("nothing", "nothing-false", "unsure", "unsure-planned"):
    open("changed.py", "w").write({"broken": "raise SystemExit(1)\\n", "secret": "k = 'sk-or-v1-" + "a" * 30 + "'\\n",
                                   "env-secret": "k = 'value-from-env-file-1234'\\n"}.get(mode, "x = 1\\n"))
if mode == "broken":
    open("test_x.py", "w").write("import unittest\\nclass T(unittest.TestCase):\\n    def test(self):\\n        self.fail('no')\\n")
good = [{"ref": "logs/market-kit.log:2", "excerpt": "[acp] settled", "meaning": "m"}]
bad = [{"ref": "logs/market-kit.log:2", "excerpt": "never said this", "meaning": "m"}]
if mode == "shifted":       # the quote is real, its line number is not (the log grew while the model wrote)
    good = [{"ref": "logs/market-kit.log:1", "excerpt": "[acp] settled", "meaning": "m"}]
good += json.loads(os.environ.get("FAKE_EVIDENCE") or "[]")
json.dump({"changed": True, "hypothesis": "h", "change_summary": "변경 요약", "target_metric": "profit", "expected": "up",
           "evidence": bad if mode in ("false-citation", "nothing-false") else [] if mode == "no-citation" else good,
           "previous_verdict": "none", "previous_reason": "", "strategy_check": check, "action_kind": action,
           "strategy": {"options": [], "chosen": "lean", "experiment": "e"},
           "evidence_gap": {"missing": (os.environ.get("FAKE_MISSING") or "breaks before or after the widening")
                                       if mode.startswith("unsure") else "",
                            "measurement": (os.environ.get("FAKE_MEASUREMENT") or "ledger seq of each break vs the 02:30 commit")
                                           if mode == "unsure-planned" else "",
                            "judge_line": {"metric": "break rate of the widened SKUs", "direction": "below",
                                           "threshold": "15%", "min_sample": "20 sales", "expires": "11-05 18:00",
                                           "undo": "narrow back"} if mode == "unsure-planned" else
                                          {"metric": "", "direction": "none", "threshold": "", "min_sample": "",
                                           "expires": "", "undo": ""}}}, open(out, "w"))
"""


class SellerRestartPolicyTest(unittest.TestCase):
    OLD = {"restock": {"sku_target_units": {"G1": 2}}, "price": {"min_profit": 1}}

    def observed_restart(self, after, paths=("pricing.json",), before=...):
        before = self.OLD if before is ... else before
        original = json.dumps(before, sort_keys=True)
        with mock.patch.object(mh, "KIT_PATTERN", "isolated-kit"), \
                mock.patch.object(mh, "pricing_now", return_value=after), \
                mock.patch.object(mh.subprocess, "run") as run, mock.patch.object(mh, "log"):
            mh.restart_seller(paths, pricing_before=before)
        self.assertEqual(json.dumps(before, sort_keys=True), original)
        return run

    def test_target_key_addition_still_restarts(self):
        new = {"restock": {"sku_target_units": {"G1": 2, "G2": 3}}, "price": {"min_profit": 1}}
        self.observed_restart(new).assert_called_once_with(["pkill", "-f", "seller.py --port"], check=False)

    def test_existing_target_values_and_reversal_preserve_talks(self):
        new = {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 1}}
        self.observed_restart(new).assert_not_called()
        self.observed_restart(self.OLD, before=new).assert_not_called()
        self.observed_restart(self.OLD).assert_not_called()

    def test_removed_target_or_other_restock_settings_still_restart(self):
        for new in (
            {"restock": {"sku_target_units": {}}, "price": {"min_profit": 1}},
            {"restock": {"sku_target_units": {"G1": 3}, "keep_cash": 100}, "price": {"min_profit": 1}},
        ):
            with self.subTest(new=new):
                self.observed_restart(new).assert_called_once()

    def test_price_warranty_and_typed_other_settings_still_restart(self):
        for new in (
            {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 2}},
            {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": True}},
            {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 1.0}},
            {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 1}, "warranty": {"max_hours": 12}},
        ):
            with self.subTest(new=new):
                self.observed_restart(new).assert_called_once()

    def test_seller_source_and_nonroot_pricing_paths_still_restart(self):
        new = {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 1}}
        for paths in (("pricing.json", "seller.py"), ("pricing.json", "harness.py"), ("other/pricing.json",)):
            with self.subTest(paths=paths):
                self.observed_restart(new, paths).assert_called_once()

    def test_unknown_or_invalid_settings_never_suppress_restart(self):
        for new in (None, [], {}, {"restock": None}, {"restock": {"sku_target_units": None}},
                    {"restock": {"sku_target_units": {"G1": True}}},
                    {"restock": {"sku_target_units": {"G1": -1}}},
                    {"restock": {"sku_target_units": {"G1": 3.0}}},
                    {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": float("nan")}}):
            with self.subTest(new=new):
                self.observed_restart(new).assert_called_once()
        self.observed_restart(self.OLD, before=None).assert_called_once()

    def test_unrelated_hook_files_do_not_restart_the_seller(self):
        self.observed_restart(self.OLD, ("restock.py", "buyer_post.py")).assert_not_called()

    def test_target_changes_still_pause_the_full_pricing_baseline(self):
        import sol_seller_margin as margin
        new = {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 1}}
        cfg = {"baseline_sha256": margin.digest(margin.baseline_snapshot(self.OLD))}
        with tempfile.TemporaryDirectory() as td:
            study = margin.MarginStudy(td)
            with mock.patch.object(margin.sol_source_load, "verifies", return_value=True), \
                    mock.patch.object(margin, "source_manifest", return_value=margin.DISK_SNAPSHOT), \
                    mock.patch.object(study, "event") as event:
                study.verify_baseline("task", cfg, self.OLD, ())
                with self.assertRaisesRegex(margin.StudyError, "paused unknown"):
                    study.verify_baseline("task", cfg, new, ())
                event.assert_called_once_with("task", cfg, "paused", reason="baseline_changed", economic_status="unknown")


class RestartFixtureIsolationTest(unittest.TestCase):
    def test_direct_restart_policy_suite_does_not_write_parent_log(self):
        with tempfile.TemporaryDirectory() as parent:
            os.makedirs(os.path.join(parent, "logs"))
            logfile = os.path.join(parent, "logs", "meta.log")
            with open(logfile, "wb") as f:
                f.write(b"parent fixture\\n")
            with open(logfile, "rb") as f:
                before = f.read()
            result = unittest.TestResult()
            with mock.patch.object(mh, "ROOT", parent):
                unittest.defaultTestLoader.loadTestsFromTestCase(SellerRestartPolicyTest).run(result)
            self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
            with open(logfile, "rb") as f:
                self.assertEqual(f.read(), before)


class HarnessCycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outer = self.tmp.name
        self.root = os.path.join(self.outer, "repo")
        os.makedirs(os.path.join(self.root, "logs"))
        os.makedirs(os.path.join(self.root, "meta"))
        with open(os.path.join(self.root, "logs", "market-kit.log"), "w") as f:
            f.write("10:00:00 [kit] market_kit 4\n10:00:01 [acp] settled x at 5\n")
        for name in ("terra", "astra", "strategy"):
            with open(os.path.join(self.root, "meta", f"{name}.md"), "w") as f:
                f.write("prompt\n")
        with open(os.path.join(self.root, ".gitignore"), "w") as f:
            f.write("__pycache__/\n.market/\n")
        with open(os.path.join(self.root, "test_ok.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        pass\n")
        with open(os.path.join(self.outer, ".env"), "w") as f:
            f.write("KITLOG_TOKEN=value-from-env-file-1234\n")
        fake = os.path.join(self.outer, "fake-codex")
        with open(fake, "w") as f:
            f.write(FAKE)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        # This fixture owns the repository until cleanup: finish native background work too.
        self.git("config", "maintenance.autoDetach", "false")
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        # cycle() reloads scorecard (79df539), which put the real board() back over the fake one below: the tests read the
        # live board, and its board_drift alert 11/06 18:2x-19:30 failed every no-code cycle. Keep the loaded module.
        patches = [mock.patch.object(mh, "ROOT", self.root), mock.patch.object(mh, "KIT_PATTERN", ""),
                   mock.patch.object(mh, "refresh_scorecard", lambda: mh.scorecard),
                   mock.patch.object(mh, "CANARY_SECONDS", 0), mock.patch.dict(os.environ, {"META_CODEX": fake}),
                   mock.patch.object(mh.scorecard, "board", lambda *a, **k: {"round": 3}),
                   mock.patch.object(mh.scorecard.patch_notes, "check", lambda *a, **k: {"error": "offline in tests"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def go(self, mode, kind="terra"):
        with mock.patch.dict(os.environ, {"FAKE_MODE": mode}), mock.patch("builtins.print"):
            return mh.cycle(kind)

    def test_target_only_cycle_keeps_the_seller_running(self):
        old = {"restock": {"sku_target_units": {"G2c023a8c": 2}}}
        new = {"restock": {"sku_target_units": {"G2c023a8c": 3}}}
        with open(os.path.join(self.root, "pricing.json"), "w") as f:
            json.dump(old, f)
        self.git("add", "pricing.json")
        self.git("commit", "-qm", "target fixture before the cycle")
        killed, real_run = [], subprocess.run
        def run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "pkill":
                killed.append(tuple(cmd))
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *args, **kwargs)
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({"pricing.json": json.dumps(new)})}), \
                mock.patch.object(mh, "KIT_PATTERN", "isolated-kit"), \
                mock.patch.object(mh.subprocess, "run", side_effect=run):
            rec = self.go("good")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertEqual(killed, [], "A restock-only target does not require discarding seller talks")
        with open(os.path.join(self.root, "pricing.json")) as f:
            self.assertEqual(json.load(f), new)

    def test_price_cycle_rollback_restarts_again_for_the_restored_price(self):
        old = {"restock": {"sku_target_units": {"G2c023a8c": 2}}, "price": {"min_profit": 1}}
        new = {"restock": {"sku_target_units": {"G2c023a8c": 2}}, "price": {"min_profit": 2}}
        with open(os.path.join(self.root, "pricing.json"), "w") as f:
            json.dump(old, f)
        self.git("add", "pricing.json")
        self.git("commit", "-qm", "price rollback fixture before the cycle")
        killed, real_run = [], subprocess.run
        def run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "pkill":
                killed.append(tuple(cmd))
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *args, **kwargs)
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({"pricing.json": json.dumps(new)})}), \
                mock.patch.object(mh, "KIT_PATTERN", "isolated-kit"), \
                mock.patch.object(mh, "canary", return_value=["isolated canary failure"]), \
                mock.patch.object(mh.subprocess, "run", side_effect=run):
            rec = self.go("good")
        self.assertEqual(rec["status"], "canary_failed", rec.get("problems"))
        self.assertEqual(killed, [("pkill", "-f", "seller.py --port")] * 2)
        with open(os.path.join(self.root, "pricing.json")) as f:
            self.assertEqual(json.load(f), old)

    def test_deep_json_snapshot_does_not_block_the_required_rollback(self):
        old = {"restock": {"sku_target_units": {"G1": 2}}, "price": {"min_profit": 1}}
        new = {"restock": {"sku_target_units": {"G1": 2}}, "price": {"min_profit": 2}}
        path = os.path.join(self.root, "pricing.json")
        with open(path, "w") as f:
            json.dump(old, f)
        self.git("add", "pricing.json")
        self.git("commit", "-qm", "deep JSON rollback fixture")
        broken, parser_faults = [], []
        real_load = json.load
        def canary():
            with open(path, "w") as f:
                f.write("[" * 2000 + "0" + "]" * 2000)
            broken.append(True)
            return ["isolated canary failure"]
        def load(f, *args, **kwargs):
            # Parser-failure injection: nesting thresholds differ across Python builds.
            if broken and not parser_faults and getattr(f, "name", None) == path:
                parser_faults.append(True)
                raise RecursionError("isolated pricing parser failure")
            return real_load(f, *args, **kwargs)
        killed, real_run = [], subprocess.run
        def run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "pkill":
                killed.append(tuple(cmd))
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *args, **kwargs)
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({"pricing.json": json.dumps(new)})}), \
                mock.patch.object(mh, "KIT_PATTERN", "isolated-kit"), \
                mock.patch.object(mh, "canary", side_effect=canary), \
                mock.patch.object(mh.json, "load", side_effect=load), \
                mock.patch.object(mh.subprocess, "run", side_effect=run):
            rec = self.go("good")
        self.assertEqual(parser_faults, [True])
        self.assertEqual(rec["status"], "canary_failed")
        self.assertEqual(len(killed), 2)
        with open(path) as f:
            self.assertEqual(json.load(f), old)

    def test_concurrent_dirty_price_is_not_mistaken_for_target_only(self):
        old = {"restock": {"sku_target_units": {"G1": 2}}, "price": {"min_profit": 1}}
        foreign = {"restock": {"sku_target_units": {"G1": 2}}, "price": {"min_profit": 2}}
        new = {"restock": {"sku_target_units": {"G1": 3}}, "price": {"min_profit": 1}}
        path = os.path.join(self.root, "pricing.json")
        with open(path, "w") as f:
            json.dump(old, f)
        self.git("add", "pricing.json")
        self.git("commit", "-qm", "concurrent price fixture")
        real_dirty, real_run = mh.dirty_paths, subprocess.run
        visits, killed = [], []
        def dirty():
            if not visits:
                with open(path, "w") as f:
                    json.dump(foreign, f)
            visits.append(True)
            return real_dirty()
        def run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "pkill":
                killed.append(tuple(cmd))
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *args, **kwargs)
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({"pricing.json": json.dumps(new)})}), \
                mock.patch.object(mh, "KIT_PATTERN", "isolated-kit"), \
                mock.patch.object(mh, "dirty_paths", side_effect=dirty), \
                mock.patch.object(mh.subprocess, "run", side_effect=run):
            rec = self.go("good")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertEqual(killed, [("pkill", "-f", "seller.py --port")])

    def exists(self):
        return os.path.exists(os.path.join(self.root, "changed.py"))

    def test_a_cited_change_is_committed_and_recorded(self):
        rec = self.go("good")
        self.assertEqual(rec["status"], "running")
        self.assertTrue(self.exists())
        self.assertIn("feat(week-08)", self.git("log", "--format=%s"))
        self.assertIn("변경 요약", self.git("log", "--format=%s"))
        with open(os.path.join(self.root, "meta", "changes.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["answer"]["evidence"][0]["excerpt"], "[acp] settled")

    def test_tests_run_without_the_market_name_and_with_this_python(self):
        # under SHOP_MARKET_NAME the shop's tests wrote into the live seller_memory_r3.json and 5 failed at any HEAD
        with open(os.path.join(self.root, "test_env.py"), "w") as f:
            f.write("import os, sys, unittest\nimport httpx\nclass T(unittest.TestCase):\n    def test(self):\n"
                    "        self.assertNotIn('SHOP_MARKET_NAME', os.environ)\n"
                    f"        self.assertEqual(sys.executable, {sys.executable!r})\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "env test")
        with mock.patch.dict(os.environ, {"SHOP_MARKET_NAME": "nocha12"}):
            rec = self.go("good")
            self.assertEqual(os.environ["SHOP_MARKET_NAME"], "nocha12")     # the harness's own environment is kept
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertEqual(mh.test_env()["PATH"].split(os.pathsep)[0], os.path.dirname(sys.executable))

    def test_tests_run_with_the_seller_source_loader_installed_first(self):
        # 11/09 09:03 terra-1109-085220 was refused on ~70 seller-study tests (fresh_frozen_load_proof): plain discover
        # imports local modules before sol_source_load can attest them; the safe runner installs it first and they pass
        # (the command itself: under the safe runner the real loader is already imported, so a fake one cannot tell)
        seen, real = [], subprocess.Popen
        def popen(cmd, **kw):
            if cmd[0] == "git":                                    # the copy's own git reads run as they are
                return real(cmd, **kw)
            seen.append(cmd)
            return mock.Mock(returncode=0, pid=0, communicate=lambda timeout=None: ("", ""))
        with mock.patch.object(mh.subprocess, "Popen", popen):
            mh.run_tests()
            with open(os.path.join(self.root, "sol_source_load.py"), "w") as f:
                f.write("def install():\n    pass\n")
            mh.run_tests()
        self.assertEqual(seen[0][1:], ["-m", "unittest", "discover", "-p", "test_*.py"])     # no loader here: as before
        self.assertEqual(seen[1][1], "-c")
        boot = seen[1][2]
        self.assertLess(boot.index("sol_source_load.install()"), boot.index("unittest.main"))
        self.assertIn("'discover', '-p', 'test_*.py'", boot)
        self.assertIn("sol_source_load.install()", mh.test_prompt())

    def test_copied_meta_root_and_import_time_paths_ignore_inherited_meta_dir(self):
        # Use the real module in a copied child, never a live operational root.
        mh.shutil.copyfile(mh.__file__, os.path.join(self.root, "meta_harness.py"))
        with open(os.path.join(self.root, "test_meta_root.py"), "w") as f:
            f.write(textwrap.dedent(f"""\
                import os, sys, unittest
                sys.path.append({HERE!r})
                import meta_harness as child
                class T(unittest.TestCase):
                    def test(self):
                        here = os.path.dirname(os.path.abspath(__file__))
                        self.assertEqual(child.ROOT, here)
                        self.assertEqual(child.TRIED, os.path.join(here, ".market", "meta_tried.json"))
                        child.log("isolated fixture")
                        child.save_tried({{"terra": 1}})
                        self.assertTrue(os.path.isfile(os.path.join(here, "logs", "meta.log")))
                        self.assertTrue(os.path.isfile(child.TRIED))
                """))
        protected = {}
        for rel in ("logs/meta.log", ".market/meta_tried.json"):
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"parent fixture must not change\\n")
            with open(path, "rb") as f:
                protected[path] = f.read()
        for value in (None, "", self.root):
            with self.subTest(inherited="absent" if value is None else "empty" if not value else "present"):
                with mock.patch.dict(os.environ):
                    os.environ.pop("META_DIR", None)
                    if value is not None:
                        os.environ["META_DIR"] = value
                    ok, output = mh.run_tests()
                    self.assertTrue(ok, output)
                    self.assertEqual(os.environ.get("META_DIR"), value)
                for path, content in protected.items():
                    with open(path, "rb") as f:
                        self.assertEqual(f.read(), content)

    def test_tests_pass_while_the_kit_keeps_writing_the_live_folder(self):
        # 11/09 09:03 and 10:07 every code change was refused: test_seller_warranty_trial hashes its folder's .market/
        # and memory/ before and after each test, and in the live checkout the kit writes there every second
        for name in (".market", "memory"):
            os.makedirs(os.path.join(self.root, name), exist_ok=True)
            with open(os.path.join(self.root, name, "state.json"), "w") as f:
                f.write("{}\n")
        with open(os.path.join(self.root, "test_live.py"), "w") as f:
            f.write(textwrap.dedent(f"""\
                import os, unittest
                HERE = os.path.dirname(os.path.abspath(__file__))
                def state():
                    out = []
                    for name in (".market", "memory"):
                        for top, _, files in os.walk(os.path.join(HERE, name)):
                            out += [(top, n, open(os.path.join(top, n)).read()) for n in sorted(files)]
                    return out
                class T(unittest.TestCase):
                    def test(self):
                        before = state()
                        for name in (".market", "memory"):     # the kit, writing the live folder meanwhile
                            with open(os.path.join({self.root!r}, name, "state.json"), "a") as f:
                                f.write("tick\\n")
                        with open(os.path.join({self.root!r}, "ran-in.txt"), "w") as f:
                            f.write(HERE)
                        self.assertEqual(state(), before)
                """))
        ok, output = mh.run_tests()
        self.assertTrue(ok, output)
        with open(os.path.join(self.root, "ran-in.txt")) as f:
            ran_in = f.read()
        self.assertNotEqual(os.path.realpath(ran_in), os.path.realpath(self.root))
        self.assertFalse(os.path.exists(ran_in))                        # the copy is gone afterwards
        with open(os.path.join(self.root, ".market", "state.json")) as f:
            self.assertEqual(f.read(), "{}\ntick\n")                    # the live folder itself was only the kit's

    def test_the_copy_carries_the_cycles_uncommitted_change(self):
        # the copy is the working tree the cycle left, not HEAD: a test that needs the change fails without it
        with open(os.path.join(self.root, "value.py"), "w") as f:
            f.write("VALUE = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "value 1")
        with open(os.path.join(self.root, "test_value.py"), "w") as f:      # new and untracked, as a model adds it
            f.write("import unittest, value\nclass T(unittest.TestCase):\n    def test(self):\n"
                    "        self.assertEqual(value.VALUE, 2)\n")
        ok, output = mh.run_tests()
        self.assertFalse(ok)                                            # HEAD's VALUE = 1: the test really runs
        self.assertIn("test_value", output)
        with open(os.path.join(self.root, "value.py"), "w") as f:
            f.write("VALUE = 2\n")                                       # the cycle's edit, not committed yet
        ok, output = mh.run_tests()
        self.assertTrue(ok, output)

    def test_the_copy_keeps_the_repo_around_the_folder_and_its_tracked_memory_and_logs(self):
        # independent review 0738-isolate C: a bare copy lost ../../scripts (market_kit), the project's AGENTS.md and the
        # tracked memory/seller_memory.json, so 8 tests failed in it whatever the change
        top = os.path.join(self.outer, "top")
        week = os.path.join(top, "sub", "week")
        for d in ("scripts", "sub/week/memory", "sub/week/logs", "sub/week/.market"):
            os.makedirs(os.path.join(top, d))
        files = {"AGENTS.md": "rules\n", "scripts/kitmod.py": "KIT = 1\n", "sub/week/memory/tracked.json": "{}\n",
                 "sub/week/logs/tracked.log": "x\n", "sub/week/memory/live.json": "{}\n",
                 "sub/week/logs/live.log": "x\n", "sub/week/.market/m.json": "{}\n",
                 "sub/week/tools/memory/keep.py": "", "sub/week/test_up.py": textwrap.dedent("""\
                     import os, sys, unittest
                     HERE = os.path.dirname(os.path.abspath(__file__))
                     sys.path.insert(0, os.path.join(HERE, "../../scripts"))
                     import kitmod
                     class T(unittest.TestCase):
                         def test(self):
                             top = os.path.dirname(os.path.dirname(HERE))
                             self.assertTrue(os.path.isfile(os.path.join(top, "AGENTS.md")))
                             for rel in ("memory/tracked.json", "logs/tracked.log", "tools/memory/keep.py"):
                                 self.assertTrue(os.path.exists(os.path.join(HERE, rel)), rel)
                             for rel in ("memory/live.json", "logs/live.log", ".market"):
                                 self.assertFalse(os.path.exists(os.path.join(HERE, rel)), rel)
                     """)}
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(top, rel)), exist_ok=True)
            with open(os.path.join(top, rel), "w") as f:
                f.write(text)
        run = lambda *a: subprocess.run(["git", *a], cwd=top, capture_output=True, check=True)
        run("init", "-q")
        run("add", "AGENTS.md", "scripts", "sub/week/memory/tracked.json", "sub/week/logs/tracked.log", "sub/week/test_up.py")
        run("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "top")
        with mock.patch.object(mh, "ROOT", week):
            ok, output = mh.run_tests()
        self.assertTrue(ok, output)

    def test_the_copy_leaves_live_state_logs_and_keys_behind(self):
        for rel in (".market/m.json", "memory/s.json", "logs/live.log", "shop_key.pem", ".env"):
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)) or self.root, exist_ok=True)
            with open(os.path.join(self.root, rel), "w") as f:
                f.write("live\n")
        with open(os.path.join(self.root, "test_copy.py"), "w") as f:
            f.write("import os, unittest\nHERE = os.path.dirname(os.path.abspath(__file__))\n"
                    "class T(unittest.TestCase):\n    def test(self):\n"
                    "        for rel in ('.market', 'memory', 'logs/live.log', 'shop_key.pem', '.env'):\n"
                    "            self.assertFalse(os.path.exists(os.path.join(HERE, rel)), rel)\n"
                    "        for rel in ('meta/terra.md', 'test_ok.py', '.gitignore', 'logs/market-kit.log'):\n"
                    "            self.assertTrue(os.path.exists(os.path.join(HERE, rel)), rel)\n")
        ok, output = mh.run_tests()
        self.assertTrue(ok, output)

    def test_hard_to_tell_without_a_measurement_is_not_accepted(self):
        rec = self.go("unsure")
        self.assertEqual(rec["status"], "no_change_unsupported")
        self.assertIn("no measurement", rec["problems"][0])
        rec = self.go("unsure-planned")
        self.assertEqual(rec["status"], "no_change")
        with open(os.path.join(self.root, "meta", "grades.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.read().splitlines()[-1])["evidence_gap"]["judge_line"]["threshold"], "15%")
        half = {"missing": "m", "measurement": "x", "judge_line": {"metric": "a", "direction": "above", "threshold": "1",
                                                                     "min_sample": "", "expires": "e", "undo": "u"}}
        self.assertTrue(mh.gap_problems({"evidence_gap": half}, []))        # no min_sample: not a whole judge line
        self.assertTrue(mh.gap_problems({"evidence_gap": {**half, "judge_line": "a string"}}, []))

    def test_a_quote_whose_line_moved_is_anchored_to_the_line_that_holds_it(self):
        # GPT Pro 11/09 07:38 §9-1: 24 cycles were refused for a real quote cited at the line it was expected on
        rec = self.go("shifted")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertEqual(rec["answer"]["evidence"][0]["ref"], "logs/market-kit.log:2")
        self.assertEqual(rec["evidence_reanchored"], ["logs/market-kit.log:1 -> logs/market-kit.log:2"])

    def test_work_left_in_the_folder_before_the_cycle_is_not_its_change(self):
        # GPT Pro 11/09 07:38 §9-2: terra-1109-051538 changed nothing, yet pricing.json.bak-0406-terra-reoff (another
        # job's file) was staged by `add -A` and judged as the cycle's change
        with open(os.path.join(self.root, "pricing.json.bak-0406"), "w") as f:
            f.write("{}\n")
        with open(os.path.join(self.root, "other_job.py"), "w") as f:
            f.write("y = 2\n")
        self.git("add", "other_job.py")                 # staged by someone else, not committed
        rec = self.go("nothing")
        self.assertEqual(rec["status"], "no_change", rec.get("problems"))
        rec = self.go("good")
        self.assertEqual((rec["status"], rec["files"]), ("running", ["changed.py"]), rec.get("problems"))
        self.assertEqual(self.git("log", "--format=%H", "--", "pricing.json.bak-0406", "other_job.py"), "")
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other_job.py")    # still staged, as it was
        self.assertIn("pricing.json.bak-0406", self.git("status", "--porcelain"))

    def test_a_gap_open_two_cycles_needs_this_cycles_measurement_of_its_ids(self):
        # GPT Pro 11/09 07:38 §9-3: "G8ec51935 ... 원가 누락 1건" was left open cycle after cycle, never measured
        grades = os.path.join(self.root, "meta", "grades.jsonl")
        with open(grades, "w", encoding="utf-8") as f:
            for c, missing in (("terra-1109-062902", "G8ec51935의 원가 누락 1건, u-d9db11f14a2a 판매"),
                               ("terra-1109-074022", "G8ec51935의 원가 누락 1건")):
                f.write(json.dumps({"cycle": c, "time": "2026-11-09T07:00:00+0900", "status": "no_change_unsupported",
                                    "evidence_gap": {"missing": missing}}) + "\n")
        with open(os.path.join(self.root, "logs", "market-kit.log"), "a") as f:
            f.write("10:00:02 [restock] G8ec51935 unit u-0a1b2c3d4e5f cost 4100\n")
        env = {"FAKE_MISSING": "G8ec51935의 원가 누락 1건", "FAKE_MEASUREMENT": "measure_r3 unit cost"}
        with mock.patch.dict(os.environ, env):
            rec = self.go("unsure-planned")
        self.assertEqual(rec["status"], "no_change_unsupported")
        self.assertTrue(any("G8ec51935" in p and "open since terra-1109-062902" in p for p in rec["problems"]), rec["problems"])
        self.assertFalse(any("u-d9db11f14a2a" in p for p in rec["problems"]))      # named in one of the two only: not carried
        with open(sorted(os.path.join(self.root, "meta", "evidence", p)
                         for p in os.listdir(os.path.join(self.root, "meta", "evidence")))[-1], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["open_gaps"]["G8ec51935"][0], "terra-1109-062902")
        env.update(FAKE_MEASUREMENT="measure_r3 G8ec51935 unit cost",
                   FAKE_EVIDENCE=json.dumps([{"ref": "logs/market-kit.log:3", "excerpt": "G8ec51935 unit u-0a1b2c3d4e5f cost 4100",
                                              "meaning": "the missing cost, measured"}]))
        with mock.patch.dict(os.environ, env):
            rec = self.go("unsure-planned")
        self.assertEqual(rec["status"], "no_change", rec.get("problems"))

    def test_a_carried_gap_is_not_closed_by_quoting_the_harness_records_or_by_dropping_it(self):
        # independent review 0738 I3: meta/grades.jsonl repeats the gap sentence, so quoting it proves nothing
        carried = {"G8ec51935": ["terra-1109-062902", "terra-1109-074022"]}
        judge = {"metric": "m", "direction": "above", "threshold": "1", "min_sample": "1", "expires": "e", "undo": "u"}
        gap = {"missing": "G8ec51935 원가", "measurement": "G8ec51935 unit cost", "judge_line": judge}
        meta_quote = [{"ref": "meta/grades.jsonl:2", "excerpt": "G8ec51935의 원가 누락 1건"}]
        self.assertTrue(mh.gap_problems({"evidence_gap": gap, "evidence": meta_quote}, [], carried))
        log_quote = [{"ref": "logs/market-kit.log:3", "excerpt": "G8ec51935 unit u-0a1b2c3d4e5f cost 4100"}]
        self.assertEqual(mh.gap_problems({"evidence_gap": gap, "evidence": log_quote}, [], carried), [])
        dropped = {**gap, "missing": "원가 누락 1건", "measurement": "unit cost"}
        self.assertIn("no longer listed", mh.gap_problems({"evidence_gap": dropped, "evidence": meta_quote}, [], carried)[0])
        self.assertEqual(mh.gap_problems({"evidence_gap": dropped, "evidence": log_quote}, [], carried), [])
        # independent review 0738-meta I2: the meta/ test was on the ref's text; ./meta/ or an absolute path passed it
        with open(os.path.join(self.root, "meta", "grades.jsonl"), "w") as f:
            f.write("x\nG8ec51935의 원가 누락 1건\n")
        for ref in ("./meta/grades.jsonl:2", os.path.join(self.root, "meta", "grades.jsonl") + ":2",
                    "logs/../meta/grades.jsonl:2", "meta//grades.jsonl:2"):
            quote = [{"ref": ref, "excerpt": "G8ec51935의 원가 누락 1건"}]
            self.assertTrue(mh.gap_problems({"evidence_gap": gap, "evidence": quote}, [], carried), ref)
            self.assertTrue(mh.gap_problems({"evidence_gap": dropped, "evidence": quote}, [], carried), ref)
        if os.path.exists(os.path.join(self.root, "META")):           # a case-insensitive disk (the Mac's): Meta/ is meta/
            quote = [{"ref": "Meta/grades.jsonl:2", "excerpt": "G8ec51935의 원가 누락 1건"}]
            self.assertTrue(mh.gap_problems({"evidence_gap": gap, "evidence": quote}, [], carried))
        abs_log = [{"ref": os.path.join(self.root, "logs", "market-kit.log") + ":3", "excerpt": log_quote[0]["excerpt"]}]
        self.assertEqual(mh.gap_problems({"evidence_gap": gap, "evidence": abs_log}, [], carried), [])

    def test_native_commit_graph_finishes_before_both_fixture_git_helpers_return(self):
        # Real native work, not a config-value assertion. The deterministic
        # post-stdio actor barrier RED/GREEN is preserved in the review evidence.
        self.git("config", "maintenance.commit-graph.enabled", "true")
        self.git("config", "maintenance.commit-graph.auto", "1")
        self.git("commit", "--allow-empty", "-qm", "native graph fixture")
        chain = os.path.join(self.root, ".git", "objects", "info", "commit-graphs", "commit-graph-chain")
        with open(chain, "rb") as f:
            before = f.read()
        self.assertTrue(before)
        mh.git("commit", "--allow-empty", "-qm", "native graph harness")
        with open(chain, "rb") as f:
            after = f.read()
        self.assertNotEqual(before, after, "the harness Git helper must finish the new graph before returning")

    def test_another_jobs_staged_secret_or_pricing_edit_is_not_the_cycles(self):
        # independent review 0738 I1/I2: a cycle that changed nothing read the whole index and pricing.json against HEAD
        with open(os.path.join(self.root, "pricing.json"), "w") as f:
            f.write('{"seller": {"refuse_buyers": []}}\n')
        self.git("add", "pricing.json")
        self.git("commit", "-qm", "pricing")
        with open(os.path.join(self.root, "pricing.json"), "w") as f:
            f.write('{"seller": {"refuse_buyers": ["x"]}}\n')                 # another job's owner-setting edit
        with open(os.path.join(self.root, "other_secret.py"), "w") as f:
            f.write("k = 'sk-or-v1-" + "b" * 30 + "'\n")
        self.git("add", "other_secret.py")                                       # another job's staged file
        rec = self.go("nothing")
        self.assertEqual(rec["status"], "no_change", rec.get("problems"))
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other_secret.py")    # left staged, untouched
        rec = self.go("good")
        self.assertEqual((rec["status"], rec["files"], rec["settings"]), ("running", ["changed.py"], []), rec.get("problems"))
        self.assertIn("pricing.json", self.git("status", "--porcelain"))

    def test_a_runtime_switch_flipped_during_the_cycle_is_never_committed_or_undone(self):
        # independent review 0738-meta I1: 14d7cee (terra-1109-085220, rejected) committed seller_warranty_trial.json,
        # which the warranty A/B turned on while the model ran; a code cycle's undo would have turned the study off
        sw = os.path.join(self.root, "seller_warranty_trial.json")
        with open(sw, "w") as f:
            f.write('{"enabled": false}\n')
        self.git("add", "seller_warranty_trial.json")
        self.git("commit", "-qm", "switch off")
        start = self.git("rev-parse", "HEAD")
        on = {"seller_warranty_trial.json": '{"enabled": true}\n', ".market/buyer_food_shelf.json": '{"enabled": true}\n'}
        # the first cycle sees the switches flip while the model runs: refused, its code undone, the switches left on;
        # later cycles find them on already (FAKE_SIDE writes what is there) and run as usual
        for i, (mode, status) in enumerate((("good", "rejected"), ("broken", "rejected"), ("nothing", "no_change"),
                                            ("good", "running"))):
            with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps(on)}):
                rec = self.go(mode)
            self.assertEqual(rec["status"], status, rec.get("problems"))
            if i == 0:
                self.assertEqual(rec["switches_changed"], [".market/buyer_food_shelf.json", "seller_warranty_trial.json"])
                self.assertIn("not the model's to set", rec["problems"][0])
                self.assertFalse(self.exists())
            else:
                self.assertNotIn("switches_changed", rec)
            self.assertEqual(open(sw).read(), '{"enabled": true}\n', mode)      # the study stays on
            self.assertEqual(self.git("log", "--format=%H", f"{start}..", "--", "seller_warranty_trial.json"), "", mode)
        self.assertEqual(rec["files"], ["changed.py"])
        self.assertIn("seller_warranty_trial.json", self.git("status", "--porcelain"))
        self.assertEqual(open(os.path.join(self.root, ".market", "buyer_food_shelf.json")).read(), '{"enabled": true}\n')

    def test_normal_first_slot_control_flip_rejects_model_code_and_preserves_owner_setting(self):
        # A normal policy's own kill is a switch too; .market runtime staging
        # protection alone cannot detect a policy change while the model runs.
        relative = ".market/restock_first_slot_default.json"
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        off = json.dumps({"enabled": False, "policy": "first-slot-default-v1", "sku": "G022ce883"})
        with open(path, "w") as f:
            f.write(off)
        for enabled in (True, False):
            changed = json.dumps({"enabled": enabled, "policy": "first-slot-default-v1", "sku": "G022ce883"})
            with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({relative: changed})}):
                rec = self.go("good")
            self.assertEqual(rec["status"], "rejected", rec.get("problems"))
            self.assertEqual(rec["switches_changed"], [relative])
            self.assertIn("not the model's to set", rec["problems"][0])
            self.assertFalse(self.exists())
            with open(path) as f:
                self.assertEqual(f.read(), changed, "the owner's ON/OFF must not be undone")
            self.assertEqual(self.git("ls-files", "--", "changed.py"), "")

    def test_every_study_switch_is_watched(self):
        # independent re-review 0738-meta: 8 .market/ switches were missing from SWITCHES, so a model edit of them passed
        here, found = os.path.dirname(os.path.abspath(mh.__file__)), set()
        for name in os.listdir(here):
            if name.endswith(".py") and not name.startswith("test_"):
                text = open(os.path.join(here, name), encoding="utf-8").read()
                found |= set(re.findall(r"^Switch: (\.market/[\w/]+\.json)", text, re.M))
                found |= {".market/" + m for m in re.findall(r"^SWITCH = ['\"]([\w/]+\.json)['\"]", text, re.M)}
                found |= {".market/" + m for m in re.findall(r'self\.root ?/ ?["\'](\w+_trial\.json)["\']', text)}
        self.assertGreaterEqual(len(found), 9)
        self.assertEqual(found - set(mh.SWITCHES), set())
        # independent review 0738-isolate I: a study's root/"adopted" or root/"disabled" file also sets its arm
        markers = set()
        for name in os.listdir(here):
            if name.endswith(".py") and not name.startswith("test_"):
                text = open(os.path.join(here, name), encoding="utf-8").read()
                markers |= set(re.findall(r'root ?/ ?["\'](\w+)["\']\)\.(?:exists|is_file)\(\)', text))
        self.assertGreaterEqual(markers, {"adopted", "disabled"})
        self.assertEqual(markers - set(mh.SWITCH_MARKERS), set())

    def test_a_study_marker_made_while_the_model_ran_refuses_the_cycle(self):
        os.makedirs(os.path.join(self.root, ".market", "opening_cue"))
        with open(os.path.join(self.root, ".market", "opening_cue", "disabled"), "w") as f:
            f.write("")
        side = {".market/late_concession/adopted": ""}
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps(side)}):
            os.remove(os.path.join(self.root, ".market", "opening_cue", "disabled"))  # gone before the model ran: not it
            rec = self.go("good")
        self.assertEqual(rec["status"], "rejected", rec.get("problems"))
        self.assertEqual(rec["switches_changed"], [".market/late_concession/adopted"])
        self.assertTrue(os.path.exists(os.path.join(self.root, ".market", "late_concession", "adopted")))
        before = mh.switch_shas()
        os.makedirs(os.path.join(self.root, ".market", "opening_cue", "disabled"))     # a folder stops the study too
        self.assertEqual(mh.switch_shas()[".market/opening_cue/disabled"], "folder")
        self.assertNotIn(".market/opening_cue/disabled", before)

    def test_a_suite_over_the_time_limit_is_a_failure_even_if_its_group_is_gone(self):
        proc = mock.Mock(pid=2 ** 22 + 7)
        calls = iter([subprocess.TimeoutExpired("t", 900), ("", "slow")])
        def communicate(timeout=None):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r
        proc.communicate = communicate
        real = subprocess.Popen
        with mock.patch.object(mh.subprocess, "Popen", lambda cmd, **kw: proc if cmd[0] != "git" else real(cmd, **kw)), \
                mock.patch.object(mh.os, "killpg", side_effect=ProcessLookupError):
            ok, output = mh.run_tests()
        self.assertFalse(ok)
        self.assertIn("over 900 s", output)

    def test_an_undo_puts_pricing_back_as_the_cycle_found_it_not_as_head(self):
        # independent review 0738-meta I1: undo checked out the commit's parent, dropping another job's pricing.json edit
        # that was there before the cycle wrote over it
        pj = os.path.join(self.root, "pricing.json")
        with open(pj, "w") as f:
            f.write('{"seller": {}, "a": 1}\n')
        self.git("add", "pricing.json")
        self.git("commit", "-qm", "pricing")
        found = '{"seller": {}, "a": 2}\n'
        with open(pj, "w") as f:
            f.write(found)                                                     # another job's edit, left uncommitted
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({"pricing.json": '{"seller": {}, "a": 3}\n'})}):
            rec = self.go("broken")
        self.assertEqual(rec["status"], "rejected", rec.get("problems"))
        self.assertIn("revert(week-08)", self.git("log", "--format=%s"))
        self.assertEqual(open(pj).read(), found)
        self.assertFalse(self.exists())
        with open(pj, "w") as f:
            f.write('{"seller": {}, "a": 1}\n')                               # clean at the start: back to HEAD, as before
        with mock.patch.dict(os.environ, {"FAKE_SIDE": json.dumps({"pricing.json": '{"seller": {}, "a": 3}\n'})}):
            self.assertEqual(self.go("broken")["status"], "rejected")
        self.assertEqual(open(pj).read(), '{"seller": {}, "a": 1}\n')
        self.assertEqual(self.git("status", "--porcelain", "--", "pricing.json"), "")

    def test_the_schedule_survives_a_restart(self):
        grades = os.path.join(self.root, "meta", "grades.jsonl")
        t = lambda s: datetime.datetime.fromisoformat(s + "+09:00").timestamp()
        with open(grades, "w") as f:
            for c, at in (("terra-a", "2026-11-03T21:05:09"), ("terra-b", "2026-11-04T07:29:46")):
                f.write(json.dumps({"cycle": c, "time": at + "+0900", "status": "no_change"}) + "\n")
        with mock.patch.object(mh, "RUN_SECONDS", 3600), mock.patch.object(mh, "ASTRA_EVERY", 12 * 3600), \
                mock.patch.object(mh, "CANARY_SECONDS", 600):
            self.assertAlmostEqual(mh.due("terra", t("2026-11-04T07:50:00")), 2986, delta=1)   # not at once
            self.assertAlmostEqual(mh.due("astra", t("2026-11-04T07:50:00")), 4509, delta=1)   # 12 h after the first
            with open(grades, "a") as f:
                f.write(json.dumps({"cycle": "astra-c", "time": "2026-11-04T09:05:00+0900", "status": "running"}) + "\n")
            self.assertAlmostEqual(mh.due("astra", t("2026-11-04T10:00:00")), 11 * 3600 + 300, delta=1)

    def test_a_failed_test_is_named_in_the_problem(self):
        rec = self.go("broken")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("FAIL: test (test_x.T.test)", rec["problems"][0])

    def test_a_false_citation_undoes_the_change_and_keeps_the_attempt(self):
        rec = self.go("false-citation")
        self.assertEqual(rec["status"], "rejected")
        self.assertFalse(self.exists())
        subjects = self.git("log", "--format=%s")
        self.assertIn("wip(week-08)", subjects)
        self.assertIn("revert(week-08)", subjects)

    def test_no_citation_at_all_is_a_failure_too(self):
        self.assertEqual(self.go("no-citation")["status"], "rejected")
        self.assertFalse(self.exists())

    def test_failing_tests_undo_the_change(self):
        rec = self.go("broken")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("tests failed", rec["problems"][0])
        self.assertFalse(self.exists())

    def test_a_failed_watch_undoes_the_change(self):
        with mock.patch.object(mh, "canary", lambda: ["tracebacks +5"]):
            rec = self.go("good")
        self.assertEqual(rec["status"], "canary_failed")
        self.assertFalse(self.exists())

    def test_a_failed_watch_undoes_astras_change_while_the_kit_writes_its_log(self):
        log = os.path.join(self.root, "logs", "market-kit.log")
        open(log, "a").write("10:01:00 [acp] settled y at 6\n")        # Astra's commit carries the log

        def watch():
            open(log, "a").write("10:05:00 [acp] settled z at 7\n")    # and the kit goes on writing it
            return ["tracebacks +5"]
        with mock.patch.object(mh, "canary", watch):
            rec = self.go("good", kind="astra")
        self.assertEqual(rec["status"], "canary_failed")
        self.assertFalse(self.exists())
        self.assertIn("settled z", open(log).read())
        self.assertIn("revert(week-08)", self.git("log", "--format=%s"))
        self.assertIn('"canary_failed"', open(os.path.join(self.root, "meta", "grades.jsonl")).read())

    def change_that_deletes(self):
        open(os.path.join(self.root, "gone.py"), "w").write("y = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "gone.py")
        os.remove(os.path.join(self.root, "gone.py"))
        open(os.path.join(self.root, "changed.py"), "w").write("x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        return self.git("rev-parse", "HEAD")

    def test_undo_brings_back_a_deleted_file_and_drops_an_added_one(self):
        sha = self.change_that_deletes()
        with mock.patch("builtins.print"):
            mh.undo(sha, "why")
        self.assertTrue(os.path.exists(os.path.join(self.root, "gone.py")))
        self.assertFalse(self.exists())
        self.assertEqual(self.git("status", "--porcelain", "--", ".", ":!logs"), "")

    def test_an_undo_that_fails_leaves_the_change_whole_and_is_recorded(self):
        sha = self.change_that_deletes()
        real = mh.git

        def git(*a, **k):
            if a[0] == "commit":
                raise RuntimeError("git commit: refused")
            return real(*a, **k)
        with mock.patch.object(mh, "git", git), mock.patch("builtins.print"):
            self.assertIn("undo failed", mh.try_undo("terra-x", sha, "why"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "gone.py")))
        self.assertTrue(self.exists())
        self.assertEqual(self.git("status", "--porcelain", "--", ".", ":!logs"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), sha)

    def test_the_watch_counts_across_the_kits_log_rotation(self):
        log = os.path.join(self.root, "logs", "market-kit.log")
        open(log, "a").write("".join(f"10:00:{i:02d} [acp] settled x at 5\n" for i in range(10)))
        clock = [0.0]

        def sleep(s):
            clock[0] += s
            if not os.path.exists(log + ".1"):                       # the kit moves its log at 20 MB
                os.replace(log, log + ".1")
                open(log, "w").write("10:09:00 [acp] settled y at 6\n")
        fake_time = mock.Mock(time=lambda: clock[0], sleep=sleep)
        with mock.patch.object(mh, "time", fake_time), mock.patch.object(mh, "KIT_PATTERN", "kit"), \
                mock.patch.object(mh, "kit_alive", lambda: True):
            self.assertEqual(mh.canary(60), [])
            self.assertEqual(mh.canary(60), ["the kit's log did not grow"])   # no move, no new line: still caught

    def test_a_noise_block_open_at_the_start_does_not_hide_new_tracebacks(self):
        # GPT Pro 11/06 04:44 7-1: 2 tracebacks of an open detach block at the start, reread as noise at the end, took 2
        # off 3 real new ones and the watch passed under its +3 limit
        log = os.path.join(self.root, "logs", "market-kit.log")
        open(log, "a").write("10:00:00 [seller] Failed to detach context\n10:00:00 [seller] Traceback (most recent call last):\n"
                             "10:00:00 [seller]   File \"/v/opentelemetry/trace/__init__.py\", line 602, in use_span\n"
                             "10:00:00 [seller] GeneratorExit\n10:00:00 [seller] Traceback (most recent call last):\n")
        clock = [0.0]

        def sleep(s):
            clock[0] += s
            if clock[0] > s:
                return
            with open(log, "a") as f:
                f.write("10:00:00 [seller] ValueError: <Token> was created in a different Context\n")
                f.write("".join(f"10:01:0{i} [restock] Traceback (most recent call last):\n10:01:0{i} [restock] KeyError: 'x'\n"
                                for i in range(3)))
        fake_time = mock.Mock(time=lambda: clock[0], sleep=sleep)
        with mock.patch.object(mh, "time", fake_time), mock.patch.object(mh, "KIT_PATTERN", "kit"), \
                mock.patch.object(mh, "kit_alive", lambda: True):
            self.assertEqual(mh.canary(60), ["tracebacks +3"])

    def test_secrets_never_enter_history_even_as_an_attempt(self):
        for mode in ("secret", "env-secret"):
            self.assertEqual(self.go(mode)["status"], "discarded_secret")
            self.assertFalse(self.exists())
        self.assertNotIn("sk-or-v1", self.git("log", "-p", "--all"))
        self.assertNotIn("value-from-env-file", self.git("log", "-p", "--all"))

    def test_a_refused_model_is_said_plainly_and_nothing_breaks(self):
        rec = self.go("refused")
        self.assertEqual(rec["status"], "model_error")
        self.assertIn("not supported", rec["reason"])

    def test_a_model_that_dies_mid_edit_leaves_no_change_live(self):
        self.assertEqual(self.go("edit-then-die")["status"], "rejected")
        self.assertFalse(self.exists())

    def test_no_change_is_allowed_but_its_citations_count_too(self):
        self.assertEqual(self.go("nothing")["status"], "no_change")
        self.assertEqual(self.go("nothing-false")["status"], "no_change_unsupported")

    def test_the_kit_log_is_not_committed_by_terra(self):
        with open(os.path.join(self.root, "logs", "market-kit.log"), "a") as f:
            f.write("10:00:02 more\n")
        self.go("good")
        self.assertNotEqual(self.git("status", "--short", "logs/market-kit.log"), "")

    def test_the_last_hour_is_read_from_the_harness_pack_only(self):
        # the models save their own audits in meta/evidence/ too; one of those is not the previous scorecard
        folder = os.path.join(self.root, "meta", "evidence")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "terra-1104-095319.json"), "w") as f:
            json.dump({"log_counts": {"lines": 10, "sold": 4}}, f)
        with open(os.path.join(folder, "terra-1104-095319-audit.json"), "w") as f:
            json.dump([{"not": "a pack"}], f)
        later = time.time() + 5
        os.utime(os.path.join(folder, "terra-1104-095319-audit.json"), (later, later))
        got = mh.since_last({"log_counts": {"lines": 15, "sold": 6}})
        self.assertEqual((got["previous"], got["log_counts"]), ("terra-1104-095319.json", {"lines": 5, "sold": 2}))

    def test_astra_commits_no_logs_or_memory(self):
        # eba7dd67: Astra's commit carried 64 MB of kit logs, a Round 2 archive and the shop's memory dumps
        os.makedirs(os.path.join(self.root, "memory"))
        for p in ("logs/market-kit.log", "logs/astra-tests.log", "memory/seller_memory_r3.json"):
            with open(os.path.join(self.root, p), "a") as f:
                f.write("10:00:02 more\n")
        rec = self.go("good", kind="astra")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        committed = self.git("log", "--name-only", "--format=", "HEAD~2..HEAD").split()
        self.assertIn("changed.py", committed)
        self.assertEqual([p for p in committed if p.startswith(("logs/", "memory/"))], [])

    def grades(self):
        with open(os.path.join(self.root, "meta", "grades.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def strategy(self):
        with open(os.path.join(self.root, "meta", "strategy.md"), encoding="utf-8") as f:
            return f.read()

    def test_the_strategy_check_is_kept_in_grades_and_changes(self):
        self.go("good")
        self.assertEqual(self.grades()[-1]["strategy_check"]["keep_or_shift"], "keep")
        with open(os.path.join(self.root, "meta", "changes.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["answer"]["strategy_check"]["current"], "lean")

    def test_a_shift_on_one_hour_alone_is_undone(self):
        rec = self.go("shift")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("previous accepted Terra cycle", " ".join(rec["problems"]))
        self.assertNotIn("shifted", self.strategy())

    def test_a_shift_after_a_watch_is_applied_without_tests_or_watch(self):
        with open(os.path.join(self.root, "test_bad.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail('old')\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a failing test from elsewhere")
        self.assertEqual(self.go("watch")["status"], "no_change")
        with mock.patch.object(mh, "canary", lambda: ["tracebacks +5"]):
            rec = self.go("shift")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertIn("shifted", self.strategy())
        self.assertEqual(self.grades()[-1]["action"], "strategy_shift")

    def test_a_shift_must_write_the_strategy_down(self):
        self.go("watch")
        rec = self.go("shift-no-doc")
        self.assertIn(f"a strategy shift must rewrite {mh.STRATEGY}", rec["problems"])

    def test_a_refused_shift_is_no_evidence_for_the_next(self):
        self.assertEqual(self.go("shift")["status"], "rejected")
        self.assertEqual(self.go("shift")["status"], "rejected")
        self.assertNotIn("shifted", self.strategy())

    def test_a_shift_stands_before_the_next(self):
        self.go("watch")
        self.assertEqual(self.go("shift")["status"], "running")
        self.go("watch")
        rec = self.go("shift")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("stands", " ".join(rec["problems"]))

    def test_terra_does_not_rewrite_the_strategy_without_a_shift(self):
        rec = self.go("strategy-doc")
        self.assertEqual(rec["status"], "rejected")
        self.assertNotIn("shifted", self.strategy())

    def test_astra_sets_the_strategy_without_tests_or_watch(self):
        with open(os.path.join(self.root, "test_bad.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail('old')\n")
        with open(os.path.join(self.root, "meta", "strategy.md"), "w") as f:
            f.write("prompt\nkeep inside hard_limits\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a failing test from elsewhere")
        with mock.patch.object(mh, "canary", lambda: ["tracebacks +5"]):
            rec = self.go("strategy-doc", kind="astra")
        self.assertEqual((rec["status"], rec["files"]), ("running", [mh.STRATEGY]), rec.get("problems"))
        self.assertIn("shifted", self.strategy())

    def test_astra_records_its_chosen_strategy(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "nothing"}), mock.patch("builtins.print"):
            mh.cycle("astra")
        self.assertEqual(self.grades()[-1]["strategy"]["chosen"], "lean")

    def test_only_this_folder_is_staged(self):
        sub = os.path.join(self.outer, "mono")
        os.makedirs(os.path.join(sub, "inner", "logs"))
        with open(os.path.join(sub, "other.txt"), "w") as f:
            f.write("a")
        subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
        with open(os.path.join(sub, "inner", "x.py"), "w") as f:
            f.write("a")
        with mock.patch.object(mh, "ROOT", os.path.join(sub, "inner")):
            self.assertEqual(mh.stage(), ["x.py"])

    def test_staging_works_with_logs_and_memory_ignored_and_keeps_tracked_evidence_out(self):
        # 11/04 11:07-16:12 every Terra cycle failed: `git add -A -- . :!logs :!memory` exits 1 once both are in .gitignore
        sub = os.path.join(self.outer, "ign")
        inner = os.path.join(sub, "inner")
        for d in ("logs", "memory"):
            os.makedirs(os.path.join(inner, d))
        subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
        for name, text in ((".gitignore", "/logs/\n/memory/\n"), ("logs/evidence.txt", "a"), ("memory/r2.json", "{}")):
            with open(os.path.join(inner, name), "w") as f:
                f.write(text)
        subprocess.run(["git", "add", "-f", ".gitignore", "logs/evidence.txt", "memory/r2.json"], cwd=inner, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=inner, check=True)
        for name in ("logs/evidence.txt", "logs/kit.log", "memory/r2.json", "pricing.json"):
            with open(os.path.join(inner, name), "a") as f:
                f.write("more")
        with mock.patch.object(mh, "ROOT", inner):
            self.assertEqual(mh.stage(), ["pricing.json"])


class ForumTest(unittest.TestCase):
    POSTS = [{"id": "p1", "author": "other", "text": "a"}, {"id": "p2", "author": "nocha12", "text": "mine"}, {"id": 3, "author": "x", "text": "b"}]
    CFG = forum.DEFAULTS

    def clean(self, final, voted=()):
        base = {"post": "", "refs": [], "thread": "", "votes": []}
        return forum.clean({**base, **final}, self.POSTS, {10, 11}, set(voted), self.CFG, "nocha12")

    def test_a_post_needs_real_deals_to_cite(self):
        self.assertEqual(self.clean({"post": "x sells", "refs": [10, 99]})[0], [{"post": "x sells", "refs": [10], "as": "buyer"}])
        with mock.patch("builtins.print"):
            self.assertEqual(self.clean({"post": "x sells", "refs": [99]})[0], [])
            self.assertEqual(self.clean({"post": "x sells"})[0], [])
        self.assertEqual(self.clean({"post": "", "refs": [10]})[0], [])

    def test_a_reply_goes_to_a_post_that_exists(self):
        self.assertEqual(self.clean({"post": "y", "refs": [10], "thread": "p1"})[0][0]["thread"], "p1")
        self.assertNotIn("thread", self.clean({"post": "y", "refs": [10], "thread": "nope"})[0][0])

    def test_votes_go_once_to_real_posts_of_others(self):
        votes = [{"post": "p1", "value": 1}, {"post": "p1", "value": -1}, {"post": "p2", "value": 1}, {"post": "gone", "value": 1},
                 {"post": "3", "value": -1}]
        lines, new = self.clean({"votes": votes})
        # patch 3.4: dislikes past 3 an hour cost the shop cash, so a -1 is never sent
        self.assertEqual(lines, [{"vote": "p1", "value": 1, "as": "buyer"}])
        self.assertEqual(new, ["p1"])
        self.assertEqual(self.clean({"votes": votes}, voted=["p1", "3"])[0], [])

    def test_one_like_per_author(self):
        posts = [{"id": "q1", "author": "x"}, {"id": "q2", "author": "x"}, {"id": "q3", "author": "y"}]
        final = {"post": "", "refs": [], "thread": "", "votes": [{"post": p["id"], "value": 1} for p in posts]}
        self.assertEqual(forum.clean(final, posts, set(), set(), self.CFG, "nocha12")[1], ["q1", "q3"])

    def test_no_dislike_is_sent_or_offered(self):
        lines, new = self.clean({"votes": [{"post": "p1", "value": -1}, {"post": "3", "value": -1}]})
        self.assertEqual((lines, new), ([], []))
        enum = forum.FINAL["properties"]["votes"]["items"]["properties"]["value"]["enum"]
        self.assertEqual(enum, [1])
        self.assertIn("50 settled deals", forum.GOAL)
        self.assertNotIn("-1 on false", forum.GOAL)

    def test_the_vote_limit_and_length_limit_hold(self):
        posts = [{"id": str(i), "author": f"o{i}"} for i in range(30)]
        final = {"post": "z" * 2000, "refs": [10], "thread": "", "votes": [{"post": str(i), "value": 1} for i in range(30)]}
        lines, new = forum.clean(final, posts, {10}, set(), self.CFG, "nocha12")
        self.assertEqual(len(new), self.CFG["max_votes"])
        self.assertEqual(len(lines[0]["post"]), self.CFG["max_chars"])

    def run_it(self, d, final):
        calls = []

        def decide(goal, schema, md, agent, given=None):
            calls.append(goal)
            return final
        with mock.patch("builtins.print"):
            return forum.maybe_run(d, decide), calls

    def test_keeps_its_pace_writes_the_outbox_and_remembers(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "forum.json"), "w") as f:
                json.dump(self.POSTS, f)
            with open(os.path.join(d, mm.ROWS), "w") as f:
                f.write(json.dumps({"k": "settle", "seq": 10}) + "\n")
            lines, calls = self.run_it(d, {"post": "true", "refs": [10], "thread": "", "votes": [{"post": "p1", "value": 1}]})
            self.assertEqual(len(lines), 2)
            with open(os.path.join(d, "outbox.jsonl")) as f:
                self.assertEqual([json.loads(x)["as"] for x in f], ["buyer", "buyer"])
            self.assertEqual(self.run_it(d, {"post": "again", "refs": [10], "thread": "", "votes": []}), (None, []))
            with open(os.path.join(d, forum.STATE)) as f:
                state = json.load(f)
            self.assertEqual((state["voted"], state["posts"]), (["p1"], ["true"]))
            state["at"] = 0
            with open(os.path.join(d, forum.STATE), "w") as f:
                json.dump(state, f)
            _, calls = self.run_it(d, {"post": "", "refs": [], "thread": "", "votes": []})
            self.assertIn("true", calls[0])
            self.assertIn("p1", calls[0])

    def test_a_failure_stays_inside_and_waits_for_the_next_turn(self):
        def boom(*a):
            raise harness.HarnessError("rate limited")
        with tempfile.TemporaryDirectory() as d, mock.patch("builtins.print"):
            self.assertIsNone(forum.maybe_run(d, boom))
            self.assertIsNone(forum.maybe_run(d, lambda *a: self.fail("asked again at once")))


class FindCodexTest(unittest.TestCase):
    def fake(self, d, name, version):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(f"#!/bin/sh\necho 'codex-cli {version}'\n")
        os.chmod(p, 0o755)
        return p

    def test_the_newest_wins_over_what_is_on_path(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ):
            os.environ.pop("META_CODEX", None)
            old, new = self.fake(d, "old", "0.153.2"), self.fake(d, "new", "0.165.0-alpha.1.2")
            with mock.patch.object(mh.shutil, "which", lambda n: old), mock.patch.object(mh, "CHATGPT_CODEX", new):
                self.assertEqual(mh.find_codex(), new)
            with mock.patch.object(mh.shutil, "which", lambda n: new), mock.patch.object(mh, "CHATGPT_CODEX", os.path.join(d, "gone")):
                self.assertEqual(mh.find_codex(), new)
            os.environ["META_CODEX"] = old
            self.assertEqual(mh.find_codex(), old)


class EvidenceTest(unittest.TestCase):
    def test_citations(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.log"), "w") as f:
                f.write("first line\nsecond line here\n")
            ok = {"ref": "a.log:2", "excerpt": "second line"}
            self.assertEqual(mh.verify_evidence([ok], d), [])
            for bad in ({"ref": "a.log:1", "excerpt": "second line"}, {"ref": "a.log:9", "excerpt": "second line"},
                        {"ref": "nope.log:1", "excerpt": "first"}, {"ref": "a.log", "excerpt": "first"},
                        {"ref": "../a.log:1", "excerpt": "first"}, {"ref": "a.log:1", "excerpt": "a"}):
                self.assertTrue(mh.verify_evidence([bad], d), bad)
            self.assertTrue(mh.verify_evidence([], d))

    def test_a_short_quote_is_good_only_as_the_whole_line(self):
        # unittest ends with a line "OK": 11/08 12 Terra cycles were reverted for citing it (GPT Pro 11/08 23:0x §7.1)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "t.log"), "w") as f:
                f.write("Ran 1143 tests in 78.480s\n\nOK\nNOT OK\n")
            self.assertEqual(mh.verify_evidence([{"ref": "t.log:3", "excerpt": "OK"}], d), [])
            self.assertTrue(mh.verify_evidence([{"ref": "t.log:4", "excerpt": "OK"}], d))
            self.assertTrue(mh.verify_evidence([{"ref": "t.log:3", "excerpt": ""}], d))

    def test_anchoring_finds_the_line_that_holds_the_quote_and_never_loosens_the_check(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "t.log"), "w") as f:
                f.write("NOT OK\nRan 1180 tests in 126.230s\n\nOK\nx\nRan 1180 tests in 126.230s\n")
            items = [{"ref": "t.log:1", "excerpt": "OK"}, {"ref": "t.log:5", "excerpt": "Ran 1180 tests in 126.230s"},
                     {"ref": "t.log:2", "excerpt": "Ran 1180 tests"}, {"ref": "t.log:1", "excerpt": "never said"},
                     {"ref": "t.log:1", "excerpt": "x"}, {"ref": "../t.log:1", "excerpt": "OK"}]
            notes = mh.anchor_evidence(items, d)
            # "OK" only as a whole line (never NOT OK), the nearest of two, a line already right stays, no invented quote
            self.assertEqual([i["ref"] for i in items], ["t.log:4", "t.log:6", "t.log:2", "t.log:1", "t.log:5", "../t.log:1"])
            self.assertEqual(notes, ["t.log:1 -> t.log:4", "t.log:5 -> t.log:6", "t.log:1 -> t.log:5"])
            self.assertEqual(len(mh.verify_evidence(items, d)), 2)       # the invented quote and the outside path

    def test_gap_ids_are_units_and_skus(self):
        self.assertEqual(mh.gap_ids("G8ec51935 원가, u-d9db11f14a2a 판매, Gx, u-12"), {"G8ec51935", "u-d9db11f14a2a"})


class RivalsTest(unittest.TestCase):
    def test_the_shops_ahead_in_profit_and_my_rank(self):
        rows = [{"name": "a", "profit": 10, "deals": 3, "card": {"description": "x" * 500}, "owner": {"login": "a"}},
                {"name": mh.scorecard.ME, "profit": 5}, {"name": "b", "profit": 20, "card": "odd"}, {"name": "c"}]
        r = mh.scorecard.rivals(rows, top=2)
        self.assertEqual((r["my_rank"], r["of"]), (3, 3))
        self.assertEqual([x["name"] for x in r["top"]], ["b", "a"])
        self.assertEqual((len(r["top"][1]["card"]), r["top"][0]["card"]), (240, ""))
        self.assertNotIn("owner", r["top"][1])


class ObjectiveTest(unittest.TestCase):
    def test_the_board_formula(self):
        # the board's own: rows it scores (all set, a shop with supplies and no deal too), mean rank on ties, stars_rank,
        # posts 0.1 of the buyer (review/W-20261104-2030: every row of 268 Round 3 snapshots within 0.0005)
        me = mh.scorecard.ME
        b = {"shops": [{"name": me, "deals": 4, "profit": 10, "revenue": 100, "stars": 5, "stars_rank": 4.5, "all": 0.7},
                       {"name": "a", "deals": 9, "profit": 20, "revenue": 50, "stars": 4, "stars_rank": 4.5, "all": 0.5},
                       {"name": "b", "deals": 0, "profit": 0, "revenue": 0, "stars": None, "stars_rank": None, "all": 0.1},
                       {"name": "c", "deals": 0, "profit": 1000, "revenue": 1000, "all": None}],
             "buyers": [{"name": me, "deals": 2, "saved": 0.1, "surplus": 100, "forum": 0.6, "posts": 3, "all": 0.38},
                        {"name": "x", "deals": 1, "saved": 0.2, "surplus": 1, "forum": 0.6, "posts": 0, "all": 0.65}],
             "players": [{"player": "Nocha12", "total": 1.08}]}
        o = mh.scorecard.objective(b)
        self.assertEqual((o["shops"]["all"], o["shops"]["of"], o["shops"]["board_all"]), (0.7, 3, 0.7))
        self.assertEqual(o["shops"]["parts"]["stars_rank"]["percentile"], 0.75)
        self.assertEqual((o["buyers"]["all"], o["players"]), (0.38, 1.08))
        self.assertEqual(o["weakest"][:2], ("buyers", "saved"))


class AlertProblemsTest(unittest.TestCase):
    def test_no_change_must_name_every_open_alert(self):
        card = {"alerts": ["llm_cap: 4 calls refused", "surge: 2 unit(s)"]}
        answer = {"grade_summary": "alert llm_cap: refusals are the seller at its own cap; measured next hour; surge seen"}
        self.assertEqual(mh.alert_problems(card, answer, []), ["alert left open with no change: surge: 2 unit(s)"])
        self.assertEqual(mh.alert_problems(card, answer, ["restock.py"]), [])
        self.assertEqual(mh.alert_problems({"alerts": []}, answer, []), [])


class ExperimentQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "meta"))
        p = mock.patch.object(mh, "ROOT", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": [{"id": "a", "hours": 2}, {"id": "b"}, "junk"]}, f)

    def answer(self, step, eid="", result=""):
        return {"experiment": {"step": step, "id": eid, "result": result}}

    def test_one_at_a_time_in_order_and_judged_when_due(self):
        v = mh.experiments()
        self.assertEqual((v["running"], v["next"]["id"], v["waiting"]), (None, "a", 2))
        p = lambda step, eid="", result="", view=None: mh.experiment_problems("terra", self.answer(step, eid, result), view or mh.experiments())
        self.assertTrue(p("start", "b"))                    # not the next one
        self.assertTrue(p("none"))                          # idle with a next one and no reason
        self.assertEqual(p("none", result="cash 3.9M of 4M: no room for it until 12:40"), [])
        self.assertTrue(p("start", "a"))                    # a start with no change in the cycle
        self.assertTrue(mh.experiment_problems("terra", self.answer("start", "a"), mh.experiments(), [mh.EXPERIMENTS]))
        self.assertEqual(mh.experiment_problems("terra", self.answer("start", "a"), mh.experiments(), ["pricing.json"]), [])
        mh.note_experiment("terra-1", self.answer("start", "a"))
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:     # removing or stretching it changes nothing
            json.dump({"queue": [{"id": "b"}]}, f)
        v = mh.experiments()
        self.assertEqual((v["running"]["id"], v["running"]["hours"], v["running"]["expired"], v["next"]["id"]),
                         ("a", 2, False, "b"))
        self.assertTrue(p("start", "b"))                    # a still runs
        self.assertEqual(p("continue", "a"), [])
        self.assertEqual(p("none"), [])
        later = mh.experiments(now=time.time() + 3 * 3600)
        self.assertTrue(p("none", view=later))              # past its 2 h: must be judged
        self.assertTrue(p("continue", "a", view=later))
        self.assertTrue(p("drop", "a", view=later))         # a judgment needs its number
        self.assertEqual(p("drop", "a", "forum 0.51 < 0.55 on 22 posts", view=later), [])
        mh.note_experiment("terra-2", self.answer("drop", "a", "forum 0.51 < 0.55 on 22 posts"))
        v = mh.experiments()
        self.assertEqual((v["running"], v["next"]["id"], v["closed"]), (None, "b", {"a": "drop"}))
        self.assertEqual(mh.experiment_problems("astra", self.answer("start", "zzz"), v), [])

    def test_no_queue_file_asks_nothing(self):
        os.remove(os.path.join(self.tmp.name, mh.EXPERIMENTS))
        self.assertEqual(mh.experiment_problems("terra", self.answer("none"), mh.experiments()), [])
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": 5}, f)
        self.assertIsNone(mh.experiments()["next"])
        self.assertEqual(mh.hours_of({"hours": 1e300}), 12)

    def test_terra_only_appends_and_never_writes_the_log(self):
        g = lambda *a: subprocess.run(["git", *a], cwd=self.tmp.name, check=True, capture_output=True)
        g("init", "-q")
        g("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "q")
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": [{"id": "a", "hours": 2}, {"id": "b"}, {"id": "c"}]}, f)
        self.assertEqual(mh.queue_problems("terra", [mh.EXPERIMENTS]), [])
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": [{"id": "b"}, {"id": "a", "hours": 2}]}, f)
        self.assertTrue(mh.queue_problems("terra", [mh.EXPERIMENTS]))
        self.assertEqual(mh.queue_problems("astra", [mh.EXPERIMENTS]), [])
        with open(os.path.join(self.tmp.name, mh.EXPERIMENT_LOG), "w") as f:
            f.write(json.dumps({"ts": 1, "id": "b", "step": "done"}) + "\n")
        g("add", "-A")
        self.assertTrue(mh.queue_problems("terra", [mh.EXPERIMENT_LOG]))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, mh.EXPERIMENT_LOG)))


class StaleCodeTest(unittest.TestCase):
    # 11/09: 2327f1c fixed verify_evidence at 23:44, but the loop started 11/08 14:08 kept the old function and
    # terra-1109-002601 and astra-1109-011816 were reverted for quoting a whole "OK" line (GPT Pro 11/09 01:39 §6.3)
    def test_a_changed_source_is_named_and_an_unchanged_one_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.py"), os.path.join(d, "b.py")
            for p in (a, b):
                with open(p, "w") as f:
                    f.write("x = 1\n")
            stamp = mh.code_stamp([a, b])
            self.assertEqual(mh.stale_code(stamp), [])
            with open(b, "w") as f:
                f.write("x = 2\n")
            self.assertEqual(mh.stale_code(stamp), ["b.py"])
            os.remove(a)
            self.assertEqual(mh.stale_code(stamp), ["a.py", "b.py"])

    def test_the_loaded_sources_are_this_folders_modules(self):
        names = {os.path.basename(p) for p in mh.loaded_sources()}
        self.assertTrue({"meta_harness.py", "hard_limits.py", "scorecard.py"} <= names)
        self.assertFalse(any(not os.path.realpath(p).startswith(os.path.realpath(mh.ROOT) + os.sep)
                             for p in mh.loaded_sources()))

    def test_tried_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(mh, "TRIED", os.path.join(d, "tried.json")):
            self.assertEqual(mh.load_tried(), {})
            mh.save_tried({"terra": 123.5})
            self.assertEqual(mh.load_tried(), {"terra": 123.5})
            with open(mh.TRIED, "w") as f:
                f.write("{not json")
            self.assertEqual(mh.load_tried(), {})
            with open(mh.TRIED, "w") as f:        # independent review: Infinity, the future or a huge number never stalls
                f.write('{"terra": Infinity, "astra": 1e400, "x": %d, "y": 99999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999}' % (time.time() + 3600))
            self.assertEqual(mh.load_tried(), {})

    def test_new_code_that_does_not_compile_keeps_the_old_loop(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.py")
            with open(a, "w") as f:
                f.write("x = 1\n")
            stamp = mh.code_stamp([a])
            with open(a, "w") as f:
                f.write("def (:\n")
            with mock.patch.object(mh, "log") as log:
                mh.restart_if_stale(stamp)                         # no SystemExit
            self.assertIn("does not compile", log.call_args[0][0])
            with open(a, "w") as f:
                f.write("x = 2\n")
            with mock.patch.object(mh, "log"), self.assertRaises(SystemExit):
                mh.restart_if_stale(stamp)

    def test_missing_loaded_source_keeps_the_loop_and_logs_once_until_repaired(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "required.py")
            with open(source, "w") as f:
                f.write("x = 1\n")
            stamp = mh.code_stamp([source])
            os.remove(source)
            with mock.patch.object(mh, "log") as log:
                mh.restart_if_stale(stamp)  # No SystemExit, even if the file remains missing.
                mh.restart_if_stale(stamp)
                self.assertEqual(log.call_count, 1)
                self.assertIn("staying on the old code", log.call_args[0][0])
            with open(source, "w") as f:
                f.write("x = 2\n")
            with mock.patch.object(mh, "log"), self.assertRaises(SystemExit) as e:
                mh.restart_if_stale(stamp)
            self.assertEqual(e.exception.code, 0)

    def test_the_loop_exits_between_cycles_when_its_code_changed(self):
        # Terra changes real source: Astra must not start with the old imports.
        calls = []
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "dependency.py")
            with open(source, "w") as f:
                f.write("x = 1\n")

            def cycle(kind):
                calls.append(kind)
                with open(source, "w") as f:
                    f.write("x = 2\n")

            with \
                mock.patch.object(mh, "TRIED", os.path.join(d, "tried.json")), \
                mock.patch.object(mh, "loaded_sources", return_value=[source]), \
                mock.patch.object(mh, "market_round", return_value=3), \
                mock.patch.object(mh, "due", return_value=0), \
                mock.patch.object(mh, "cycle", side_effect=cycle), \
                mock.patch.object(mh, "push"), mock.patch.object(mh, "log"), \
                mock.patch.object(mh.signal, "signal"):
                with self.assertRaises(SystemExit) as e:
                    mh.run()
                self.assertEqual(e.exception.code, 0)
                self.assertEqual(calls, ["terra"])
                self.assertEqual(set(mh.load_tried()), {"terra"})


if __name__ == "__main__":
    unittest.main()
