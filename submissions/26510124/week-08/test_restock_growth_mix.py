"""Planner and hook integration for reinvestment, proven depth, and food pilots."""
from copy import deepcopy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from feedback import atomic_json, load_json
import restock
from shop_policy import policy_at
from supply_pricing import worst_cost

ROOT = Path(__file__).resolve().parent
ACE, SIMMONS = "G8095ddd6", "Gb5d0dde7"
GOODS = ("G00000001", "G00000002", "G00000003")
FOODS = ("F00000001", "F00000002", "F00000003", "F00000004")


class RestockGrowthMixTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        # These fixtures exercise normal restocking, independently of the live round deadline.
        self.policy["horizon"]["ends_at"] = datetime.fromtimestamp(
            self.now + 30 * 86400, timezone.utc).isoformat()
        self.cfg = self.policy["restock"]
        # Preserve the earlier Lee-inspired mix; current coverage is tested separately.
        self.cfg["max_units_per_sku"] = 2
        self.cfg["adaptive_goods"].update(max_units_per_run=4, low_stock_units=12)
        self.cfg["adaptive_goods"]["replenishment"] = {"enabled": False}
        self.cfg["adaptive_goods"]["turnover_goods"] = {"enabled": False}
        self.cfg["adaptive_goods"]["food_pilots"].update(max_candidates=4, max_units_per_run=2)
        self.inv = {"round": 3, "balance": 11695754, "start": 5000000, "injected": 0,
                    "units": [], "surge": [], "restock": {
                        "open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {
            ACE: {"name": "ACE fixture", "list_price": 851400, "category": "beds", "kind": "goods"},
            SIMMONS: {"name": "Simmons fixture", "list_price": 1094400, "category": "beds", "kind": "goods"},
            **{sku: {"name": sku, "list_price": 400000, "category": "other-goods", "kind": "goods"} for sku in GOODS},
            **{sku: {"name": sku, "list_price": 20000, "category": "food", "kind": "food", "shelf_hours": 72} for sku in FOODS}}
        self.demand = [{"category": category} for category in ("beds", "other-goods", "food")]
        self.memory = {"schema": 1, "round": 3, "sales": [], "updated_at": self.now}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [],
            "market_stock": {"round": 3, "observed_at": self.now,
                "by_sku": {sku: {"units": 10, "shops": 3} for sku in self.catalog},
                "shop_names_by_url": {f"https://shop-{i}.invalid": f"shop-{i}" for i in range(3)}}}
        for sku, product in self.catalog.items():
            for i in range(5 if product["kind"] == "food" else 3):
                self.public["events"].append({"seq": len(self.public["events"]) + 1, "ts": self.now - 30,
                    "kind": "settle", "sku": sku, "buyer": f"buyer-{i % 3}", "shop": f"https://shop-{i % 3}.invalid",
                    "price": int(product["list_price"] * .75), "grade": "A",
                    "warranty_hours": 0 if product["kind"] == "food" else 6})
        for target in ("harness.decide", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("No model/network allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def plan(self, **kwargs):
        return restock.plan(self.inv, self.catalog, self.demand, self.memory, [], self.cfg,
                            self.now, self.public, **kwargs)

    def sale(self, index, **changes):
        return {"ledger": index + 1, "unit": f"sold-{index}", "sku": GOODS[0],
                "category": "other-goods", "kind": "goods", "grade": "A", "cost": 200000,
                "price": 300000, "warranty_hours": 6, "receipt_time": self.now - 7 * 3600,
                "supplied_at": self.now - 8 * 3600, "settled_at": self.now - 7 * 3600, **changes}

    def quantity(self, decision, kind):
        return sum(row["qty"] for row in decision["buy"] if self.catalog[row["sku"]]["kind"] == kind)

    def test_earlier_mix_plans_two_food_pilots_inside_four_adaptive_units(self):
        self.assertTrue(self.cfg["reinvestment"]["enabled"])
        self.assertEqual(self.cfg["adaptive_goods"]["proven_max_units_per_sku"], 2)
        self.assertEqual(self.cfg["adaptive_goods"]["food_pilots"]["max_units_per_run"], 2)
        decision, detail = self.plan()
        self.assertEqual(decision["buy"][:2], [{"sku": ACE, "qty": 1}, {"sku": SIMMONS, "qty": 1}])
        self.assertEqual(self.quantity(decision, "food"), 2)
        self.assertEqual(sum(row["qty"] for row in decision["buy"]), 6)
        self.assertEqual(len([row for row in detail["choices"] if row["reason"].startswith("adaptive_")]), 4)
        self.assertTrue(all(row["qty"] == 1 for row in decision["buy"]))
        self.assertEqual(detail["budgeted_cost"], 2189027 + 2 * worst_cost(20000) + 2 * worst_cost(400000))
        self.assertGreater(detail["budgeted_cost"], 3000000)
        self.assertEqual(detail["exposure"]["limits"]["run"], 5008726)
        self.assertEqual(detail["exposure"]["limits"]["food"], 500000)
        self.assertGreaterEqual(detail["projected_min_balance"], 2000000)

    def test_proven_replenishment_obeys_held_units_and_outstanding_credit(self):
        self.memory["sales"] = [self.sale(i) for i in range(3)]
        row = {"planned_qty": 0, "settled_qty": 0, "retry_credit": 0,
               "last_ledger": 3, "observed_at_plan": 0}
        for outstanding, expected in ((0, 2), (1, 1), (2, 0)):
            control = {GOODS[0]: {**row, "planned_qty": outstanding}}
            with self.subTest(outstanding=outstanding):
                decision, detail = self.plan(adaptive_control=control)
                bought = next((x["qty"] for x in decision["buy"] if x["sku"] == GOODS[0]), 0)
                self.assertEqual(bought, expected)
                self.assertEqual(detail["adaptive_goods"]["pool"][GOODS[0]]["remaining_credit"], expected)
        self.inv["units"] = [{"unit": "held", "sku": GOODS[0], "category": "other-goods",
                              "kind": "goods", "cost": 200000, "supplied_at": self.now - 10}]
        decision, _ = self.plan(adaptive_control={GOODS[0]: row})
        self.assertEqual(next(x["qty"] for x in decision["buy"] if x["sku"] == GOODS[0]), 1)
        self.inv["units"].append({**self.inv["units"][0], "unit": "held-2"})
        self.assertFalse(any(x["sku"] == GOODS[0] for x in self.plan(adaptive_control={GOODS[0]: row})[0]["buy"]))

    def test_unproven_stays_one_pending_preserves_proof_and_refunds_or_unknowns_block(self):
        decision, _ = self.plan()
        self.assertEqual(next(x["qty"] for x in decision["buy"] if x["sku"] == GOODS[0]), 1)
        self.memory["sales"] = [self.sale(i) for i in range(3)] + [self.sale(3, settled_at=self.now - 30)]
        decision, detail = self.plan()
        self.assertEqual(next(x["qty"] for x in decision["buy"] if x["sku"] == GOODS[0]), 2)
        self.assertTrue(any(row["reason"] == "adaptive_proven_replenishment" for row in detail["choices"]))
        for bad in (self.sale(4, cost=None), self.sale(4, refunds=[{"seq": 20, "amount": 300000, "ts": self.now - 10}])):
            self.memory["sales"] = [self.sale(i) for i in range(3)] + [bad]
            with self.subTest(bad=bad):
                self.assertFalse(any(x["sku"] == GOODS[0] for x in self.plan()[0]["buy"]))

    def test_cash_and_fixed_food_exposure_can_reduce_the_bigger_plan(self):
        self.inv["balance"] = 2000000
        self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["balance"] += worst_cost(20000)
        decision, detail = self.plan()
        self.assertEqual(self.quantity(decision, "food"), 1)
        self.assertEqual(self.quantity(decision, "goods"), 0)
        self.assertEqual(detail["projected_min_balance"], 2000000)
        self.inv["balance"] = 11695754
        self.inv["units"] = [{"unit": "held-food", "sku": "Fffffffff", "category": "other-food",
            "kind": "food", "cost": 500000 - worst_cost(20000), "supplied_at": self.now - 10}]
        decision, detail = self.plan()
        self.assertEqual(self.quantity(decision, "food"), 1)
        self.assertEqual(detail["exposure"]["food_cost_after_plan"], 500000)
        self.assertGreater(self.quantity(decision, "goods"), 0)

    def test_hook_persists_mixed_credits_and_restart_cannot_repeat_opening(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root, md = Path(directory), Path(directory) / ".market"
            atomic_json(root / "shop-policy.json", self.policy)
            atomic_json(md / "inventory.json", self.inv)
            atomic_json(md / "catalog.json", self.catalog)
            atomic_json(md / "demand.json", self.demand)
            with patch("restock.refresh_market", return_value=self.public) as refresh:
                decision = restock.run_once(root, self.now)
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), 6)
                self.assertEqual(self.quantity(decision, "food"), 2)
                saved = load_json(md / "restock-control.json")
                self.assertEqual(sum(row["planned_qty"] for row in saved["adaptive_supply"].values()), 4)
                self.assertEqual(restock.run_once(root, self.now + 30), {"buy": []})
                refresh.assert_called_once()
                self.assertEqual(load_json(md / "restock-control.json")["adaptive_supply"], saved["adaptive_supply"])
            report = json.loads((root / "logs/restock-decisions.jsonl").read_text().splitlines()[0])
            self.assertEqual(report["exposure"]["capital_basis"]["effective"], 8347877)
            self.assertEqual(report["cash_floor"], 2000000)

    def test_new_policy_bounds_reject_broader_risk_without_approval(self):
        cases = [(("restock", "reinvestment", "cash_profit_share_percent"), 51),
                 (("restock", "reinvestment", "max_capital_percent"), 201),
                 (("restock", "adaptive_goods", "proven_max_units_per_sku"), 3),
                 (("restock", "adaptive_goods", "proven_max_units_per_sku"), True)]
        food = ("restock", "adaptive_goods", "food_pilots")
        cases += [(food + (field,), value) for field, value in (
            ("max_candidates", 5), ("max_units_per_run", 3), ("max_list_price", 100001),
            ("min_shelf_hours", 71), ("minimum_public_sales", 4), ("minimum_public_buyers", 2),
            ("enabled", 1))]
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root = Path(directory)
            for path, value in cases:
                policy = deepcopy(self.policy)
                target = policy
                for key in path[:-1]: target = target[key]
                target[path[-1]] = value
                atomic_json(root / "shop-policy.json", policy)
                with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                    policy_at(root)


if __name__ == "__main__":
    unittest.main()
