import json
from pathlib import Path
import tempfile
import unittest
from market_inventory import InventoryUnit
from strategy import Policy, eligible, pricing, stock_order, comps


class StrategyTests(unittest.TestCase):
    def test_turnover_floor_is_above_cost_for_all_grades_and_ages(self):
        for grade in ('A', 'B', 'C'):
            for held in (0, 18, 24, 72):
                unit = self.unit(grade=grade, cost=11580)
                terms = pricing(unit, Policy(turnover_markup=.01), 1000 + held * 3600)
                expected = 23160 if grade == 'A' else 12275
                self.assertEqual((terms['reserve'], terms['target_price']), (expected, expected))
                self.assertEqual(terms['pricing_mode'], 'turnover')
                self.assertEqual(terms['offered_warranty_hours'], 0)
        for invalid in (0, -0.01, True, float('nan')):
            with self.assertRaises(ValueError):
                Policy(turnover_markup=invalid)

    def unit(self, **kw):
        return InventoryUnit(**dict(dict(unit="u", sku="G11111111", name="item", category="goods",
            kind="goods", cost=100, list_price=200, grade="B", expires_at=None, supplied_at=1000), **kw))

    def test_fresh_a_has_double_cost_floor(self):
        terms = pricing(self.unit(grade="A"), Policy(), 1000)
        self.assertEqual((terms["reserve"], terms["target_price"], terms["pricing_mode"]),
                         (200, 200, "normal"))

    def test_old_or_bc_starts_at_grade_specific_floor_without_storage(self):
        for grade, now in [("B", 1000), ("C", 1000), ("A", 87400), ("B", 87400), ("C", 87400)]:
            with self.subTest(grade=grade, now=now):
                terms = pricing(self.unit(grade=grade), Policy(), now)
                expected = 200 if grade == "A" else 106
                self.assertEqual((terms["reserve"], terms["target_price"], terms["pricing_mode"]),
                                 (expected, expected, "clearance"))
        self.assertEqual(pricing(self.unit(grade="A"), Policy(), 87399)["pricing_mode"], "aged_floor")

    def test_aging_a_uses_existing_floor_after_eighteen_hours(self):
        unit = self.unit(grade="A", cost=10000)
        before = pricing(unit, Policy(), 1000 + 18 * 3600 - 1)
        aged = pricing(unit, Policy(), 1000 + 18 * 3600)
        self.assertEqual(before["pricing_mode"], "normal")
        self.assertEqual(aged["pricing_mode"], "aged_floor")
        self.assertEqual(aged["reserve"], 20000)
        self.assertEqual(aged["target_price"], aged["reserve"])
        self.assertEqual(before["target_price"], aged["target_price"])

    def test_clearance_rounds_up_and_overrides_normal_minimum(self):
        terms = pricing(self.unit(cost=85), Policy(minimum_markup=.10), 87400)
        self.assertEqual((terms['reserve'], terms['target_price']), (91, 91))
        self.assertGreater(terms['storage_cost'], 0)

    def test_c_food_does_not_use_old_below_cost_clearance(self):
        terms = pricing(self.unit(grade="C", kind="food", expires_at=8200), Policy(), 1000)
        self.assertEqual((terms["reserve"], terms["target_price"]), (106, 106))
        self.assertEqual(terms["storage_cost"], 0)

    def test_b_first_only_after_eligibility(self):
        units = [self.unit(unit="a", grade="A"), self.unit(unit="b", grade="B"),
                 self.unit(unit="bad", grade="B",kind="food",expires_at=1100)]
        chosen = sorted([u for u in units if eligible(u, Policy(), 1000)], key=stock_order)
        self.assertEqual([u.unit for u in chosen], ["b","a"])
        self.assertFalse(eligible(self.unit(), Policy(),1000, grades=["A"]))

    def test_positive_warranties_cannot_be_enabled_without_liability_tracking(self):
        for hours in [1,72,73]:
            with self.assertRaises(ValueError): Policy(offered_warranty_hours=hours)

    def test_unknown_or_stale_comps_are_not_price_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "forum-candidates.json"
            row = {"sku":"G11111111", "grade":"B", "warranty_hours":0,
                   "warranty_status":"known", "sample_settlements":3, "sample_median_settled_price":125}
            report = {"market":"https://market.ourolabs.ai", "generated_at":1000,"condition_candidates":[row]}
            p.write_text(json.dumps(report))
            self.assertEqual(comps(Path(tmp),self.unit(),1001)["median"],125)
            self.assertIsNone(comps(Path(tmp),self.unit(),1400))
            row["warranty_status"]="unknown"
            p.write_text(json.dumps(report))
            self.assertIsNone(comps(Path(tmp),self.unit(),1001))
