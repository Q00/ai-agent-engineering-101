"""price.high_price_trial: 30만+ goods split by SKU x grade, the trial half opening at a lower share of the median.

    python -m unittest test_price_trial -v        (from submissions/26622007/week-08)
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import profit_price as pp    # noqa: E402

TRIAL = {"factor": 0.93, "share": 0.5, "salt": "t1"}


def unit(sku, grade="A", cost=500000, lp=1000000, kind="goods"):
    return {"unit": "u", "sku": sku, "grade": grade, "cost": cost, "list_price": lp, "kind": kind}


def pricing(trial=TRIAL):
    p = {"price": {"min_profit": 1, "min_profit_share": 0, "goods_break": 0.035, "median_up": 1.0,
                   "high_median_up": {"min_list": 300000, "factor": 0.97}}}
    if trial is not None:
        p["price"]["high_price_trial"] = trial
    return p


class TrialTest(unittest.TestCase):
    # GPT Pro 11/06 23:40 (메인 23:40): price cuts only on 30만+ goods, tried per SKU x grade, never under the floor
    def arms(self):
        keys = [(f"G{i:04d}", g) for i in range(40) for g in "AB"]
        return {k: pp.trial_arm(k[0], k[1], TRIAL) for k in keys}

    def test_about_half_of_the_sku_grades_are_in_the_trial_and_the_split_is_stable(self):
        arms = self.arms()
        self.assertTrue(25 <= sum(arms.values()) <= 55)
        self.assertEqual(arms, self.arms())
        self.assertFalse(pp.trial_arm("G0001", "A", None))
        self.assertFalse(pp.trial_arm("G0001", "A", {"factor": 0.93, "share": 0}))

    def test_trial_units_open_lower_never_under_the_floor(self):
        arms = self.arms()
        on = next(k for k, v in arms.items() if v)
        off = next(k for k, v in arms.items() if not v)
        q_on = pp.quote(unit(*on), 800000, None, (), pricing())
        q_off = pp.quote(unit(*off), 800000, None, (), pricing())
        self.assertEqual(q_off["open"], 776000)                 # 0.97 x median
        self.assertEqual(q_on["open"], 744000)                  # 0.93 x median
        self.assertIn("trial", q_on["why"])
        dear = pp.quote(unit(*on, cost=760000), 800000, None, (), pricing())
        self.assertEqual(dear["open"], dear["floor"])           # floor above 0.93 x median: the floor
        self.assertEqual(pp.quote(unit(*on), 800000, None, (), pricing(None))["open"], 776000)   # no trial set

    def test_cheap_goods_food_and_no_median_stay_as_they_were(self):
        on = next(k for k, v in self.arms().items() if v)
        self.assertEqual(pp.quote(unit(*on, cost=100000, lp=200000), 180000, None, (), pricing())["open"],
                         pp.quote(unit(*on, cost=100000, lp=200000), 180000, None, (), pricing(None))["open"])
        self.assertEqual(pp.quote(unit(*on, kind="food"), 800000, None, (), pricing())["open"],
                         pp.quote(unit(*on, kind="food"), 800000, None, (), pricing(None))["open"])
        self.assertEqual(pp.quote(unit(*on), None, None, (), pricing())["open"],
                         pp.quote(unit(*on), None, None, (), pricing(None))["open"])

    def test_bad_settings_change_nothing(self):
        on = next(k for k, v in self.arms().items() if v)
        base = pp.quote(unit(*on), 800000, None, (), pricing(None))["open"]
        for bad in ({"factor": 0.2, "share": 0.5}, {"factor": 1.2, "share": 0.5}, {"factor": True, "share": 0.5},
                    {"factor": 0.93, "share": "x"}, "x", {"factor": float("nan"), "share": 0.5}):
            self.assertEqual(pp.quote(unit(*on), 800000, None, (), pricing(bad))["open"], base, bad)

    def test_shipped_trial(self):
        # Latest instruction: keep the new price trial OFF and the baseline at 0.97.
        with open("pricing.json", encoding="utf-8") as f:
            p = json.load(f)["price"]
        t = p["high_price_trial"]
        self.assertEqual((p["high_median_up"]["factor"], t["factor"], t["share"]), (0.97, 0.97, 0))
        self.assertEqual(t["salt"], "20261109-book-margin")

    def test_shipped_trial_is_off_and_opens_at_097_never_under_the_floor(self):
        with open("pricing.json", encoding="utf-8") as f:
            shipped = {"price": json.load(f)["price"]}
        t = shipped["price"]["high_price_trial"]
        keys = [(f"G{i:04d}", g) for i in range(60) for g in "ABC"]
        arms = {k: pp.trial_arm(k[0], k[1], t) for k in keys}
        self.assertFalse(any(arms.values()))
        hold, up = keys[0], keys[-1]
        # the first ask against the median is this test's subject: the floors are break-even here (the shipped
        # grade_profit_share, 11/11, lifts a floor over 0.97 x this median and is pinned in test_profit_rules)
        cfg = {"price": {**shipped["price"], "ask_caps": {}, "scarce_asks": {}, "grade_profit_share": {}}}
        for key in keys:
            q = pp.quote(unit(*key, cost=500000), 800001, None, (), cfg)
            if key[1] == "C":   # grade_break C 0.42 (11/16): (500,000 + 1,250 + 1) / 0.58 = 864,225.9 -> its floor wins
                self.assertEqual((q["open"], q["floor"]), (864226, 864226))
            else:
                self.assertEqual(q["open"], 776001)
        dear = pp.quote(unit(*hold, cost=760000), 800000, None, (), cfg)
        self.assertEqual(dear["open"], dear["floor"])               # the floor still wins over 0.97 x
        self.assertGreaterEqual(dear["floor"], 776000)
        cheap = unit(*up, cost=100000, lp=200000)                   # under 30만: median_up 1.0 as before
        self.assertEqual(pp.quote(cheap, 180000, None, (), cfg)["open"], 180000)
        capped = {"price": {**cfg["price"], "ask_caps": {hold[0]: {hold[1]: 700000}}}}
        self.assertEqual(pp.quote(unit(*hold, cost=500000), 800001, None, (), capped)["open"], 700000)   # caps after
        # Without a median the OFF baseline applies to kind ratio; grade C's higher floor still wins.
        self.assertEqual(pp.quote(unit(*hold, cost=500000), None, None, (), cfg)["open"], 732350)
        self.assertEqual(pp.quote(unit(*up, cost=500000), None, None, (), cfg)["open"], 864226)     # C at 0.42 (0.344: 764,103)


if __name__ == "__main__":
    unittest.main()
