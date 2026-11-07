"""Evidence-based coverage fills available slots while retaining risk limits.

All public sales, local sales, stock and clocks are synthetic. No live market,
model or supply endpoint is called by these planner and hook regressions.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from feedback import atomic_json, load_json
import restock
from shop_policy import policy_at
from supply_pricing import worst_cost
from test_inventory import unit

ROOT = Path(__file__).resolve().parent
CHEAP = tuple(f"G1100000{i}" for i in range(4))
HIGH = tuple(f"G2200000{i}" for i in range(6))
FOOD = ("F33000001", "F33000002")
ACE, SIMMONS = "G8095ddd6", "Gb5d0dde7"


class RestockCoverageTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        self.cfg = self.policy["restock"]
        self.rules = self.cfg["adaptive_goods"]
        self.inv = {"round": 3, "start": 5000000, "balance": 11695754, "injected": 0,
                    "units": [], "surge": [], "restock": {
                        "open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {
            **{sku: {"name": sku, "kind": "goods", "category": f"cheap-{i % 2}",
                     "list_price": 100000 + i * 40000} for i, sku in enumerate(CHEAP)},
            **{sku: {"name": sku, "kind": "goods", "category": f"high-{i % 2}",
                     "list_price": 800000 + i * 1000} for i, sku in enumerate(HIGH)},
            **{sku: {"name": sku, "kind": "food", "category": "food",
                     "list_price": 20000 + i * 5000, "shelf_hours": 96} for i, sku in enumerate(FOOD)}}
        self.demand = [{"category": category} for category in sorted({p["category"] for p in self.catalog.values()})]
        self.memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": []}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [],
            "market_stock": {"round": 3, "observed_at": self.now, "by_sku": {},
                "shop_names_by_url": {f"https://shop-{i}.invalid": f"registered-{i}" for i in range(5)}}}
        for sku in self.catalog:
            self.public_sales(sku)
        (ROOT / ".market").mkdir(exist_ok=True)
        for target in ("harness.decide", "urllib.request.urlopen", "restock.refresh_market"):
            guard = patch(target, side_effect=AssertionError("No live model/network call allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def public_sales(self, sku):
        product = self.catalog[sku]
        self.public["market_stock"]["by_sku"][sku] = {"units": 10, "shops": 5}
        for j in range(5):
            self.public["events"].append({"seq": len(self.public["events"]) + 1,
                "ts": self.now - 30, "kind": "settle", "sku": sku,
                "buyer": f"buyer-{j}", "shop": f"https://shop-{j}.invalid",
                "price": int(product["list_price"] * .8), "grade": "A",
                "warranty_hours": 0 if product["kind"] == "food" else 6})

    def own_sales(self, sku, *, mature=3, recent=0):
        for age in [7 * 3600] * mature + [300] * recent:
            number = len(self.memory["sales"]) + 1
            sold = self.now - age
            self.memory["sales"].append({"ledger": number, "unit": f"sold-{number}", "sku": sku,
                "category": self.catalog[sku]["category"], "kind": "goods", "grade": "A",
                "cost": 50000, "price": 100000, "warranty_hours": 6,
                "receipt_time": sold - 5, "settled_at": sold, "observed_at": sold + 1,
                "supplied_at": sold - 3600})

    def plan(self, cfg=None, **kwargs):
        return restock.plan(self.inv, self.catalog, self.demand, self.memory, [],
                            self.cfg if cfg is None else cfg, self.now, self.public, **kwargs)

    def only_demand(self, skus):
        self.public["events"] = [row for row in self.public["events"] if row["sku"] in skus]

    def quantity(self, decision, skus):
        return sum(row["qty"] for row in decision["buy"] if row["sku"] in skus)

    def write_hook(self, root):
        md = root / ".market"
        for path, value in ((root / "shop-policy.json", self.policy), (md / "inventory.json", self.inv),
                            (md / "catalog.json", self.catalog), (md / "demand.json", self.demand),
                            (md / "sales-performance.json", self.memory)):
            atomic_json(path, value)
        return md

    def test_twelve_eligible_candidates_fill_eight_not_four_without_fixed_focus(self):
        previous = deepcopy(self.cfg)
        previous["max_units_per_sku"] = 2
        previous["adaptive_goods"].update(max_units_per_run=4, low_stock_units=12)
        previous["adaptive_goods"]["replenishment"] = {"enabled": False}
        previous["adaptive_goods"]["food_pilots"]["max_units_per_run"] = 1
        old, _ = self.plan(previous)
        current, detail = self.plan()
        self.assertEqual(sum(row["qty"] for row in old["buy"]), 4)
        self.assertEqual(sum(row["qty"] for row in current["buy"]), 8)
        self.assertEqual(len(detail["adaptive_goods"]["pool"]), 12)
        self.assertGreaterEqual(len(current["buy"]), 4)
        self.assertEqual([row["sku"] for row in detail["choices"][:2]], list(CHEAP[:2]))
        self.assertIn(detail["choices"][2]["sku"], FOOD)
        self.assertEqual(self.quantity(current, FOOD), 2)
        self.assertGreater(self.quantity(current, HIGH), 0)
        self.assertTrue(all(row["qty"] == 1 for row in current["buy"]))
        self.assertGreaterEqual(detail["projected_min_balance"], 2000000)
        self.assertLessEqual(detail["budgeted_cost"], detail["exposure"]["limits"]["run"])

    def test_recent_confirmed_sales_raise_only_profitable_proven_goods_depth(self):
        for mature, recent, expected in ((0, 6, 1), (3, 0, 2), (3, 4, 3), (3, 6, 4)):
            self.memory["sales"] = []
            self.own_sales(CHEAP[0], mature=mature, recent=recent)
            with self.subTest(mature=mature, recent=recent):
                decision, detail = self.plan()
                self.assertEqual(detail["depth_limits"][CHEAP[0]], expected)
                self.assertEqual(self.quantity(decision, (CHEAP[0],)), expected)
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), 8)
                self.assertGreaterEqual(len(decision["buy"]), 4)
                self.assertTrue(all(row["qty"] == 1 for row in decision["buy"] if row["sku"] in FOOD))
                self.assertLessEqual(self.quantity(decision, FOOD), 2)

    def test_at_least_four_distinct_skus_precede_depth_and_food_precedes_focus(self):
        self.own_sales(CHEAP[0], recent=6)
        for sku in (ACE, SIMMONS):
            self.catalog[sku] = {"name": sku, "kind": "goods", "category": "beds", "list_price": 400000}
            self.public_sales(sku)
        self.demand.append({"category": "beds"})
        _, detail = self.plan()
        chosen = [row["sku"] for row in detail["choices"]]
        self.assertEqual(len(chosen), 8)
        self.assertIn(chosen[2], FOOD)
        self.assertTrue(all(chosen.index(sku) > 2 for sku in (ACE, SIMMONS)))
        seen = set()
        for sku in chosen:
            if sku in seen:
                self.assertGreaterEqual(len(seen), 4)
                break
            seen.add(sku)

    def test_depth_respects_held_and_outstanding_credit_instead_of_resetting_capacity(self):
        self.own_sales(CHEAP[0], recent=6)
        self.only_demand((CHEAP[0],))
        base = {"planned_qty": 0, "settled_qty": 0, "retry_credit": 0,
                "last_ledger": len(self.memory["sales"]), "observed_at_plan": 0}
        for outstanding, expected in ((0, 4), (1, 3), (3, 1), (4, 0)):
            with self.subTest(outstanding=outstanding):
                decision, _ = self.plan(adaptive_control={CHEAP[0]: {**base, "planned_qty": outstanding}})
                self.assertEqual(self.quantity(decision, (CHEAP[0],)), expected)
        for held, expected in ((1, 3), (3, 1), (4, 0)):
            self.inv["units"] = [unit(unit=f"held-{i}", sku=CHEAP[0], category="cheap-0",
                                      cost=50000, supplied_at=self.now - 10) for i in range(held)]
            with self.subTest(held=held):
                decision, _ = self.plan(adaptive_control={CHEAP[0]: base})
                self.assertEqual(self.quantity(decision, (CHEAP[0],)), expected)

    def test_more_capacity_preserves_total_twenty_stock_and_cash_and_food_exposure(self):
        for held, expected in ((17, 3), (19, 1), (20, 0)):
            self.inv["units"] = [unit(unit=f"other-{i}", sku=f"Gffff{i:04x}", category="unrelated",
                                      cost=1000, supplied_at=self.now - 10) for i in range(held)]
            with self.subTest(held=held):
                decision, _ = self.plan()
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), expected)
                self.assertLessEqual(held + expected, 20)
        self.inv["units"] = []
        self.inv["balance"] = 2000000
        self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["balance"] += worst_cost(20000)
        decision, detail = self.plan()
        self.assertEqual(self.quantity(decision, FOOD), 1)
        self.assertEqual(sum(row["qty"] for row in decision["buy"]), 1)
        self.assertEqual(detail["projected_min_balance"], 2000000)
        self.inv["balance"] = 11695754
        self.inv["units"] = [unit(unit="food-held", sku="Fffffffff", category="unrelated-food", kind="food",
            cost=500000 - worst_cost(20000), supplied_at=self.now - 10)]
        decision, detail = self.plan()
        self.assertEqual(self.quantity(decision, FOOD), 1)
        self.assertEqual(detail["exposure"]["food_cost_after_plan"], 500000)
        self.assertEqual(detail["exposure"]["limits"]["food"], 500000)

    def test_missing_evidence_or_own_refund_never_forces_reserved_slots(self):
        self.public["events"] = []
        self.assertEqual(self.plan()[0], {"buy": []})
        self.public_sales(CHEAP[0])
        self.own_sales(CHEAP[0], recent=6)
        self.memory["sales"][0]["refunds"] = [{"seq": 900, "amount": 100000, "ts": self.now - 10}]
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_hook_persists_four_unit_credit_and_restart_cannot_repeat_opening(self):
        self.own_sales(CHEAP[0], recent=6)
        self.only_demand((CHEAP[0],))
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root = Path(directory)
            md = self.write_hook(root)
            with patch("restock.refresh_market", return_value=self.public) as refresh:
                first = restock.run_once(root, self.now)
                self.assertEqual(first, {"buy": [{"sku": CHEAP[0], "qty": 4}]})
                saved = load_json(md / "restock-control.json")
                self.assertEqual(saved["adaptive_supply"][CHEAP[0]]["planned_qty"], 4)
                self.assertEqual(restock.run_once(root, self.now + 30), {"buy": []})
                self.assertEqual(load_json(md / "restock-control.json")["adaptive_supply"], saved["adaptive_supply"])
                refresh.assert_called_once()

    def test_supply_deadline_still_limits_units_before_any_eight_unit_plan(self):
        for remaining, expected in ((5, 0), (35, 2), (95, 8)):
            with self.subTest(remaining=remaining), tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
                root = Path(directory)
                self.inv["restock"]["closes_at"] = self.now + remaining
                self.write_hook(root)
                with patch("restock.refresh_market", return_value=self.public) as refresh:
                    decision = restock.run_once(root, self.now)
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), expected)
                self.assertEqual(refresh.call_count, int(expected > 0))
                rows = [json.loads(line) for line in (root / "logs/restock-decisions.jsonl").read_text().splitlines()]
                if expected:
                    self.assertLessEqual(sum(row["qty"] for row in decision["buy"]), rows[0]["window_unit_cap"])

    def test_current_policy_and_unsupported_broader_limits(self):
        self.assertEqual((self.cfg["max_units_per_run"], self.cfg["max_stock_units"], self.cfg["max_units_per_sku"]), (8, 20, 4))
        self.assertEqual((self.rules["max_units_per_run"], self.rules["low_stock_units"], self.rules["proven_max_units_per_sku"]), (8, 20, 2))
        self.assertEqual(self.rules["replenishment"], {"enabled": True, "lookback_seconds": 3600,
            "cover_seconds": 2400, "max_units_per_sku": 4, "distinct_skus_before_depth": 4, "food_priority_units": 1})
        self.assertEqual(self.rules["food_pilots"]["max_units_per_run"], 2)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root = Path(directory)
            for section, key, value in (("adaptive", "max_units_per_run", 9), ("adaptive", "low_stock_units", 21),
                    ("replenishment", "max_units_per_sku", 5), ("replenishment", "max_units_per_sku", True),
                    ("replenishment", "cover_seconds", 3601), ("replenishment", "food_priority_units", 2)):
                candidate = deepcopy(self.policy)
                target = candidate["restock"]["adaptive_goods"]
                if section == "replenishment": target = target["replenishment"]
                target[key] = value
                atomic_json(root / "shop-policy.json", candidate)
                with self.subTest(section=section, key=key, value=value), self.assertRaises(ValueError):
                    policy_at(root)


if __name__ == "__main__":
    unittest.main()
