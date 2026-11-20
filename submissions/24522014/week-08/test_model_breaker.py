"""The breaker turns the model off on limits, on old or unreadable usage and on damaged state, and recovers by itself."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import model_breaker as mb

NOW = 1793800000.0
USAGE = {"five_hour_percent": 40, "five_hour_resets_at": NOW + 3600, "weekly_percent": 9, "weekly_resets_at": NOW + 86400}


class ChildEnvironment(unittest.TestCase):
    """Names only are compared, and the values are fixtures: a failure here cannot print a real secret."""

    HIDDEN = ("MARKET_OWNER", "MARKET_DIR", "MARKET_LEASE_GUARD", "market_owner")
    KEPT = ("PATH", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_HOME", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "SOME_AUTH", "MY_MARKET_OWNER")

    def environment(self):
        return {name: "fixture" for name in self.HIDDEN+self.KEPT}

    def test_only_market_names_are_dropped(self):
        self.assertEqual(sorted(mb.without_market_env(self.environment())), sorted(self.KEPT))

    def test_the_source_is_left_alone(self):
        source = self.environment()
        mb.without_market_env(source)
        self.assertEqual(source, self.environment())

    def test_the_usage_read_gets_no_market_names_and_keeps_the_login(self):
        seen = {}
        def refuse(command, **kwargs):
            seen.update(kwargs["env"])
            raise OSError("fixture")
        with patch.dict(mb.os.environ, self.environment()), patch.object(mb.subprocess, "Popen", refuse):
            self.assertIsNone(mb.read_usage(1, command=["codex", "app-server"]))
        self.assertEqual(sorted(set(seen) & set(self.HIDDEN)), [])
        for name in ("OPENAI_API_KEY", "CODEX_HOME", "PATH"):
            self.assertIn(name, seen)


class Breaker(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        printing = patch("builtins.print")
        printing.start()
        self.addCleanup(printing.stop)
        self.reads = []

    def reader(self, value):
        def read(_timeout):
            self.reads.append(1)
            return value
        return read

    def check(self, usage=USAGE, now=NOW):
        return mb.check(self.root, "buyer", now, self.reader(usage))

    def test_on_below_the_thresholds(self):
        self.assertEqual(self.check(), (True, "ok"))

    def test_each_threshold_turns_it_off_exactly_at_the_value(self):
        for usage, expected in ((dict(USAGE, five_hour_percent=85), (False, "five_hour_usage_high")),
                                (dict(USAGE, five_hour_percent=84.9), (True, "ok")),
                                (dict(USAGE, weekly_percent=85), (False, "weekly_usage_high")),
                                (dict(USAGE, weekly_percent=84.9), (True, "ok"))):
            with self.subTest(usage=usage):
                self.setUp()
                self.assertEqual(self.check(usage), expected)

    def test_thresholds_come_from_the_config_file(self):
        (self.root / "model_breaker.json").write_text(json.dumps({"weekly_limit_percent": 5}), encoding="utf-8")
        self.assertEqual(self.check(), (False, "weekly_usage_high"))

    def test_a_bad_config_keeps_the_protective_defaults(self):
        for content in ("{broken", "[]", json.dumps({"weekly_limit_percent": "high", "five_hour_limit_percent": -1, "enabled": "no"})):
            with self.subTest(content=content):
                (self.root / "model_breaker.json").write_text(content, encoding="utf-8")
                settings = mb.load_config(self.root)
                self.assertEqual((settings["enabled"], settings["weekly_limit_percent"], settings["five_hour_limit_percent"]), (True, 85, 85))

    def test_explicit_false_disables_it(self):
        (self.root / "model_breaker.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
        self.assertEqual(self.check(dict(USAGE, weekly_percent=100)), (True, "breaker_disabled"))
        self.assertEqual(self.reads, [])

    def test_unreadable_usage_means_off(self):
        self.assertEqual(self.check(None), (False, "usage_unreadable"))

    def test_usage_is_read_at_most_once_per_interval(self):
        self.check()
        self.check(now=NOW + 299)
        self.assertEqual(len(self.reads), 1)
        self.check(now=NOW + 300)
        self.assertEqual(len(self.reads), 2)

    def test_old_usage_means_off_when_it_cannot_be_refreshed(self):
        self.assertTrue(self.check()[0])
        self.assertEqual(self.check(None, now=NOW + 1000), (True, "ok"))
        self.assertEqual(self.check(None, now=NOW + 1801), (False, "usage_stale"))
        self.assertEqual(self.check(USAGE, now=NOW + 1900), (True, "ok"))

    def test_damaged_state_is_off_until_usage_is_read_again(self):
        path = self.root / ".market/model-breaker.json"
        path.parent.mkdir(parents=True)
        for content in ("{broken", "[]", json.dumps({"usage": "x"}), json.dumps({"usage": {"read_at": "x"}}), json.dumps({"tripped_until": "x"})):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertEqual(self.check(None), (False, "usage_unreadable"))
                path.write_text(content, encoding="utf-8")
                self.assertEqual(self.check(), (True, "ok"))

    def test_a_limit_error_turns_it_off_until_the_exhausted_window_resets(self):
        used = dict(USAGE, five_hour_percent=100)
        mb.trip(self.root, "seller", NOW, self.reader(used))
        self.assertEqual(self.check(now=NOW + 3599), (False, "usage_limit_error"))
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(self.check(now=NOW + 3601), (True, "ok"))

    def test_the_weekly_reset_holds_it_off_longer(self):
        used = dict(USAGE, five_hour_percent=100, weekly_percent=100)
        mb.trip(self.root, "buyer", NOW, self.reader(used))
        self.assertTrue(mb.is_tripped(self.root, NOW + 86399))
        self.assertFalse(mb.is_tripped(self.root, NOW + 86401))

    def test_unknown_reset_uses_the_configured_time(self):
        mb.trip(self.root, "buyer", NOW, self.reader(None))
        self.assertTrue(mb.is_tripped(self.root, NOW + 3599))
        self.assertFalse(mb.is_tripped(self.root, NOW + 3601))

    def test_transitions_are_logged_once_without_account_data(self):
        self.check()
        self.check(now=NOW + 10)
        self.check(dict(USAGE, weekly_percent=90), now=NOW + 400)
        rows = [json.loads(line) for line in (self.root / "logs/model-breaker.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["model"], r["reason"]) for r in rows], [("on", "ok"), ("off", "weekly_usage_high")])
        self.assertEqual(set(rows[0]), {"at", "role", "model", "reason", "five_hour_percent", "weekly_percent"})

    def test_unexpected_failures_mean_off_and_never_raise(self):
        def explode(_timeout):
            raise RuntimeError("boom")
        self.assertEqual(mb.check(self.root, "buyer", NOW, explode), (False, "breaker_error"))
        mb.trip(self.root, "buyer", NOW, explode)

    def test_a_write_failure_does_not_change_the_answer(self):
        with patch.object(mb.os, "replace", side_effect=OSError("disk")):
            self.assertEqual(self.check(), (True, "ok"))


class Reading(unittest.TestCase):
    def test_parse_keeps_only_percent_and_reset(self):
        answer = {"result": {"rateLimits": {"primary": {"usedPercent": 48, "windowDurationMins": 300, "resetsAt": 5},
                                            "secondary": {"usedPercent": 9, "resetsAt": 6}, "planType": "plus"},
                             "rateLimitResetCredits": {"credits": [{"id": "secret"}]}}}
        self.assertEqual(mb.parse_usage(answer), {"five_hour_percent": 48, "five_hour_resets_at": 5, "weekly_percent": 9, "weekly_resets_at": 6})

    def test_parse_rejects_anything_incomplete(self):
        for answer in (None, {}, {"result": None}, {"result": {"rateLimits": {"primary": {"usedPercent": 1}}}},
                       {"result": {"rateLimits": {"primary": {"usedPercent": "1"}, "secondary": {"usedPercent": 1}}}}, {"error": {}}):
            with self.subTest(answer=answer):
                self.assertIsNone(mb.parse_usage(answer))

    def test_read_usage_talks_to_a_process_and_stops_it(self):
        script = ("import json,sys\n"
                  "for line in sys.stdin:\n"
                  "    row=json.loads(line)\n"
                  "    if row.get('id')==2:\n"
                  "        print(json.dumps({'id':2,'result':{'rateLimits':{'primary':{'usedPercent':7,'resetsAt':1},'secondary':{'usedPercent':8,'resetsAt':2}}}}),flush=True)\n")
        self.assertEqual(mb.read_usage(10, [sys.executable, "-c", script])["weekly_percent"], 8)

    def test_read_usage_returns_none_when_it_cannot(self):
        self.assertIsNone(mb.read_usage(1, ["definitely-not-a-program-xyz"]))
        self.assertIsNone(mb.read_usage(1, [sys.executable, "-c", "import time; time.sleep(30)"]))

    def test_limit_wording(self):
        for text in ("You have hit your usage limit", "status 429 Too Many Requests", "rate_limit_exceeded", "insufficient_quota"):
            with self.subTest(text=text):
                self.assertTrue(mb.looks_like_limit_error(text))
        self.assertFalse(mb.looks_like_limit_error("Harness exceeded 90 seconds"))
        self.assertFalse(mb.looks_like_limit_error(None))


if __name__ == "__main__":
    unittest.main()
