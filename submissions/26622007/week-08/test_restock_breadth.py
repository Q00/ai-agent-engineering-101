"""Finite breadth study: switch, assignment, listing, and existing guard limits."""
import json
import os
import tempfile
import unittest

import meta_harness
import restock
import restock_breadth as breadth

NOW = 1794290000.0
ON = {"study": "breadth-test", "salt": "s1", "skus": ["G1", "G2"], "units": 2,
      "treat": 0.5, "start_at": NOW - 60, "stop_at": NOW + 3600}


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def write(self, data):
        with open(os.path.join(self.md, breadth.SWITCH), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_validity_boundaries_and_stop(self):
        self.write({"enabled": True, **ON})
        self.assertEqual(breadth.config(self.md, env={}, now=ON["start_at"])["units"], 2)
        self.assertIsNone(breadth.config(self.md, env={}, now=ON["stop_at"]))
        self.assertIsNone(breadth.config(self.md, env={}, now=ON["start_at"] - 1))
        for value in ("0", "false", "no", "off"):
            self.assertIsNone(breadth.config(self.md, env={"RESTOCK_BREADTH": value}, now=NOW))
        bads = ({"enabled": False}, {"study": ""}, {"salt": 1}, {"skus": []},
                {"skus": ["G1", "G1"]}, {"skus": [""]}, {"skus": [1]},
                {"skus": ["G"] * 41}, {"units": 0}, {"units": 4}, {"units": True},
                {"units": 2.0}, {"units": "2"}, {"treat": 0.29}, {"treat": 1.01},
                {"start_at": "x"}, {"stop_at": float("inf")},
                {"stop_at": ON["start_at"] + 12 * 3600 + 1})
        for bad in bads:
            with self.subTest(bad=bad):
                self.write({"enabled": True, **ON, **bad})
                self.assertIsNone(breadth.config(self.md, env={}, now=NOW))
        for missing in ("enabled", "study", "salt", "skus", "start_at", "stop_at"):
            with self.subTest(missing=missing):
                self.write({k: v for k, v in {"enabled": True, **ON}.items() if k != missing})
                self.assertIsNone(breadth.config(self.md, env={}, now=NOW))
        for omitted in ("units", "treat"):
            self.write({k: v for k, v in {"enabled": True, **ON}.items() if k != omitted})
            self.assertIsNotNone(breadth.config(self.md, env={}, now=NOW))

    def test_switch_on_round_trip_and_bad_write_preserves_old(self):
        d = breadth.switch_on(self.md, study="x", salt="s", skus=["G1"], start_at=NOW, hours=12)
        self.assertEqual(breadth.config(self.md, env={}, now=NOW + 1)["skus"], ["G1"])
        self.assertEqual(d["stop_at"], NOW + 12 * 3600)
        path = os.path.join(self.md, breadth.SWITCH)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path, "rb") as f:
            old = f.read()
        for bad in ({"hours": 12 + 1 / 3600}, {"units": True}, {"skus": ["G1", "G1"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                breadth.switch_on(self.md, **{"study": "x", "salt": "s", "skus": ["G1"],
                                               "start_at": NOW, "hours": 12, **bad})
            with open(path, "rb") as f:
                self.assertEqual(f.read(), old)

    def test_arms_and_harness(self):
        arms = [breadth.arm(ON, f"G{i:08x}") for i in range(400)]
        self.assertEqual(arms, [breadth.arm(ON, f"G{i:08x}") for i in range(400)])
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertNotEqual(arms, [breadth.arm({**ON, "salt": "another"}, f"G{i:08x}") for i in range(400)])
        self.assertIsNone(breadth.arm(None, "G1"))
        self.assertIn(".market/" + breadth.SWITCH, meta_harness.SWITCHES)

    def test_second_switch_is_independent_and_names_are_restricted(self):
        self.assertEqual(breadth.SWITCH, breadth.SWITCHES[0])
        for name in breadth.SWITCHES:
            self.assertIn(".market/" + name, meta_harness.SWITCHES)
        first, second = breadth.SWITCHES
        self.write({"enabled": True, **ON})
        first_path = os.path.join(self.md, first)
        with open(first_path, "rb") as f:
            original = f.read()
        breadth.switch_on(self.md, study="second", salt="s2", skus=["G3"],
                          start_at=NOW, hours=2, name=second)
        self.assertEqual(breadth.config(self.md, env={}, now=NOW, name=second)["study"], "second")
        self.assertEqual(breadth.config(self.md, env={}, now=NOW, name=first)["study"], ON["study"])
        with open(first_path, "rb") as f:
            self.assertEqual(f.read(), original)
        bad = "other.json"
        self.assertIsNone(breadth.config(self.md, env={}, now=NOW, name=bad))
        with self.assertRaises(ValueError):
            breadth.switch_on(self.md, study="bad", salt="s", skus=["G4"],
                              start_at=NOW, hours=2, name=bad)
        self.assertFalse(os.path.exists(os.path.join(self.md, bad)))

    def test_configs_order_and_global_stop(self):
        first, second = breadth.SWITCHES
        self.assertEqual(breadth.configs(self.md, env={}, now=NOW), [])
        self.write({"enabled": True, **ON})
        self.assertEqual([c["study"] for c in breadth.configs(self.md, env={}, now=NOW)], [ON["study"]])
        os.unlink(os.path.join(self.md, first))
        breadth.switch_on(self.md, study="second", salt="s2", skus=["G3"],
                          start_at=NOW, hours=2, name=second)
        self.assertEqual([c["study"] for c in breadth.configs(self.md, env={}, now=NOW)], ["second"])
        self.write({"enabled": True, **ON})
        self.assertEqual([c["study"] for c in breadth.configs(self.md, env={}, now=NOW)],
                         [ON["study"], "second"])
        self.assertEqual(breadth.configs(self.md, env={"RESTOCK_BREADTH": "off"}, now=NOW), [])


class ListingTest(unittest.TestCase):
    def test_two_studies_order_overlap_and_independent_validation(self):
        first = {**ON, "skus": ["G1", "G2"], "treat": 1.0}
        second = {**ON, "study": "second", "salt": "s2", "skus": ["G2", "G3"],
                  "units": 3, "treat": 1.0}
        p, t = ["old"], {"old": 1}
        cfg = {"breadth": [first, second], "priority_skus": p, "sku_target_units": t}
        self.assertEqual(breadth.listing(cfg, NOW),
                         (["old", "G1", "G2", "G3"], {"old": 1, "G1": 2, "G2": 2, "G3": 3},
                          {"G1": 2, "G2": 2, "G3": 3}))
        self.assertEqual(breadth.listing({**cfg, "breadth": (first, second)}, NOW),
                         breadth.listing(cfg, NOW))
        self.assertEqual((p, t), (["old"], {"old": 1}))
        expected = breadth.listing({**cfg, "breadth": first}, NOW)
        self.assertEqual(breadth.listing({**cfg, "breadth": [first]}, NOW), expected)
        for bad in ({**second, "stop_at": NOW}, {**second, "skus": []}, None, "bad"):
            with self.subTest(bad=bad):
                self.assertEqual(breadth.listing({**cfg, "breadth": [first, bad]}, NOW), expected)
        self.assertEqual(breadth.listing({**cfg, "breadth": [None, second]}, NOW)[2],
                         {"G2": 3, "G3": 3})
        self.assertEqual(breadth.listing({**cfg, "priority_skus": ["old", "G2"],
                                          "breadth": [first, second]}, NOW)[2],
                         {"G1": 2, "G3": 3})
        for items in ([None, "bad", {**second, "skus": []}], []):
            got_p, got_t, added = breadth.listing({**cfg, "breadth": items}, NOW)
            self.assertIs(got_p, p)
            self.assertIs(got_t, t)
            self.assertEqual(added, {})
        already = ["G1", "G2"]
        got_p, got_t, added = breadth.listing({**cfg, "priority_skus": already,
                                                "breadth": [first]}, NOW)
        self.assertIs(got_p, already)
        self.assertIs(got_t, t)
        self.assertEqual(added, {})

    def test_second_study_guard_keeps_unit_cap_and_cash_buffer(self):
        second = {**ON, "study": "second", "salt": "s2", "skus": ["G3"],
                  "units": 2, "treat": 1.0}
        cfg = {**restock.DEFAULTS, "breadth": [second], "priority_skus": [],
               "sku_target_units": {}, "keep_cash": 0, "cash_buffer": 0,
               "max_stock_cost": float("inf"), "high_price_goods": 100000,
               "high_price_max_units": 20, "high_price_listed_only": True}
        cfg["priority_skus"], cfg["sku_target_units"], added = breadth.listing(cfg, NOW)
        self.assertEqual(added, {"G3": 2})
        catalog = {"G3": {"kind": "goods", "list_price": 300000}}
        inv = {"balance": 10000000, "units": [{"sku": "G3", "kind": "goods", "cost": 1}]}
        self.assertEqual(restock.guard([{"sku": "G3", "qty": 5}], inv, catalog, cfg),
                         [{"sku": "G3", "qty": 1}])
        self.assertEqual(restock.guard([{"sku": "G3", "qty": 1}],
                                       {**inv, "balance": 1500000}, catalog,
                                       {**cfg, "cash_buffer": 1400000}), [])

    def test_appends_only_new_b_in_switch_order_without_mutation(self):
        candidates = [f"G{i}" for i in range(100)]
        bs = [s for s in candidates if breadth.arm(ON, s) == "B"][:6]
        a = next(s for s in candidates if breadth.arm(ON, s) == "A")
        on = {**ON, "skus": [bs[0], a, bs[1], bs[2], bs[3], bs[4], bs[5]]}
        priority, targets = ["old"], {"old": 4, bs[4]: 3}
        cfg = {"breadth": on, "priority_skus": priority, "sku_target_units": targets,
               "skip_skus": [bs[1]], "high_price_proven_skus": [bs[2]],
               "other": "untouched"}
        got_p, got_t, added = breadth.listing(cfg, NOW)
        self.assertEqual(got_p, ["old", bs[0], bs[3], bs[5]])
        self.assertEqual(added, {bs[0]: 2, bs[3]: 2, bs[5]: 2})
        self.assertEqual(got_t, {**targets, **added})
        self.assertEqual(priority, ["old"])
        self.assertEqual(targets, {"old": 4, bs[4]: 3})
        self.assertIsNot(got_p, priority)
        self.assertIsNot(got_t, targets)
        self.assertNotIn(a, got_p)

    def test_off_expired_and_malformed_preserve_identity(self):
        p, t = ["old"], {"old": 2}
        for cfg, now in (({"priority_skus": p, "sku_target_units": t}, NOW),
                         ({"breadth": ON, "priority_skus": p, "sku_target_units": t}, ON["stop_at"]),
                         ({"breadth": ON, "priority_skus": tuple(p), "sku_target_units": t}, NOW),
                         ({"breadth": ON, "priority_skus": p, "sku_target_units": list(t)}, NOW)):
            with self.subTest(cfg=cfg):
                got_p, got_t, added = breadth.listing(cfg, now)
                self.assertIs(got_p, cfg["priority_skus"])
                self.assertIs(got_t, cfg["sku_target_units"])
                self.assertEqual(added, {})

    def test_guard_keeps_listed_cap_and_cash(self):
        b = next(f"G{i}" for i in range(100) if breadth.arm(ON, f"G{i}") == "B")
        a = next(f"G{i}" for i in range(100) if breadth.arm(ON, f"G{i}") == "A")
        cfg = {**restock.DEFAULTS, "breadth": {**ON, "skus": [a, b]},
               "priority_skus": [], "sku_target_units": {}, "keep_cash": 0,
               "cash_buffer": 0, "max_stock_cost": float("inf"),
               "high_price_goods": 100000, "high_price_max_units": 20,
               "high_price_listed_only": True}
        cfg["priority_skus"], cfg["sku_target_units"], _ = breadth.listing(cfg, NOW)
        catalog = {s: {"kind": "goods", "list_price": 300000} for s in (a, b)}
        inv = {"balance": 10000000, "units": [{"sku": b, "kind": "goods", "cost": 1}]}
        self.assertEqual(restock.guard([{"sku": b, "qty": 5}], inv, catalog, cfg), [{"sku": b, "qty": 1}])
        self.assertEqual(restock.guard([{"sku": a, "qty": 1}], inv, catalog, cfg), [])
        poor = {**inv, "balance": 1500000}
        line = [{"sku": b, "qty": 1}]
        self.assertEqual(restock.guard(line, poor, catalog, cfg), line)
        self.assertEqual(restock.guard(line, poor, catalog, {**cfg, "cash_buffer": 1400000}), [])


class DefaultTest(unittest.TestCase):
    """breadth-1111 keep: pricing.json lists the study's 10 arm-B SKUs at 2 (arm A was never listed and stays out),
    and the study's switch left on adds nothing more."""
    B = ["G04ee8423", "G6841e8a3", "G7aac0d6f", "Ga4e6abc9", "Gbf98024f", "G2d1fbb87", "G5b96e0fd", "G40e27aec",
         "Gfe4f052a", "Ge7edc329"]
    A = ["Gea0cca52", "G4e4eabdb", "Gc44de87c", "G0132ff94", "Gcc75125e"]

    def test_pricing_lists_arm_b_only_and_the_switch_adds_none(self):
        here = os.path.dirname(os.path.abspath(restock.__file__))
        with open(os.path.join(here, "pricing.json"), encoding="utf-8") as f:
            cfg = json.load(f)["restock"]
        self.assertIn("breadth_default_source", cfg)
        self.assertEqual(cfg["priority_skus"][-18:-8], self.B)        # breadth-t2-1111's 8 come after (T2Test)
        self.assertEqual({s: cfg["sku_target_units"][s] for s in self.B}, {s: 2 for s in self.B})
        self.assertFalse(set(self.A) & (set(cfg["priority_skus"]) | set(cfg["sku_target_units"])))
        on = {**ON, "skus": self.B + self.A, "salt": "breadth-1111-b4", "treat": 0.5}
        self.assertEqual([s for s in on["skus"] if breadth.arm(on, s) == "B"], self.B)     # the ops switch's own arms
        p, t, added = breadth.listing({**cfg, "breadth": on}, NOW)
        self.assertEqual(added, {})
        self.assertIs(p, cfg["priority_skus"])


class T2DefaultTest(unittest.TestCase):
    """breadth-t2-1111 keep: its 8 arm-B SKUs at 2 at the end of priority_skus; arm A stays out; its switch adds none."""
    SKUS = ["G53b150b5", "Ge4f8ef71", "G037721ac", "G5c543233", "G6a308a3b", "Gcfe62c2c", "Gf3e749a1", "G0c4045d3",
            "G9257554b", "G77834f4a", "G28bd9142", "G196023bf", "G326ebe9a", "G5e3784e6", "Ga0aa1835"]
    B = ["G53b150b5", "Ge4f8ef71", "G5c543233", "G0c4045d3", "G28bd9142", "G196023bf", "G326ebe9a", "G5e3784e6"]

    def test_pricing_lists_t2_arm_b_last_and_the_switch_adds_none(self):
        here = os.path.dirname(os.path.abspath(restock.__file__))
        with open(os.path.join(here, "pricing.json"), encoding="utf-8") as f:
            cfg = json.load(f)["restock"]
        self.assertIn("breadth_t2_default_source", cfg)
        on = {**ON, "skus": self.SKUS, "salt": "breadth-t2-1111", "treat": 0.5}
        self.assertEqual([s for s in self.SKUS if breadth.arm(on, s) == "B"], self.B)     # the ops switch's own arms
        self.assertEqual(cfg["priority_skus"][-8:], self.B)
        self.assertEqual({s: cfg["sku_target_units"][s] for s in self.B}, {s: 2 for s in self.B})
        a = set(self.SKUS) - set(self.B)
        self.assertFalse(a & (set(cfg["priority_skus"]) | set(cfg["sku_target_units"])))
        p, t, added = breadth.listing({**cfg, "breadth": on}, NOW)
        self.assertEqual(added, {})
        self.assertIs(p, cfg["priority_skus"])


if __name__ == "__main__":
    unittest.main()
