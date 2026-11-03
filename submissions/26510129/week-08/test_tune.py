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

    def test_no_answer_keeps_the_params(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"MARKET_DIR": d}), \
                patch.object(tune, "HISTORY", Path(d, "h.jsonl")):
            self.assertEqual(strategy_params.DEFAULTS, tune.apply(None, strategy_params.DEFAULTS, SINCE, UNTIL))


if __name__ == "__main__":
    unittest.main()
