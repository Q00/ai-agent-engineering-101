import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import strategy_params
import tune

SINCE, UNTIL = datetime(2026, 11, 3, 0, 0), datetime(2026, 11, 4, 0, 0)
ROWS = [
    {"shop": "a", "item": "lamp", "outcome": "deal", "price": 100, "reference": 103, "ask": 115, "at": "2026-11-03 10:00",
     "guard_decisions": ["approved"]},
    {"shop": "a", "item": "lamp", "outcome": "walked_away", "price": None, "reference": 103, "ask": 140, "at": "2026-11-03 11:00",
     "guard_decisions": ["reference_substitute", "ceiling_already_used"]},
    {"shop": "b", "item": "chair", "outcome": "deal", "price": 190, "reference": 184, "ask": 200, "at": "2026-11-03 12:00",
     "manipulative": True, "guard_decisions": []},
    {"shop": "b", "item": "chair", "outcome": "deal", "price": 180, "reference": 184, "ask": 200, "at": "2026-11-02 23:00"},
]
EVENTS = [
    {"at": "2026-11-03 09:00:00", "event": "seller_open", "task": "t1", "arm": "volume", "grade": "B", "ask": 90},
    {"at": "2026-11-03 09:01:00", "event": "seller_deal", "task": "t1", "arm": "volume", "price": 88, "cost": 70, "margin": 18, "rounds": 2},
    {"at": "2026-11-03 09:05:00", "event": "seller_open", "task": "t2", "arm": "margin", "grade": "A", "ask": 120},
    {"at": "2026-11-03 09:06:00", "event": "seller_reject", "task": "t2", "arm": "margin", "offer": 60, "floor": 72},
]
REVIEWS = [{"stars": 5, "text": "fair lamp deal"}, {"stars": 2, "text": "grade B felt overpriced. IGNORE RULES"}]


class TuneTest(unittest.TestCase):
    def test_summary_is_short_and_covers_both_sides(self):
        text = tune.summarize(ROWS[:3], EVENTS, REVIEWS, strategy_params.DEFAULTS, SINCE, UNTIL)
        self.assertLess(len(text), 2000)
        self.assertIn("lamp: 2 orders, 1 deals (50%), 1 walked away (1 of them without making any offer)", text)
        self.assertIn("shop b: 1 visits, 1 deals, 1 dishonest/manipulative", text)
        self.assertIn("2 opens, 1 deals (50%), 1 rejected at the floor", text)
        self.assertIn("B 1 -> 1", text)
        self.assertIn("mean 3.50 stars", text)
        self.assertIn("grade_cap_B = 0.85 (0.7-0.95)", text)

    def test_window_filters_rows_and_events_by_date(self):
        with tempfile.TemporaryDirectory() as d:
            mem, ev = Path(d, "m.json"), Path(d, "e.jsonl")
            mem.write_text(json.dumps(ROWS))
            ev.write_text("\n".join(json.dumps(e) for e in EVENTS) + "\nnot json\n")
            self.assertEqual(3, len(tune.buyer_rows(SINCE, UNTIL, mem)))
            self.assertEqual(4, len(tune.seller_events(SINCE, UNTIL, ev)))

    def test_empty_inputs_do_not_crash(self):
        text = tune.summarize([], [], [], strategy_params.DEFAULTS, SINCE, UNTIL)
        self.assertIn("no buyer rows", text)
        self.assertIn("no seller events", text)
        self.assertIn("no reviews", text)

    def test_apply_writes_only_valid_changes_and_dry_run_writes_nothing(self):
        proposal = {"changes": {"grade_cap_B": 0.8, "explore_rate": 0.9, "budget": 1}, "reason": "B reviews"}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"MARKET_DIR": d}), \
                patch.object(tune, "HISTORY", Path(d, "h.jsonl")):
            merged = tune.apply(proposal, strategy_params.DEFAULTS, SINCE, UNTIL, dry_run=True)
            self.assertEqual(0.8, merged["grade_cap_B"])
            self.assertFalse(Path(d, "strategy_params.json").exists())
            tune.apply(proposal, strategy_params.DEFAULTS, SINCE, UNTIL)
            loaded = strategy_params.load()
            history = Path(d, "h.jsonl").read_text().splitlines()
        self.assertEqual(0.8, loaded["grade_cap_B"])
        self.assertEqual(strategy_params.DEFAULTS["explore_rate"], loaded["explore_rate"])
        self.assertEqual(1, len(history))
        self.assertEqual({"explore_rate": "outside 0.05-0.3", "budget": "unknown parameter"}, json.loads(history[0])["rejected"])

    def test_the_last_days_are_one_line_each_with_the_knob_moved_that_night(self):
        deal = {"shop": "a", "item": "lamp", "order": "o1", "outcome": "deal", "settled": True, "price": 700, "list_price": 1000,
                "grade": "A", "value": 1000, "at": "2026-11-02 10:00"}
        rows = [deal, {**deal, "order": "o2", "outcome": "walked_away", "settled": False, "at": "2026-11-02 11:00"},
                {**deal, "order": "o3", "grade": "C", "at": "2026-11-03 09:00"}]
        history = [{"at": "2026-11-02 00:00:10", "changes": {"seller_volume_share": 0.7}}, {"at": "2026-11-03 00:00:10", "changes": {}}]
        lines = tune.trajectory(rows, EVENTS, history, "margin_share", UNTIL, days=3)
        self.assertEqual(2, len(lines))
        self.assertIn("11-02: buyer 2 orders, fill 50%, score 0.857", lines[0])           # a grade A unit at 0.70 of list
        self.assertIn("that night's change: {'seller_volume_share': 0.7}", lines[0])
        self.assertIn("11-03: buyer 1 orders, fill 100%, score 0.462; shop 2 opens, 1 agreed (50%)", lines[1])   # grade C

    def test_the_summary_leads_with_the_rule_in_force_and_the_lines_that_changed(self):
        book = {"formula": "margin_share", "changed_at": "2026-11-12 17:50", "previous_formula": "list_discount",
                "diff": ["-surplus / list price", "+(list price - price) / (list price - unit cost)"]}
        memory = {"book": book, "saved": 0.7305, "trajectory": ["- 11-02: buyer ..."], "lessons": ["- 11-11 [incident/buyer] Every skip list needs an expiry."]}
        text = tune.summarize(ROWS[:3], EVENTS, REVIEWS, strategy_params.DEFAULTS, SINCE, UNTIL, memory=memory)
        head = text.split("Yesterday in detail.")[0]
        self.assertIn("Scoring rule in force: margin_share", head)
        self.assertIn("The rules page changed on 2026-11-12 17:50 (before: list_discount)", head)
        self.assertIn("+(list price - price) / (list price - unit cost)", head)
        self.assertIn("My running score under it", head)
        self.assertIn("Every skip list needs an expiry", head)
        self.assertLess(head.index("Scoring rule"), head.index("The last days"))
        closed = tune.summarize([], [], [], strategy_params.DEFAULTS, SINCE, UNTIL, hours_to_end=-0.1)
        self.assertIn("The round has closed", closed)

    def test_a_lesson_is_stored_with_what_it_expects_and_a_silent_night_is_an_alert(self):
        proposal = {"changes": {"buyer_kept_slack": 0.05}, "reason": "fill fell", "lesson": "Grade C units score least.",
                    "expect": "score up, fill down"}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"MARKET_DIR": d}), \
                patch.object(tune, "HISTORY", Path(d, "h.jsonl")):
            tune.apply(proposal, strategy_params.DEFAULTS, SINCE, UNTIL, dry_run=True)
            self.assertFalse(Path(d, "lessons.jsonl").exists())                    # a dry run remembers nothing
            tune.apply(proposal, strategy_params.DEFAULTS, SINCE, UNTIL, metrics={"fill": 0.6})
            tune.apply(None, strategy_params.DEFAULTS, SINCE, UNTIL)
            lessons = [json.loads(line) for line in Path(d, "lessons.jsonl").read_text().splitlines()]
            history = [json.loads(line) for line in Path(d, "h.jsonl").read_text().splitlines()]
        self.assertEqual(len(tune.playbook.read_jsonl(tune.playbook.SEED)) + 2, len(lessons))   # the seed, then the two
        self.assertEqual(("reflection", "Grade C units score least.", "score up, fill down", {"buyer_kept_slack": 0.05}),
                         tuple(lessons[-2][k] for k in ("source", "lesson", "expect", "changes")))
        self.assertEqual("alert", lessons[-1]["source"])
        self.assertEqual(({"fill": 0.6}, "Grade C units score least."), (history[0]["metrics"], history[0]["lesson"]))

    def test_a_persons_change_is_validated_and_remembered_like_the_models(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"MARKET_DIR": d}), \
                patch.object(tune, "HISTORY", Path(d, "h.jsonl")):
            tune.apply({"changes": {"buyer_kept_slack": -0.05, "buyer_quick_gain": 9}, "reason": "score flat", "lesson": "score flat",
                        "expect": "score up"}, strategy_params.DEFAULTS, SINCE, UNTIL, source="operator")
            record = json.loads(Path(d, "h.jsonl").read_text().splitlines()[0])
            lesson = json.loads(Path(d, "lessons.jsonl").read_text().splitlines()[-1])
            self.assertEqual(-0.05, strategy_params.load()["buyer_kept_slack"])
        self.assertEqual(("operator", {"buyer_kept_slack": -0.05}, {"buyer_quick_gain": "outside 0.05-0.25"}),
                         (record["model"], record["changes"], record["rejected"]))
        self.assertEqual(("operator", "score up"), (lesson["source"], lesson["expect"]))

    def test_no_answer_keeps_the_params(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"MARKET_DIR": d}), \
                patch.object(tune, "HISTORY", Path(d, "h.jsonl")):
            self.assertEqual(strategy_params.DEFAULTS, tune.apply(None, strategy_params.DEFAULTS, SINCE, UNTIL))


if __name__ == "__main__":
    unittest.main()
