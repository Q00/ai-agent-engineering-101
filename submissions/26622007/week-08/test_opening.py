"""opening.py: Round 3's first three supply windows, as the GPT Pro review proposed."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import opening
import restock

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1800000000.0
CAT = {"F1": {"kind": "food", "category": "식품>과자", "list_price": 12000, "shelf_hours": 720},
       "F2": {"kind": "food", "category": "식품>과자", "list_price": 13000, "shelf_hours": 720},
       "F9": {"kind": "food", "category": "식품>야채", "list_price": 9000, "shelf_hours": 48},
       "G1": {"kind": "goods", "category": "가전>램프", "list_price": 12000},
       "G2": {"kind": "goods", "category": "가구>침대", "list_price": 40000},
       "G3": {"kind": "goods", "category": "가전>TV", "list_price": 5300}}
DEMAND = [{"category": "식품>과자", "median_price": 12000}, {"category": "식품>야채", "median_price": 9000},
          {"category": "가전>램프", "median_price": 12000}, {"category": "가구>침대", "median_price": 344640},
          {"category": "가전>TV", "median_price": 299000}]
CFG = {**opening.DEFAULTS, "phases": [["F1", "G1", "F9", "G2", "G3"], ["F2"], ["G1"]]}


class OpeningTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.md)

    def run_it(self, now, units=(), rows=(), restock_info=None, cfg=CFG):
        inv = {"round": 3, "balance": 5000000, "units": [{"sku": s} for s in units]}
        if restock_info:
            inv["restock"] = restock_info
        return opening.lines(self.md, inv, CAT, DEMAND, list(rows), cfg, now=now)

    def test_phase_one_only_what_fits_one_each(self):
        out, why = self.run_it(NOW)
        self.assertEqual(out, [{"sku": "F1", "qty": 1}, {"sku": "G1", "qty": 1}])   # short shelf life, far from the median
        self.assertIn("phase 1", why)

    def test_next_phase_waits_for_arrival_and_the_gap_then_retries_a_missed_window(self):
        self.run_it(NOW)
        self.assertEqual(self.run_it(NOW + 60)[0], [])                 # nothing arrived yet
        self.assertEqual(self.run_it(NOW + 400)[0], [{"sku": "F1", "qty": 1}, {"sku": "G1", "qty": 1}])  # missed: again
        self.assertEqual(self.run_it(NOW + 460, units=["F1"])[0], [])  # arrived, gap not over
        self.assertEqual(self.run_it(NOW + 400 + 16 * 60, units=["F1"])[0], [{"sku": "F2", "qty": 1}])
        self.run_it(NOW + 400 + 17 * 60, units=["F1", "F2"])
        self.assertEqual(self.run_it(NOW + 400 + 40 * 60, units=["F1", "F2"])[0], [{"sku": "G1", "qty": 1}])
        self.run_it(NOW + 400 + 41 * 60, units=["F1", "F2", "G1"])
        self.assertIsNone(self.run_it(NOW + 400 + 60 * 60, units=["F1", "F2", "G1"])[0])

    def test_a_closed_window_buys_nothing_and_does_not_use_up_the_phase(self):
        self.assertEqual(self.run_it(NOW, restock_info={"open": False, "opens_at": NOW + 600})[0], [])
        self.assertEqual(len(self.run_it(NOW + 600, restock_info={"open": True})[0]), 2)

    def test_the_ledger_drops_or_replaces_conditional_candidates(self):
        rows = [{"k": "settle", "sku": "F1"}] * 3 + [{"k": "settle", "sku": "G3"}] * 60
        cand, notes = opening.revise(["F2", "G1"], rows, CAT, {d["category"]: d["median_price"] for d in DEMAND}, CFG)
        self.assertEqual(cand, ["F1"])                                 # F2 -> F1 (sells more, same rules); 램프 sold nothing
        self.assertEqual(len(notes), 2)
        self.assertEqual(opening.revise(["F2"], rows[:10], CAT, {}, CFG)[0], ["F2"])   # too few settles to judge

    def test_a_phase_nothing_of_which_arrives_is_skipped_after_two_sends(self):
        self.run_it(NOW)
        self.assertTrue(self.run_it(NOW + 400)[0])                     # second send
        self.assertEqual(self.run_it(NOW + 800)[0], [])                # skipped; the gap to phase 2 starts again
        with open(os.path.join(self.md, "opening_state.json")) as f:
            self.assertTrue(json.load(f)["sent"]["1"]["skipped"])
        self.assertEqual(self.run_it(NOW + 400 + 16 * 60)[0], [{"sku": "F2", "qty": 1}])

    def test_a_window_about_to_close_waits_and_the_guard_decides_what_counts(self):
        self.assertEqual(self.run_it(NOW, restock_info={"open": True, "closes_at": NOW + 30})[0], [])
        inv = {"round": 3, "units": []}
        out, _ = opening.lines(self.md, inv, CAT, DEMAND, [], CFG, now=NOW, keep=lambda b: b[:1])
        self.assertEqual(out, [{"sku": "F1", "qty": 1}])
        with open(os.path.join(self.md, "opening_state.json")) as f:
            self.assertEqual(json.load(f)["sent"]["1"]["skus"], ["F1"])

    def test_no_median_means_caps_only(self):
        out, _ = opening.lines(self.md, {"round": 3, "units": []}, CAT, [], [], CFG, now=NOW)
        self.assertEqual([b["sku"] for b in out], ["F1", "G1", "G2", "G3"])

    def test_the_exploration_budget(self):
        out, why = self.run_it(NOW, cfg={**CFG, "explore_budget_worst": 10000})
        self.assertEqual(out, [{"sku": "F1", "qty": 1}])
        self.assertIn("budget", why)


class ShippedOpeningTest(unittest.TestCase):
    def test_the_opening_is_over_and_its_list_is_kept(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            o = json.load(f)["opening"]
        self.assertEqual(o["phases"], [])                       # GPT Pro profit review: no more opening phases
        self.assertEqual([len(p) for p in o["retired_phases"]["phases"]], [10, 10, 4])
        self.assertEqual(o["retired_phases"]["phases"][0][:2], ["Gdf79cd07", "G2108d9bc"])
        self.assertEqual(opening.lines("/nonexistent", {}, {}, [], [], {**opening.DEFAULTS, **o}), (None, "no opening configured"))

    def test_restock_main_sends_the_opening_in_round_three_only(self):
        for rnd, expect in ((3, [{"sku": "F1", "qty": 1}]), (2, None)):
            md = tempfile.mkdtemp()
            try:
                for name, data in (("inventory.json", {"round": rnd, "balance": 5000000, "units": []}), ("catalog.json", CAT),
                                   ("demand.json", DEMAND)):
                    with open(os.path.join(md, name), "w") as f:
                        json.dump(data, f)
                printed = []
                env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
                with mock.patch.object(restock, "read_model", lambda *a: None), \
                        mock.patch.object(restock, "load", lambda p, d, real=restock.load: {"opening": {"phases": [["F1"]]}}
                                          if p == "pricing.json" else real(p, d)), \
                        mock.patch.dict(os.environ, {**env, "AGENT_MEMORY_DIR": os.path.join(md, "mem")}, clear=True), \
                        mock.patch.object(sys, "argv", ["x", "--state", md]), \
                        mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                    restock.main()
                out = json.loads(printed[-1])["buy"]
                if expect is None:
                    self.assertFalse(os.path.exists(os.path.join(md, "opening_state.json")))
                else:
                    self.assertEqual(out, expect)
            finally:
                shutil.rmtree(md)


class WindowLeadTest(unittest.TestCase):
    """The kit gives a line 3 tries about 30 s apart: a line let out more than about 60 s early is lost (05:41:19)."""

    def closed(self, opens_in):
        return {"restock": {"open": False, "opens_at": NOW + opens_in, "closes_at": NOW + opens_in + 180}}

    def test_holds_lines_until_the_kit_can_reach_the_window(self):
        self.assertTrue(opening.window_closed(self.closed(70), NOW))        # 05:40:13: 70 s before, refused 3 times
        self.assertTrue(opening.window_closed(self.closed(opening.WINDOW_LEAD + 1), NOW))
        self.assertFalse(opening.window_closed(self.closed(opening.WINDOW_LEAD), NOW))
        self.assertFalse(opening.window_closed(self.closed(5), NOW))
        self.assertLessEqual(opening.WINDOW_LEAD, 55)

    def test_open_window_and_missing_info(self):
        self.assertFalse(opening.window_closed({"restock": {"open": True, "closes_at": NOW + 100}}, NOW))
        self.assertTrue(opening.window_closed({"restock": {"open": True, "closes_at": NOW + 10}}, NOW, need=25))
        self.assertFalse(opening.window_closed({}, NOW))

    def test_open_window_past_its_close_is_closed_without_need(self):
        # inventory.json read at 16:25:13 said open, closes 16:25:15; the run's lines went out at 16:25:24 (11/05)
        self.assertTrue(opening.window_closed({"restock": {"open": True, "closes_at": NOW - 9}}, NOW))
        self.assertTrue(opening.window_closed({"restock": {"open": True, "closes_at": NOW}}, NOW))
        self.assertFalse(opening.window_closed({"restock": {"open": True, "closes_at": NOW + 1}}, NOW))
        self.assertFalse(opening.window_closed({"restock": {"open": True}}, NOW))


class LateLinesTest(unittest.TestCase):
    """restock.late_lines: lines the kit cannot start before the window closes are held back, the agent's requeued."""

    def setUp(self):
        self.md = tempfile.mkdtemp()
        self.buy = [{"sku": s, "qty": 1} for s in ("G1", "F1", "G2", "F2")]

    def tearDown(self):
        shutil.rmtree(self.md)

    def queue(self):
        return restock.load(os.path.join(self.md, "restock_queue.json"), {}).get("lines") or []

    def run_at(self, closes_in, agent=(), inv=None):
        with mock.patch("sys.stderr"):
            return restock.late_lines(self.md, inv if inv is not None else {"restock": {"open": True, "closes_at": NOW + closes_in}},
                                      self.buy, set(agent), now=NOW)

    def test_after_close_nothing_goes_and_agent_lines_wait(self):
        self.assertEqual(self.run_at(-9, agent={"G1", "F2"}), [])
        self.assertEqual([b["sku"] for b in self.queue()], ["G1", "F2"])

    def test_near_close_only_the_lines_that_can_start(self):
        sent = self.run_at(restock.LATE_MARGIN + opening.SECONDS_PER_LINE + 1, agent={"F2"})
        self.assertEqual([b["sku"] for b in sent], ["G1", "F1"])
        self.assertEqual([b["sku"] for b in self.queue()], ["F2"])         # G2 is the formula's: planned again

    def test_enough_time_or_unknown_window_changes_nothing(self):
        self.assertEqual(self.run_at(120), self.buy)
        self.assertEqual(self.run_at(0, inv={"restock": {"open": False, "opens_at": NOW + 30}}), self.buy)
        self.assertEqual(self.run_at(0, inv={}), self.buy)
        self.assertEqual(self.queue(), [])


if __name__ == "__main__":
    unittest.main()
