"""strategist/guard.py: allowlist, number checks, clamp, step, rounding, cooldown, limits, cross-validation."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-guard-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy
from strategist import guard as g

H = 3600
NOW = 1_800_000_000


def pol(**over):
    p = copy.deepcopy(policy.DEFAULTS)
    for k, v in over.items():
        sec, key = k.split("__")
        p[sec][key] = v
    return p


def run(*proposals, p=None, last=None, **kw):
    return g.guard(list(proposals), p or pol(), last or {}, NOW, **kw)


def prop(key, value, reason="r"):
    return {"key": key, "value": value, "reason": reason}


class Table(unittest.TestCase):
    def test_every_tunable_key_is_a_number_in_defaults_and_inside_its_bounds(self):
        for key in g.TUNABLE:
            v = policy.get(policy.DEFAULTS, key)
            self.assertTrue(isinstance(v, (int, float)) and not isinstance(v, bool), key)
            lo, hi, step = g.bounds(key)
            self.assertTrue(lo <= v <= hi, key)
            self.assertGreater(step, 0, key)

    def test_bounds_intersect_policy_ranges(self):
        self.assertEqual(g.bounds("stock.max_lines"), (3, 10, 2))
        self.assertEqual(g.bounds("buyer.continue_p"), (0.5, 0.95, 0.05))

    def test_tunable_view_shows_current_values(self):
        rows = g.tunable_view(pol(seller__floor_list=0.55))
        self.assertEqual(len(rows), len(g.TUNABLE))
        row = next(r for r in rows if r["key"] == "seller.floor_list")
        self.assertEqual(row, {"key": "seller.floor_list", "value": 0.55, "lo": 0.40, "hi": 0.80, "max_step": 0.05})


class Rejects(unittest.TestCase):
    def test_frozen_keys(self):
        acc, rej = run(prop("stock.cash_floor", 400000), prop("stock.end_ts", 1), prop("buyer.binding", 0),
                       prop("llm.planner_enabled", 1), prop("policy.seller.floor_list", 0.55),
                       prop("seller.warranty_grades", 1), prop(None, 0.5), prop(5, 0.5))
        self.assertEqual(acc, [])
        self.assertEqual([r["why"] for r in rej], ["frozen"] * 8)
        self.assertEqual(rej[0], {"key": "stock.cash_floor", "requested": 400000, "why": "frozen"})

    def test_not_a_number(self):
        bad = [True, False, "0.9", float("nan"), float("inf"), float("-inf"), None, [0.9], {"v": 0.9}]
        acc, rej = run(*[prop("seller.first_ask_list", v) for v in bad])
        self.assertEqual(acc, [])
        self.assertEqual([r["why"] for r in rej], ["not a number"] * len(bad))

    def test_huge_int_is_not_a_number_and_does_not_sink_the_others(self):
        acc, rej = run(prop("buyer.max_shops", 10 ** 400), prop("seller.floor_list", 0.55))
        self.assertEqual([(r["key"], r["why"]) for r in rej], [("buyer.max_shops", "not a number")])
        self.assertEqual([(a["key"], a["new"]) for a in acc], [("seller.floor_list", 0.55)])

    def test_hand_set_value_outside_tunable_range_is_left_alone(self):
        p = pol(seller__first_ask_list=0.5, seller__floor_list=0.45)      # valid in RANGES, below TUNABLE lo 0.70
        acc, rej = run(prop("seller.first_ask_list", 0.45), prop("seller.first_ask_list", 0.9), p=p)
        self.assertEqual(acc, [])
        self.assertIn("outside tunable range", rej[0]["why"])
        self.assertEqual(rej[1]["why"], "duplicate")

    def test_malformed_proposals(self):
        acc, rej = run("seller.floor_list=0.55", ["seller.floor_list", 0.55], {"value": 0.55})
        self.assertEqual(acc, [])
        self.assertEqual([r["why"] for r in rej], ["malformed", "malformed", "frozen"])
        self.assertEqual(g.guard(None, pol(), {}, NOW), ([], []))
        self.assertEqual(g.guard({"key": "seller.floor_list", "value": 0.55}, pol(), {}, NOW), ([], []))

    def test_no_change(self):
        acc, rej = run(prop("seller.floor_list", 0.60), prop("stock.demand_window_h", 6.4))
        self.assertEqual(acc, [])
        self.assertEqual([r["why"] for r in rej], ["no change", "no change"])

    def test_cooldown(self):
        last = {"seller.floor_list": NOW - 2 * H, "buyer.open_list": NOW - 3 * H, "buyer.cap_food": None}
        acc, rej = run(prop("seller.floor_list", 0.55), prop("buyer.open_list", 0.55), prop("buyer.cap_food", 0.92),
                       last=last)
        self.assertEqual([a["key"] for a in acc], ["buyer.open_list", "buyer.cap_food"])
        self.assertEqual(rej, [{"key": "seller.floor_list", "requested": 0.55, "why": "cooldown"}])
        acc, rej = run(prop("seller.floor_list", 0.55), last={"seller.floor_list": NOW - H}, cooldown_h=0.5)
        self.assertEqual([a["key"] for a in acc], ["seller.floor_list"])

    def test_duplicate_keeps_first(self):
        acc, rej = run(prop("seller.floor_list", 0.55, "first"), prop("seller.floor_list", 0.65, "second"))
        self.assertEqual([(a["new"], a["reason"]) for a in acc], [(0.55, "first")])
        self.assertEqual(rej, [{"key": "seller.floor_list", "requested": 0.65, "why": "duplicate"}])

    def test_max_changes(self):
        props = [prop("seller.floor_list", 0.55), prop("buyer.open_list", 0.55), prop("buyer.cap_food", 0.92),
                 prop("stock.per_sku_qty", 3), prop("stock.cash_floor", 1)]
        acc, rej = run(*props)
        self.assertEqual(len(acc), 3)
        self.assertEqual([(r["key"], r["why"]) for r in rej],
                         [("stock.per_sku_qty", "over max_changes"), ("stock.cash_floor", "frozen")])
        acc, rej = run(*props, max_changes=1)
        self.assertEqual([a["key"] for a in acc], ["seller.floor_list"])


class Settles(unittest.TestCase):
    def test_clamp_to_range(self):
        acc, _ = run(prop("seller.first_ask_list", 1.5), p=pol(seller__first_ask_list=0.98))
        self.assertEqual(acc[0]["new"], 1.0)
        self.assertTrue(acc[0]["clamped"])
        acc, _ = run(prop("buyer.cap_food", 0.5), p=pol(buyer__cap_food=0.86))
        self.assertEqual(acc[0]["new"], 0.85)
        self.assertEqual(acc[0]["requested"], 0.5)

    def test_max_step(self):
        acc, _ = run(prop("seller.first_ask_list", 0.99), prop("seller.floor_list", 0.40),
                     prop("seller.warranty_cap_6h", 900000))
        self.assertEqual([(a["old"], a["new"], a["clamped"]) for a in acc],
                         [(0.85, 0.9, True), (0.60, 0.55, True), (500000, 600000, True)])
        acc, _ = run(prop("seller.first_ask_list", 0.88))
        self.assertEqual((acc[0]["new"], acc[0]["clamped"]), (0.88, False))

    def test_int_keys_are_rounded_to_int(self):
        acc, _ = run(prop("stock.per_sku_qty", 2.6), prop("buyer.target_s", 23.4), prop("stock.demand_window_h", 7.9))
        self.assertEqual([a["new"] for a in acc], [3, 23, 8])
        self.assertTrue(all(type(a["new"]) is int for a in acc))

    def test_floats_rounded_to_4_decimals(self):
        acc, _ = run(prop("buyer.cap_food", 0.961234), prop("buyer.cap_goods_warranty", 1))
        self.assertEqual([a["new"] for a in acc], [0.9612, 0.98])
        self.assertTrue(all(type(a["new"]) is float for a in acc))

    def test_accepted_row_shape_and_reason_truncated(self):
        acc, rej = run(prop("buyer.open_list", 0.55, "x" * 500))
        self.assertEqual(rej, [])
        self.assertEqual(set(acc[0]), {"key", "old", "new", "requested", "clamped", "reason"})
        self.assertEqual(len(acc[0]["reason"]), 200)

    def test_policy_is_not_mutated(self):
        p = pol()
        run(prop("seller.floor_list", 0.55), p=p)
        self.assertEqual(p, policy.DEFAULTS)


class CrossValidate(unittest.TestCase):
    def test_validate_failure_rejects_all(self):
        p = pol(seller__first_ask_list=0.80, seller__floor_list=0.78)
        acc, rej = run(prop("seller.first_ask_list", 0.75), prop("seller.floor_list", 0.80),
                       prop("stock.per_sku_qty", 3), prop("stock.cash_floor", 1), p=p)
        self.assertEqual(acc, [])
        self.assertEqual(rej[0]["why"], "frozen")
        self.assertEqual([r["key"] for r in rej[1:]], ["seller.first_ask_list", "seller.floor_list", "stock.per_sku_qty"])
        for r in rej[1:]:
            self.assertTrue(r["why"].startswith("validate: "), r)
            self.assertIn("seller.floor_list: above seller.first_ask_list", r["why"])

    def test_each_change_alone_passes(self):
        p = pol(seller__first_ask_list=0.80, seller__floor_list=0.78)
        self.assertEqual(len(run(prop("seller.first_ask_list", 0.85), p=p)[0]), 1)
        self.assertEqual(len(run(prop("seller.floor_list", 0.75), p=p)[0]), 1)


if __name__ == "__main__":
    unittest.main()
