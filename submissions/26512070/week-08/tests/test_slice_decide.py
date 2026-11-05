"""outer.slice and outer.decide with a scripted model: summaries, a profile, a brief with options, the audit,
then the user's decision installs the next strategy version.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from outer import decide, slice as outer_slice  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


def j(tool, **args):
    return json.dumps({"tool": tool, "args": args})


class SliceDecideTest(Base):
    def setUp(self):
        super().setUp()
        shutil.copytree(HERE / "card", paths.root() / "card")
        # 11/5 18:0x: night auto-install is off in production (AUTO_HOURS empty); these tests keep the mechanism
        # covered with the hours it had (00:00-07:59)
        self._auto_hours = outer_slice.AUTO_HOURS
        outer_slice.AUTO_HOURS = range(0, 8)

    def tearDown(self):
        outer_slice.AUTO_HOURS = self._auto_hours
        super().tearDown()

    def answers(self, options):
        return iter([
            j("rec.profile", name="elm", side="shop", summary="cheap, honest", interpreted=[
                {"claim": "first ask near cost", "evidence": ["ledger 10"]}]),
            j("rec.summary", summary="drinks sell", findings=[{"claim": "drinks lead", "evidence": ["live.selling"]}]),
            j("rec.summary", summary="we sold 1", findings=[{"claim": "one deal", "evidence": ["eval.unit_economics"]}]),
            j("rec.brief", score="no board yet", happened=[{"claim": "first deal", "evidence": ["ledger 11"]}],
              hypothesis="H1 too early", counterparts="elm is new", options=options,
              recommendation=options[0]["id"] if options else None),
            j("rec.audit", agrees=False, prefer="B", points=[{"claim": "A is early", "why": "only 1 deal"}]),
        ])

    def run_slice(self, options, now):
        it = self.answers(options)
        return outer_slice.run(now=now, model_call=lambda p, s, t: next(it))

    def test_slice_then_decide(self):
        import time
        opts = [{"id": "A", "change": {"phase": "profit"}, "targets": "shop profit", "expected": "+margin",
                 "risk": "fewer buyers", "reversible": True},
                {"id": "B", "change": {"context": ["keep exposure"]}, "targets": "deals", "expected": "more buyers",
                 "risk": "losses", "reversible": True}]
        now = time.mktime(time.strptime("2026-11-05 12:00", "%Y-%m-%d %H:%M"))
        out = self.run_slice(opts, now)
        self.assertTrue(all(s["ok"] for s in out["steps"].values()), out)
        pid = out["slice"]
        md = (paths.root() / "logs" / "strategy" / "briefs" / f"{pid}.md").read_text(encoding="utf-8")
        self.assertIn("### A  (recommended)", md)
        self.assertIn("agrees: False, prefers B", md)
        self.assertIn(pid, (paths.root() / "logs" / "strategy" / "PENDING.md").read_text(encoding="utf-8"))
        self.assertTrue((paths.memory() / "profiles" / "elm.json").exists())
        d = decide.decide(pid, "B", note="test")
        active = atomic.read_json(paths.memory() / "strategy" / "active.json")
        self.assertEqual((d["installed"], active["context"], active["version"]), (2, ["keep exposure"], 2))
        self.assertIn("option B", active["approved"])
        self.assertIn("(nothing)", (paths.root() / "logs" / "strategy" / "PENDING.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            decide.decide(pid, "A")

    def installed_at(self, t):
        import os
        a = paths.memory() / "strategy" / "active.json"
        os.utime(a, (t, t))

    def night_answers(self, options, agrees):
        it = list(self.answers(options))[:4]
        return iter(it + [j("rec.audit", agrees=agrees, points=[{"claim": "c", "why": "w"}])])

    def test_night_mode_installs_the_recommendation_when_the_audit_agrees(self):
        # user 11/3 23:2x: at night the strategy agent decides by itself every 30 minutes (outer.slice AUTO_HOURS)
        import time
        opts = [{"id": "A", "change": {"phase": "profit"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True},
                {"id": "B", "change": {"phase": "exposure"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True}]
        now = time.mktime(time.strptime("2026-11-05 03:30", "%Y-%m-%d %H:%M"))
        self.installed_at(now - outer_slice.HOLD_S - 60)
        it = self.night_answers(opts, True)
        out = outer_slice.run(now=now, model_call=lambda p, s, t: next(it))
        self.assertEqual(out["slice"], "2026-11-05T0330", out)
        self.assertIn("auto", out["steps"], out)
        self.assertEqual(out["steps"]["auto"]["option"], "A")
        active = atomic.read_json(paths.memory() / "strategy" / "active.json")
        self.assertEqual((active["phase"], active["version"]), ("profit", 2))
        self.assertTrue(active["approved"].startswith("auto "), active["approved"])

    def test_night_mode_holds_without_the_audit_or_a_safe_change(self):
        import time
        opts = [{"id": "A", "change": {"phase": "profit"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True},
                {"id": "B", "change": {"phase": "exposure"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True}]
        now = time.mktime(time.strptime("2026-11-05 03:00", "%Y-%m-%d %H:%M"))
        self.installed_at(now - outer_slice.HOLD_S - 60)
        it = self.night_answers(opts, False)
        out = outer_slice.run(now=now, model_call=lambda p, s, t: next(it))
        self.assertIsNone(out["steps"]["auto"]["installed"])
        self.assertEqual(atomic.read_json(paths.memory() / "strategy" / "active.json")["version"], 1)
        active = atomic.read_json(paths.memory() / "strategy" / "active.json")
        loose = json.loads(json.dumps(active))
        loose["limits"]["cash_floor"] = (active["limits"].get("cash_floor") or 0) - 1
        self.assertIn("cash_floor", outer_slice.unsafe(active, loose))
        self.assertIsNone(outer_slice.unsafe(active, active))

    def test_hold_after_an_install(self):
        # user 11/5 01:3x: nothing is installed while the strategy in force is younger than HOLD_S
        import time
        opts = [{"id": "A", "change": {"phase": "profit"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True},
                {"id": "B", "change": {"phase": "exposure"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True}]
        now = time.mktime(time.strptime("2026-11-05 03:30", "%Y-%m-%d %H:%M"))
        self.installed_at(now - 1200)
        it = iter(list(self.answers(opts))[:4])
        out = outer_slice.run(now=now, model_call=lambda p, s, t: next(it))
        self.assertNotIn("auto", out["steps"], out)
        self.assertEqual(atomic.read_json(paths.memory() / "strategy" / "active.json")["version"], 1)
        self.assertEqual(outer_slice.held(now), outer_slice.HOLD_S - 1200)
        self.assertEqual(outer_slice.held(now + outer_slice.HOLD_S), 0)

    def test_recent_notes(self):
        import time
        text = "# 2026-11-04 02:2x - old\nold body\n\n# 2026-11-05 01:1x - new\nnew body"
        now = time.mktime(time.strptime("2026-11-05 03:00", "%Y-%m-%d %H:%M"))
        got = outer_slice.recent_notes(text, now)
        self.assertIn("new body", got)
        self.assertNotIn("old", got)
        self.assertEqual(outer_slice.recent_notes(text, now + 86400), "")

    def test_slice_ids_and_due(self):
        import importlib.util
        import time
        spec = importlib.util.spec_from_file_location("outer_loop", HERE / "outer.py")
        ol = importlib.util.module_from_spec(spec)      # outer.py (the loop); "outer" is the package
        spec.loader.exec_module(ol)
        t = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))
        self.assertEqual(ol.due_slice(t("2026-11-05 00:10"), None), "2026-11-05T0000")
        self.assertEqual(ol.due_slice(t("2026-11-05 00:40"), "2026-11-05T0000"), "2026-11-05T0030")
        self.assertIsNone(ol.due_slice(t("2026-11-05 00:50"), "2026-11-05T0030"))
        self.assertEqual(ol.due_slice(t("2026-11-05 12:05"), None), "2026-11-05T12")
        self.assertIsNone(ol.due_slice(t("2026-11-05 13:05"), "2026-11-05T12"))

    def test_stock_delegation(self):
        # user 11/3 23:5x: the stock cap is the strategy agent's by day too (strategy.delegation.auto = ["stock"])
        d = {"delegation": {"auto": ["stock"]}}
        self.assertTrue(outer_slice.delegated(d, {"limits": {"max_stock_share": 0.4}, "context": ["x"]}))
        self.assertTrue(outer_slice.delegated(d, {"restock": {"per_asked_category": 4}}))
        self.assertFalse(outer_slice.delegated(d, {"limits": {"cash_floor": 1}}))
        self.assertFalse(outer_slice.delegated(d, {"priorities": ["x"]}))
        self.assertFalse(outer_slice.delegated({"delegation": {}}, {"limits": {"max_stock_share": 0.4}}))
        a = {"limits": {"max_stock_share": 0.45}}
        self.assertIn("max_stock_share", outer_slice.unsafe(a, {"limits": {"max_stock_share": 0.6}}))

    def test_review_fixes(self):
        # 11/4 review: playbook merges two levels, round-end and food terms hold, a stale brief is not installed
        from tools.record import apply_change
        active = atomic.read_json(paths.memory() / "strategy" / "active.json")
        base = dict(active, playbook={"restock": {"mode": "rules", "cover_windows": 2, "market_top": 8}})
        got = apply_change(base, {"playbook": {"restock": {"cover_windows": 3}}})
        self.assertEqual(got["playbook"]["restock"], {"mode": "rules", "cover_windows": 3, "market_top": 8})
        a = {"restock": {"stop_after": 100}, "limits": {"food_share_max": 0.0}}
        self.assertIn("stop_after", outer_slice.unsafe(a, {"restock": {}, "limits": {"food_share_max": 0.0}}))
        self.assertIn("food_share_max", outer_slice.unsafe(a, {"restock": {"stop_after": 100}, "limits": {"food_share_max": 1.0}}))
        import time
        opts = [{"id": "A", "change": {"phase": "profit"}, "targets": "x", "expected": "x", "risk": "x", "reversible": True},
                {"id": "B", "change": {"context": ["keep"]}, "targets": "x", "expected": "x", "risk": "x", "reversible": True}]
        out = self.run_slice(opts, time.mktime(time.strptime("2026-11-05 12:00", "%Y-%m-%d %H:%M")))
        newer = dict(atomic.read_json(paths.memory() / "strategy" / "active.json"), version=5, epoch="e5")
        atomic.write_json(paths.memory() / "strategy" / "active.json", newer)
        with self.assertRaises(ValueError):
            decide.decide(out["slice"], "B")
        self.assertEqual(decide.decide(out["slice"], "B", stale_ok=True)["installed"], 6)

    def test_bad_option_change_is_refused(self):
        from tools.record import apply_change
        from tools.registry import Refused
        with self.assertRaises(Refused):
            apply_change(atomic.read_json(paths.memory() / "strategy" / "active.json"), {"version": 9})
        with self.assertRaises(Refused):
            apply_change(atomic.read_json(paths.memory() / "strategy" / "active.json"), {"limits": {"cash_floor": -1}})


if __name__ == "__main__":
    unittest.main()
