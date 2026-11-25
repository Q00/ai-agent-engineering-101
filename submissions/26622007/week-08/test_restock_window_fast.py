"""restock_window_fast: the second restock run of a supply window sends the lines the first one planned, without the
ledger read, and never spends what the usual run would not."""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import hard_limits
import market_model
import measure
import meta_harness
import restock
import restock_window_fast as wf

CATALOG = {"G1": {"kind": "goods", "category": "가구>소파", "list_price": 1000000},
           "G2": {"kind": "goods", "category": "가구>침대", "list_price": 500000},
           "G3": {"kind": "goods", "category": "가구>장", "list_price": 400000},
           "F1": {"kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720},
           "H1": {"kind": "goods", "category": "가구>카펫", "list_price": 300000}}


def on(now, **over):
    return {"study": "wf-test", "salt": "s1", "treat": 1.0, "start_at": now - 60, "stop_at": now + 3600, **over}


def switch(md, d):
    with open(os.path.join(md, wf.SWITCH), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, **d}, f)


class SwitchTest(unittest.TestCase):
    def test_on_only_inside_a_finite_window(self):
        now = 1794300000.0
        md = tempfile.mkdtemp()
        self.assertIsNone(wf.config(md, env={}, now=now))
        switch(md, on(now))
        self.assertEqual(wf.config(md, env={}, now=now)["study"], "wf-test")
        self.assertIsNone(wf.config(md, env={}, now=now - 61))
        self.assertIsNone(wf.config(md, env={}, now=now + 3600))                    # the boundary is off
        self.assertIsNone(wf.config(md, env={"RESTOCK_WINDOW_FAST": "off"}, now=now))
        for bad in ({"stop_at": None}, {"start_at": "x"}, {"stop_at": float("inf")}, {"stop_at": now + 13 * 3600},
                    {"treat": 0.1}, {"treat": 2}, {"treat": True}, {"salt": ""}, {"study": 3}, {"enabled": 1}):
            switch(md, on(now, **bad))
            self.assertIsNone(wf.config(md, env={}, now=now), bad)
        with open(os.path.join(md, wf.SWITCH), "w", encoding="utf-8") as f:
            f.write("[1]")
        self.assertIsNone(wf.config(md, env={}, now=now))

    def test_switch_on_writes_what_config_reads(self):
        md = tempfile.mkdtemp()
        d = wf.switch_on(md, study="x", salt="y", start_at=1794300000.0, hours=12)
        self.assertEqual(wf.config(md, env={}, now=1794300001.0)["stop_at"], d["stop_at"])
        self.assertIsNone(wf.config(md, env={}, now=d["stop_at"]))
        with self.assertRaises(ValueError):
            wf.switch_on(md, study="x", salt="y", start_at=1794300000.0, hours=12.5)

    def test_arms_are_by_window_and_stable(self):
        cfg = on(0, treat=0.5)
        arms = [wf.arm(cfg, 1794300000 + 1200 * i) for i in range(400)]
        self.assertEqual(arms, [wf.arm(cfg, 1794300000.4 + 1200 * i) for i in range(400)])   # one arm for one window
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertEqual({wf.arm(on(0), 1794300000 + i) for i in range(50)}, {"B"})
        for bad in (None, "x", float("nan"), True):
            self.assertIsNone(wf.arm(cfg, bad))
        self.assertIsNone(wf.arm(None, 1794300000))

    def test_the_meta_harness_watches_the_switch(self):
        self.assertIn(".market/" + wf.SWITCH, meta_harness.SWITCHES)


class CarryTest(unittest.TestCase):
    NOW = 1794300000.0

    def inv(self, open_=True, opens=NOW - 100, closes=NOW + 80):
        return {"restock": {"open": open_, "opens_at": opens, "closes_at": closes}}

    def test_taken_once_and_only_in_its_own_open_window(self):
        md, lines = tempfile.mkdtemp(), [{"sku": "G1", "qty": 2}, {"sku": "F1", "qty": 20}]
        before = self.inv(False, self.NOW + 20, self.NOW + 200)                   # planned 20 s before the window opens
        self.assertTrue(wf.save_carry(md, before, lines, now=self.NOW))
        self.assertIsNone(wf.take_carry(md, before, now=self.NOW + 5))            # not open yet
        open_ = self.inv(True, self.NOW + 20, self.NOW + 200)
        self.assertIsNone(wf.take_carry(md, self.inv(True, self.NOW + 1220, self.NOW + 1400), now=self.NOW + 130))   # another window
        self.assertIsNone(wf.take_carry(md, open_, now=self.NOW + 200))           # the window has closed
        self.assertIsNone(wf.take_carry(md, open_, now=self.NOW + wf.CARRY_SECONDS + 1))
        self.assertIsNone(wf.take_carry(md, open_, now=self.NOW - 1))
        self.assertIsNone(wf.take_carry(md, {"restock": {"open": True, "opens_at": self.NOW + 20}}, now=self.NOW + 130))
        self.assertEqual(wf.take_carry(md, open_, now=self.NOW + 130), (lines, self.NOW))
        self.assertIsNone(wf.take_carry(md, open_, now=self.NOW + 131))           # once

    def test_a_carry_that_is_not_what_was_written_is_not_used(self):
        md, inv = tempfile.mkdtemp(), self.inv()
        self.assertIsNone(wf.take_carry(md, inv, now=self.NOW))                   # none
        for bad in ([], [{"sku": "G1", "qty": 0}], [{"sku": "G1", "qty": 21}], [{"sku": "G1", "qty": True}],
                    [{"sku": 5, "qty": 1}], [{"sku": "G1"}], ["G1"], [{"sku": "G1", "qty": 1}] * 11, "x"):
            with open(os.path.join(md, wf.CARRY), "w", encoding="utf-8") as f:
                json.dump({"at": self.NOW - 10, "opens_at": inv["restock"]["opens_at"], "lines": bad}, f)
            self.assertIsNone(wf.take_carry(md, inv, now=self.NOW), bad)
        with open(os.path.join(md, wf.CARRY), "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertIsNone(wf.take_carry(md, inv, now=self.NOW))

    def test_a_run_with_nothing_to_carry_removes_the_old_one(self):
        md, inv = tempfile.mkdtemp(), self.inv()
        wf.save_carry(md, inv, [{"sku": "G1", "qty": 1}], now=self.NOW)
        self.assertFalse(wf.save_carry(md, inv, [], now=self.NOW + 1))
        self.assertFalse(os.path.exists(os.path.join(md, wf.CARRY)))
        self.assertFalse(wf.save_carry(md, {"restock": None}, [{"sku": "G1", "qty": 1}], now=self.NOW))
        self.assertTrue(wf.save_carry(md, inv, [{"sku": f"G{i}", "qty": 1} for i in range(15)], now=self.NOW))
        self.assertEqual(len(wf.take_carry(md, inv, now=self.NOW + 1)[0]), wf.CARRY_LINES)

    def test_a_mark_that_cannot_be_written_gives_no_lines(self):
        md, inv = tempfile.mkdtemp(), self.inv()
        wf.save_carry(md, inv, [{"sku": "G1", "qty": 1}], now=self.NOW)
        with mock.patch.object(wf, "_write", side_effect=OSError("disk")):
            self.assertIsNone(wf.take_carry(md, inv, now=self.NOW + 1))


class FastRunTest(unittest.TestCase):
    """restock.window_fast and main() at a real clock, on a state folder of its own; the ledger sync is a stub."""

    def setUp(self):
        self.now = time.time()
        self.md = tempfile.mkdtemp()
        self.pricing = {"restock": {"priority_skus": ["G1", "G2", "G3", "F1", "H1"], "sku_target_units": {"G1": 4, "G2": 3, "G3": 2},
                                    "keep_cash": 0, "max_lines": 10, "explore_lines": 0, "high_price_goods": 100000,
                                    "high_price_max_units": 20, "high_price_proven_max_units": 20, "food_max_qty": 20,
                                    "max_stock_cost": 10 ** 9, "food_max_stock_cost": 3000000, "fill_listed": True},
                        "next_window": {"id": "t1", "targets": {"H1": 1}, "hold_after_done": True}}
        self.cfg = hard_limits.clamp_cfg("restock", {**restock.DEFAULTS, **self.pricing["restock"]})
        self.cfg["fresh_slots"] = None
        self.inv = {"round": 3, "balance": 50000000, "bankrupt_line": 500000, "units": [],
                    "restock": {"open": True, "opens_at": self.now - 120, "closes_at": self.now + 60}}
        self.write("inventory.json", self.inv)
        self.write("catalog.json", CATALOG)
        self.write(restock.NEXT_WINDOW_STATE, {"id": "t1", "done": True})
        switch(self.md, on(self.now))
        self.sync = mock.patch.object(market_model, "sync", return_value=3)
        self.synced = self.sync.start()
        self.addCleanup(self.sync.stop)

    def write(self, name, d):
        with open(os.path.join(self.md, name), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def carry(self, lines, at=None):
        self.assertTrue(wf.save_carry(self.md, self.inv, lines, now=self.now - 100 if at is None else at))

    def fast(self, inv=None, pricing=None, cfg=None):
        err = io.StringIO()
        with redirect_stderr(err):
            out = restock.window_fast(self.md, inv or self.inv, pricing or self.pricing, cfg or dict(self.cfg), CATALOG)
        return out, err.getvalue()

    def test_the_carried_lines_go_out_guarded_and_noted(self):
        self.carry([{"sku": "G1", "qty": 6}, {"sku": "G2", "qty": 2}, {"sku": "F1", "qty": 20}])
        out, err = self.fast()
        self.assertEqual(out, {"buy": [{"sku": "G1", "qty": 4}, {"sku": "G2", "qty": 2}, {"sku": "F1", "qty": 20}]})   # G1: its target
        self.assertEqual(self.synced.call_count, 1)                                # the ledger is kept fresh, as in every run
        self.assertIn("window_fast wf-test", err)
        self.assertIn("cash buffer", err)
        sent = json.load(open(os.path.join(self.md, restock.SENT)))
        self.assertEqual([(s["sku"], s["qty"]) for s in sent], [("G1", 4), ("G2", 2), ("F1", 20)])
        self.assertIsNone(self.fast()[0])                                          # the carry is spent: the usual run

    def test_the_default_rule_needs_no_switch_and_only_true_turns_it_on(self):
        os.remove(os.path.join(self.md, wf.SWITCH))
        self.carry([{"sku": "G2", "qty": 2}])
        self.assertIsNone(self.fast()[0])                                          # no switch, no rule: the usual run
        for not_on in (1, "true", None, False):
            self.assertIsNone(self.fast(cfg={**self.cfg, "window_fast": not_on})[0], not_on)
        self.assertEqual(restock.window_fast_arm(self.md, {**self.cfg, "window_fast": True}, self.inv), ("B", "default"))
        self.assertEqual(restock.window_fast_arm(self.md, self.cfg, self.inv), (None, None))
        out, err = self.fast(cfg={**self.cfg, "window_fast": True})
        self.assertEqual(out, {"buy": [{"sku": "G2", "qty": 2}]})
        self.assertIn("window_fast default", err)
        switch(self.md, on(self.now))
        self.assertEqual(restock.window_fast_arm(self.md, self.cfg, self.inv), ("B", "wf-test"))
        with mock.patch.object(wf, "arm", return_value="A"):                       # the rule is every window, whatever a study says
            self.assertEqual(restock.window_fast_arm(self.md, {**self.cfg, "window_fast": True}, self.inv), ("B", "default"))
            self.assertEqual(restock.window_fast_arm(self.md, self.cfg, self.inv), ("A", "wf-test"))

    def test_the_shipped_settings_leave_the_rule_off(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(restock.__file__)), "pricing.json"), encoding="utf-8") as f:
            shipped = hard_limits.clamp_cfg("restock", {**restock.DEFAULTS, **json.load(f)["restock"]})
        self.assertIs(shipped.get("window_fast"), False)           # off: more lines a window is not yet an economic keep
        os.remove(os.path.join(self.md, wf.SWITCH))                # (work-state instruction 7, 11/11 05:35)
        self.assertEqual(restock.window_fast_arm(self.md, shipped, self.inv), (None, None))
        self.assertEqual(restock.window_fast_arm(self.md, {**shipped, "window_fast": True}, self.inv), ("B", "default"))

    def test_it_is_the_usual_run_whenever_anything_is_off(self):
        lines = [{"sku": "G1", "qty": 1}]
        cases = {
            "no switch": lambda: os.remove(os.path.join(self.md, wf.SWITCH)),
            "next_window not done": lambda: self.write(restock.NEXT_WINDOW_STATE, {"id": "t1", "done": False}),
            "next_window state of another plan": lambda: self.write(restock.NEXT_WINDOW_STATE, {"id": "t0", "done": True}),
            "opening phases": lambda: self.pricing.update(opening={"phases": [["G1"]]}),
            "sent lines unreadable": lambda: self.write(restock.SENT, {"not": "a list"}),
            "a sent line without its worst cost": lambda: self.write(restock.SENT, [{"at": self.now - 5, "sku": "G2", "qty": 1, "worst": "x"}]),
            "sync fails": lambda: setattr(self.synced, "side_effect", OSError("market")),
        }
        for name, break_it in cases.items():
            with self.subTest(name):
                self.setUp()
                self.carry(lines)
                break_it()
                self.assertIsNone(self.fast()[0], name)
        self.setUp()
        self.assertIsNone(self.fast()[0])                                          # no carry
        self.carry(lines)
        for inv in ({**self.inv, "round": 2}, {**self.inv, "restock": {**self.inv["restock"], "open": False}},
                    {**self.inv, "restock": {**self.inv["restock"], "closes_at": self.now - 1}}):
            self.assertIsNone(self.fast(inv)[0])
        with mock.patch.object(restock, "warranted_sales", return_value=None):     # exposure unknown
            self.assertIsNone(self.fast()[0])
        self.setUp()
        self.carry(lines)
        with mock.patch.object(wf, "arm", return_value="A"):
            self.assertIsNone(self.fast()[0])
        self.assertEqual(self.fast()[0], {"buy": lines})                           # and arm A did not spend the carry

    def test_the_cash_buffer_and_the_lines_already_sent_bound_it(self):
        # 6 h of warranted goods sales worth 10M: the buffer keeps at least the three biggest in full
        sales = [(self.now - 60, 4000000), (self.now - 120, 3000000), (self.now - 180, 3000000)]
        self.carry([{"sku": "G1", "qty": 4}, {"sku": "G2", "qty": 3}])
        inv = {**self.inv, "balance": 13000000}
        self.write("inventory.json", inv)
        with mock.patch.object(restock, "warranted_sales", return_value=sales):
            out, err = self.fast(inv)
        buffer = hard_limits.cash_buffer(0, sales)
        self.assertGreaterEqual(buffer, 10500000)
        spent = sum(CATALOG[b["sku"]]["list_price"] * 0.75 * (1 + hard_limits.BUY_STORAGE) * b["qty"] for b in out["buy"])
        self.assertLessEqual(spent, 13000000 - buffer)
        self.assertEqual(out, {"buy": [{"sku": "G1", "qty": 3}]})                  # 2.5M over the buffer: three sofas, no bed
        # a line sent a minute ago and already paid for is still reserved in full: tighter than the usual run, never looser
        self.setUp()
        self.carry([{"sku": "G1", "qty": 4}])
        self.write(restock.SENT, [{"at": self.now - 60, "sku": "G2", "qty": 3, "worst": 375000},
                                  {"at": self.now - 60, "sku": "G1", "qty": 1, "worst": 750000}])
        inv = {**self.inv, "balance": 4500000}
        with mock.patch.object(restock, "warranted_sales", return_value=[]):
            out, _ = self.fast(inv)
        # 4.5M - 1M line - (3 x 375k + 750k) on their way = 1.625M: two sofas at 757.5k; and G1 counts 1 on its way
        self.assertEqual(out, {"buy": [{"sku": "G1", "qty": 2}]})
        self.setUp()
        self.carry([{"sku": "G1", "qty": 4}])
        with mock.patch.object(restock, "warranted_sales", return_value=[]):
            self.assertIsNone(self.fast({**self.inv, "balance": 1500000})[0])     # nothing fits: the usual run decides

    def test_the_guard_and_the_last_word_each_keep_the_cash_on_their_own(self):
        # independent review 0c27ae5 I2: either defence alone passed the test above. Balance 4.5M, buffer 1M, lines sent
        # a minute ago worth 1.875M still on their way: 1.625M may be spent, two sofas at 757.5k
        def run(**patches):
            self.setUp()
            self.carry([{"sku": "G1", "qty": 4}])
            self.write(restock.SENT, [{"at": self.now - 60, "sku": "G2", "qty": 3, "worst": 375000},
                                      {"at": self.now - 60, "sku": "G3", "qty": 2, "worst": 375000}])
            with contextlib_stack() as stack:
                stack.enter_context(mock.patch.object(restock, "warranted_sales", return_value=[]))
                for name, fn in patches.items():
                    stack.enter_context(mock.patch.object(*name_of(name), side_effect=fn))
                return self.fast({**self.inv, "balance": 4500000})[0]

        self.assertEqual(run(), {"buy": [{"sku": "G1", "qty": 2}]})
        seen = {}
        real_guard, real_final = restock.guard, hard_limits.final_buy

        def loose_guard(buy, inv_, catalog, cfg, breaks=None, waiting=()):      # a guard that forgot the cash
            seen["guard"] = (cfg.get("cash_buffer"), [dict(w) for w in waiting])
            return [dict(b) for b in buy]

        def loose_final(buy, balance, catalog, worst=0.75, buffer=hard_limits.STOP_BUYING_BELOW):
            seen["final"] = buffer
            return [dict(b) for b in buy]

        self.assertEqual(run(**{"restock.guard": loose_guard}), {"buy": [{"sku": "G1", "qty": 2}]})    # final_buy alone holds
        self.assertEqual(seen["guard"][0], hard_limits.cash_buffer(1875000, []))    # the buffer main() computes, handed to guard
        self.assertEqual(sorted((w["sku"], w["qty"], w["cash_qty"], w["worst"]) for w in seen["guard"][1]),
                         [("G2", 3, 3, 375000), ("G3", 2, 2, 375000)])
        self.assertEqual(run(**{"hard_limits.final_buy": loose_final}), {"buy": [{"sku": "G1", "qty": 2}]})   # guard alone holds
        self.assertEqual(seen["final"], hard_limits.cash_buffer(1875000, []) + 1875000)   # buffer + what the sent lines owe
        both = run(**{"restock.guard": loose_guard, "hard_limits.final_buy": loose_final})
        self.assertEqual(both, {"buy": [{"sku": "G1", "qty": 4}]})                   # the test can tell: with neither, four
        self.assertIs(restock.guard, real_guard)
        self.assertIs(hard_limits.final_buy, real_final)

    def test_the_buffer_keeps_the_storage_of_the_lines_on_their_way(self):
        # independent review a477814 m1: the stock the buffer's storage day is taken on includes the sent lines
        sales = [(self.now - 60, 2000000)]                                 # over the 1M floor: the storage term shows
        self.carry([{"sku": "G1", "qty": 1}])
        self.write(restock.SENT, [{"at": self.now - 60, "sku": "G2", "qty": 20, "worst": 375000}])
        inv = {**self.inv, "units": [{"unit": "u1", "sku": "G3", "kind": "goods", "cost": 300000, "supplied_at": self.now - 50}]}
        with mock.patch.object(restock, "warranted_sales", return_value=sales):
            out, err = self.fast(inv)
        want = hard_limits.cash_buffer(300000 + 20 * 375000, sales)
        self.assertGreater(want, hard_limits.cash_buffer(300000, sales))   # 75,000 of storage on the 7.5M on its way
        self.assertIn(f"cash buffer {want} ", err)
        self.assertEqual(out, {"buy": [{"sku": "G1", "qty": 1}]})

    def test_sent_waiting_is_never_less_than_pending(self):
        sent = [{"at": self.now - 30, "sku": "G1", "qty": 2, "worst": 750000}, {"at": self.now - 300, "sku": "G2", "qty": 1, "worst": 1},
                {"at": self.now - 10, "sku": "F1", "qty": 20, "worst": 15000}, "junk", {"at": self.now - 10, "sku": "G3", "qty": True}]
        self.write(restock.SENT, sent)
        inv = {**self.inv, "units": [{"unit": "u1", "sku": "G1", "supplied_at": self.now - 20, "cost": 1}]}
        usual = {w["sku"]: w for w in restock.pending(self.md, inv, self.now)}
        fast = {w["sku"]: w for w in restock.sent_waiting(self.md, self.now)}
        self.assertEqual(set(fast), set(usual))
        self.assertEqual(usual["G1"]["qty"], 1)                                    # one of the two has arrived
        for sku in usual:
            self.assertGreaterEqual(fast[sku]["qty"], usual[sku]["qty"])
            self.assertEqual(fast[sku]["cash_qty"], usual[sku]["cash_qty"])
            self.assertEqual(fast[sku]["worst"], usual[sku]["worst"])
        self.assertEqual(restock.sent_waiting(tempfile.mkdtemp(), self.now), [])   # nothing sent yet
        self.write(restock.SENT, [{"at": self.now - 5, "sku": "G1", "qty": 1, "worst": -1}])
        with self.assertRaises(ValueError):
            restock.sent_waiting(self.md, self.now)

    def test_held_skus_and_the_window_close_cut_the_lines(self):
        self.carry([{"sku": "H1", "qty": 1}, {"sku": "G1", "qty": 1}, {"sku": "G2", "qty": 1}, {"sku": "G3", "qty": 1}])
        inv = {**self.inv, "restock": {**self.inv["restock"], "closes_at": self.now + 17}}
        out, err = self.fast(inv)
        self.assertEqual(out, {"buy": [{"sku": "G1", "qty": 1}, {"sku": "G2", "qty": 1}]})    # H1 is next_window's; 12 s: two calls
        self.assertIn("held back", err)

    def test_main_takes_the_fast_run_and_skips_the_ledger_read(self):
        self.carry([{"sku": "G2", "qty": 2}])
        here = os.path.dirname(os.path.abspath(restock.__file__))
        out, ticks = io.StringIO(), []
        with mock.patch.object(restock.sys, "argv", ["restock.py", "--state", self.md]), \
                mock.patch.object(restock, "load", side_effect=lambda p, d: self.pricing if p == "pricing.json" else restock_load(p, d)), \
                mock.patch.object(restock, "read_model", side_effect=AssertionError("the usual run")), \
                mock.patch.object(measure, "tick", side_effect=lambda *a, **k: ticks.append((a, k))), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(restock.main(), 0)
        printed = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(printed["buy"], [{"sku": "G2", "qty": 2}])
        # the kit reads the balance again before each line: the run's buffer and guard()'s cost of a unit go with them
        self.assertEqual(printed["cash"], {"floor": hard_limits.cash_buffer(0, []), "unit": {"G2": 500000 * 0.75 * 1.01}})
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0][0][2], [{"sku": "G2", "qty": 2}])
        self.assertIs(ticks[0][1].get("skip_sales"), True)
        self.assertEqual(ticks[0][1].get("window_arm"), "B-fast")                  # a study's fast run; the rule's: default-fast
        self.assertTrue(os.path.isdir(here))


restock_load = restock.load


class MainSlowRunTest(unittest.TestCase):
    """restock.main()'s usual run in arm B (independent review 0c27ae5 I1): the held SKU is planned out before the cut,
    and a carry is left only when no agent line waits, no trial runs and no opening decides. The demand model, the
    agent's turn and the hooks are stubs; plan(), guard(), the queue file and the carry file are the real ones."""

    def setUp(self):
        self.now = time.time()
        self.md = tempfile.mkdtemp()
        self.cat = {**CATALOG, **{f"G{i}": {"kind": "goods", "category": f"가구>c{i}", "list_price": 200000} for i in range(4, 25)}}
        skus = ["H1"] + [f"G{i}" for i in range(1, 25)]
        self.pricing = {"restock": {"priority_skus": skus, "sku_target_units": {s: 2 for s in skus}, "keep_cash": 0,
                                    "max_lines": 10, "explore_lines": 0, "high_price_goods": 100000,
                                    "high_price_max_units": 20, "high_price_proven_max_units": 20,
                                    "max_stock_cost": 10 ** 9, "fill_listed": True, "round_ends_at": None},
                        "next_window": {"id": "t1", "targets": {"H1": 1}, "hold_after_done": True}}
        self.model = {"active": True, "cats": set(), "skus": {s: {"rate": 1.0, "my_share": 0.1, "price_ratio": 0.7} for s in skus}}
        self.inv = {"round": 3, "balance": 10 ** 9, "bankrupt_line": 500000, "units": [],
                    "restock": {"open": False, "opens_at": self.now + 20, "closes_at": self.now + 200}}
        self.write("inventory.json", self.inv)
        self.write("catalog.json", self.cat)
        self.write(restock.NEXT_WINDOW_STATE, {"id": "t1", "done": True})
        switch(self.md, on(self.now))

    def write(self, name, d):
        with open(os.path.join(self.md, name), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def run_main(self, agent=None, env=None):
        """main()'s last JSON line and the carry file's lines (None when there is none). agent: what agent_turn returns."""
        out, stubs = io.StringIO(), ("loss_watch.check", "agent_memory.maybe_consolidate", "shop_forum.maybe_run",
                                     "tool_forge.maybe_run", "rival_vote.maybe_run", "forum.maybe_run")
        self.ticks = []
        with contextlib_stack() as stack:
            stack.enter_context(mock.patch.object(restock.sys, "argv", ["restock.py", "--state", self.md]))
            stack.enter_context(mock.patch.object(
                restock, "load", side_effect=lambda p, d: self.pricing if p == "pricing.json" else restock_load(p, d)))
            stack.enter_context(mock.patch.object(restock, "read_model", return_value=self.model))
            stack.enter_context(mock.patch.object(restock, "read_breaks", return_value=None))
            stack.enter_context(mock.patch.object(restock, "warranted_sales", return_value=[]))
            stack.enter_context(mock.patch.object(restock, "rows_of", return_value=[]))
            stack.enter_context(mock.patch.object(restock, "log_prices", return_value=None))
            stack.enter_context(mock.patch.object(restock, "agent_turn", return_value=agent))
            stack.enter_context(mock.patch.object(measure, "tick", side_effect=lambda *a, **k: self.ticks.append(k)))
            for name in stubs:
                mod, fn = name.split(".")
                stack.enter_context(mock.patch.object(getattr(restock, mod), fn, return_value=None))
            stack.enter_context(mock.patch.dict(os.environ, {"HARNESS": "", **(env or {})}))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(io.StringIO()))
            self.assertEqual(restock.main(), 0)
        self.printed = json.loads(out.getvalue().strip().splitlines()[-1])
        buy = self.printed["buy"]
        path = os.path.join(self.md, wf.CARRY)
        return buy, (json.load(open(path))["lines"] if os.path.exists(path) else None)

    def test_arm_b_plans_the_held_sku_out_and_leaves_the_next_ten(self):
        buy, carry = self.run_main()
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 11)])          # ten lines, none of them H1
        self.assertEqual([b["sku"] for b in carry], [f"G{i}" for i in range(11, 21)])       # the plan's next ten
        self.assertEqual(self.ticks[-1].get("window_arm"), "B")                              # the arm is on the sent line
        self.assertEqual(json.load(open(os.path.join(self.md, wf.CARRY)))["opens_at"], self.inv["restock"]["opens_at"])

    def test_the_default_rule_runs_main_as_arm_b_without_a_switch(self):
        os.remove(os.path.join(self.md, wf.SWITCH))
        self.pricing["restock"]["window_fast"] = True
        buy, carry = self.run_main()
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 11)])
        self.assertEqual([b["sku"] for b in carry], [f"G{i}" for i in range(11, 21)])
        self.assertEqual(self.ticks[-1].get("window_arm"), "default")              # told from a study's arm B
        with mock.patch.dict(os.environ, {"RESTOCK_WINDOW_FAST": "off"}):          # the stop in the environment holds
            self.assertEqual(restock.window_fast_arm(self.md, {**restock.DEFAULTS, "window_fast": True}, self.inv), (None, None))

    def test_off_and_arm_a_print_todays_nine_and_leave_no_carry(self):
        os.remove(os.path.join(self.md, wf.SWITCH))
        buy, carry = self.run_main()
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 10)])           # H1 planned, then dropped: nine
        self.assertIsNone(carry)
        self.assertIsNone(self.ticks[-1].get("window_arm"))                                  # off: no arm
        # the usual run hands the kit its cash floor and guard()'s cost of a unit with the lines, switch or not
        # (independent review 1e2c08b I1: nothing pinned it; without it the kit sends as before, unchecked)
        self.assertEqual(self.printed["cash"]["floor"], hard_limits.cash_buffer(0, []))
        self.assertEqual(self.printed["cash"]["unit"], {b["sku"]: self.cat[b["sku"]]["list_price"] * 0.75 * 1.01 for b in buy})
        os.remove(os.path.join(self.md, restock.SENT))       # the lines that run noted would count as on their way
        switch(self.md, on(self.now))
        with mock.patch.object(wf, "arm", return_value="A"):
            buy, carry = self.run_main()
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 10)])
        self.assertIsNone(carry)

    def test_no_carry_while_an_agent_line_waits_and_an_old_one_is_removed(self):
        wf.save_carry(self.md, self.inv, [{"sku": "G20", "qty": 1}], now=self.now - 30)      # a carry of an earlier run
        self.write("restock_queue.json", {"at": self.now, "lines": [{"sku": "G24", "qty": 1}]})
        buy, carry = self.run_main()
        self.assertTrue(buy)
        self.assertIsNone(carry)             # the queue's lines are the next run's: nothing carried, the old carry gone
        self.write("restock_queue.json", {"at": self.now, "lines": []})
        self.assertIsNotNone(self.run_main()[1])

    def test_the_agents_run_carries_only_when_none_of_its_lines_waits(self):
        self.cat["A1"] = {"kind": "goods", "category": "가구>agent", "list_price": 50000}      # the agent's own, on no list
        self.write("catalog.json", self.cat)
        self.write("restock_queue.json", {"at": self.now, "lines": []})
        buy, carry = self.run_main(agent=[], env={"HARNESS": "stub"})                        # its queue is sent: the formula fills
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 11)])           # ten, H1 planned out
        self.assertEqual([b["sku"] for b in carry], [f"G{i}" for i in range(11, 21)])
        os.remove(os.path.join(self.md, restock.SENT))
        # an agent line the ten listed lines push past max_lines goes back to the queue: it is the next run's, no carry
        buy, carry = self.run_main(agent=[{"sku": "A1", "qty": 1}], env={"HARNESS": "stub"})
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 11)])
        self.assertEqual(restock_load(os.path.join(self.md, "restock_queue.json"), {})["lines"], [{"sku": "A1", "qty": 1}])
        self.assertIsNone(carry)

    def test_no_carry_when_a_trial_runs_the_opening_decides_or_nothing_is_bought(self):
        self.pricing["next_window"] = {"id": "t2", "targets": {"H1": 1}, "expires_hours": 2}     # a new plan: its window
        os.remove(os.path.join(self.md, restock.NEXT_WINDOW_STATE))
        self.inv["restock"] = {"open": True, "opens_at": self.now - 10, "closes_at": self.now + 170}
        self.write("inventory.json", self.inv)
        buy, carry = self.run_main()
        self.assertEqual(buy[0], {"sku": "H1", "qty": 1})                                     # the trial's line goes first
        self.assertIsNone(carry)
        self.setUp()
        with mock.patch.object(restock.opening, "lines", return_value=([{"sku": "G1", "qty": 1}], "phase 1")):
            buy, carry = self.run_main()
        self.assertEqual(buy, [{"sku": "G1", "qty": 1}])
        self.assertIsNone(carry)
        self.setUp()
        closed = {**self.inv, "restock": {"open": False, "opens_at": self.now + 900, "closes_at": self.now + 1080}}
        self.write("inventory.json", closed)
        wf.save_carry(self.md, closed, [{"sku": "G20", "qty": 1}], now=self.now - 30)
        buy, carry = self.run_main()
        self.assertEqual(buy, [])                                                              # supply closed: nothing sent
        self.assertIsNone(carry)                                                               # and the old carry is gone


def name_of(dotted):
    mod, fn = dotted.split(".")
    return {"restock": restock, "hard_limits": hard_limits}[mod], fn


def contextlib_stack():
    import contextlib
    return contextlib.ExitStack()


class CarryPlanTest(unittest.TestCase):
    """What the first run of a window leaves for the second: the plan's next lines, the same guard."""

    def setUp(self):
        self.now = time.time()
        self.cat = {**CATALOG, **{f"G{i}": {"kind": "goods", "category": f"가구>c{i}", "list_price": 200000} for i in range(4, 20)}}
        skus = ["H1"] + [f"G{i}" for i in range(1, 20)]
        self.cfg = {**restock.DEFAULTS, "priority_skus": skus, "high_price_proven_skus": [],
                    "sku_target_units": {s: 2 for s in skus}, "keep_cash": 0, "cash_buffer": 0, "max_lines": 10,
                    "explore_lines": 0, "max_stock_cost": float("inf"), "high_price_goods": 100000, "high_price_max_units": 20,
                    "high_price_proven_max_units": 20, "high_price_listed_only": True, "fill_listed": True,
                    "round_ends_at": None, "fresh_slots": None}
        self.model = {"active": True, "cats": set(), "skus": {s: {"rate": 1.0, "my_share": 0.1, "price_ratio": 0.7} for s in skus}}
        self.inv = {"balance": 10 ** 9, "units": []}

    def test_a_held_sku_takes_no_place_among_the_ten(self):
        usual = restock.plan(self.inv, self.cat, [], self.cfg, self.model)["buy"]
        self.assertEqual([b["sku"] for b in usual][:2], ["H1", "G1"])
        self.assertEqual(len(restock.with_trial(None, usual, self.inv, self.cat, self.cfg, hold={"H1"})), 9)     # today: nine
        buy = restock.plan(self.inv, self.cat, [], restock.without_held(self.cfg, {"H1"}), self.model)["buy"]
        self.assertEqual([b["sku"] for b in buy], [f"G{i}" for i in range(1, 11)])
        self.assertEqual(restock.with_trial(None, buy, self.inv, self.cat, self.cfg, hold={"H1"}), buy)           # ten
        self.assertEqual(restock.without_held({**self.cfg, "skip_skus": ["G3", 5]}, {"H1"})["skip_skus"], ["G3", 5, "H1"])
        self.assertEqual(restock.without_held({**self.cfg, "skip_skus": "x"}, set())["skip_skus"], [])

    def test_the_carry_is_the_plans_next_lines_without_the_sent_and_the_held(self):
        sent = restock.plan(self.inv, self.cat, [], restock.without_held(self.cfg, {"H1"}), self.model)["buy"]
        err = io.StringIO()
        with redirect_stderr(err):
            carry = restock.carry_lines(self.inv, self.cat, [], self.cfg, self.model, None, (), sent, {"H1"})
        self.assertEqual(carry, [{"sku": f"G{i}", "qty": 2} for i in range(11, 20)])
        self.assertEqual(err.getvalue(), "")                                       # the guard's lines are not printed twice
        # the cash the sent lines take is counted before the carried ones: G1-G3 (1M, 500k, 400k) and G4-G10 (200k) x 2 at
        # 0.7575 of list are sent, and what is over that and the 1M line pays for 3 more units
        first_ten = 2 * (757500 + 378750 + 303000) + 14 * 151500
        tight = {**self.inv, "balance": hard_limits.STOP_BUYING_BELOW + first_ten + 3 * 151500 + 1000}
        carry = restock.carry_lines(tight, self.cat, [], self.cfg, self.model, None, (), sent, {"H1"})
        self.assertEqual(carry, [{"sku": "G11", "qty": 2}, {"sku": "G12", "qty": 1}])
        self.assertEqual(restock.carry_lines(self.inv, self.cat, [], self.cfg, None, None, (), sent, set()), [])   # no model


if __name__ == "__main__":
    unittest.main()
