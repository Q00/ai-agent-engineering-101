"""price.grade_break_trial (profit_price.break_trial_arm / unit_break): a unit-hash half of grade B goods priced at a
higher break chance, off outside its window or on any odd field; both arms logged; the pick order does not see it."""
import hashlib
import unittest
import unittest.mock as mock

import profit_price

NOW = 1_794_210_000.0
TRIAL = {"share": 0.5, "salt": "gbt-test", "grade_break": {"B": 0.19}, "start_at": NOW - 60, "stop_at": NOW + 60}


def unit(uid, grade="B", kind="goods", cost=600_000, list_price=1_000_000):
    return {"unit": uid, "sku": "Gtest", "grade": grade, "kind": kind, "cost": cost, "list_price": list_price}


def in_half(uid, salt="gbt-test", share=0.5):
    h = hashlib.sha1(f"{salt}|{uid}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32 < share


UIDS = [f"u-{i:04d}" for i in range(400)]
# (floor at b .122 = OFF, floor at b .19) for two real units, cost 353,335 / list 545,930 and 871,152 / 1,776,000
FLOORS = {"u-21a880c0efac": {"trial": (403_439, 437_308), "control": (403_439, 403_439)},
          "u-36474c9458ac": {"trial": (994_683, 1_078_187), "control": (994_683, 994_683)}}
TREAT = [u for u in UIDS if in_half(u)]
CTRL = [u for u in UIDS if not in_half(u)]


def cfg(**trial):
    return {**profit_price.DEFAULTS, "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344}, "goods_break": 0.035,
            "min_profit": 1, "min_profit_share": 0.0, **({"grade_break_trial": trial} if trial else {})}


def pricing(**trial):
    return {"price": {k: v for k, v in cfg(**trial).items() if k != "kind_ratio"}, "warranty": {"max_break": 0.5}}


class GradeBreakTrial(unittest.TestCase):
    def test_hash_split_is_about_half(self):
        self.assertTrue(150 < len(TREAT) < 250)

    def test_treated_b_unit_takes_trial_rate(self):
        self.assertEqual(profit_price.unit_break(unit(TREAT[0]), None, cfg(**TRIAL), 0.5, now=NOW), (0.19, "trial"))

    def test_control_b_unit_keeps_usual_rate_and_is_labelled(self):
        self.assertEqual(profit_price.unit_break(unit(CTRL[0]), None, cfg(**TRIAL), 0.5, now=NOW), (0.122, "control"))

    def test_never_lowers_a_higher_measured_rate(self):
        self.assertEqual(profit_price.unit_break(unit(TREAT[0]), 0.30, cfg(**TRIAL), 0.5, now=NOW), (0.30, "trial"))

    def test_raise_over_max_break_is_not_made(self):
        t = {**TRIAL, "grade_break": {"B": 0.6}}
        self.assertEqual(profit_price.unit_break(unit(TREAT[0]), None, cfg(**t), 0.5, now=NOW), (0.122, "trial"))
        with mock.patch.object(profit_price.time, "time", return_value=NOW):
            q = profit_price.quote(unit(TREAT[0]), median=700_000, pricing=pricing(**t))
        self.assertFalse(q["refuse"])

    def test_other_grades_and_food_untouched(self):
        for u, want in ((unit(TREAT[0], "A"), 0.035), (unit(TREAT[0], "C"), 0.344), (unit(TREAT[0], kind="food"), 0.0)):
            self.assertEqual(profit_price.unit_break(u, None, cfg(**TRIAL), 0.5, now=NOW), (want, None), u)

    def test_window_is_half_open_and_fail_closed(self):
        u = unit(TREAT[0])
        for now in (TRIAL["start_at"] - 1, TRIAL["stop_at"], TRIAL["stop_at"] + 1):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**TRIAL), now=now))
        self.assertEqual(profit_price.break_trial_arm(u, cfg(**TRIAL), now=TRIAL["start_at"]), ("trial", 0.19))
        for bad in ({"stop_at": None}, {"start_at": float("nan")}, {"stop_at": float("inf")}, {"share": 0},
                    {"share": 1.5}, {"share": True}, {"salt": ""}, {"grade_break": {"B": 1.0}},
                    {"grade_break": {"B": "0.19"}}, {"grade_break": None}, {"start_at": NOW + 120}):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**{**TRIAL, **bad}), now=NOW), bad)
        self.assertIsNone(profit_price.break_trial_arm(u, cfg(), now=NOW))
        self.assertIsNone(profit_price.break_trial_arm({**u, "unit": None}, cfg(**TRIAL), now=NOW))

    def test_quote_floor_rises_only_for_treated(self):
        p = pricing(**TRIAL)
        with mock.patch.object(profit_price.time, "time", return_value=NOW):
            qt = profit_price.quote(unit(TREAT[0]), median=700_000, pricing=p)
            qc = profit_price.quote(unit(CTRL[0]), median=700_000, pricing=p)
        self.assertEqual(qc["floor"], profit_price.floor(600_000, 1_000_000, "goods", 0.122, profit_price.cfg_of(p)))
        self.assertEqual(qt["floor"], profit_price.floor(600_000, 1_000_000, "goods", 0.19, profit_price.cfg_of(p)))
        self.assertGreater(qt["floor"], qc["floor"])
        self.assertGreaterEqual(qt["open"], qt["floor"])
        self.assertEqual((qt["b_trial"], qc["b_trial"]), ("trial", "control"))
        self.assertIn("grade_break_trial trial", profit_price.line(unit(TREAT[0]), qt))
        self.assertIn("grade_break_trial control", profit_price.line(unit(CTRL[0]), qc))

    def test_off_quote_is_unchanged(self):
        q = profit_price.quote(unit(TREAT[0]), median=700_000, pricing=pricing())
        self.assertEqual(q["b"], 0.122)
        self.assertIsNone(q["b_trial"])
        self.assertNotIn("grade_break_trial", profit_price.line(unit(TREAT[0]), q))

    def test_pick_order_does_not_see_the_arm(self):
        import seller
        p = pricing(**TRIAL)
        with mock.patch.object(seller, "break_chance", return_value=None), \
                mock.patch.object(profit_price.time, "time", return_value=NOW):
            ft = seller.floor_rank(unit(TREAT[0]), p)
            fc = seller.floor_rank(unit(CTRL[0]), p)
        self.assertEqual(ft, fc)

    def test_categories_limit_the_trial_to_their_prefixes(self):
        c = cfg(**TRIAL, categories=["가구/침구>"])
        bed = {**unit(TREAT[0]), "category": "가구/침구>가구>침대/매트리스"}
        pc = {**unit(TREAT[0]), "category": "컴퓨터>노트북"}
        self.assertEqual(profit_price.break_trial_arm(bed, c, NOW), ("trial", 0.19))
        self.assertIsNone(profit_price.break_trial_arm(pc, c, NOW))
        self.assertIsNone(profit_price.break_trial_arm(unit(TREAT[0]), c, NOW))   # no category: not eligible
        self.assertEqual(profit_price.unit_break(pc, None, c, now=NOW), (0.122, None))

    def test_odd_categories_turn_the_trial_off(self):
        bed = {**unit(TREAT[0]), "category": "가구/침구>가구>소파"}
        for bad in ([], "가구/침구>", [""], [1], None):
            self.assertIsNone(profit_price.break_trial_arm(bed, cfg(**TRIAL, categories=bad), NOW), bad)


    def test_values_that_raised_in_the_quote_are_off(self):
        # an int too big for a float and a lone surrogate (json.load gives both) raised in math.isfinite / encode:
        # the quote of every goods unit would have failed
        u = unit(TREAT[0])
        for bad in ({"stop_at": 10 ** 400}, {"start_at": -10 ** 400}, {"share": 10 ** 400},
                    {"grade_break": {"B": 10 ** 400}}, {"grade_break": {"B": 0.19, "A": 10 ** 400}},
                    {"salt": "\ud800"}, {"grade_break": {}}, {"start_at": True}, {"stop_at": False}):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**{**TRIAL, **bad}), now=NOW), bad)
            with mock.patch.object(profit_price.time, "time", return_value=NOW):
                q = profit_price.quote(u, median=700_000, pricing=pricing(**{**TRIAL, **bad}))
            self.assertEqual((q["b"], q["b_trial"]), (0.122, None), bad)
        for now in (float("nan"), float("inf"), 10 ** 400, "1794210000"):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**TRIAL), now=now), now)

    def test_window_longer_than_twelve_hours_is_off(self):
        u = unit(TREAT[0])
        for stop in (NOW + 10 ** 12, TRIAL["start_at"] + 12 * 3600 + 1):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**{**TRIAL, "stop_at": stop}), now=NOW), stop)
        twelve = {**TRIAL, "stop_at": TRIAL["start_at"] + 12 * 3600}
        self.assertEqual(profit_price.break_trial_arm(u, cfg(**twelve), now=NOW), ("trial", 0.19))

    def test_real_b_furniture_units(self):
        # u-... from the shop's inventory 11/10 03:3x (cert left out: profit_price does not read it)
        real = [{"unit": "u-21a880c0efac", "sku": "G021aae64", "category": "가구/침구>가구>침대/매트리스", "list_price": 545930,
                 "kind": "goods", "cost": 353335, "supplied_at": 1794225885, "expires_at": None, "grade": "B"},
                {"unit": "u-36474c9458ac", "sku": "G9f7c7d1b", "category": "가구/침구>가구>소파/의자/스툴", "list_price": 1776000,
                 "kind": "goods", "cost": 871152, "supplied_at": 1794247531, "expires_at": None, "grade": "B"}]
        t = {**TRIAL, "categories": ["가구/침구>"]}
        p = pricing(**t)
        for u in real:
            want = "trial" if in_half(u["unit"]) else "control"
            with mock.patch.object(profit_price.time, "time", return_value=NOW):
                q = profit_price.quote(u, median=None, pricing=p)
                off = profit_price.quote(u, median=None, pricing=pricing())
            self.assertEqual(q["b_trial"], want)
            self.assertEqual(q["b"], 0.19 if want == "trial" else 0.122)
            self.assertEqual(q["floor"], profit_price.floor(u["cost"], u["list_price"], "goods", q["b"], profit_price.cfg_of(p)))
            # by hand: ceil((C + .0025 C + 1) / (1 - b)), review/W-20261109-1520-AXPro주기/gbt-activate-1110/plan.md 3
            self.assertEqual((off["floor"], q["floor"]), FLOORS[u["unit"]][want])
            self.assertFalse(q["refuse"])
            self.assertEqual(off["b"], 0.122)
            self.assertIn(f"grade_break_trial {want}", profit_price.line(u, q))
            a = {**u, "grade": "A"}
            with mock.patch.object(profit_price.time, "time", return_value=NOW):
                self.assertEqual(profit_price.quote(a, pricing=p), profit_price.quote(a, pricing=pricing()))

    def test_huge_max_break_does_not_raise(self):
        # restock.log_prices passes pricing as it is (seller clamps max_break, restock does not): review eb9f836 M1
        p = {**pricing(**TRIAL), "warranty": {"max_break": 10 ** 400}}
        with mock.patch.object(profit_price.time, "time", return_value=NOW):
            q = profit_price.quote(unit(TREAT[0]), median=700_000, pricing=p)
        self.assertEqual((q["b"], q["b_trial"]), (0.19, "trial"))


class Switch(unittest.TestCase):
    """grade_break_trial_switch: the setting and the pricing.json line."""

    def setUp(self):
        import os
        import shutil
        import tempfile
        import grade_break_trial_switch as sw
        self.sw, self.os = sw, os
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "pricing.json")
        here = os.path.dirname(os.path.abspath(__file__))
        shutil.copyfile(os.path.join(here, "pricing.json"), self.path)    # the real hand-formatted file
        os.chmod(self.path, 0o644)
        self.bak = os.path.join(self.dir, "bak")
        import json
        with open(self.path, encoding="utf-8") as f:
            live = "grade_break_trial" in json.load(f)["price"]
        if live:      # the shop's file while a trial runs (the meta harness tests there): start from it without one
            try:
                sw.switch_off(self.path, backup_dir=self.bak, stamp="setup")
            except ValueError:    # the line was reformatted: drop the key the plain way (the format tests then see json)
                with open(self.path, encoding="utf-8") as f:
                    d = json.load(f)
                del d["price"]["grade_break_trial"]
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                    f.write("\n")

    def setting(self, **kw):
        return self.sw.setting(**{"salt": "grade-break-furn-b-test-v1", "start_at": NOW, "hours": 9,
                                  "categories": ["가구/침구>"], "source": "test", **kw})

    def test_setting_is_a_finite_nine_hour_window_the_quote_runs(self):
        s = self.setting()
        self.assertEqual((s["start_at"], s["stop_at"], s["share"], s["grade_break"]), (NOW, NOW + 9 * 3600, 0.5, {"B": 0.19}))
        self.assertTrue(profit_price.trial_setting_ok(s))
        bed = {**unit(TREAT[0]), "category": "가구/침구>가구>소파"}
        c = {**cfg(), "grade_break_trial": {**s, "salt": TRIAL["salt"]}}
        self.assertIsNone(profit_price.break_trial_arm(bed, c, now=NOW - 0.001))
        self.assertEqual(profit_price.break_trial_arm(bed, c, now=NOW), ("trial", 0.19))
        self.assertIsNotNone(profit_price.break_trial_arm(bed, c, now=NOW + 9 * 3600 - 0.001))
        self.assertIsNone(profit_price.break_trial_arm(bed, c, now=NOW + 9 * 3600))

    def test_setting_refuses_what_would_not_run(self):
        for kw in ({"hours": 0}, {"hours": -1}, {"hours": 12.001}, {"hours": 10 ** 400}, {"hours": True},
                   {"hours": float("nan")}, {"hours": float("inf")}, {"start_at": None}, {"start_at": True},
                   {"start_at": float("nan")}, {"start_at": 10 ** 400}, {"salt": ""}, {"salt": "\ud800"},
                   {"share": 0}, {"share": 1.01}, {"grade_break": {"B": 1}}, {"grade_break": {}},
                   {"categories": []}, {"categories": "가구/침구>"}, {"source": 1}):
            with self.assertRaises(ValueError, msg=kw):
                self.setting(**kw)
        self.assertEqual(self.setting(hours=12)["stop_at"], NOW + 12 * 3600)

    def test_on_adds_one_line_and_off_gives_the_same_bytes_back(self):
        import json
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        old = json.loads(raw)
        s = self.setting()
        b1 = self.sw.switch_on(self.path, s, backup_dir=self.bak, stamp="t1")
        with open(self.path, encoding="utf-8") as f:
            on = f.read()
        with open(b1, encoding="utf-8") as f:
            self.assertEqual(f.read(), raw)
        self.assertEqual(oct(self.os.stat(self.path).st_mode & 0o777), oct(0o644))
        self.assertEqual(oct(self.os.stat(b1).st_mode & 0o777), oct(0o600))
        new = json.loads(on)
        self.assertEqual(new["price"]["grade_break_trial"], s)
        self.assertEqual({**new, "price": {k: v for k, v in new["price"].items() if k != "grade_break_trial"}}, old)
        added = [ln for ln in on.splitlines() if ln not in raw.splitlines()]
        self.assertEqual(len(added), 1)
        self.assertEqual(len(on.splitlines()), len(raw.splitlines()) + 1)
        import shop_memory
        with mock.patch.object(shop_memory, "PRICING", self.path):
            loaded = shop_memory.load_pricing()
        self.assertEqual(profit_price.cfg_of(loaded)["grade_break_trial"], s)
        with self.assertRaises(ValueError):            # never over a trial already there
            self.sw.switch_on(self.path, self.setting(salt="other"), backup_dir=self.bak, stamp="t2")
        self.sw.switch_off(self.path, backup_dir=self.bak, stamp="t3")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), raw)
        with self.assertRaises(ValueError):
            self.sw.switch_off(self.path, backup_dir=self.bak, stamp="t4")

    def test_off_refuses_a_reformatted_trial(self):
        import json
        self.sw.switch_on(self.path, self.setting(), backup_dir=self.bak, stamp="t1")
        with open(self.path, encoding="utf-8") as f:
            d = json.load(f)
        text = json.dumps(d, ensure_ascii=False, indent=2) + "\n"     # the trial is now over several lines
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        with self.assertRaises(ValueError):
            self.sw.switch_off(self.path, backup_dir=self.bak, stamp="t2")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), text)

    def test_on_leaves_the_file_when_refused(self):
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        bad = {**self.setting(), "stop_at": NOW + 10 ** 12}
        with self.assertRaises(ValueError):
            self.sw.switch_on(self.path, bad, backup_dir=self.bak, stamp="t1")
        real_open = open
        calls = []

        def racing_open(p, *a, **kw):              # another writer between the read and the swap
            calls.append(p)
            if p == self.path and len([c for c in calls if c == self.path]) == 2:
                with real_open(p, "w", encoding="utf-8") as f:
                    f.write(raw + " ")
            return real_open(p, *a, **kw)
        with mock.patch("builtins.open", racing_open):
            with self.assertRaises(ValueError):
                self.sw.switch_on(self.path, self.setting(), backup_dir=self.bak, stamp="t2")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), raw + " ")
        self.assertEqual([n for n in self.os.listdir(self.dir) if ".tmp-gbt-" in n], [])


if __name__ == "__main__":
    unittest.main()
