"""Cash-profit growth is bounded, excludes injections, and adds no model calls."""
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from adaptive_stock import candidates
import restock
from shop_policy import policy_at
from stock_budget import StockBudget, validate
from supply_pricing import worst_cost

RULES = {"enabled": True, "cash_profit_share_percent": 50, "max_capital_percent": 200}
CFG = {"max_run_cost_percent": 60, "max_sku_cost_percent": 40,
       "max_category_cost_percent": 60, "max_food_cost_percent": 10,
       "focused_goods": {"enabled": True, "secondary_skus": ["G03d0199f"],
                         "max_secondary_sku_cost_percent": 40}, "reinvestment": RULES}


def held(uid, sku, cost, category="goods", kind="goods"):
    return {"unit": uid, "sku": sku, "cost": cost, "category": category, "kind": kind}


class ProfitReinvestmentTests(unittest.TestCase):
    def setUp(self):
        self.cfg = deepcopy(CFG)
        self.inv = {"round": 3, "start": 5000000, "balance": 11695754,
                    "injected": 0, "units": [], "surge": []}

    def test_positive_cash_profit_scales_only_goods_exposure(self):
        budget = StockBudget(self.inv, self.cfg)
        self.assertEqual(budget.summary()["capital_basis"], {
            "initial": 5000000, "effective": 8347877, "cash_profit": 6695754,
            "retained_profit_allocated": 3347877, "status": "cash_profit_reinvested"})
        self.assertEqual(budget.limits, {"run": 5008726, "sku": 3339150,
            "category": 5008726, "food": 500000, "secondary_sku": 3339150})
        self.assertEqual(self.inv["balance"], 11695754)  # No transfer or mutable wallet.

    def test_absent_or_disabled_option_preserves_original_caps(self):
        self.inv["balance"] = 100000000
        for rules in (None, {"enabled": False}, {**RULES, "enabled": False}):
            cfg = deepcopy(self.cfg)
            if rules is None:
                cfg.pop("reinvestment")
            else:
                cfg["reinvestment"] = rules
            with self.subTest(rules=rules):
                budget = StockBudget(self.inv, cfg)
                self.assertEqual(budget.limits["run"], 3000000)
                self.assertEqual(budget.limits["sku"], 2000000)
                self.assertEqual(budget.summary()["capital_basis"]["status"], "disabled")

    def test_missing_or_invalid_injection_only_disables_optional_growth(self):
        cases = [("missing", "unavailable_injected"), (None, "invalid_injected"),
                 (True, "invalid_injected"), (-1, "invalid_injected"),
                 ("0", "invalid_injected"), (float("nan"), "invalid_injected"),
                 (float("inf"), "invalid_injected")]
        for value, status in cases:
            inv = deepcopy(self.inv)
            if value == "missing":
                inv.pop("injected")
            else:
                inv["injected"] = value
            with self.subTest(value=value):
                budget = StockBudget(inv, self.cfg)
                self.assertEqual(budget.limits["run"], 3000000)
                self.assertEqual(budget.summary()["capital_basis"]["status"], status)
                json.dumps(budget.summary(), allow_nan=False)
        for balance in (None, True, "11695754", float("nan"), float("inf")):
            with self.subTest(balance=balance):
                budget = StockBudget({**self.inv, "balance": balance}, self.cfg)
                self.assertEqual(budget.limits["run"], 3000000)
                self.assertEqual(budget.summary()["capital_basis"]["status"], "invalid_balance")

    def test_bankruptcy_funding_unsold_inventory_and_negative_cash_are_not_profit(self):
        self.inv["units"] = [held("stock", "G00000001", 4000000)]
        # Cash restored/added by bankruptcy must not look like earned profit.
        for balance, injected in ((15000000, 10000000), (14900000, 10000000), (4900000, 0)):
            with self.subTest(balance=balance, injected=injected):
                inv = {**self.inv, "balance": balance, "injected": injected}
                budget = StockBudget(inv, self.cfg)
                self.assertEqual(budget.summary()["capital_basis"]["effective"], 5000000)
                self.assertLessEqual(budget.summary()["capital_basis"]["cash_profit"], 0)
                self.assertEqual(budget.limits["category"], 3000000)
        # A real positive remainder is eligible even after an injection, but
        # only after subtracting the entire injection and starting capital.
        budget = StockBudget({**self.inv, "balance": 16000000, "injected": 10000000}, self.cfg)
        self.assertEqual(budget.summary()["capital_basis"]["effective"], 5500000)

    def test_growth_never_exceeds_double_start_and_rounds_down(self):
        budget = StockBudget({**self.inv, "balance": 100000000}, self.cfg)
        self.assertEqual(budget.summary()["capital_basis"]["effective"], 10000000)
        self.assertEqual(budget.limits["run"], 6000000)
        self.assertEqual(budget.limits["food"], 500000)
        budget = StockBudget({**self.inv, "balance": 5000003}, self.cfg)
        self.assertEqual(budget.summary()["capital_basis"]["effective"], 5000001)
        self.assertEqual(budget.summary()["capital_basis"]["retained_profit_allocated"], 1)
        fractional = StockBudget({**self.inv, "start": 5000000.75, "balance": 5000000.76}, self.cfg)
        self.assertEqual(fractional.summary()["capital_basis"]["retained_profit_allocated"], 0)
        self.assertEqual(fractional.limits["run"], 3000000)
        cfg = {**self.cfg, "reinvestment": {**RULES, "max_capital_percent": 100}}
        self.assertEqual(StockBudget(self.inv, cfg).summary()["capital_basis"]["effective"], 5000000)

    def test_concentration_food_and_run_caps_still_reject_excess(self):
        baseline = StockBudget(self.inv, self.cfg)
        sku_limit, category_limit = baseline.limits["sku"], baseline.limits["category"]
        product = {"kind": "goods", "category": "goods", "worst_cost": 1}
        inv = {**self.inv, "units": [held("one", "G00000001", sku_limit)]}
        self.assertEqual(StockBudget(inv, self.cfg).reason("G00000001", product), "sku_cost_cap")
        inv = {**self.inv, "units": [held("one", "G00000001", category_limit)]}
        self.assertEqual(StockBudget(inv, self.cfg).reason("G00000002", product), "category_cost_cap")
        budget = StockBudget(self.inv, self.cfg)
        budget.add("G00000001", {**product, "worst_cost": 3000000})
        budget.add("G00000002", {**product, "category": "other", "worst_cost": budget.limits["run"] - 3000000})
        self.assertEqual(budget.reason("G00000003", {**product, "category": "third"}), "run_cost_cap")
        inv = {**self.inv, "units": [held("food", "F00000001", 500000, "fruit", "food")]}
        self.assertEqual(StockBudget(inv, self.cfg).reason("F00000002", {
            "kind": "food", "category": "meat", "worst_cost": 1}), "food_cost_cap")
        # If refunds/losses reduce cash, existing stock is retained, but no
        # additional exposure is silently granted using the earlier high cap.
        inv = {**self.inv, "balance": 4900000, "units": [held("one", "G00000001", 2500000)]}
        before = deepcopy(inv)
        self.assertEqual(StockBudget(inv, self.cfg).reason("G00000001", product), "sku_cost_cap")
        self.assertEqual(inv, before)

    def test_shared_candidate_and_planner_budgets_grow_but_cash_floor_does_not(self):
        now = time.time()
        cfg = policy_at()["restock"]
        cfg["reinvestment"] = deepcopy(RULES)
        sku = "G00000001"
        catalog = {sku: {"name": "Synthetic expensive goods", "kind": "goods", "category": "goods", "list_price": 2000000}}
        public = {"schema": 1, "round": 3, "updated_at": now, "events": [
            {"seq": i + 1, "ts": now - 30, "kind": "settle", "sku": sku,
             "buyer": f"buyer-{i}", "shop": f"https://shop-{i}.invalid", "price": 1600000,
             "grade": "A", "warranty_hours": 6} for i in range(3)], "market_stock": {
                 "round": 3, "observed_at": now, "by_sku": {sku: {"units": 3, "shops": 3}},
                 "shop_names_by_url": {f"https://shop-{i}.invalid": f"shop-{i}" for i in range(3)}}}
        memory = {"schema": 1, "round": 3, "sales": []}
        old = deepcopy(cfg)
        old.pop("reinvestment")
        with patch("harness.decide", side_effect=AssertionError("No model")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("No network")):
            self.assertEqual(candidates(self.inv, catalog, {"goods"}, {}, old, now, public), {})
            self.assertIn(sku, candidates(self.inv, catalog, {"goods"}, {}, cfg, now, public))
            decision, detail = restock.plan(self.inv, catalog, [{"category": "goods"}], memory, [], cfg, now, public)
            self.assertEqual(decision, {"buy": [{"sku": sku, "qty": 1}]})
            self.assertEqual(detail["cash_floor"], 2000000)
            self.assertEqual(detail["exposure"]["capital_basis"]["effective"], 8347877)
            catalog[sku]["list_price"] = 300000
            for balance, expected in ((2000000, 0), (2000000 + worst_cost(300000), 1)):
                with self.subTest(balance=balance):
                    decision, detail = restock.plan({**self.inv, "balance": balance}, catalog,
                        [{"category": "goods"}], memory, [], cfg, now, public)
                    self.assertEqual(sum(row["qty"] for row in decision["buy"]), expected)
                    self.assertEqual(detail["projected_min_balance"], 2000000)

    def test_malformed_configuration_is_rejected_instead_of_expanding_caps(self):
        for rules in (None, [], {}, {"enabled": 1}, {"enabled": True},
                      {**RULES, "cash_profit_share_percent": 0},
                      {**RULES, "cash_profit_share_percent": 51},
                      {**RULES, "cash_profit_share_percent": True},
                      {**RULES, "max_capital_percent": 99},
                      {**RULES, "max_capital_percent": 201},
                      {**RULES, "max_capital_percent": 200.0}):
            with self.subTest(rules=rules), self.assertRaisesRegex(ValueError, "reinvestment policy"):
                validate({**self.cfg, "reinvestment": rules})


if __name__ == "__main__":
    unittest.main()
