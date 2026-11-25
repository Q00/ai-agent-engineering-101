"""Finite food stock-out study: switch, assignment, food-only listing, line order and existing guard limits."""
import json
import os
import tempfile
import unittest

import meta_harness
import restock
import restock_food_stockout as fso

NOW = 1794380000.0
ON = {"study": "fso-test", "salt": "s1", "skus": ["F1", "F2"], "units": 10,
      "treat": 0.5, "start_at": NOW - 60, "stop_at": NOW + 3600}
FOOD = {"kind": "food", "list_price": 20000, "shelf_hours": 168}


def b_and_a(on, n=2):
    names = [f"F{i}" for i in range(200)]
    return [s for s in names if fso.arm(on, s) == "B"][:n], next(s for s in names if fso.arm(on, s) == "A")


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def write(self, data):
        with open(os.path.join(self.md, fso.SWITCH), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_missing_switch_is_off(self):
        self.assertIsNone(fso.config(self.md, env={}, now=NOW))
        self.assertIsNone(fso.config(os.path.join(self.md, "nope"), env={}, now=NOW))
        with open(os.path.join(self.md, fso.SWITCH), "w") as f:
            f.write("{not json")
        self.assertIsNone(fso.config(self.md, env={}, now=NOW))

    def test_validity_boundaries_and_stop(self):
        self.write({"enabled": True, **ON})
        self.assertEqual(fso.config(self.md, env={}, now=ON["start_at"])["units"], 10)
        self.assertIsNone(fso.config(self.md, env={}, now=ON["stop_at"]))
        self.assertIsNone(fso.config(self.md, env={}, now=ON["start_at"] - 1))
        for value in ("0", "false", "no", "off", " OFF "):
            self.assertIsNone(fso.config(self.md, env={"RESTOCK_FOOD_STOCKOUT": value}, now=NOW))
        bads = ({"enabled": False}, {"enabled": "true"}, {"study": ""}, {"salt": 1}, {"skus": []},
                {"skus": ["F1", "F1"]}, {"skus": [""]}, {"skus": [1]}, {"skus": "F1"},
                {"skus": ["F"] * 41}, {"units": 0}, {"units": 21}, {"units": True},
                {"units": 2.0}, {"units": "2"}, {"treat": 0.29}, {"treat": 1.01}, {"treat": float("nan")},
                {"start_at": "x"}, {"stop_at": float("inf")}, {"start_at": 0},
                {"stop_at": ON["start_at"]}, {"stop_at": ON["start_at"] + 12 * 3600 + 1})
        for bad in bads:
            with self.subTest(bad=bad):
                self.write({"enabled": True, **ON, **bad})
                self.assertIsNone(fso.config(self.md, env={}, now=NOW))
        for missing in ("enabled", "study", "salt", "skus", "start_at", "stop_at"):
            with self.subTest(missing=missing):
                self.write({k: v for k, v in {"enabled": True, **ON}.items() if k != missing})
                self.assertIsNone(fso.config(self.md, env={}, now=NOW))
        for omitted, default in (("units", fso.UNITS), ("treat", fso.TREAT)):
            self.write({k: v for k, v in {"enabled": True, **ON}.items() if k != omitted})
            self.assertEqual(fso.config(self.md, env={}, now=NOW)[omitted], default)
        self.write({"enabled": True, **ON, "units": 20, "treat": 1.0})
        self.assertEqual(fso.config(self.md, env={}, now=NOW)["units"], 20)

    def test_switch_on_round_trip_and_bad_write_preserves_old(self):
        d = fso.switch_on(self.md, study="x", salt="s", skus=["F1"], start_at=NOW, hours=12)
        self.assertEqual(fso.config(self.md, env={}, now=NOW + 1)["skus"], ["F1"])
        self.assertEqual(d["stop_at"], NOW + 12 * 3600)
        self.assertIsNone(fso.config(self.md, env={}, now=NOW + 12 * 3600))
        path = os.path.join(self.md, fso.SWITCH)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path, "rb") as f:
            old = f.read()
        for bad in ({"hours": 12 + 1 / 3600}, {"hours": 0}, {"units": True}, {"units": 21},
                    {"skus": ["F1", "F1"]}, {"treat": 0.2}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fso.switch_on(self.md, **{"study": "x", "salt": "s", "skus": ["F1"],
                                          "start_at": NOW, "hours": 12, **bad})
            with open(path, "rb") as f:
                self.assertEqual(f.read(), old)
        self.assertEqual([n for n in os.listdir(self.md) if ".tmp-" in n], [])

    def test_arms_and_harness(self):
        arms = [fso.arm(ON, f"G{i:08x}") for i in range(400)]
        self.assertEqual(arms, [fso.arm(ON, f"G{i:08x}") for i in range(400)])
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertNotEqual(arms, [fso.arm({**ON, "salt": "another"}, f"G{i:08x}") for i in range(400)])
        self.assertEqual({fso.arm({**ON, "treat": 1.0}, f"G{i:08x}") for i in range(400)}, {"B"})
        self.assertIsNone(fso.arm(None, "F1"))
        self.assertIsNone(fso.arm({}, "F1"))
        self.assertIn(".market/" + fso.SWITCH, meta_harness.SWITCHES)


class ListingTest(unittest.TestCase):
    def test_appends_only_new_b_food_in_switch_order_without_mutation(self):
        bs, a = b_and_a(ON, 7)
        on = {**ON, "skus": [bs[0], a, bs[1], bs[2], bs[3], bs[4], bs[5], bs[6]]}
        catalog = {s: dict(FOOD) for s in bs + [a]}
        catalog[bs[5]] = {"kind": "goods", "list_price": 20000}
        catalog[bs[6]] = {**FOOD, "shelf_hours": 48}
        priority, targets = ["old"], {"old": 4, bs[4]: 3}
        cfg = {"food_stockout": on, "priority_skus": priority, "sku_target_units": targets,
               "skip_skus": [bs[1]], "high_price_proven_skus": [bs[2]], "other": "untouched"}
        got_p, got_t, added = fso.listing(cfg, catalog, NOW)
        self.assertEqual(got_p, ["old", bs[0], bs[3]])
        self.assertEqual(added, {bs[0]: 10, bs[3]: 10})
        self.assertEqual(got_t, {**targets, **added})
        self.assertEqual((priority, targets), (["old"], {"old": 4, bs[4]: 3}))
        self.assertIsNot(got_p, priority)
        self.assertNotIn(a, got_p)

    def test_missing_or_odd_catalog_entries_are_not_listed(self):
        bs, _ = b_and_a(ON, 6)
        on = {**ON, "skus": bs}
        catalog = {bs[1]: None, bs[2]: {"kind": "food"}, bs[3]: {**FOOD, "shelf_hours": "168"},
                   bs[4]: {**FOOD, "shelf_hours": float("inf")}, bs[5]: dict(FOOD)}
        got_p, _, added = fso.listing({"food_stockout": on, "priority_skus": [], "sku_target_units": {}}, catalog, NOW)
        self.assertEqual(added, {bs[5]: 10})
        self.assertEqual(got_p, [bs[5]])

    def test_off_expired_and_malformed_preserve_identity(self):
        p, t = ["old"], {"old": 2}
        catalog = {"F1": dict(FOOD), "F2": dict(FOOD)}
        on = {**ON, "treat": 1.0}
        for cfg, cat, now in (({"priority_skus": p, "sku_target_units": t}, catalog, NOW),
                              ({"food_stockout": None, "priority_skus": p, "sku_target_units": t}, catalog, NOW),
                              ({"food_stockout": on, "priority_skus": p, "sku_target_units": t}, catalog, on["stop_at"]),
                              ({"food_stockout": on, "priority_skus": tuple(p), "sku_target_units": t}, catalog, NOW),
                              ({"food_stockout": on, "priority_skus": p, "sku_target_units": list(t)}, catalog, NOW),
                              ({"food_stockout": on, "priority_skus": p, "sku_target_units": t}, None, NOW),
                              ({"food_stockout": on, "priority_skus": p, "sku_target_units": t}, {}, NOW)):
            with self.subTest(cfg=cfg, cat=cat, now=now):
                got_p, got_t, added = fso.listing(cfg, cat, now)
                self.assertIs(got_p, cfg["priority_skus"])
                self.assertIs(got_t, cfg["sku_target_units"])
                self.assertEqual(added, {})
        self.assertEqual(fso.listing(None, catalog, NOW), (None, None, {}))


class LineOrderAndGuardTest(unittest.TestCase):
    """The lever: an empty listed food SKU's line goes first (urgent_listed), an unlisted one after every listed line."""
    CATALOG = {"G1": {"kind": "goods", "list_price": 400000}, "G2": {"kind": "goods", "list_price": 400000},
               "F9": dict(FOOD)}

    def plan(self, cfg):
        ranked = [(9.0, "F9", 10), (1.0, "G1", 2), (1.0, "G2", 2)]       # F9 asked by the demand model, best per won
        orig = restock.wanted, restock.fill_listed
        restock.wanted = lambda *a, **k: list(ranked)
        restock.fill_listed = lambda r, *a, **k: r
        try:
            inv = {"balance": 10 ** 9, "units": [{"sku": "G1", "category": "c", "cost": 1}]}
            return [b["sku"] for b in restock.plan(inv, self.CATALOG, [], cfg, {"active": True, "skus": {}})["buy"]]
        finally:
            restock.wanted, restock.fill_listed = orig

    def cfg(self, on=None):
        cfg = dict(restock.DEFAULTS, priority_skus=["G1", "G2"], sku_target_units={}, max_lines=2,
                   explore_lines=0, cash_buffer=0, keep_cash=0)
        if on is not None:
            cfg["food_stockout"] = on
            cfg["priority_skus"], cfg["sku_target_units"], _ = fso.listing(cfg, self.CATALOG)
        return cfg

    def test_off_the_unlisted_food_line_is_cut_on_the_listed_lines(self):
        self.assertEqual(self.plan(self.cfg()), ["G2", "G1"])

    def test_on_the_empty_food_goes_first_and_its_target_caps_the_line(self):
        import time
        now = time.time()
        on = {**ON, "skus": ["F9"], "treat": 1.0, "units": 6, "start_at": now - 60, "stop_at": now + 3600}
        cfg = self.cfg(on)
        self.assertEqual(self.plan(cfg), ["F9", "G2"])
        self.assertEqual(restock.guard([{"sku": "F9", "qty": 10}], {"balance": 10 ** 9, "units": []}, self.CATALOG, cfg),
                         [{"sku": "F9", "qty": 6}])
        held = {"balance": 10 ** 9, "units": [{"sku": "F9", "kind": "food", "cost": 10000}] * 4}
        self.assertEqual(restock.guard([{"sku": "F9", "qty": 10}], held, self.CATALOG, cfg), [{"sku": "F9", "qty": 2}])

    def test_guard_keeps_food_stock_cost_and_cash(self):
        import time
        now = time.time()
        on = {**ON, "skus": ["F9"], "treat": 1.0, "units": 20, "start_at": now - 60, "stop_at": now + 3600}
        cfg = self.cfg(on)
        line = [{"sku": "F9", "qty": 20}]
        inv = {"balance": 10 ** 9, "units": []}
        self.assertEqual(restock.guard(line, inv, self.CATALOG, {**cfg, "food_max_stock_cost": 15000 * 5}),
                         [{"sku": "F9", "qty": 5}])        # worst cost 0.75 x 20,000 a unit
        self.assertEqual(restock.guard(line, inv, self.CATALOG, {**cfg, "food_max_qty": 3}), [{"sku": "F9", "qty": 3}])
        self.assertEqual(restock.guard(line, {"balance": 1500000, "units": []}, self.CATALOG,
                                       {**cfg, "cash_buffer": 1500000}), [])


class MainWiringTest(unittest.TestCase):
    def test_main_reads_the_switch_after_breadth(self):
        src = open(restock.__file__, encoding="utf-8").read()
        body = src[src.index("def main():"):]
        self.assertLess(body.index("restock_breadth.listing(cfg)"), body.index("restock_food_stockout.listing(cfg, catalog)"))
        self.assertLess(body.index("restock_food_stockout.listing(cfg, catalog)"), body.index("out = plan("))


if __name__ == "__main__":
    unittest.main()
