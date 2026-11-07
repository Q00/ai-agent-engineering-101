"""Larger restock budgets preserve cash, evidence and supply-window limits.

The old/new comparison embeds observed catalog prices. Other cases use
explicit synthetic alternatives so these tests need no live market data.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from feedback import atomic_json
import restock
from shop_policy import policy_at
from stock_budget import StockBudget
from supply_pricing import worst_cost
from test_inventory import unit

ROOT = Path(__file__).resolve().parent
ACE, SIMMONS = "G8095ddd6", "Gb5d0dde7"
ALTS = tuple(f"G000001{i:02x}" for i in range(8))


class AggressiveRestockTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        self.cfg = self.policy["restock"]
        # Isolate the Nov 6 cost-limit experiment from the later YMS slot ordering.
        self.cfg["adaptive_goods"]["turnover_goods"] = {"enabled": False}
        self.inv = {"round": 3, "balance": 10248601, "start": 5000000,
                    "units": [], "surge": [], "restock": {
                        "open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {
            ACE: {"name": "ACE fixture", "list_price": 851400, "category": "beds", "kind": "goods"},
            SIMMONS: {"name": "Simmons fixture", "list_price": 1094400, "category": "beds", "kind": "goods"},
            **{sku: {"name": f"Synthetic alternative {sku}", "list_price": 300000,
                     "category": "alternatives", "kind": "goods"} for sku in ALTS}}
        self.demand = [{"category": "beds"}, {"category": "alternatives"}]
        self.memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": []}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [
            {"seq": i * 10 + j + 1, "ts": self.now - 30, "kind": "settle", "sku": sku,
             "buyer": f"buyer-{j}", "shop": f"https://shop-{j}.invalid", "price": 200000,
             "grade": "A", "warranty_hours": 6}
            for i, sku in enumerate(self.catalog) for j in range(3)], "market_stock": {
                "round": 3, "observed_at": self.now,
                "by_sku": {sku: {"units": 10, "shops": 3} for sku in self.catalog},
                "shop_names_by_url": {f"https://shop-{j}.invalid": f"shop-{j}" for j in range(3)}}}
        # Both the plan and the hook must remain deterministic. These failures
        # catch an accidental model invocation or real HTTP request in any case.
        for target in ("harness.decide", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("No model/network allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def plan(self, cfg=None, **kwargs):
        return restock.plan(self.inv, self.catalog, self.demand, self.memory, [],
                            self.cfg if cfg is None else cfg, self.now, self.public, **kwargs)

    def no_primary_demand(self):
        self.public["events"] = [e for e in self.public["events"] if e["sku"] in ALTS]

    def held_other(self, count):
        return [unit(unit=f"held-{i}", sku=f"Gffff{i:04x}", category="other", cost=10000,
                     supplied_at=self.now - 10) for i in range(count)]

    def sale(self, sku, **changes):
        return {"ledger": 1, "unit": "sold", "sku": sku, "category": "alternatives",
                "kind": "goods", "grade": "A", "cost": 100000, "price": 200000,
                "warranty_hours": 6, "supplied_at": self.now - 8 * 3600,
                "settled_at": self.now - 7 * 3600, **changes}

    def test_current_policy_grows_purchases_without_weakening_risk_terms(self):
        self.assertEqual({k: self.cfg[k] for k in (
            "cash_reserve_percent", "max_units_per_run", "max_run_cost_percent", "max_sku_cost_percent",
            "max_category_cost_percent", "max_food_cost_percent", "max_stock_units")}, {
            "cash_reserve_percent": 40, "max_units_per_run": 8, "max_run_cost_percent": 60,
            "max_sku_cost_percent": 40, "max_category_cost_percent": 60,
            "max_food_cost_percent": 10, "max_stock_units": 20})
        rules = self.cfg["adaptive_goods"]
        self.assertEqual((rules["max_units_per_run"], rules["low_stock_units"], rules["max_candidates"]), (4, 12, 12))
        self.assertEqual((rules["minimum_public_sales"], rules["minimum_public_buyers"],
                          rules["minimum_public_shops"], rules["max_surge_factor"]), (3, 2, 2, 1.10))
        self.assertEqual(self.policy["grade_margin_percent"], {"A": 10, "B": 5, "C": 0})
        self.assertEqual(self.policy["goods_warranty_hours"]["A"], 6)

    def test_real_primary_prices_leave_room_for_two_alternatives_under_new_run_cap(self):
        # Public demand is controlled test data; catalog prices/categories
        # come from the cached market and the user's observed candidate list.
        observed = (("G1d19ebee", 229000, "curtains"),
                    ("G429bd69e", 928000, "sofas"),
                    ("G021aae64", 545930, "beds"),
                    ("G1515d951", 423810, "beds"),
                    ("G2c023a8c", 500500, "beds"),
                    ("G754bf0ca", 694530, "beds"))
        aliases = {ALTS[i]: sku for i, (sku, _, _) in enumerate(observed)}
        self.catalog = {**{sku: self.catalog[sku] for sku in (ACE, SIMMONS)},
            **{sku: {"name": sku, "list_price": price, "category": category, "kind": "goods"}
               for sku, price, category in observed}}
        self.demand = [{"category": category} for category in ("beds", "curtains", "sofas")]
        self.public["events"] = [{**event, "sku": aliases.get(event["sku"], event["sku"])}
            for event in self.public["events"] if event["sku"] in aliases or event["sku"] in (ACE, SIMMONS)]
        self.public["market_stock"]["by_sku"] = {sku: {"units": 10, "shops": 3} for sku in self.catalog}
        previous = deepcopy(self.cfg)
        previous.update(max_units_per_run=4, max_run_cost_percent=44, max_sku_cost_percent=26)
        previous["adaptive_goods"].update(max_units_per_run=2, low_stock_units=8, max_candidates=6)
        for balance in (10248601, 5000000):
            self.inv["balance"] = balance
            with self.subTest(balance=balance):
                old, old_detail = self.plan(previous)
                new, detail = self.plan()
                self.assertEqual(old["buy"], [{"sku": ACE, "qty": 1}, {"sku": SIMMONS, "qty": 1}])
                self.assertEqual(old_detail["budgeted_cost"], 2189027)
                self.assertEqual(new["buy"][:2], old["buy"])
                self.assertEqual(sum(row["qty"] for row in new["buy"]), 4)
                self.assertEqual(new["buy"][2:], [{"sku": "G1d19ebee", "qty": 1},
                                                  {"sku": "G1515d951", "qty": 1}])
                self.assertEqual(detail["budgeted_cost"], 2923441)
                self.assertEqual(detail["cash_floor"], 2000000)
                self.assertGreaterEqual(detail["projected_min_balance"], 2000000)
                self.assertEqual(detail["exposure"]["limits"]["run"], 3000000)
                self.assertLessEqual(detail["budgeted_cost"], 3000000)

    def test_low_cash_never_spends_reserve_even_when_no_stock(self):
        for balance in (1900000, 2000000):
            self.inv["balance"] = balance
            with self.subTest(balance=balance):
                decision, detail = self.plan()
                self.assertEqual(decision, {"buy": []})
                self.assertEqual(detail["operation_status"], "cash_floor_no_stock")
                self.assertFalse(detail["automatic_bankruptcy"])
        self.inv["balance"] = 2000000 + worst_cost(300000)
        decision, detail = self.plan()
        self.assertEqual(sum(row["qty"] for row in decision["buy"]), 1)
        self.assertIn(decision["buy"][0]["sku"], ALTS)
        self.assertEqual(detail["projected_min_balance"], 2000000)

    def test_four_adaptive_pilots_keep_one_unit_per_sku(self):
        self.no_primary_demand()
        decision, detail = self.plan()
        self.assertEqual(len(decision["buy"]), 4)
        self.assertTrue(all(row["qty"] == 1 for row in decision["buy"]))
        self.assertTrue(all(row["reason"] == "adaptive_public_demand_pilot" for row in detail["choices"]))
        self.assertLessEqual(detail["budgeted_cost"], 3000000)

    def test_adaptive_twelve_target_does_not_replace_overall_twenty_cap(self):
        self.no_primary_demand()
        for held, expected in ((10, 2), (11, 1), (12, 0)):
            self.inv["units"] = self.held_other(held)
            with self.subTest(held=held):
                decision, _ = self.plan()
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), expected)
        # Fixed, evidence-gated primaries can replenish above the adaptive
        # threshold. The separate total stock cap still applies to every buy.
        self.public["events"].extend(
            {"seq": 1000 + j, "ts": self.now - 30, "kind": "settle", "sku": ACE,
             "buyer": f"buyer-{j}", "shop": f"https://shop-{j}.invalid", "price": 690000,
             "grade": "A", "warranty_hours": 6} for j in range(3))
        self.assertEqual(self.plan()[0]["buy"], [{"sku": ACE, "qty": 1}])
        self.inv["units"] = self.held_other(20)
        self.assertEqual(self.plan()[0]["buy"], [])

    def test_held_category_cost_still_blocks_new_exposure_but_not_other_categories(self):
        self.no_primary_demand()
        self.inv["units"] = [unit(unit="held-cost", sku="Gffff0001", category="alternatives",
                                  cost=2800000, supplied_at=self.now - 10)]
        before = deepcopy(self.inv["units"])
        self.assertEqual(self.plan()[0]["buy"], [])
        self.catalog[ALTS[0]]["category"] = "separate-category"
        self.demand.append({"category": "separate-category"})
        self.assertEqual(self.plan()[0]["buy"], [{"sku": ALTS[0], "qty": 1}])
        self.assertEqual(self.inv["units"], before)

    def test_sku_run_category_and_food_limits_have_exact_boundaries(self):
        product = {"category": "beds", "kind": "goods", "worst_cost": worst_cost(851400)}
        held_cost = 2000000 - product["worst_cost"]
        for extra, reason in ((0, None), (1, "sku_cost_cap")):
            self.inv["units"] = [unit(unit="held", sku=ACE, category="beds", cost=held_cost + extra)]
            with self.subTest(extra=extra):
                budget = StockBudget(self.inv, self.cfg)
                self.assertEqual(budget.reason(ACE, product), reason)
        self.inv["units"] = []
        budget = StockBudget(self.inv, self.cfg)
        budget.add(ACE, {"category": "beds", "kind": "goods", "worst_cost": 1500000})
        budget.add(SIMMONS, {"category": "beds", "kind": "goods", "worst_cost": 1500000})
        self.assertEqual(budget.reason(ALTS[0], {"category": "other", "kind": "goods", "worst_cost": 1}), "run_cost_cap")
        self.inv["units"] = [unit(unit="held", sku=ACE, category="beds", cost=2000000),
                             unit(unit="held2", sku=SIMMONS, category="beds", cost=1000000)]
        budget = StockBudget(self.inv, self.cfg)
        self.assertEqual(budget.reason(ALTS[0], {"category": "beds", "kind": "goods", "worst_cost": 1}), "category_cost_cap")
        self.inv["units"] = [unit(unit="food", sku="F00000001", category="food", kind="food", cost=500000)]
        budget = StockBudget(self.inv, self.cfg)
        self.assertEqual(budget.reason("F00000002", {"category": "food", "kind": "food", "worst_cost": 1}), "food_cost_cap")

    def test_more_capacity_never_buys_without_fresh_diverse_evidence(self):
        original = deepcopy(self.public)
        self.no_primary_demand()
        valid_alternatives = deepcopy(self.public)
        for variant in ("absent", "stale", "one_buyer", "one_named_shop", "two_sales", "surge"):
            self.public = deepcopy(valid_alternatives)
            self.inv["surge"] = []
            if variant == "absent": self.public["events"] = []
            if variant == "stale": self.public["updated_at"] -= 3601
            if variant == "one_buyer":
                for event in self.public["events"]: event["buyer"] = "one"
            if variant == "one_named_shop":
                self.public["market_stock"]["shop_names_by_url"] = {
                    f"https://shop-{i}.invalid": "same-shop" for i in range(3)}
            if variant == "two_sales":
                self.public["events"] = [e for e in self.public["events"] if e["buyer"] != "buyer-2"]
            if variant == "surge":
                self.inv["surge"] = [{"sku": sku, "factor": 1.11, "units": 105} for sku in ALTS]
            with self.subTest(variant=variant):
                self.assertEqual(self.plan()[0]["buy"], [])
        self.public = original

    def test_refund_unknown_cost_and_unresolved_supply_do_not_get_more_credit(self):
        self.no_primary_demand()
        self.public["events"] = [e for e in self.public["events"] if e["sku"] == ALTS[0]]
        for changes in ({"cost": None}, {"refunds": [{"seq": 99, "amount": 200000, "ts": self.now - 10}]}):
            self.memory["sales"] = [self.sale(ALTS[0], **changes)]
            with self.subTest(changes=changes):
                self.assertEqual(self.plan()[0]["buy"], [])
        self.memory["sales"] = []
        lifecycle = {"units": {"missing": {"sku": ALTS[0], "category": "alternatives", "kind": "goods",
            "cost": 100000, "state": "missing", "missing_reason": "unconfirmed"}}, "archived": {}}
        decision, detail = self.plan(lifecycle=lifecycle)
        self.assertEqual(decision["buy"], [])
        self.assertIn(ALTS[0], detail["paused_skus"])
        control = {ALTS[0]: {"planned_qty": 1, "settled_qty": 0, "retry_credit": 0,
                              "last_ledger": 0, "observed_at_plan": 0}}
        self.assertEqual(self.plan(adaptive_control=control)[0]["buy"], [])

    def test_expanded_hook_is_still_bounded_by_closing_window_and_single_refresh(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        for seconds, expected in ((5, 0), (35, 2)):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
                root = Path(directory)
                md = root / ".market"
                inv = deepcopy(self.inv)
                inv["restock"]["closes_at"] = self.now + seconds
                atomic_json(root / "shop-policy.json", self.policy)
                atomic_json(md / "inventory.json", inv)
                atomic_json(md / "catalog.json", self.catalog)
                atomic_json(md / "demand.json", self.demand)
                with patch("restock.refresh_market", return_value=self.public) as refresh:
                    decision = restock.run_once(root, self.now)
                    self.assertEqual(sum(row["qty"] for row in decision["buy"]), expected)
                    self.assertEqual(refresh.call_count, int(expected > 0))
                    if expected:
                        self.assertEqual(restock.run_once(root, self.now), {"buy": []})
                        self.assertEqual(refresh.call_count, 1)
                rows = [json.loads(line) for line in (root / "logs/restock-decisions.jsonl").read_text().splitlines()]
                if expected:
                    self.assertEqual(rows[0]["window_unit_cap"], 2)
                else:
                    self.assertEqual(rows[0]["status"], "window_closing")


if __name__ == "__main__":
    unittest.main()
