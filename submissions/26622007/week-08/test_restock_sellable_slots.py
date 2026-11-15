"""restock_sellable_slots: a listed goods SKU's unit target counts only the units that can still sell at a profit (not a
dear draw held past dear_hours, not a unit held past hours), held to 3 x the target and a cash floor, and the listed
refills go by what one supply call is expected to net (W-20261115-1605-AX매입칸)."""
import collections
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import meta_harness
import restock
import restock_sellable_slots as ss

NOW = 1794730000.0
ON = {"study": "sellable-test", "hours": 6.0, "dear_share": 0.70, "dear_hours": 1.0, "hold_times": 3,
      "cash_floor": 100_000_000, "fresh_sell": 0.39, "start_at": NOW - 60, "stop_at": NOW + 6 * 3600}
CATALOG = {"G1": {"kind": "goods", "category": "가구>소파", "list_price": 1000000},
           "G2": {"kind": "goods", "category": "가구>침대", "list_price": 500000},
           "F1": {"kind": "food", "category": "식품>김치", "list_price": 20000},
           "U1": {"kind": "goods", "category": "가전>컵", "list_price": 30000}}
CFG = {"high_price_proven_skus": [], "priority_skus": ["G1", "G2", "F1"], "sku_target_units": {"G1": 4, "G2": 2, "F1": 9}}


def unit(sku, share=0.60, age_h=0.2, kind="goods", now=NOW):
    lp = CATALOG[sku]["list_price"]
    return {"sku": sku, "kind": kind, "cost": round(lp * share), "list_price": lp, "supplied_at": now - age_h * 3600}


def switch(d, **over):
    md = tempfile.mkdtemp()
    with open(os.path.join(md, ss.SWITCH), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, **d, **over}, f)
    return md


class SwitchTest(unittest.TestCase):
    def test_on_only_inside_a_finite_window_with_sane_numbers(self):
        self.assertEqual(ss.config(switch(ON), env={}, now=NOW)["study"], "sellable-test")
        self.assertIsNone(ss.config(tempfile.mkdtemp(), env={}, now=NOW))
        self.assertIsNone(ss.config(switch(ON, enabled=False), env={}, now=NOW))
        self.assertIsNone(ss.config(switch(ON, enabled="true"), env={}, now=NOW))
        self.assertIsNone(ss.config(switch(ON), env={}, now=ON["start_at"] - 1))
        self.assertIsNone(ss.config(switch(ON), env={}, now=ON["stop_at"]))
        self.assertIsNone(ss.config(switch(ON), env={"RESTOCK_SELLABLE_SLOTS": "0"}, now=NOW))
        for bad in ({"stop_at": None}, {"start_at": "x"}, {"stop_at": float("inf")}, {"stop_at": NOW + 13 * 3600},
                    {"hours": 0}, {"hours": 100}, {"dear_share": 0.3}, {"dear_share": 1.2}, {"dear_hours": -1},
                    {"hold_times": 0}, {"hold_times": 2.5}, {"hold_times": True}, {"hold_times": 9},
                    {"cash_floor": 1000}, {"cash_floor": None}, {"fresh_sell": 0}, {"fresh_sell": 1.5}, {"study": ""}):
            self.assertIsNone(ss.config(switch(ON, **bad), env={}, now=NOW), bad)
        md = tempfile.mkdtemp()
        with open(os.path.join(md, ss.SWITCH), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(ss.config(md, env={}, now=NOW))

    def test_switch_on_writes_what_config_reads_and_refuses_a_bad_window(self):
        md = tempfile.mkdtemp()
        ss.switch_on(md, study="x", start_at=NOW, hours=6)
        got = ss.config(md, env={}, now=NOW + 1)
        self.assertEqual((got["study"], got["stop_at"], got["cash_floor"], got["hold_times"]),
                         ("x", NOW + 6 * 3600, ss.CASH_FLOOR, ss.HOLD_TIMES))
        self.assertEqual(os.stat(os.path.join(md, ss.SWITCH)).st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            ss.switch_on(md, study="x", start_at=NOW, hours=13)
        with self.assertRaises(ValueError):
            ss.switch_on(md, study="x", start_at=NOW, hours=6, cash_floor=1)
        self.assertIsNotNone(ss.config(md, env={}, now=NOW + 1))     # the old file stays

    def test_the_floors_cannot_be_set_under_the_safety_lines(self):
        self.assertGreaterEqual(ss.CASH_FLOOR_LOW, 100_000_000)
        self.assertLessEqual(ss.HOLD_HIGH, 3)

    def test_the_meta_harness_watches_the_switch(self):
        self.assertIn(".market/" + ss.SWITCH, meta_harness.SWITCHES)


class StuckTest(unittest.TestCase):
    def test_a_unit_is_stuck_when_old_or_a_dear_draw_that_waited(self):
        self.assertFalse(ss.stuck(unit("G1", 0.60, 0.2), ON, NOW))
        self.assertFalse(ss.stuck(unit("G1", 0.60, 5.9), ON, NOW))
        self.assertTrue(ss.stuck(unit("G1", 0.60, 6.0), ON, NOW))            # past hours: sells at ~break-even
        self.assertFalse(ss.stuck(unit("G1", 0.72, 0.5), ON, NOW))           # dear but new: may still sell
        self.assertTrue(ss.stuck(unit("G1", 0.72, 1.0), ON, NOW))            # dear and waited dear_hours
        self.assertTrue(ss.stuck(unit("G1", 0.70, 1.5), ON, NOW))            # the share is inclusive
        self.assertFalse(ss.stuck(unit("G1", 0.699, 1.5), ON, NOW))

    def test_a_unit_without_usable_numbers_is_not_stuck(self):
        for bad in ({"supplied_at": None}, {"cost": None}, {"list_price": 0}, {"cost": True}, {"supplied_at": "x"}):
            u = {**unit("G1", 0.72, 3.0), **bad}
            if "supplied_at" in bad:
                self.assertFalse(ss.stuck(u, ON, NOW), bad)
            else:   # no share: only the age reads
                self.assertFalse(ss.stuck({**u, "supplied_at": NOW - 3600 * 3}, ON, NOW), bad)


class CountedTest(unittest.TestCase):
    def counted(self, held, cfg=None, factors=None, on=ON):
        return ss.counted(held, {**CFG, **(cfg or {}), "sellable_slots": on}, CATALOG, factors or {}, NOW)

    def test_off_counts_every_unit(self):
        held = [unit("G1", 0.72, 3)] * 3 + [unit("F1", 0.5, 9, kind="food")] * 2
        self.assertEqual(self.counted(held, on=None)[0], {"G1": 3, "F1": 2})
        self.assertEqual(self.counted(held, on={**ON, "stop_at": NOW - 1})[0], {"G1": 3, "F1": 2})

    def test_stuck_units_leave_the_target_count(self):
        held = [unit("G1", 0.60, 0.2)] + [unit("G1", 0.72, 3)] * 3       # target 4: 1 sellable, 3 stuck
        n, changed = self.counted(held)
        self.assertEqual(n["G1"], 1)
        self.assertEqual(changed["G1"], {"held": 4, "stuck": 3, "counted": 1})

    def test_never_more_than_hold_times_the_target(self):
        held = [unit("G2", 0.72, 3)] * 5           # target 2, hold 3x = 6: counted at least 5 - 4 = 1
        self.assertEqual(self.counted(held)[0]["G2"], 1)
        held = [unit("G2", 0.72, 3)] * 7
        self.assertEqual(self.counted(held)[0]["G2"], 3)          # 7 - 4: no room left under the target
        self.assertEqual(self.counted(held, on={**ON, "hold_times": 1})[0]["G2"], 7)     # 1x: today's count

    def test_food_unlisted_untargeted_and_surging_skus_count_every_unit(self):
        held = [unit("F1", 0.5, 9, kind="food")] * 3 + [unit("U1", 0.72, 9)] * 2 + [unit("G1", 0.72, 9)] * 2
        n = self.counted(held, factors={"G1": 1.2})
        self.assertEqual((n[0]["F1"], n[0]["U1"], n[0]["G1"]), (3, 2, 2))
        self.assertEqual(self.counted([unit("G1", 0.72, 9)] * 2, cfg={"sku_target_units": {}})[0]["G1"], 2)

    def test_trouble_inside_is_the_old_count(self):
        held = [unit("G1", 0.72, 9)] * 2
        with mock.patch.object(ss, "stuck", side_effect=RuntimeError("x")):
            self.assertEqual(self.counted(held)[0]["G1"], 2)


class ValueTest(unittest.TestCase):
    MODEL = {"skus": {"G1": {"price_ratio": 0.68}, "G2": {"price_ratio": 0.60}, "F1": {"price_ratio": 0.70}}}

    def test_net_per_unit_from_the_market_price_break_and_sell_chance(self):
        # goods: list x (ratio x (1 - break) - 0.59) x fresh_sell; food sells within its shelf
        g1 = ss.unit_net("G1", CATALOG, self.MODEL, ON, prior_break=0.05)
        self.assertAlmostEqual(g1, 1000000 * (0.68 * 0.95 - 0.59) * 0.39)
        self.assertAlmostEqual(ss.unit_net("F1", CATALOG, self.MODEL, ON, prior_break=0.05), 20000 * (0.70 - 0.50))
        self.assertLess(ss.unit_net("G2", CATALOG, self.MODEL, ON, prior_break=0.05), 0)
        self.assertIsNone(ss.unit_net("U1", CATALOG, self.MODEL, ON, prior_break=0.05))     # no model row

    def test_listed_refills_go_by_net_per_call_after_the_empty_skus(self):
        cfg = {**CFG, "priority_skus": ["F1", "G2", "G1"], "sku_target_units": {"G1": 4, "G2": 2, "F1": 9},
               "sellable_slots": ON, "fresh_now": NOW, "prior_break": 0.05}
        model = {"skus": {"G1": {"rate": 2, "my_share": 0.1, "price_ratio": 0.68},
                          "G2": {"rate": 2, "my_share": 0.1, "price_ratio": 0.68},
                          "F1": {"rate": 50, "my_share": 0.1, "price_ratio": 0.70}}}
        # G1: 1 sellable + 3 stuck -> need 3 x 34.6k; F1: 8 held of 9 -> 1 x 4k; G2 empty -> first as today
        inv = {"units": [unit("G1", 0.60, 0.2)] + [unit("G1", 0.72, 3)] * 3 + [unit("F1", 0.5, 0.2, kind="food")] * 8}
        with mock.patch.object(restock.time, "time", return_value=NOW):
            order = restock.urgent_listed(cfg, inv, CATALOG, (), model)
            self.assertEqual(order, ["G2", "G1", "F1"])
            off = restock.urgent_listed({**cfg, "sellable_slots": None}, inv, CATALOG, (), model)
        self.assertEqual(off, ["G2", "F1", "G1"])         # today: by the hours the held units last

    def test_the_order_uses_the_measured_break_rate_not_prior_break(self):
        # review ad9cee9 I1: at prior_break 0.15 both nets go negative and the small line came first; the ledger's
        # rates (5-10%) make them positive and the line that nets most goes first
        cat = {**CATALOG, "G3": {"kind": "goods", "category": "가구>책상", "list_price": 1000000}}
        cfg = {**CFG, "priority_skus": ["G3", "G1"], "sku_target_units": {"G1": 10, "G3": 2},
               "sellable_slots": ON, "fresh_now": NOW, "prior_break": 0.15}
        model = {"skus": {s: {"rate": 2, "my_share": 0.1, "price_ratio": 0.68} for s in ("G1", "G3")}}
        inv = {"units": [unit("G1", 0.72, 3)] * 9 + [{**unit("G1", 0.72, 3), "sku": "G3"}]}
        breaks = {"sold": 1000, "rate": 0.06, "by_sku": {"G1": {"sold": 500, "broke": 30}, "G3": {"sold": 500, "broke": 30}}}
        with mock.patch.object(restock.time, "time", return_value=NOW):
            self.assertEqual(restock.urgent_listed(cfg, inv, cat, (), model, breaks), ["G1", "G3"])
        self.assertGreater(ss.unit_net("G1", cat, model, ON, prior_break=0.06), 0)
        self.assertLess(ss.unit_net("G1", cat, model, ON, prior_break=0.15), 0)

    def test_surge_raises_the_expected_cost(self):
        a = ss.unit_net("G1", CATALOG, self.MODEL, ON, prior_break=0.05)
        self.assertLess(ss.unit_net("G1", CATALOG, self.MODEL, ON, prior_break=0.05, factor=1.04), a)

    def test_need_keeps_to_max_units_per_sku(self):
        cfg = {**CFG, "sku_target_units": {"G1": 10}, "sellable_slots": ON, "fresh_now": NOW, "max_units_per_sku": 12}
        have, counts = collections.Counter({"G1": 10}), collections.Counter({"G1": 0})
        v = ss.call_value("G1", cfg, CATALOG, self.MODEL, have, counts, 0.05)
        self.assertAlmostEqual(v, 2 * ss.unit_net("G1", CATALOG, self.MODEL, ON, prior_break=0.05))

    def test_a_fast_selling_held_food_goes_before_the_goods_refills(self):
        # review ad9cee9 M1: a food that lasts 0.4 h is not left behind ~3 windows of goods refills
        cfg = {**CFG, "priority_skus": ["G1", "F1"], "sellable_slots": ON, "fresh_now": NOW, "prior_break": 0.05}
        model = {"skus": {"G1": {"rate": 2, "my_share": 0.1, "price_ratio": 0.68},
                          "F1": {"rate": 50, "my_share": 0.5, "price_ratio": 0.70}}}
        inv = {"units": [unit("G1", 0.72, 3)] * 4 + [unit("F1", 0.5, 0.2, kind="food")] * 8}     # F1: 8 / 25 an hour
        with mock.patch.object(restock.time, "time", return_value=NOW):
            self.assertEqual(restock.urgent_listed(cfg, inv, CATALOG, (), model), ["F1", "G1"])
            slow = {"skus": {**model["skus"], "F1": {"rate": 2, "my_share": 0.5, "price_ratio": 0.70}}}
            self.assertEqual(restock.urgent_listed(cfg, inv, CATALOG, (), slow), ["G1", "F1"])


class PlanGuardTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {**restock.DEFAULTS, "high_price_proven_skus": [], "priority_skus": ["G1"],
                    "sku_target_units": {"G1": 4}, "fill_listed": True, "keep_cash": 0, "max_stock_cost": 10 ** 12,
                    "max_units_per_sku": 20, "high_price_proven_max_units": 20, "high_price_max_units": 20,
                    "cash_buffer": 0, "fresh_now": NOW, "round_ends_at": NOW + 50 * 3600, "prior_break": 0.05}
        self.held = [unit("G1", 0.60, 0.2)] + [unit("G1", 0.72, 3)] * 3

    def guard(self, on, balance=400_000_000, qty=10, held=None):
        inv = {"balance": balance, "units": list(self.held if held is None else held)}
        with mock.patch.object(restock.time, "time", return_value=NOW):
            return restock.guard([{"sku": "G1", "qty": qty}], inv, CATALOG, {**self.cfg, "sellable_slots": on})

    def test_off_is_todays_cap(self):
        self.assertEqual(self.guard(None), [])                  # 4 held of 4: nothing
        self.assertEqual(self.guard(None, held=self.held[:2]), [{"sku": "G1", "qty": 2}])

    def test_on_fills_the_sellable_count_up_to_the_target(self):
        self.assertEqual(self.guard(ON), [{"sku": "G1", "qty": 3}])     # 1 sellable of 4

    def test_on_never_holds_over_hold_times_the_target(self):
        held = [unit("G1", 0.72, 3)] * 11
        self.assertEqual(self.guard(ON, held=held), [{"sku": "G1", "qty": 1}])     # 11 + 1 = 12 = 3 x 4
        self.assertEqual(self.guard(ON, held=held + [unit("G1", 0.72, 3)]), [])

    def test_the_extra_units_keep_the_cash_floor(self):
        worst = 1000000 * restock.WORST_COST * (1 + restock.hard_limits.BUY_STORAGE)
        # 100M floor + 1.5 worst units: one extra unit, not three
        self.assertEqual(self.guard(ON, balance=100_000_000 + int(1.5 * worst)), [{"sku": "G1", "qty": 1}])
        self.assertEqual(self.guard(ON, balance=99_000_000), [])
        # today's room is not held to the floor: under it the old cap still buys
        self.assertEqual(self.guard(ON, balance=50_000_000, held=self.held[:2] + [unit("G1", 0.72, 3)]),
                         [{"sku": "G1", "qty": 1}])

    def test_the_floor_holds_over_the_whole_run_with_todays_lines_behind_the_extras(self):
        # review ad9cee9 I2: extras on a listed line first, then a line of today's rule behind it; the run must leave
        # the balance at the floor or over it, and today's line keeps its units
        cat = {**CATALOG, "G4": {"kind": "goods", "category": "가구>의자", "list_price": 1000000}}
        cfg = {**self.cfg, "priority_skus": ["G1", "G4"], "sku_target_units": {"G1": 4, "G4": 4}}
        each = 1000000 * restock.WORST_COST * (1 + restock.hard_limits.BUY_STORAGE)
        balance = 100_000_000 + int(4.5 * each)
        inv = {"balance": balance, "units": list(self.held)}         # G1: 1 sellable of 4 held; G4: none
        with mock.patch.object(restock.time, "time", return_value=NOW):
            got = restock.guard([{"sku": "G1", "qty": 10}, {"sku": "G4", "qty": 4}], inv, cat,
                                {**cfg, "sellable_slots": ON})
        self.assertEqual(got, [{"sku": "G4", "qty": 4}])       # 4 of today's room kept, 0.5 unit over: no extra
        with mock.patch.object(restock.time, "time", return_value=NOW):
            got = restock.guard([{"sku": "G1", "qty": 10}, {"sku": "G4", "qty": 4}], dict(inv, balance=balance + int(2 * each)),
                                cat, {**cfg, "sellable_slots": ON})
        self.assertEqual(got, [{"sku": "G1", "qty": 2}, {"sku": "G4", "qty": 4}])
        spent = sum(x["qty"] for x in got) * each
        self.assertGreaterEqual(balance + int(2 * each) - spent, 100_000_000 - 1)

    def test_plan_asks_the_sellable_gap_through_fill_listed(self):
        model = {"active": True, "skus": {"G1": {"rate": 2, "my_share": 0.1, "price_ratio": 0.70, "mine_rate": 0.2}},
                 "cats": {}}
        inv = {"balance": 400_000_000, "units": list(self.held)}
        with mock.patch.object(restock.time, "time", return_value=NOW):
            off = restock.plan(inv, CATALOG, [], {**self.cfg, "sellable_slots": None}, model)
            on = restock.plan(inv, CATALOG, [], {**self.cfg, "sellable_slots": ON}, model)
        self.assertEqual(off["buy"], [])
        self.assertEqual(on["buy"], [{"sku": "G1", "qty": 3}])


if __name__ == "__main__":
    unittest.main()
