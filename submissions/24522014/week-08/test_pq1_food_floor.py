"""pq1 (price_cap.json "pq1") and the food floor (r3_policy.json food_pricing_v2.floor_cost_multiple), both approved by the user on
11-10 and shipped off; the seller reads them on every deal once it is restarted on code that reports pq1_v1 and food_floor_108_v1.
pq1: by task hash (salt pq1, share .5) the test arm opens at max(floor, floor(list x .70)) on every goods unit, 1M+ included; the
control arm keeps today's cap (.75 under 1M, 1M+ left alone). Food floor: at least cost x multiple on every food path."""

import copy
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import price_cap
import r3_policy
import seller_r3

CAP = {"enabled": True, "ratio": 0.75, "max_list_price": 1000000}
PQ1 = {"enabled": True, "share": 0.5, "salt": "pq1", "ratio": 0.70}


def arm_of(task):
    return "test" if int(hashlib.sha256(f"pq1:{task}".encode()).hexdigest(), 16) % 10000 < 5000 else "control"


TEST_TASK = next(f"t{i}" for i in range(200) if arm_of(f"t{i}") == "test")
CONTROL_TASK = next(f"t{i}" for i in range(200) if arm_of(f"t{i}") == "control")


def goods(listed, unit="u1"):
    return {"unit": unit, "sku": "G1", "kind": "goods", "list_price": listed}


class Pq1Test(unittest.TestCase):
    def capped(self, terms, unit, task, pq1=PQ1, cap=CAP):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "price_cap.json"
            path.write_text(json.dumps({**cap, "pq1": pq1} if pq1 is not None else cap), encoding="utf-8")
            return price_cap.capped(terms, goods(unit["listed"]) if "listed" in unit else unit, path, task=task)

    def test_shipped_on_at_half_and_off_is_today(self):
        # Switched on 11-10 after the user's seller restart (installed off in 6768fed3).
        self.assertEqual(price_cap.pq1_settings(), {"share": 0.5, "salt": "pq1", "ratio": 0.7})
        shipped = json.loads(price_cap.CONFIG.read_text(encoding="utf-8"))["pq1"]
        self.assertEqual((shipped["enabled"], shipped["share"], shipped["salt"], shipped["ratio"]), (True, 0.5, "pq1", 0.7))
        terms = {"quote": 100000, "floor": 60000}
        for task in (TEST_TASK, CONTROL_TASK, None):
            self.assertEqual(self.capped(terms, {"listed": 100000}, task, pq1={**PQ1, "enabled": False}),
                             self.capped(terms, {"listed": 100000}, None, pq1=None))

    def test_the_test_arm_opens_at_70_percent_of_list_on_all_goods(self):
        for listed in (100000, 2000000):
            got = self.capped({"quote": listed, "floor": int(listed * .5)}, {"listed": listed}, TEST_TASK)
            self.assertEqual((got["quote"], got["price_cap"]["arm"], got["price_cap"]["state"]), (math.floor(listed * .7), "test", "capped"))

    def test_the_floor_stays_in_charge_and_the_quote_is_never_raised(self):
        got = self.capped({"quote": 100000, "floor": 80000}, {"listed": 100000}, TEST_TASK)
        self.assertEqual((got["quote"], got["floor"], got["price_cap"]["state"]), (80000, 80000, "floor_bound"))
        got = self.capped({"quote": 60000, "floor": 50000}, {"listed": 100000}, TEST_TASK)
        self.assertEqual((got["quote"], got["price_cap"]["state"]), (60000, "under"))

    def test_the_control_arm_keeps_todays_cap(self):
        got = self.capped({"quote": 100000, "floor": 50000}, {"listed": 100000}, CONTROL_TASK)
        self.assertEqual((got["quote"], got["price_cap"]["arm"]), (75000, "control"))
        got = self.capped({"quote": 2000000, "floor": 1000000}, {"listed": 2000000}, CONTROL_TASK)
        self.assertEqual((got["quote"], got["price_cap"]["state"], got["price_cap"]["arm"]), (2000000, "above_max_list_price", "control"))

    def test_food_and_unitless_terms_are_left_to_todays_cap(self):
        food = {"unit": "u", "sku": "F", "kind": "food", "list_price": 100000}
        got = self.capped({"quote": 100000, "floor": 50000}, food, TEST_TASK)
        self.assertNotIn("arm", got.get("price_cap", {}))

    def test_the_split_is_about_half_by_task_hash(self):
        tests = sum(arm_of(f"task-{i}") == "test" for i in range(4000))
        self.assertTrue(1850 <= tests <= 2150, tests)
        self.assertEqual(price_cap.pq1_arm(TEST_TASK, PQ1), "test")

    def test_a_broken_section_is_off(self):
        for bad in ({**PQ1, "enabled": "true"}, {**PQ1, "share": 1.5}, {**PQ1, "share": "x"}, {**PQ1, "salt": ""}, {**PQ1, "ratio": .4}, {**PQ1, "ratio": 1.1}, [], None):
            with self.subTest(bad=bad), TemporaryDirectory() as folder:
                path = Path(folder) / "price_cap.json"
                path.write_text(json.dumps({**CAP, "pq1": bad}), encoding="utf-8")
                self.assertIsNone(price_cap.pq1_settings(path))

    def test_the_seller_passes_its_task_and_reports_the_features(self):
        source = Path(seller_r3.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("capped(selling_terms(u, catalog, signals=signals, runtime=effective(MARKET_DIR)), u, task=task.id)"), 2)
        self.assertIn("pq1_v1", seller_r3.FEATURES)
        self.assertIn("food_floor_108_v1", seller_r3.FEATURES)


class FoodFloor(unittest.TestCase):
    RUNTIME = {"version": 1, "experiment": None, "controls": {"quote_multipliers": {}}}

    def terms(self, left, multiple, cost=10000, listed=20000):
        policy = copy.deepcopy(r3_policy.load_policy())
        policy["selling"]["food_pricing_v2"]["floor_cost_multiple"] = multiple
        now = time.time()
        unit = {"unit": "u", "sku": "F", "kind": "food", "cost": cost, "list_price": listed, "grade": "A", "expires_at": now + left * 100 * 3600}
        with patch.object(r3_policy, "load_policy", return_value=policy):
            return r3_policy.selling_terms(unit, {"F": {"shelf_hours": 100}}, now=now, runtime=self.RUNTIME)

    def test_shipped_on_at_108(self):
        # Switched on 11-10 with the food probe, after the user's seller restart (installed off in 6768fed3).
        self.assertEqual(r3_policy.load_policy()["selling"]["food_pricing_v2"]["floor_cost_multiple"], 1.08)

    def test_off_is_today(self):
        for left in (.9, .3, .15, .05):
            with self.subTest(left=left):
                self.assertEqual(self.terms(left, None), self.terms(left, "1.08"))

    def test_on_every_food_path_sells_at_cost_x_108_or_more(self):
        for left in (.9, .3, .15, .05):  # fresh (ordinary pricing), both market bands, expiry clearance
            with self.subTest(left=left):
                t = self.terms(left, 1.08)
                self.assertGreaterEqual(t["floor"], math.ceil(10000 * 1.08))
                self.assertGreaterEqual(t["quote"], t["floor"])
                self.assertFalse(t["below_cost_allowed"])

    def test_the_floor_never_lowers_a_higher_one(self):
        # Market band floor .6 x 20000 = 12000 is above cost x 1.08 = 10800.
        self.assertEqual(self.terms(.3, 1.08)["floor"], max(12000, self.terms(.3, None)["floor"]))

    def test_out_of_bounds_multiples_are_off(self):
        for bad in (0.9, 1.6, float("inf")):
            with self.subTest(bad=bad):
                self.assertEqual(self.terms(.3, bad), self.terms(.3, None))


if __name__ == "__main__":
    unittest.main()
