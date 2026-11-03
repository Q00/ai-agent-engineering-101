"""The Luna agent loop and the meta-harness. The fake codex is a script that edits a throwaway git repository.

    python -m unittest test_meta -v        (from submissions/26622007/week-08)
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
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
if mode == "refused":
    print("The 'gpt-6.1-terra' model is not supported", file=sys.stderr)
    sys.exit(1)
if mode == "edit-then-die":
    open("changed.py", "w").write("x = 1\\n")
    sys.exit(2)
if mode != "nothing" and mode != "nothing-false":
    open("changed.py", "w").write({"broken": "raise SystemExit(1)\\n", "secret": "k = 'sk-or-v1-" + "a" * 30 + "'\\n",
                                   "env-secret": "k = 'value-from-env-file-1234'\\n"}.get(mode, "x = 1\\n"))
if mode == "broken":
    open("test_x.py", "w").write("import unittest\\nclass T(unittest.TestCase):\\n    def test(self):\\n        self.fail('no')\\n")
good = [{"ref": "logs/market-kit.log:2", "excerpt": "[acp] settled", "meaning": "m"}]
bad = [{"ref": "logs/market-kit.log:2", "excerpt": "never said this", "meaning": "m"}]
json.dump({"changed": True, "hypothesis": "h", "change_summary": "변경 요약", "target_metric": "profit", "expected": "up",
           "evidence": bad if mode in ("false-citation", "nothing-false") else [] if mode == "no-citation" else good,
           "previous_verdict": "none", "previous_reason": ""}, open(out, "w"))
"""


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
        for name in ("terra", "astra"):
            with open(os.path.join(self.root, "meta", f"{name}.md"), "w") as f:
                f.write("prompt")
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
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        patches = [mock.patch.object(mh, "ROOT", self.root), mock.patch.object(mh, "KIT_PATTERN", ""),
                   mock.patch.object(mh, "CANARY_SECONDS", 0), mock.patch.dict(os.environ, {"META_CODEX": fake}),
                   mock.patch.object(mh.scorecard, "board", lambda: {"round": 3})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def go(self, mode, kind="terra"):
        with mock.patch.dict(os.environ, {"FAKE_MODE": mode}), mock.patch("builtins.print"):
            return mh.cycle(kind)

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

    def test_the_kit_log_is_not_committed_by_terra_but_is_by_astra(self):
        with open(os.path.join(self.root, "logs", "market-kit.log"), "a") as f:
            f.write("10:00:02 more\n")
        self.go("good")
        self.assertNotEqual(self.git("status", "--short", "logs/market-kit.log"), "")

    def test_astra_commits_the_logs_and_saves_rotated_ones(self):
        with open(os.path.join(self.root, "logs", "market-kit.log"), "a") as f:
            f.write("10:00:02 more\n")
        with open(os.path.join(self.root, "logs", "market-kit.log.1"), "w") as f:
            f.write("old part\n")
        with mock.patch.dict(os.environ, {"FAKE_MODE": "nothing"}), mock.patch("builtins.print"), \
                mock.patch.object(mh, "SCHEMAS", {**mh.SCHEMAS, "astra": mh.SCHEMAS["terra"]}):
            mh.cycle("astra")
        self.assertEqual(self.git("status", "--short", "logs/market-kit.log"), "")
        self.assertTrue(any(n.startswith("market-kit-rotated-") for n in os.listdir(os.path.join(self.root, "logs"))))

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
        self.assertEqual(lines, [{"vote": "p1", "value": 1, "as": "buyer"}, {"vote": 3, "value": -1, "as": "buyer"}])
        self.assertEqual(new, ["p1", "3"])
        self.assertEqual(self.clean({"votes": votes}, voted=["p1", "3"])[0], [])

    def test_the_vote_limit_and_length_limit_hold(self):
        posts = [{"id": str(i), "author": "o"} for i in range(30)]
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


if __name__ == "__main__":
    unittest.main()
