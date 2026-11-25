"""restock_fresh_slots: a listed goods SKU's unit target counts the units that still sell at the market's price, so the
units that waited past `hours` (dear draws whose break-even floor sits over what buyers pay) no longer stop its refills."""
import json
import os
import tempfile
import time
import unittest

import meta_harness
import restock
import restock_fresh_slots as fs

NOW = 1794290000.0
ON = {"study": "fresh-slots-test", "salt": "s1", "hours": 2.0, "aged_units": 1.0, "step": 3, "treat": 1.0,
      "start_at": NOW - 60, "stop_at": NOW + 3600}
CATALOG = {"G1": {"kind": "goods", "category": "가구>소파", "list_price": 1000000},
           "G2": {"kind": "goods", "category": "가구>침대", "list_price": 500000},
           "F1": {"kind": "food", "category": "식품>김치", "list_price": 20000},
           "U1": {"kind": "goods", "category": "가전>컵", "list_price": 30000}}
CFG = {"high_price_proven_skus": [], "priority_skus": ["G1", "G2", "F1"], "sku_target_units": {"G1": 4, "G2": 2, "F1": 9}}


def units(sku, fresh=0, aged=0, now=NOW, kind="goods"):
    return ([{"sku": sku, "kind": kind, "cost": 1, "supplied_at": now - 600} for _ in range(fresh)]
            + [{"sku": sku, "kind": kind, "cost": 1, "supplied_at": now - 5 * 3600} for _ in range(aged)])


def switch(d, **over):
    md = tempfile.mkdtemp()
    with open(os.path.join(md, fs.SWITCH), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, **{k: v for k, v in d.items()}, **over}, f)
    return md


class SwitchTest(unittest.TestCase):
    def test_on_only_inside_a_finite_window_with_sane_numbers(self):
        self.assertEqual(fs.config(switch(ON), env={}, now=NOW)["study"], "fresh-slots-test")
        self.assertIsNone(fs.config(tempfile.mkdtemp(), env={}, now=NOW))                       # no file
        self.assertIsNone(fs.config(switch(ON, enabled=False), env={}, now=NOW))
        self.assertIsNone(fs.config(switch(ON), env={}, now=ON["start_at"] - 1))                 # before
        self.assertIsNone(fs.config(switch(ON), env={}, now=ON["stop_at"]))                      # the boundary is off
        self.assertIsNone(fs.config(switch(ON), env={"RESTOCK_FRESH_SLOTS": "0"}, now=NOW))
        for bad in ({"stop_at": None}, {"start_at": "x"}, {"stop_at": float("inf")}, {"stop_at": NOW + 13 * 3600},
                    {"hours": 0}, {"hours": 100}, {"aged_units": -1}, {"aged_units": 9}, {"step": 0}, {"step": 2.5},
                    {"step": True}, {"treat": 0.1}, {"treat": 1.5}, {"salt": ""}, {"study": 3}):
            self.assertIsNone(fs.config(switch(ON, **bad), env={}, now=NOW), bad)
        md = tempfile.mkdtemp()
        with open(os.path.join(md, fs.SWITCH), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(fs.config(md, env={}, now=NOW))

    def test_switch_on_writes_what_config_reads_and_refuses_a_bad_window(self):
        md = tempfile.mkdtemp()
        fs.switch_on(md, study="x", salt="y", start_at=NOW, hours=9)
        got = fs.config(md, env={}, now=NOW + 1)
        self.assertEqual((got["study"], got["stop_at"], got["hours"], got["step"]), ("x", NOW + 9 * 3600, fs.HOURS, fs.STEP))
        with self.assertRaises(ValueError):
            fs.switch_on(md, study="x", salt="y", start_at=NOW, hours=13)
        self.assertIsNotNone(fs.config(md, env={}, now=NOW + 1))     # the old file stays

    def test_arms_are_by_sku_and_stable(self):
        half = {**ON, "treat": 0.5}
        arms = [fs.arm(half, f"G{i:08x}") for i in range(400)]
        self.assertEqual(arms, [fs.arm(half, f"G{i:08x}") for i in range(400)])
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertEqual({fs.arm(ON, f"G{i}") for i in range(50)}, {"B"})      # treat 1.0: every SKU
        self.assertIsNone(fs.arm(None, "G1"))
        self.assertNotEqual([fs.arm({**half, "salt": "other"}, f"G{i:08x}") for i in range(400)], arms)

    def test_the_meta_harness_watches_the_switch(self):
        self.assertIn(".market/" + fs.SWITCH, meta_harness.SWITCHES)


class CountedTest(unittest.TestCase):
    def counted(self, held, cfg=None, factors=None, on=ON):
        return fs.counted(held, {**CFG, **(cfg or {}), "fresh_slots": on}, CATALOG, factors or {}, NOW)[0]

    def test_off_counts_every_unit(self):
        held = units("G1", 1, 3) + units("F1", 2, kind="food")
        self.assertEqual(self.counted(held, on=None), {"G1": 4, "F1": 2})

    def test_units_past_the_hours_leave_room_a_step_at_a_time(self):
        # target 4, all four waited 5 h: the old rule buys none; now up to `step` more, and the SKU never holds more than
        # target fresh + aged_units x target waiting units
        self.assertEqual(self.counted(units("G1", 0, 4))["G1"], 1)                    # room 3 = step
        self.assertEqual(self.counted(units("G1", 3, 4))["G1"], 3)                    # 3 fresh: room 1
        self.assertEqual(self.counted(units("G1", 4, 4))["G1"], 4)                    # target fresh units: full
        self.assertEqual(self.counted(units("G1", 0, 6))["G1"], 2)                    # 2 past the 4 aged it may keep
        self.assertEqual(self.counted(units("G1", 0, 9))["G1"], 5)                    # 9 aged: over the target, none bought
        self.assertEqual(self.counted(units("G1", 0, 4), on={**ON, "step": 1})["G1"], 3)
        self.assertEqual(self.counted(units("G1", 0, 4), on={**ON, "aged_units": 0.0})["G1"], 4)   # no aged allowance
        self.assertEqual(self.counted(units("G1", 2, 0))["G1"], 2)                    # nothing aged: as before

    def test_a_sku_never_holds_more_than_target_plus_its_aged_allowance(self):
        held, bought = units("G1", 0, 4), 0
        for _ in range(40):                                                            # every unit bought ages unsold
            room = CFG["sku_target_units"]["G1"] - self.counted(held)["G1"]
            if room <= 0:
                break
            self.assertLessEqual(room, ON["step"])
            held, bought = held + units("G1", 0, room), bought + room
        self.assertEqual(len(held), 8)                                                 # 4 + 1.0 x 4
        self.assertEqual(bought, 4)

    def test_only_listed_goods_with_a_target_in_arm_b_and_out_of_a_surge(self):
        held = units("G1", 0, 4) + units("F1", 0, 9, kind="food") + units("U1", 0, 5)
        self.assertEqual(self.counted(held), {"G1": 1, "F1": 9, "U1": 5})              # food and unlisted: every unit
        self.assertEqual(self.counted(held, {"sku_target_units": {"F1": 9}})["G1"], 4)          # no target
        self.assertEqual(self.counted(held, {"sku_target_units": {"G1": True}})["G1"], 4)       # not a number of units
        self.assertEqual(self.counted(held, {"sku_target_units": [4]})["G1"], 4)
        self.assertEqual(self.counted(held, factors={"G1": 1.2})["G1"], 4)             # dearer units now: none extra
        self.assertEqual(self.counted(held, factors={"G1": 1.04})["G1"], 1)
        arm_a = next(s for s in (f"s{i}" for i in range(99)) if fs.arm({**ON, "treat": 0.5, "salt": s}, "G1") == "A")
        self.assertEqual(self.counted(held, on={**ON, "treat": 0.5, "salt": arm_a})["G1"], 4)

    def test_a_unit_with_no_usable_arrival_time_counts_as_fresh(self):
        held = [{"sku": "G1", "kind": "goods"}, {"sku": "G1", "kind": "goods", "supplied_at": "x"},
                {"sku": "G1", "kind": "goods", "supplied_at": NOW + 99}, {"sku": "G1", "kind": "goods", "supplied_at": float("nan")}]
        self.assertEqual(self.counted(held)["G1"], 4)

    def test_what_it_changed_is_reported(self):
        got = fs.counted(units("G1", 1, 5) + units("G2", 2, 0), {**CFG, "fresh_slots": ON}, CATALOG, {}, NOW)[1]
        self.assertEqual(got, {"G1": {"held": 6, "fresh": 1, "aged": 5, "counted": 2}})


class RestockTest(unittest.TestCase):
    """Through restock's own guard, plan and order, at a real clock (restock reads time.time())."""

    def setUp(self):
        self.now = time.time()
        self.on = {**ON, "start_at": self.now - 60, "stop_at": self.now + 3600}
        self.cfg = {**restock.DEFAULTS, **CFG, "keep_cash": 0, "cash_buffer": 0, "max_stock_cost": float("inf"),
                    "high_price_goods": 100000, "high_price_max_units": 20, "high_price_proven_max_units": 20,
                    "high_price_listed_only": True, "explore_lines": 0, "fill_listed": True, "round_ends_at": None}

    def inv(self, held, balance=10 ** 9):
        return {"balance": balance, "units": [{**u, "unit": f"u{i}", "category": "c"} for i, u in enumerate(held)]}

    def test_guard_refills_a_sku_whose_slots_hold_only_units_that_waited(self):
        inv, buy = self.inv(units("G1", 0, 4, self.now)), [{"sku": "G1", "qty": 4}]
        self.assertEqual(restock.guard(buy, inv, CATALOG, self.cfg), [])                               # today: full
        on = {**self.cfg, "fresh_slots": self.on}
        self.assertEqual(restock.guard(buy, inv, CATALOG, on), [{"sku": "G1", "qty": 3}])
        waiting = [{"sku": "G1", "qty": 3, "worst": 750000}]                                           # sent: on its way
        self.assertEqual(restock.guard(buy, inv, CATALOG, on, None, waiting), [])
        self.assertEqual(restock.guard(buy, inv, CATALOG, {**on, "fresh_slots": {**self.on, "stop_at": self.now - 1}}),
                         [])            # past stop_at the count is the old one, whoever passed the switch on

    def test_guard_still_keeps_the_cash(self):
        inv = self.inv(units("G1", 0, 4, self.now), balance=2600000)
        on = {**self.cfg, "fresh_slots": self.on, "cash_buffer": 1000000}
        self.assertEqual(restock.guard([{"sku": "G1", "qty": 4}], inv, CATALOG, on), [{"sku": "G1", "qty": 2}])
        self.assertEqual(restock.guard([{"sku": "G1", "qty": 4}], {**inv, "balance": 1500000}, CATALOG, on), [])

    def test_plan_asks_the_listed_target_again_for_a_clogged_sku(self):
        model = {"active": True, "cats": set(), "skus": {"G1": {"rate": 1.0, "my_share": 0.1, "price_ratio": 0.7},
                                                         "G2": {"rate": 1.0, "my_share": 0.1, "price_ratio": 0.7}}}
        inv = self.inv(units("G1", 0, 4, self.now) + units("G2", 2, 0, self.now))
        self.assertEqual(restock.plan(inv, CATALOG, [], self.cfg, model)["buy"], [])
        self.assertEqual(restock.plan(inv, CATALOG, [], {**self.cfg, "fresh_slots": self.on}, model)["buy"],
                         [{"sku": "G1", "qty": 3}])

    def test_a_line_that_exists_only_for_the_waiting_units_goes_after_every_line_of_todays_order(self):
        # independent review 8023a19 I1: ordered by the units that count, the clogged SKUs went ahead of food refills
        # and pushed stocked SKUs out of the window's calls
        cat = {**CATALOG, "G3": {"kind": "goods", "category": "가구>장", "list_price": 400000},
               "G4": {"kind": "goods", "category": "가구>장", "list_price": 400000},
               "G5": {"kind": "goods", "category": "가구>장", "list_price": 400000}}
        cfg = {**self.cfg, "priority_skus": ["G1", "G2", "G3", "G4", "G5", "F1"],
               "sku_target_units": {"G1": 4, "G2": 2, "G3": 2, "G4": 6, "G5": 2, "F1": 9}}
        model = {"skus": {s: {"rate": 1.0, "my_share": 0.5} for s in ("G1", "G2", "G3", "G4", "F1")}}      # G5: no rate
        held = (units("G1", 0, 4, self.now) + units("G2", 1, 0, self.now) + units("F1", 3, 0, self.now, "food")
                + units("G4", 0, 3, self.now) + units("G5", 1, 0, self.now))
        inv = self.inv(held)
        today = restock.urgent_listed(cfg, inv, cat, (), model)
        self.assertEqual(today, ["G3", "G2", "G4", "F1", "G1", "G5"])      # by hours of stock, list order on a tie
        on = restock.urgent_listed({**cfg, "fresh_slots": self.on}, inv, cat, (), model)
        self.assertEqual(on, ["G3", "G2", "G4", "F1", "G5", "G1"])       # G1 holds its target: last; G4 (3 of 6) stays
        self.assertEqual([s for s in on if s != "G1"], [s for s in today if s != "G1"])
        self.assertEqual(restock.urgent_listed({**cfg, "fresh_slots": self.on}, inv, cat, ()),      # no model: the same
                         ["G3", "F1", "G2", "G4", "G5", "G1"])
        self.assertEqual(restock.urgent_listed(cfg, inv, cat, ()), ["G3", "F1", "G1", "G2", "G4", "G5"])

    def test_plan_keeps_todays_lines_first_and_only_grows_or_appends(self):
        cat = {**CATALOG, "G3": {"kind": "goods", "category": "가구>장", "list_price": 400000}}
        cfg = {**self.cfg, "priority_skus": ["G1", "G2", "G3", "F1"], "sku_target_units": {"G1": 4, "G2": 6, "G3": 2, "F1": 9},
               "max_lines": 10}
        model = {"active": True, "cats": set(), "skus": {s: {"rate": 1.0, "my_share": 0.1, "price_ratio": 0.7}
                                                         for s in ("G1", "G2", "G3", "F1")}}
        inv = self.inv(units("G1", 0, 4, self.now) + units("G2", 0, 5, self.now) + units("F1", 3, 0, self.now, "food"))
        off = restock.plan(inv, cat, [], cfg, model)["buy"]
        on = restock.plan(inv, cat, [], {**cfg, "fresh_slots": self.on}, model)["buy"]
        self.assertEqual([b["sku"] for b in off], ["G3", "F1", "G2"])
        self.assertEqual([b["sku"] for b in on], ["G3", "F1", "G2", "G1"])            # today's lines first, G1 appended
        self.assertEqual({b["sku"]: b["qty"] for b in off}["G2"], 1)
        self.assertEqual({b["sku"]: b["qty"] for b in on}, {"G3": 2, "F1": 6, "G2": 4, "G1": 3})    # G2: 1 + step

    def test_the_extra_lines_go_after_unlisted_and_agent_lines_too(self):
        # independent review 798955a I4: plan put them before the formula's unlisted lines, batch before the agent's
        cat = {**CATALOG, "F2": {"kind": "food", "category": "식품>밥", "list_price": 20000, "shelf_hours": 720}}
        cfg = {**self.cfg, "priority_skus": ["G1", "F1"], "sku_target_units": {"G1": 4}, "max_lines": 2,
               "max_units_per_sku": 20, "min_expected": 0.1, "high_price_listed_only": False}
        model = {"active": True, "cats": set(), "skus": {s: {"rate": 5.0, "my_share": 0.5, "price_ratio": 0.9}
                                                         for s in ("G1", "F2", "U1")}}
        inv = self.inv(units("G1", 0, 4, self.now))
        off = restock.plan(inv, cat, [], cfg, model)["buy"]
        on = restock.plan(inv, cat, [], {**cfg, "fresh_slots": self.on}, model)["buy"]
        self.assertEqual(len(off), 2)
        self.assertNotIn("G1", [b["sku"] for b in off])
        self.assertEqual(on, off)                                   # max_lines 2: today's two lines, G1 behind them
        more = restock.plan(inv, cat, [], {**cfg, "fresh_slots": self.on, "max_lines": 3}, model)["buy"]
        self.assertEqual(more, off + [{"sku": "G1", "qty": 3}])
        on_cfg = {**self.cfg, "fresh_slots": self.on, "max_lines": 3}
        extra = restock.fresh_extra(on_cfg, inv, CATALOG)
        self.assertEqual(extra, {"G1"})
        self.assertEqual(restock.fresh_extra(self.cfg, inv, CATALOG), set())
        self.assertEqual(restock.fresh_extra(on_cfg, self.inv(units("G1", 0, 3, self.now)), CATALOG), set())   # under target
        self.assertEqual(restock.fresh_extra(on_cfg, {"units": 5}, CATALOG), set())
        food_inv = self.inv(units("G1", 0, 4, self.now) + units("F1", 5, 0, self.now, "food"))
        small = restock.small_food(CATALOG, food_inv)
        last = lambda b: 2 if b.get("sku") in extra else int(small(b))
        agent, formula = [{"sku": "U1", "qty": 2}], [{"sku": "G1", "qty": 3}, {"sku": "F1", "qty": 2}, {"sku": "G2", "qty": 1}]
        order = restock.urgent_listed(on_cfg, food_inv, CATALOG)
        send, back = restock.batch(agent, formula, formula, lambda lines: lines, on_cfg, order, last)
        self.assertEqual([b["sku"] for b in send], ["G2", "U1", "F1"])      # the agent's and the food top-up before G1
        self.assertEqual(back, [])
        send, _ = restock.batch(agent, formula, formula, lambda lines: lines, {**on_cfg, "max_lines": 9}, order, last)
        self.assertEqual([b["sku"] for b in send], ["G2", "U1", "F1", "G1"])
        send, _ = restock.batch(agent, formula, formula, lambda lines: lines, {**self.cfg, "max_lines": 9},
                                restock.urgent_listed(self.cfg, food_inv, CATALOG), small)      # off: as before
        self.assertEqual([b["sku"] for b in send], ["G2", "G1", "U1", "F1"])

    def test_the_extra_lines_take_the_cash_last(self):
        # independent review ecef27b M1: batch() guarded before it sorted, so an extra sofa took the cash of agent lines
        cat = {**CATALOG, "X1": {"kind": "goods", "category": "가전>등", "list_price": 100000}}
        inv = self.inv(units("G1", 0, 4, self.now), balance=hard_limits_stop() + 2 * 757500 + 1000)    # room for two sofas' worth
        cfg = {**self.cfg, "fresh_slots": self.on, "max_lines": 10, "high_price_listed_only": False}
        keep = lambda lines: restock.guard(lines, inv, cat, cfg)
        extra = restock.fresh_extra(cfg, inv, cat)
        self.assertEqual(extra, {"G1"})
        agent, formula = [{"sku": "X1", "qty": 20}], [{"sku": "G1", "qty": 3}]
        last = restock.line_class(extra, restock.small_food(cat, inv))
        send, _ = restock.batch(agent, formula, formula, keep, cfg, restock.urgent_listed(cfg, inv, cat), last)
        self.assertEqual(send, [{"sku": "X1", "qty": 20}])            # 20 x 75,750 = 1.515M: the agent's line is whole, no sofa
        old = lambda b: 2 if b.get("sku") in extra else int(restock.small_food(cat, inv)(b))
        self.assertEqual([last(b) for b in agent + formula], [old(b) for b in agent + formula])
        # without the extra class the same lines are guarded in today's order: the listed SKU first
        send, _ = restock.batch(agent, formula, formula, keep, cfg, restock.urgent_listed(cfg, inv, cat), restock.small_food(cat, inv))
        self.assertEqual(send, [{"sku": "G1", "qty": 2}])
        food_inv = self.inv(units("F1", 5, 0, self.now, "food"))
        self.assertEqual([restock.line_class({"G1"}, restock.small_food(cat, food_inv))(b) for b in
                          ({"sku": "G1", "qty": 1}, {"sku": "F1", "qty": 2}, {"sku": "F1", "qty": 9}, {"sku": "X1", "qty": 1})],
                         [2, 1, 0, 0])

    def test_one_clock_for_a_whole_run(self):
        # independent review ecef27b M3: plan(), guard() and the order each read the time; a unit crossing the 2 h line
        # or the switch its stop_at between them changed one of them only
        held = units("G1", 0, 4, self.now - 100)                       # 5 h old 100 s ago
        edge = [{**u, "supplied_at": self.now - 2 * 3600 + 30} for u in held]     # 30 s short of 2 h now
        cfg = {**self.cfg, "fresh_slots": self.on}
        self.assertEqual(fs.counted(edge, cfg, CATALOG, {}, self.now)[0]["G1"], 4)             # fresh: all count
        self.assertEqual(fs.counted(edge, cfg, CATALOG, {}, self.now + 60)[0]["G1"], 1)        # a minute later: waiting
        fixed = {**cfg, "fresh_now": self.now}
        self.assertEqual(fs.counted(edge, fixed, CATALOG, {}, self.now + 60)[0]["G1"], 4)      # the run's clock decides
        self.assertEqual(restock.fresh_extra(fixed, self.inv(edge), CATALOG, (), self.now + 60), set())
        ending = {**cfg, "fresh_slots": {**self.on, "stop_at": self.now + 5}, "fresh_now": self.now}
        inv = self.inv(units("G1", 0, 4, self.now))
        self.assertEqual(restock.guard([{"sku": "G1", "qty": 4}], inv, CATALOG, ending), [{"sku": "G1", "qty": 3}])
        self.assertEqual(fs.counted(inv["units"], ending, CATALOG, {}, self.now + 60)[0]["G1"], 1)   # still on at the run's clock
        self.assertEqual(fs.counted(inv["units"], {**ending, "fresh_now": "x"}, CATALOG, {}, self.now + 60)[0]["G1"], 4)

    def test_the_other_unit_caps_count_every_unit_held(self):
        # independent review 8023a19 I2: only the target reads the fresh count
        inv, buy = self.inv(units("G1", 0, 4, self.now)), [{"sku": "G1", "qty": 4}]
        on = {**self.cfg, "fresh_slots": self.on}
        self.assertEqual(restock.guard(buy, inv, CATALOG, {**on, "max_units_per_sku": 5}), [{"sku": "G1", "qty": 1}])
        self.assertEqual(restock.guard(buy, inv, CATALOG, {**on, "max_units_per_sku": 4}), [])
        self.assertEqual(restock.guard(buy, inv, CATALOG, {**on, "high_price_max_units": 6}), [{"sku": "G1", "qty": 2}])
        self.assertEqual(restock.guard(buy, inv, CATALOG, {**on, "high_price_max_units": 3}), [])
        both = [{"sku": "G1", "qty": 2}, {"sku": "G1", "qty": 2}]                       # two lines of one SKU in a run
        self.assertEqual(restock.guard(both, inv, CATALOG, on), [{"sku": "G1", "qty": 2}, {"sku": "G1", "qty": 1}])
        self.assertEqual(restock.guard(both, inv, CATALOG, {**on, "max_units_per_sku": 5}), [{"sku": "G1", "qty": 1}])
        surge_inv = {**inv, "surge": [{"sku": "G1", "factor": 1.31}]}
        self.assertEqual(restock.guard(buy, surge_inv, CATALOG, on), [])                # deep in a surge: one unit, held

    def test_no_sequence_of_runs_takes_a_sku_past_any_cap(self):
        import random
        rnd = random.Random(1110)
        for _ in range(300):
            t, cap, top = rnd.randint(1, 12), rnd.randint(1, 20), rnd.randint(1, 20)
            cfg = {**self.cfg, "sku_target_units": {"G1": t}, "max_units_per_sku": cap, "high_price_max_units": top,
                   "fresh_slots": {**self.on, "step": rnd.randint(1, 5), "aged_units": rnd.choice([0.0, 0.5, 1.0, 2.0, 3.0])}}
            held = units("G1", rnd.randint(0, 3), rnd.randint(0, 14), self.now)
            limit = max(len(held), min(cap, top, t + int(cfg["fresh_slots"]["aged_units"] * t)))
            for _ in range(12):
                got = restock.guard([{"sku": "G1", "qty": rnd.randint(1, 20)}], self.inv(held), CATALOG, cfg)
                off = restock.guard([{"sku": "G1", "qty": 20}], self.inv(held), CATALOG, {**cfg, "fresh_slots": None})
                extra = sum(b["qty"] for b in got)
                self.assertLessEqual(extra, sum(b["qty"] for b in off) + cfg["fresh_slots"]["step"])
                held = held + units("G1", 0, extra, self.now) if rnd.random() < 0.7 else held + units("G1", extra, 0, self.now)
                self.assertLessEqual(len(held), limit)


def hard_limits_stop():
    import hard_limits
    return hard_limits.STOP_BUYING_BELOW


if __name__ == "__main__":
    unittest.main()
