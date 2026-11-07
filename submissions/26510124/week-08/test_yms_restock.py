"""Affordable turnover reserves real purchase capacity without adding risk.

Synthetic settlements and inventory exercise the complete deterministic
planner and hook. These tests never call a model, market, or supply endpoint.
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
ACE, SIMMONS, SECONDARY = "G8095ddd6", "Gb5d0dde7", "G03d0199f"
CHEAP = tuple(f"G1000000{i}" for i in range(4))
PROVEN = "G20000001"
FOOD = ("F30000001", "F30000002")


class YmsRestockTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        self.cfg = self.policy["restock"]
        self.rules = self.cfg["adaptive_goods"]
        self.inv = {"round": 3, "start": 5000000, "balance": 5000000,
                    "injected": 0, "units": [], "surge": [], "restock": {
                        "open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {sku: {"name": sku, "kind": "goods", "category": "beds", "list_price": 400000}
                        for sku in (ACE, SIMMONS, SECONDARY)}
        self.catalog.update({sku: {"name": sku, "kind": "goods", "category": f"goods-{i % 2}",
                                  "list_price": price}
                             for i, (sku, price) in enumerate(zip(CHEAP, (100000, 120000, 180000, 250000)))})
        self.catalog[PROVEN] = {"name": PROVEN, "kind": "goods", "category": "other-goods", "list_price": 400000}
        self.catalog.update({sku: {"name": sku, "kind": "food", "category": "food",
                                  "list_price": 20000 + i * 5000, "shelf_hours": 96}
                             for i, sku in enumerate(FOOD)})
        self.demand = [{"category": category} for category in sorted({p["category"] for p in self.catalog.values()})]
        self.memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": []}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [
            {"seq": i * 10 + j + 1, "ts": self.now - 30, "kind": "settle", "sku": sku,
             "buyer": f"buyer-{j}", "shop": f"https://shop-{j}.invalid",
             "price": int(product["list_price"] * .8), "grade": "A", "warranty_hours": 6}
            for i, (sku, product) in enumerate(self.catalog.items()) for j in range(5)],
            "market_stock": {"round": 3, "observed_at": self.now,
                "by_sku": {sku: {"units": 10, "shops": 5} for sku in self.catalog},
                "shop_names_by_url": {f"https://shop-{j}.invalid": f"shop-{j}" for j in range(5)}}}
        (ROOT / ".market").mkdir(exist_ok=True)
        for target in ("harness.decide", "urllib.request.urlopen", "restock.refresh_market"):
            guard = patch(target, side_effect=AssertionError("No live model/network call allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def plan(self, **kwargs):
        return restock.plan(self.inv, self.catalog, self.demand, self.memory, [], self.cfg,
                            self.now, self.public, **kwargs)

    def only_demand(self, skus):
        self.public["events"] = [event for event in self.public["events"] if event["sku"] in skus]

    def mature(self, skus):
        for sku in skus:
            for _ in range(3):
                number = len(self.memory["sales"]) + 1
                self.memory["sales"].append({"ledger": number, "unit": f"sold-{number}", "sku": sku,
                    "category": self.catalog[sku]["category"], "kind": "goods", "grade": "A",
                    "cost": 50000, "price": 100000, "warranty_hours": 6,
                    "supplied_at": self.now - 8 * 3600, "settled_at": self.now - 7 * 3600})

    def held(self, sku, **changes):
        return unit(unit=f"held-{sku}", sku=sku, category=self.catalog[sku]["category"],
                    cost=50000, supplied_at=self.now - 10, **changes)

    def test_policy_reserves_two_affordable_distinct_goods_before_focused_beds(self):
        self.assertEqual(self.rules["turnover_goods"], {"enabled": True, "max_candidates": 4,
            "priority_units_per_run": 2, "max_list_price": 300000})
        self.assertEqual(self.rules["food_pilots"]["max_units_per_run"], 1)
        decision, detail = self.plan()
        choices = detail["choices"]
        self.assertEqual([row["sku"] for row in choices[:2]], list(CHEAP[:2]))
        self.assertTrue(all(row["reason"] == "adaptive_affordable_turnover" for row in choices[:2]))
        self.assertEqual({row["sku"] for row in choices[2:4]}, {ACE, SIMMONS})
        adaptive = [row for row in choices if row["sku"] not in (ACE, SIMMONS, SECONDARY)]
        self.assertEqual(len(adaptive), 4)  # The first two consume the same four-unit allowance.
        self.assertEqual(sum(row["qty"] for row in decision["buy"]), 6)
        self.assertLessEqual(len(choices), 8)
        self.assertEqual(detail["adaptive_goods"]["turnover_goods"]["priority_units_planned"], 2)
        self.assertGreaterEqual(detail["projected_min_balance"], 2000000)

    def test_three_proven_primaries_can_take_only_six_remaining_global_slots(self):
        # A supported three-primary policy exposes the old full-loop overflow:
        # six focus units plus two reserved affordable units must equal eight.
        focus = self.cfg["focused_goods"]
        focus.update(primary_skus=[ACE, SIMMONS, SECONDARY], secondary_skus=[])
        self.mature(focus["primary_skus"])
        decision, detail = self.plan()
        self.assertEqual([row["sku"] for row in detail["choices"][:2]], list(CHEAP[:2]))
        bought = {row["sku"]: row["qty"] for row in decision["buy"]}
        self.assertEqual({sku: bought[sku] for sku in focus["primary_skus"]},
                         {ACE: 2, SIMMONS: 2, SECONDARY: 2})
        self.assertEqual(sum(bought.values()), 8)
        self.assertEqual(sum(bought.get(sku, 0) for sku in CHEAP), 2)
        self.assertLessEqual(detail["budgeted_cost"], 3000000)

    def test_one_proven_affordable_sku_does_not_take_both_distinct_priority_slots(self):
        self.mature([CHEAP[0]])
        _, detail = self.plan()
        self.assertEqual([row["sku"] for row in detail["choices"][:2]], list(CHEAP[:2]))
        self.assertEqual(detail["depth_limits"][CHEAP[0]], 2)

    def test_low_cash_still_buys_an_affordable_unit_without_using_reserve(self):
        self.only_demand((ACE, SIMMONS, *CHEAP))
        self.inv["balance"] = 2000000 + worst_cost(self.catalog[CHEAP[0]]["list_price"])
        decision, detail = self.plan()
        self.assertEqual(decision, {"buy": [{"sku": CHEAP[0], "qty": 1}]})
        self.assertEqual(detail["projected_min_balance"], 2000000)
        self.assertEqual(detail["choices"][0]["reason"], "adaptive_affordable_turnover")
        self.inv["balance"] = 2000000
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_remaining_capacity_prefers_proven_goods_before_one_food_pilot(self):
        self.only_demand((*CHEAP, PROVEN, *FOOD))
        self.mature([PROVEN])
        self.inv["units"] = [self.held(PROVEN)]
        _, detail = self.plan()
        choices = detail["choices"]
        self.assertEqual(len(choices), 4)
        self.assertEqual([row["sku"] for row in choices[:2]], list(CHEAP[:2]))
        self.assertEqual(choices[2]["sku"], PROVEN)
        self.assertEqual(choices[2]["reason"], "adaptive_proven_replenishment")
        self.assertEqual(sum(row["sku"] in FOOD for row in choices), 1)
        self.assertEqual(choices[3]["reason"], "adaptive_food_demand_pilot")
        # If both proven positions need filling, both beat food; there is no
        # mandatory food purchase or extra slot outside the adaptive budget.
        self.inv["units"] = []
        _, detail = self.plan()
        self.assertEqual([row["sku"] for row in detail["choices"][2:]], [PROVEN, PROVEN])

    def test_food_one_unit_limit_is_shared_across_two_eligible_food_skus(self):
        self.only_demand((*CHEAP, *FOOD))
        decision, detail = self.plan()
        self.assertEqual(len(detail["adaptive_goods"]["pool"]), 6)
        self.assertEqual(sum(row["qty"] for row in decision["buy"] if row["sku"] in FOOD), 1)
        self.assertEqual(sum(row["qty"] for row in decision["buy"]), 4)

    def test_priority_and_later_adaptive_slots_share_twelve_unit_stock_target(self):
        self.only_demand((*CHEAP, PROVEN, *FOOD))
        for count, expected in ((9, 3), (10, 2), (11, 1), (12, 0)):
            self.inv["units"] = [unit(unit=f"held-{i}", sku=f"Gffff{i:04x}", category="unrelated",
                                      cost=10000, supplied_at=self.now - 10) for i in range(count)]
            with self.subTest(held=count):
                decision, detail = self.plan()
                bought = sum(row["qty"] for row in decision["buy"])
                self.assertEqual(bought, expected)
                self.assertLessEqual(count + bought, 12)
                self.assertEqual(detail["adaptive_goods"]["turnover_goods"]["priority_units_planned"],
                                 min(2, expected))

    def test_missing_stale_or_nondiverse_evidence_cannot_force_priority_or_expensive_fallback(self):
        original = deepcopy(self.public)
        for variant in ("missing", "stale", "one_buyer", "one_named_shop", "two_sales", "surge"):
            self.public = deepcopy(original)
            self.only_demand(CHEAP)  # No valid evidence for any focused/expensive alternative.
            self.inv["surge"] = []
            if variant == "missing": self.public["events"] = []
            if variant == "stale": self.public["updated_at"] -= 3601
            if variant == "one_buyer":
                for row in self.public["events"]: row["buyer"] = "one"
            if variant == "one_named_shop":
                self.public["market_stock"]["shop_names_by_url"] = {
                    f"https://shop-{i}.invalid": "one" for i in range(5)}
            if variant == "two_sales":
                self.public["events"] = [row for row in self.public["events"] if row["buyer"] in ("buyer-0", "buyer-1")]
            if variant == "surge":
                self.inv["surge"] = [{"sku": sku, "factor": 1.11, "units": 105} for sku in CHEAP]
            with self.subTest(variant=variant):
                decision, detail = self.plan()
                self.assertEqual(decision, {"buy": []})
                self.assertEqual(detail["adaptive_goods"]["turnover_goods"]["priority_units_planned"], 0)

    def test_absent_affordable_demand_leaves_valid_focus_capacity_available(self):
        self.only_demand((ACE, SIMMONS))
        decision, detail = self.plan()
        self.assertEqual({row["sku"] for row in decision["buy"]}, {ACE, SIMMONS})
        self.assertEqual(detail["adaptive_goods"]["turnover_goods"]["priority_units_planned"], 0)

    def test_held_depth_and_unknown_supply_cannot_be_bypassed_by_priority(self):
        self.only_demand((CHEAP[0],))
        self.inv["units"] = [self.held(CHEAP[0])]
        self.assertEqual(self.plan()[0], {"buy": []})
        self.mature([CHEAP[0]])
        self.assertEqual(self.plan()[0], {"buy": [{"sku": CHEAP[0], "qty": 1}]})
        self.inv["units"].append({**self.held(CHEAP[0]), "unit": "held-second"})
        self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["units"] = []
        control = {CHEAP[0]: {"planned_qty": 2, "settled_qty": 0, "retry_credit": 0,
                             "last_ledger": 0, "observed_at_plan": 0}}
        self.assertEqual(self.plan(adaptive_control=control)[0], {"buy": []})
        lifecycle = {"units": {"missing": {"sku": CHEAP[0], "category": "goods-0", "kind": "goods",
            "cost": 50000, "state": "missing", "missing_reason": "unconfirmed"}}, "archived": {}}
        self.assertEqual(self.plan(lifecycle=lifecycle)[0], {"buy": []})

    def test_affordable_priority_preserves_sku_category_and_run_cost_bounds(self):
        self.only_demand((CHEAP[0],))
        self.mature([CHEAP[0]])
        cost = worst_cost(self.catalog[CHEAP[0]]["list_price"])
        for extra, count in ((0, 1), (1, 0)):
            self.inv["units"] = [{**self.held(CHEAP[0]), "cost": 2000000 - cost + extra}]
            with self.subTest(cap="sku", extra=extra):
                decision, _ = self.plan()
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), count)
            self.inv["units"] = [unit(unit="category-held", sku="Gffffffff", category="goods-0",
                                      cost=3000000 - cost + extra, supplied_at=self.now - 10)]
            with self.subTest(cap="category", extra=extra):
                decision, _ = self.plan()
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), count)
        self.inv["units"] = []
        # Tighten a supported run limit enough that even an affordable SKU
        # cannot be bought. Priority must never bypass StockBudget.
        self.cfg["max_run_cost_percent"] = 1
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_repeated_open_window_does_not_reset_priority_or_purchase_credits(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root, md = Path(directory), Path(directory) / ".market"
            for path, value in ((root / "shop-policy.json", self.policy), (md / "inventory.json", self.inv),
                                (md / "catalog.json", self.catalog), (md / "demand.json", self.demand)):
                atomic_json(path, value)
            with patch("restock.refresh_market", return_value=self.public) as refresh:
                first = restock.run_once(root, self.now)
                controls = load_json(md / "restock-control.json")
                again = restock.run_once(root, self.now + 30)
                self.assertEqual(again, {"buy": []})
                after = load_json(md / "restock-control.json")
                for field in ("last_window", "adaptive_supply", "focused_supply"):
                    self.assertEqual(after[field], controls[field])
                self.assertEqual(refresh.call_count, 1)
            self.assertEqual([row["sku"] for row in first["buy"][:2]], list(CHEAP[:2]))
            self.assertEqual(sum(row["planned_qty"] for row in controls["adaptive_supply"].values()), 4)
            self.assertEqual(sum(row["planned_qty"] for row in controls["focused_supply"].values()), 2)
            rows = [json.loads(line) for line in (root / "logs/restock-decisions.jsonl").read_text().splitlines()]
            self.assertEqual(sum(row["status"] == "planned" for row in rows), 1)

    def test_turnover_policy_rejects_unsupported_values(self):
        original = deepcopy(self.policy)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root = Path(directory)
            for key, value in (("enabled", 1), ("max_candidates", 0), ("max_candidates", 5),
                               ("priority_units_per_run", 0), ("priority_units_per_run", 3),
                               ("priority_units_per_run", True), ("max_list_price", 0),
                               ("max_list_price", 300001), ("max_list_price", 100000.0)):
                candidate = deepcopy(original)
                candidate["restock"]["adaptive_goods"]["turnover_goods"][key] = value
                atomic_json(root / "shop-policy.json", candidate)
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    policy_at(root)


if __name__ == "__main__":
    unittest.main()
