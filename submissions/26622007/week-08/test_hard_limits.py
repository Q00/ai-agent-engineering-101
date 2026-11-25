"""hard_limits.py: the lines the meta-harness cannot move. Protected like the module itself: the meta-harness runs
these tests before any change stands, so a change that unhooks a guard elsewhere fails here."""
import inspect
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

import agent_tools
import hard_limits as hl
import meta_harness
import restock

HERE = os.path.dirname(os.path.abspath(__file__))


class HardLimitsTest(unittest.TestCase):
    def pricing(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_shipped_settings_are_inside_the_bounds(self):
        shipped = self.pricing()
        problems = hl.check_policy_change({}, shipped)
        added = ("restock.window_fast", "price.grade_profit_until", "price.grade_profit_hours",
                 "price.grade_profit_share.A", "price.grade_profit_default")
        expected = {f"pricing.json {key} was added (hard_limits.FIXED, a person's setting)" for key in added}
        self.assertEqual(set(problems), expected)
        self.assertEqual(len(problems), len(expected))
        self.assertEqual(hl.check_policy_change(shipped, shipped), [])

    def test_the_meta_cannot_move_person_decided_seller_settings(self):
        old = self.pricing()

        def changed(section, key, value):
            new = json.loads(json.dumps(old))
            new[section][key] = value
            return new

        for section, key, value in (("price", "grade_profit_until", 10 ** 11),
                                    ("price", "grade_profit_hours", 600),
                                    ("restock", "window_fast", True)):
            with self.subTest(key=key, value=value):
                self.assertTrue(any(f"{section}.{key}" in p for p in hl.check_policy_change(old, changed(section, key, value))))

        for grade, share in (("B", 0.08), ("C", 0.15), ("A", 0.1)):
            new = json.loads(json.dumps(old))
            new["price"]["grade_profit_share"][grade] = share
            with self.subTest(grade=grade):
                self.assertTrue(any(f"price.grade_profit_share.{grade}" in p for p in hl.check_policy_change(old, new)))

        for section, key in (("price", "grade_profit_until"), ("price", "grade_profit_hours"),
                             ("restock", "window_fast")):
            new = json.loads(json.dumps(old))
            del new[section][key]
            with self.subTest(removed=key):
                self.assertTrue(any(f"{section}.{key}" in p and "removed" in p for p in hl.check_policy_change(old, new)))
            absent = json.loads(json.dumps(old))
            del absent[section][key]
            with self.subTest(added=key):
                self.assertTrue(any(f"{section}.{key}" in p for p in hl.check_policy_change(absent, old)))

        new = json.loads(json.dumps(old))
        del new["price"]["grade_profit_share"]["A"]
        self.assertTrue(any("price.grade_profit_share.A" in p and "removed" in p for p in hl.check_policy_change(old, new)))
        self.assertEqual(hl.check_policy_change(old, old), [])
        self.assertEqual(hl.check_policy_change(old, changed("price", "some_new_note", "ok")), [])
        target = json.loads(json.dumps(old))
        sku = next(iter(target["restock"]["sku_target_units"]))
        target["restock"]["sku_target_units"][sku] += 1
        self.assertEqual(hl.check_policy_change(old, target), [])

    def test_meta_cannot_add_change_or_remove_grade_profit_default(self):
        absent = {"price": {}}
        enabled = {"price": {"grade_profit_default": True}}
        disabled = {"price": {"grade_profit_default": False}}
        for old, new in ((absent, enabled), (enabled, disabled), (enabled, absent)):
            with self.subTest(old=old, new=new):
                self.assertTrue(any("price.grade_profit_default" in p
                                    for p in hl.check_policy_change(old, new)))

    def test_never_spends_into_the_bankruptcy_zone(self):
        self.assertEqual(hl.spendable(900000), 0)
        self.assertEqual(hl.spendable(1500000), 500000)
        self.assertGreater(hl.STOP_BUYING_BELOW, hl.BANKRUPT_LINE)

    def test_a_rise_is_not_limited_but_the_bounds_and_the_shape_are(self):
        # a person, 11/06 19:03: hard_limits open for compounding, only no bankruptcy
        old = self.pricing()
        old["restock"]["max_stock_cost"] = 2000000
        new = json.loads(json.dumps(old))
        new["restock"]["max_stock_cost"] = 2000000 * 500
        self.assertEqual(hl.check_policy_change(old, new), [])
        new = json.loads(json.dumps(old))
        new["restock"]["keep_cash"] = 900000
        self.assertTrue(any("outside" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["restock"]["target_per_category"] = 2           # small integers may move by one
        self.assertEqual(hl.check_policy_change(old, new), [])
        del new["restock"]["max_break"]
        self.assertTrue(any("removed" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["markdown"]["below_cost"] = ["food_expiry", "everything"]
        self.assertTrue(hl.check_policy_change(old, new))

    def test_a_rise_from_zero_or_without_bounds_passes(self):
        self.assertEqual(hl.check_policy_change({"restock": {"food_explore_qty": 0}}, {"restock": {"food_explore_qty": 20}}), [])
        self.assertEqual(hl.check_policy_change({"x": {"y": 0}}, {"x": {"y": 10 ** 9}}), [])
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 1000000}},
                                                {"restock": {"max_stock_cost": 10 ** 12}}, hl.MAX_STEP_STRONG), [])

    def test_a_fall_is_half_at_most_whatever_the_sample(self):
        old = {"restock": {"max_break": 0.2, "cover_hours": 6}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"max_break": 0.1, "cover_hours": 3}}, hl.MAX_STEP_STRONG), [])
        self.assertIn("more than 50%", hl.check_policy_change(old, {"restock": {"max_break": 0.05, "cover_hours": 6}},
                                                              hl.MAX_STEP_STRONG)[0])

    def test_the_meta_harness_keeps_the_stock_cap_over_its_floor(self):
        old = {"restock": {"max_stock_cost": 15000000}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"max_stock_cost": 10000000}}, hl.MAX_STEP_STRONG), [])
        self.assertTrue(any("META_FLOOR" in p for p in
                            hl.check_policy_change(old, {"restock": {"max_stock_cost": 9000000}}, hl.MAX_STEP_STRONG)))
        # under the floor already (a person's choice): the usual steps apply either way, the floor only stops a crossing
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 2000000}},
                                                {"restock": {"max_stock_cost": 30000000}}), [])
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 2000000}},
                                                {"restock": {"max_stock_cost": 1500000}}), [])
        self.assertEqual(hl.clamp_cfg("restock", {"max_stock_cost": 2000000})["max_stock_cost"], 2000000)   # a person's file stands

    def test_settings_without_bounds_rise_freely_and_fall_by_half(self):
        old = {"restock": {"food_max_stock_cost": 650000, "sku_target_units": {"G1": 4}}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"food_max_stock_cost": 65000000, "sku_target_units": {"G1": 400}}}), [])
        self.assertEqual(len(hl.check_policy_change(old, {"restock": {"food_max_stock_cost": 300000, "sku_target_units": {"G1": 1}}})), 2)
        self.assertTrue(hl.check_policy_change({"x": {"y": 0.1}}, {"x": {"y": -0.1}}, hl.MAX_STEP_STRONG))   # a sign flip is a fall

    def test_explore_price_caps_reach_the_dearest_catalog_items(self):
        self.assertEqual((hl.BOUNDS["restock.goods_max_price"], hl.BOUNDS["restock.food_max_price"]),
                         ((5000, 10000000), (5000, 1000000)))

    def test_protected_files_and_guard_lines(self):
        self.assertTrue(hl.guard_problems(["hard_limits.py"], ""))
        self.assertTrue(hl.guard_problems(["restock.py"], "-    spare = min(x, hard_limits.spendable(balance))\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    x = 1\n-    y = 2\n"), [])
        self.assertTrue(hl.guard_problems(["restock.py"], "+hard_limits.STOP_BUYING_BELOW = 0\n"))
        self.assertTrue(hl.guard_problems(["restock.py"], "+    setattr(hard_limits, 'BOUNDS', {})\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    if hard_limits.spendable(b) == 0:\n"), [])

    def test_the_sandbox_shuts_out_network_keys_and_writes(self):
        self.assertIn("(deny network*)", hl.SANDBOX)
        self.assertIn(".env", hl.SANDBOX)
        self.assertIn("(deny file-write*)", hl.SANDBOX)


class GuardsStayHookedTest(unittest.TestCase):
    CATALOG = {"H1": {"kind": "goods", "category": "가전>컵", "list_price": 20000}}

    def test_whatever_restock_decides_it_prints_nothing_past_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in (("inventory.json", {"round": 3, "balance": 1090000, "units": []}), ("catalog.json", self.CATALOG)):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            printed = []
            env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
            with mock.patch.object(restock, "read_model", lambda *a: None), \
                    mock.patch.object(restock.opening, "lines", lambda *a, **k: (None, "test")), \
                    mock.patch.object(restock, "plan", lambda *a, **k: {"buy": [{"sku": "H1", "qty": 20}]}), \
                    mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["x", "--state", d]), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                restock.main()
        out = json.loads(printed[-1])
        self.assertEqual(out["buy"], [{"sku": "H1", "qty": 5}])          # (1,090,000 - cash buffer 1,000,000) // 15,150

    def test_the_stock_cap_is_open(self):
        # a person, 11/06 19:03: "hard_limits 는 완전 풀어버려 그냥 파산만 안되면 문제없어"
        self.assertEqual(hl.BOUNDS["restock.max_stock_cost"][1], hl.OPEN)
        self.assertEqual(hl.clamp_cfg("restock", {"max_stock_cost": float("inf")})["max_stock_cost"], 150000000)   # SAFE
        self.assertTrue(hl.check_policy_change({}, {"restock": {"keep_cash": float("inf")}}))
        self.assertEqual(hl.clamp_cfg("restock", {"max_stock_cost": 3 * 10 ** 9})["max_stock_cost"], 3 * 10 ** 9)
        self.assertEqual(hl.META_FLOOR["restock.max_stock_cost"], 10000000)     # the meta-harness still keeps 10M+

    def test_the_widened_bounds(self):
        b, inf, top = hl.BOUNDS, float("inf"), hl.OPEN
        self.assertEqual((b["restock.max_units_per_sku"], b["restock.target_per_category"], b["restock.food_explore_qty"],
                          b["restock.max_sku_share"], b["opening.explore_budget_worst"], b["restock.keep_cash"],
                          b["restock.high_price_max_units"], b["restock.high_price_proven_max_units"]),
                         ((1, 1000), (0, 100), (0, 20), (0.01, 1.0), (0, top), (1000000, top), (0, 1000), (0, 1000)))
        self.assertEqual((hl.MAX_STEP, hl.MAX_STEP_STRONG, hl.MAX_STEP_UNBOUNDED), (inf, inf, inf))

    def test_the_cash_buffer_covers_the_expected_refunds_and_storage(self):
        # a person 11/06 22:22 ("최대한 점수를 올려", no bankruptcy): the refunds the last 6 h of warranted goods sales are
        # expected to bring at the measured rate (5.6% -> 6%), plus a day of storage
        now = 1800000000
        self.assertEqual(hl.cash_buffer(0, [], now), 1000000)                                    # STOP_BUYING_BELOW
        self.assertEqual(hl.cash_buffer(100000000, [], now), 500000 + 1000000)                 # one day of 1% storage
        sold = [(now, 1000000)] * 100 + [(now - 3 * 3600, 1000000)] * 100 + [(now - 7 * 3600, 9000000)]  # new, half-way, past 6 h
        self.assertEqual(hl.cash_buffer(0, sold, now), 500000 + 30000000)         # 6% of 150M weighted (9M) < 20% (30M)
        self.assertEqual(hl.cash_buffer(0, [(now, 1000000)] * 400, now), 500000 + 80000000)   # 20% of 400M, no cap
        # the worst rate's exposure (20%: the most any past 6 h refunded of its weighted exposure was 18.6%, GPT Pro
        # 11/07 04:30 S1 replay) is not capped any more: at 20M it fell short 3 of 549 times (11/06 20:2x-20:3x)
        self.assertEqual(hl.cash_buffer(0, [(now, 100000)] * 100, now), 500000 + 2000000)
        self.assertEqual(hl.cash_buffer(0, [(now, 100000)] * 600, now), 500000 + 12000000)
        self.assertEqual(hl.cash_buffer(0, [(now, 100000)] * 2000, now), 500000 + 40000000)
        self.assertGreater(hl.REFUNDS_6H_WORST, 14438775)
        self.assertGreaterEqual(hl.UNKNOWN_EXPOSURE, hl.REFUNDS_6H_WORST)   # an unreadable ledger keeps no less

    def test_the_cash_buffer_covers_the_three_biggest_sales_still_under_warranty(self):
        # GPT Pro 11/06 23:40 (메인 23:40 파산 방지 최우선): with 6% / 13% capped at 5.8M, 14 of 481 past board times would
        # have crossed the line had nothing sold after them; the three biggest warranted sales of the last 6 h, refunded
        # in full, makes it 0 of 481 (review/W-20261106-2225-AX극단상향/buffer_replay.py)
        now = 1800000000
        big = [(now - 5 * 3600, 2500000), (now - 3600, 2400000), (now, 2300000), (now, 100000), (now - 7 * 3600, 9000000)]
        self.assertEqual(hl.cash_buffer(0, big, now), 500000 + 2500000 + 2400000 + 2300000)
        self.assertEqual(hl.BIGGEST_SALES, 3)
        self.assertEqual(hl.cash_buffer(0, [(now, 3000000), (now, 0), (now, -9000000)], now), 500000 + 3000000)  # fewer than 3
        self.assertGreaterEqual(hl.cash_buffer(0, None, now), hl.OPEN)              # ledger unreadable: nothing is bought
        self.assertEqual(hl.cash_buffer(-5, [("x", None), (now, float("nan")), (now, float("inf"))], now), 1000000)
        self.assertGreaterEqual(hl.cash_buffer(float("inf"), [], now), hl.OPEN)    # storage not known: no buys
        self.assertEqual((hl.REFUND_EXPECTED, hl.REFUND_WORST, hl.REFUNDS_6H_WORST, hl.BREAK_HOURS, hl.STORAGE_DAYS),
                         (0.06, 0.20, 20000000, 6, 1.0))
        self.assertEqual(hl.BOUNDS["price.goods_break"], (0.03, 0.5))
        self.assertTrue(hl.check_policy_change({"price": {"goods_break": 0.035}}, {"price": {"goods_break": 0.02}}))
        self.assertEqual(hl.spendable(30000000, 20000000), 10000000)
        self.assertEqual(hl.spendable(30000000, 0), 29000000)                  # never under STOP_BUYING_BELOW
        self.assertEqual((hl.spendable(30000000, float("nan")), hl.spendable(float("inf"), 0)), (0, 0))   # fails closed
        cat = {"H1": {"list_price": 1000000}}
        self.assertEqual(hl.final_buy([{"sku": "H1", "qty": 20}], 30000000, cat, buffer=20000000), [{"sku": "H1", "qty": 13}])  # 757,500 each
        self.assertEqual(hl.final_buy([{"sku": "H1", "qty": 20}], 19000000, cat, buffer=20000000), [])

    def test_restock_reads_its_own_warranted_sales_and_says_so_when_it_cannot(self):
        now = 1800000000
        cat = {"H1": {"kind": "goods", "list_price": 100}, "F1": {"kind": "food", "list_price": 100}}
        rows = [{"k": "result", "seq": 1, "ts": now - 7 * 3600, "order": "o1", "seller": "Nocha Furniture"},
                {"k": "settle", "seq": 2, "ts": now - 3600, "order": "o1", "sku": "H1", "amount": 90, "warranty": 72.0},
                {"k": "settle", "seq": 3, "ts": now - 3600, "order": "o2", "sku": "H1", "amount": 80, "warranty": 6.0},
                {"k": "result", "seq": 4, "ts": now - 3500, "order": "o3", "seller": "Nocha Furniture"},
                {"k": "settle", "seq": 5, "ts": now - 3500, "order": "o3", "sku": "F1", "amount": 70, "warranty": None},
                {"k": "settle", "seq": 6, "ts": now - 8 * 3600, "order": "o1", "sku": "H1", "amount": 60, "warranty": 6.0}]
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SHOP_CARD_NAME": "Nocha Furniture"}):
            self.assertEqual(restock.warranted_sales(d, cat, now), [])                 # no ledger yet
            with open(os.path.join(d, "ledger_rows.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
            self.assertEqual(restock.warranted_sales(d, cat, now), [(now - 3600, 90)])
            with mock.patch.object(restock, "SALES_TAIL_BYTES", 300), mock.patch.object(restock, "SALES_TAIL_MAX_BYTES", 300):
                self.assertIsNone(restock.warranted_sales(d, cat, now))                 # the tail misses 6 h
            with mock.patch.object(restock, "SALES_TAIL_BYTES", 300):                   # a longer tail is read first
                self.assertEqual(restock.warranted_sales(d, cat, now), [(now - 3600, 90)])


class NoBankruptcyReplayTest(unittest.TestCase):
    """GPT Pro 11/07 04:30 S1: every past board time whose next 6 h the ledger has seen (549, 11/04 21:15 - 11/06 22:3x),
    with the cash spent down to cash_buffer() and nothing sold after it, must still pay the refunds that actually came
    for the warranted goods sold in the 6 h before it, and that window's storage fee (0.25% of the stock), above the
    bankruptcy line. The old buffer (13% capped at 20M) fell short 3 times (worst 673,731 under, 11/06 20:33)."""

    def test_no_past_board_time_crosses_the_line(self):
        with open(os.path.join(HERE, "testdata", "r3_cash_replay.json"), encoding="utf-8") as f:
            data = json.load(f)
        sales, short = data["sales"], []
        for t, stock, refunds in data["cases"]:
            live = [(ts, a) for ts, a in sales if t - 6 * 3600 < ts <= t]
            left = hl.cash_buffer(stock, live, t) - refunds - 0.0025 * stock
            if left < hl.BANKRUPT_LINE:
                short.append((t, round(hl.BANKRUPT_LINE - left)))
        self.assertEqual(len(data["cases"]), 549)
        self.assertEqual(short, [])

    def test_unknown_exposure_or_pending_buys_nothing(self):
        self.assertEqual(hl.spendable(10 ** 11, hl.cash_buffer(0, None)), 0)        # own sales unreadable
        cat = {"H1": {"kind": "goods", "category": "가전>컵", "list_price": 20000}}
        with tempfile.TemporaryDirectory() as d:
            for name, data in (("inventory.json", {"round": 3, "balance": 900000000, "units": []}), ("catalog.json", cat)):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            printed = []
            env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
            with mock.patch.object(restock, "read_model", lambda *a: None), \
                    mock.patch.object(restock.opening, "lines", lambda *a, **k: (None, "test")), \
                    mock.patch.object(restock, "plan", lambda *a, **k: {"buy": [{"sku": "H1", "qty": 20}]}), \
                    mock.patch.object(restock, "pending", mock.Mock(side_effect=ValueError("bad file"))), \
                    mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["x", "--state", d]), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                restock.main()
        self.assertEqual(json.loads(printed[-1])["buy"], [])
        with tempfile.TemporaryDirectory() as d:      # a damaged record of sent lines is not "nothing sent" (review)
            inv = {"units": []}
            self.assertEqual(restock.pending(d, inv), [])                           # no record yet
            for bad in ("{bad", '{"a": 1}', json.dumps([{"sku": "H1", "qty": 2, "at": time.time(), "worst": "x"}]),
                        '[{"sku": "H1", "qty": 2, "at": %f, "worst": NaN}]' % time.time()):
                with open(os.path.join(d, restock.SENT), "w") as f:
                    f.write(bad)
                with self.assertRaises(ValueError):
                    restock.pending(d, inv)
        self.assertGreaterEqual(hl.cash_buffer(float("nan"), [], 1800000000), hl.OPEN)  # stock not known

    def test_a_buy_keeps_the_storage_of_what_it_buys(self):
        # 1,000,000 x 0.75 = 750,000 a unit and a day of its storage (7,500): 15,100,000 over the buffer buys 20 units
        # at cost alone (15,000,000) but 19 once each keeps its own storage (20 x 757,500 = 15,150,000)
        cat = {"H1": {"list_price": 1000000}}
        self.assertEqual(hl.final_buy([{"sku": "H1", "qty": 20}], 35100000, cat, buffer=20000000), [{"sku": "H1", "qty": 19}])
        inv = {"balance": 35100000, "units": []}
        cfg = dict(restock.DEFAULTS, keep_cash=0, cash_buffer=20000000, max_units_per_sku=100)
        with mock.patch.object(restock, "surge", lambda inv: {}):
            self.assertEqual(restock.guard([{"sku": "H1", "qty": 20}], inv, {"H1": {"kind": "goods", "list_price": 1000000}},
                                           cfg), [{"sku": "H1", "qty": 19}])

    def test_the_meta_harness_keeps_cash_for_refunds_and_fees(self):
        old = {"restock": {"keep_cash": 4500000}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"keep_cash": 3000000}}, hl.MAX_STEP_STRONG), [])
        self.assertTrue(any("META_FLOOR" in p for p in
                            hl.check_policy_change(old, {"restock": {"keep_cash": 2900000}}, hl.MAX_STEP_STRONG)))
        self.assertIn("more than 50%", hl.check_policy_change({"restock": {"keep_cash": 8000000}},
                                                              {"restock": {"keep_cash": 3900000}}, hl.MAX_STEP_STRONG)[0])

    def test_unlisted_dear_goods_stay_out_whatever_the_meta_writes(self):
        old = {"restock": {"high_price_listed_only": True}}
        self.assertTrue(any("FIXED" in p for p in hl.check_policy_change(old, {"restock": {"high_price_listed_only": False}})))
        self.assertTrue(any("removed" in p for p in hl.check_policy_change(old, {"restock": {}})))
        self.assertTrue(any("FIXED" in p for p in hl.check_policy_change(old, {"restock": {"high_price_listed_only": 1}})))
        self.assertEqual(hl.check_policy_change(old, old), [])
        # nor by lifting the line above every price: high_price_goods is bounded and moves a step at a time
        self.assertTrue(any("outside" in p for p in hl.check_policy_change({"restock": {"high_price_goods": 800000}},
                                                                           {"restock": {"high_price_goods": 1600000}},
                                                                           hl.MAX_STEP_STRONG)))
        self.assertEqual(hl.clamp_cfg("restock", {"high_price_goods": 5000000})["high_price_goods"], 1000000)
        self.assertEqual(hl.clamp_cfg("restock", {"high_price_goods": None})["high_price_goods"], 100000)
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)["restock"]["high_price_listed_only"], True)

    def test_the_lines_that_stay(self):
        self.assertEqual(hl.BOUNDS["restock.keep_cash"][0], hl.STOP_BUYING_BELOW)   # the least that keeps supplies going
        self.assertEqual((hl.BANKRUPT_LINE, hl.STOP_BUYING_BELOW), (500000, 1000000))
        self.assertEqual((hl.BUYER_GOODS_WARRANTY_HOURS, hl.SELLER_GOODS_WARRANTY_HOURS), (6, 72))
        self.assertEqual(hl.PROTECTED, ("hard_limits.py", "test_hard_limits.py", "run_market.sh", "run_meta.sh", "ensure_running.sh"))
        self.assertEqual(hl.MAX_STEP_DOWN, 0.5)
        self.assertEqual(hl.BOUNDS["restock.max_lines"], (1, 20))
        self.assertEqual(hl.BOUNDS["buyer.quote_shops"], (1, 100))
        self.assertEqual(hl.clamp_cfg("restock", {"max_lines": 18})["max_lines"], 18)
        self.assertEqual(hl.clamp_cfg("restock", {"max_lines": 21})["max_lines"], 20)
        self.assertEqual(hl.SAFE["restock"]["max_lines"], 10)
        self.assertEqual(hl.SAFE["buyer"]["quote_shops"], 3)
        self.assertEqual(hl.META_FLOOR, {"restock.max_stock_cost": 10000000, "restock.keep_cash": 3000000})
        self.assertEqual(hl.FIXED, {"restock.high_price_listed_only": True, "buyer.oneshot": True,
                                    "negotiate.engine": "llm", "negotiate.formula": "share",
                                    "negotiate.counter_share": [], "restock.window_fast": False,
                                    "price.grade_profit_until": 0, "price.grade_profit_hours": 0, "price.grade_profit_default": False})

    def test_settings_that_are_not_numbers_fall_back_to_the_cautious_start(self):
        cfg = hl.clamp_cfg("restock", {"food_max_qty": None, "keep_cash": "0", "max_stock_cost": None})
        self.assertEqual((cfg["food_max_qty"], cfg["keep_cash"], cfg["max_stock_cost"]), (4, 4500000, 150000000))
        self.assertEqual(hl.clamp_cfg("markdown", {})["below_cost"], ["food_expiry"])
        self.assertTrue(hl.check_policy_change({"restock": {"max_break": 0.1}}, {"restock": {"max_break": None}}))
        self.assertTrue(hl.check_policy_change({"markdown": {"below_cost": ["food_expiry"]}}, {"markdown": {}}))

    def test_the_buyer_has_no_cap_per_deal_but_keeps_its_budget_and_list_price(self):
        import market_buyer
        cfg = hl.clamp_cfg("buyer", {"max_price_share": 1.0, "max_spend_per_deal": 10000, "quote_shops": 3})
        self.assertNotIn("max_spend_per_deal", cfg)          # whoever writes one into pricing.json
        self.assertNotIn("buyer.max_spend_per_deal", hl.BOUNDS)
        self.assertNotIn("max_spend_per_deal", hl.SAFE["buyer"])
        self.assertEqual(hl.clamp_cfg("buyer", {"max_price_share": 5})["max_price_share"], 1.0)   # never past list
        offer = {"list_price": 5 * 10 ** 9}           # far over any cap a later change could slip into the code
        self.assertEqual(market_buyer.value_limit(offer, 9 * 10 ** 9, None, cfg), 5 * 10 ** 9)
        self.assertEqual(market_buyer.value_limit(offer, 2500000, None, cfg), 2500000)           # the order's budget
        self.assertEqual(market_buyer.value_limit(offer, 9 * 10 ** 9, None, market_buyer.buyer_cfg()), 5 * 10 ** 9)
        with mock.patch.object(market_buyer, "__file__", os.path.join(tempfile.mkdtemp(), "market_buyer.py")):
            self.assertEqual(market_buyer.value_limit(offer, 9 * 10 ** 9, None, market_buyer.buyer_cfg()), 5 * 10 ** 9)
        self.assertEqual(market_buyer.accept_policy({"kind": "goods"}), {"warranty_hours": hl.BUYER_GOODS_WARRANTY_HOURS})
        self.assertFalse(hl.check_policy_change({"buyer": {"max_spend_per_deal": 800000}}, {"buyer": {}}))

    def test_the_meta_harness_still_checks_and_tests(self):
        src = inspect.getsource(meta_harness.cycle)
        self.assertIn("problems += limit_problems(code)", src)
        self.assertIn("run_tests()", src)
        lim = inspect.getsource(meta_harness.limit_problems)
        self.assertIn("hard_limits.check_policy_change", lim)
        self.assertIn("hard_limits.guard_problems", lim)
        self.assertIn('"discover"', inspect.getsource(meta_harness.run_tests))

    def test_goods_warranty_rules_stay_fixed(self):
        import market_buyer
        import seller
        self.assertGreaterEqual(hl.BUYER_GOODS_WARRANTY_HOURS, 6)
        self.assertGreaterEqual(hl.SELLER_GOODS_WARRANTY_HOURS, 6)
        self.assertEqual(market_buyer.accept_policy({"kind": "goods"}), {"warranty_hours": hl.BUYER_GOODS_WARRANTY_HOURS})
        self.assertIsNotNone(market_buyer.warranty_short({"warranty_hours": 5}, {"kind": "goods"}))
        self.assertIn("hard_limits.SELLER_GOODS_WARRANTY_HOURS", inspect.getsource(seller.Seller.warranty_terms))
        self.assertIn("problems or short", inspect.getsource(market_buyer.main))

    def test_no_tool_runs_without_the_sandbox(self):
        with mock.patch.object(shutil, "which", lambda name: None):
            self.assertIn("error", agent_tools.execute("my_memory", {"query": "x"}, tempfile.gettempdir()))

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox")
    def test_a_tool_cannot_reach_the_keys_the_outbox_or_other_programs(self):
        tmp = tempfile.mkdtemp()
        try:
            tools, md = os.path.join(tmp, "tools"), os.path.join(tmp, "md")
            os.makedirs(md)
            probes = {"outbox": "import os,json;open(os.path.join(os.environ['MARKET_DIR'],'outbox.jsonl'),'a').write('x');print(1)",
                      "ps": "import subprocess,os;print(subprocess.run(['ps','eww',str(os.getppid())],capture_output=True).stdout)",
                      "history": "import os;print(len(open(os.path.expanduser('~/.zsh_history'),'rb').read()))",
                      "net": "import socket;socket.create_connection(('1.1.1.1',443),3);print(1)"}
            for name, code in probes.items():
                os.makedirs(os.path.join(tools, name))
                with open(os.path.join(tools, name, "tool.py"), "w") as f:
                    f.write(code)
            for name in probes:
                out = agent_tools.execute(name, {}, md, tools)
                self.assertIn("error", out if isinstance(out, dict) else {}, name)
            self.assertFalse(os.path.exists(os.path.join(md, "outbox.jsonl")))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
