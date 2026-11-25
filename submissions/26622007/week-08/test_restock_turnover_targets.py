"""A finite turnover target study leaves controls and the ordinary stock guards intact."""
import json
import os
import tempfile
import time
import unittest

import meta_harness
import restock
import restock_turnover_targets as tt

NOW = 1794290000.0
ON = {"study": "turnover-test", "salt": "s1", "skus": ["G1", "G2", "G3"], "factor": 2.0,
      "treat": 1.0, "start_at": NOW - 60, "stop_at": NOW + 3600}


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def write(self, data):
        with open(os.path.join(self.md, tt.SWITCH), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_validity_and_boundaries(self):
        self.write({"enabled": True, **ON})
        self.assertEqual(tt.config(self.md, env={}, now=ON["start_at"])["study"], ON["study"])
        self.assertIsNone(tt.config(self.md, env={}, now=ON["stop_at"]))
        self.assertIsNone(tt.config(self.md, env={}, now=ON["start_at"] - 1))
        for value in ("0", "false", "no", "off"):
            self.assertIsNone(tt.config(self.md, env={"RESTOCK_TURNOVER_TARGETS": value}, now=NOW))
        for bad in ({"enabled": False}, {"study": ""}, {"salt": 4}, {"skus": []},
                    {"skus": ["G1", "G1"]}, {"skus": [""]}, {"skus": [1]},
                    {"skus": ["G"] * 41}, {"factor": True}, {"factor": 1.24}, {"factor": 3.01},
                    {"treat": 0.29}, {"treat": 1.01}, {"start_at": "x"},
                    {"stop_at": float("inf")}, {"stop_at": ON["start_at"] + 12 * 3600 + 1}):
            with self.subTest(bad=bad):
                self.write({"enabled": True, **ON, **bad})
                self.assertIsNone(tt.config(self.md, env={}, now=NOW))
        self.write({"enabled": True, **{k: v for k, v in ON.items() if k != "skus"}})
        self.assertIsNone(tt.config(self.md, env={}, now=NOW))
        for missing in ("study", "salt", "skus", "start_at", "stop_at"):
            with self.subTest(missing=missing):
                self.write({"enabled": True, **{k: v for k, v in ON.items() if k != missing}})
                self.assertIsNone(tt.config(self.md, env={}, now=NOW))

    def test_switch_on_round_trip_and_invalid_keeps_old(self):
        d = tt.switch_on(self.md, study="x", salt="s", skus=["G1"], start_at=NOW, hours=12)
        self.assertEqual(tt.config(self.md, env={}, now=NOW + 1)["skus"], ["G1"])
        self.assertEqual(d["stop_at"], NOW + 12 * 3600)
        self.assertEqual(os.stat(os.path.join(self.md, tt.SWITCH)).st_mode & 0o777, 0o600)
        with open(os.path.join(self.md, tt.SWITCH), "rb") as f:
            before = f.read()
        with self.assertRaises(ValueError):
            tt.switch_on(self.md, study="x", salt="s", skus=["G1"], start_at=NOW, hours=12 + 1 / 3600)
        with open(os.path.join(self.md, tt.SWITCH), "rb") as f:
            self.assertEqual(f.read(), before)

    def test_arms_and_harness(self):
        half = {**ON, "treat": 0.5}
        arms = [tt.arm(half, f"G{i:08x}") for i in range(400)]
        self.assertEqual(arms, [tt.arm(half, f"G{i:08x}") for i in range(400)])
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertNotEqual(arms, [tt.arm({**half, "salt": "different"}, f"G{i:08x}") for i in range(400)])
        self.assertEqual(tt.arm(None, "G1"), None)
        self.assertIn(".market/" + tt.SWITCH, meta_harness.SWITCHES)


class TargetTest(unittest.TestCase):
    def test_boosts_only_named_treated_integer_targets_without_mutation(self):
        base = {"G1": 3, "G2": 11, "G3": 2, "G4": 4, "bad1": True, "bad2": "3", "bad3": 0, "bad4": -1}
        cfg = {"turnover_targets": {**ON, "skus": list(base)}}
        got, changes = tt.targets(cfg, base, NOW)
        self.assertEqual({k: got[k] for k in ("G1", "G2", "G3", "G4")},
                         {"G1": 6, "G2": 20, "G3": 4, "G4": 8})
        self.assertEqual(changes["G2"], {"target": 11, "boosted": 20})
        self.assertEqual(base["G1"], 3)
        self.assertIsNot(got, base)
        quarter = {**cfg, "turnover_targets": {**cfg["turnover_targets"], "factor": 1.25}}
        self.assertEqual(tt.targets(quarter, {"G3": 2}, NOW)[0]["G3"], 3)
        # a target pricing.json already holds over the cap is never lowered (review of the first draft: it came out 20)
        self.assertEqual(tt.targets({"turnover_targets": {**ON, "skus": ["G2"]}}, {"G2": 25}, NOW),
                         ({"G2": 25}, {}))
        self.assertEqual([got[k] for k in ("bad1", "bad2", "bad3", "bad4")], [True, "3", 0, -1])

    def test_controls_off_and_bad_input(self):
        half = {**ON, "treat": 0.5}
        a = next(f"G{i}" for i in range(100) if tt.arm(half, f"G{i}") == "A")
        b = next(f"G{i}" for i in range(100) if tt.arm(half, f"G{i}") == "B")
        self.assertEqual(tt.targets({"turnover_targets": {**half, "skus": [a, b]}}, {a: 3, b: 3}, NOW),
                         ({a: 3, b: 6}, {b: {"target": 3, "boosted": 6}}))
        base = {"G1": 3}
        for cfg, at in (({}, NOW), ({"turnover_targets": ON}, ON["stop_at"])):
            got, changed = tt.targets(cfg, base, at)
            self.assertIs(got, base)
            self.assertEqual(changed, {})
        self.assertEqual(tt.targets({"turnover_targets": ON}, [3], NOW), ([3], {}))

    def test_restock_uses_larger_target_and_still_keeps_cash(self):
        now = time.time()
        on = {**ON, "start_at": now - 60, "stop_at": now + 3600, "skus": ["G1"]}
        boosted, _ = tt.targets({"turnover_targets": on}, {"G1": 3}, now)
        cfg = {**restock.DEFAULTS, "priority_skus": ["G1"], "sku_target_units": boosted,
               "keep_cash": 0, "cash_buffer": 0, "max_stock_cost": float("inf"),
               "high_price_goods": 100000, "high_price_max_units": 20,
               "high_price_proven_max_units": 20, "fill_listed": True, "explore_lines": 0,
               "round_ends_at": None}
        catalog = {"G1": {"kind": "goods", "category": "가구>소파", "list_price": 1000000}}
        inv = {"balance": 10 ** 9, "units": [{"sku": "G1", "kind": "goods", "cost": 1} for _ in range(3)]}
        model = {"active": True, "cats": set(), "skus": {"G1": {"rate": 1.0, "my_share": 0.1, "price_ratio": 0.7}}}
        self.assertEqual(restock.guard([{"sku": "G1", "qty": 6}], inv, catalog, cfg), [{"sku": "G1", "qty": 3}])
        self.assertEqual(restock.plan(inv, catalog, [], cfg, model)["buy"], [{"sku": "G1", "qty": 3}])
        poor = {**inv, "balance": 2500000}
        self.assertEqual(restock.guard([{"sku": "G1", "qty": 6}], poor, catalog, {**cfg, "cash_buffer": 1000000}),
                         [{"sku": "G1", "qty": 1}])


if __name__ == "__main__":
    unittest.main()
