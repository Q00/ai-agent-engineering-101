"""Evidence-based alternatives stay small, affordable and fully reconciled."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from adaptive_stock import candidates, prepare_control, remaining_credit
from feedback import atomic_json, load_json
import restock
from shop_policy import policy_at
from test_inventory import unit

ROOT = Path(__file__).resolve().parent
ACE, SIMMONS = "G8095ddd6", "Gb5d0dde7"
ALT1, ALT2, ALT3 = "G021aae64", "G754bf0ca", "G75f11c19"


class AdaptiveStockTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        self.cfg, self.rules = self.policy["restock"], self.policy["restock"]["adaptive_goods"]
        # Earlier focus-first experiments; YMS turnover priority has separate integration tests.
        self.rules["turnover_goods"] = {"enabled": False}
        # Preserve the earlier 4->6->8 diversification regressions explicitly.
        # The current larger policy is covered in test_aggressive_restock.py.
        self.cfg.update(max_stock_units=20, max_units_per_run=4, max_units_per_sku=2, max_run_cost_percent=44, max_sku_cost_percent=26)
        self.rules.update(max_candidates=6, max_units_per_run=2, low_stock_units=8)
        self.rules["replenishment"] = {"enabled": False}
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": [], "surge": [],
                    "restock": {"open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {sku: {"name": sku, "list_price": 200000, "category": "furniture", "kind": "goods"}
                        for sku in (ACE, SIMMONS, ALT1, ALT2, ALT3)}
        self.demand = [{"category": "furniture"}]
        self.memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": []}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [
            {"seq": i * 10 + j + 1, "ts": self.now - 30, "kind": "settle", "sku": sku,
             "buyer": f"buyer-{j}", "shop": f"https://shop-{j}.invalid", "price": 150000,
             "grade": "A", "warranty_hours": 6}
            for i, sku in enumerate(self.catalog) for j in range(3)]}
        self.public["market_stock"] = {"round": 3, "observed_at": self.now,
            "by_sku": {sku: {"units": 10, "shops": 3} for sku in self.catalog},
            "shop_names_by_url": {f"https://shop-{j}.invalid": f"shop-{j}" for j in range(3)}}

    def pool(self, *, economics=None, **kw):
        return candidates(self.inv, self.catalog, {"furniture"}, economics or {}, self.cfg,
                          self.now, self.public, **kw)

    def control(self, previous=None, pool=None, **kw):
        return prepare_control(previous, self.memory, self.pool() if pool is None else pool, self.rules,
                               lifecycle_stats=kw.pop("lifecycle_stats", {}), held=kw.pop("held", {}), **kw)

    def plan(self, *, control=None, lifecycle=None, horizon=None):
        return restock.plan(self.inv, self.catalog, self.demand, self.memory, [], self.cfg, self.now,
                            self.public, lifecycle=lifecycle, horizon=horizon,
                            adaptive_control=control)

    def test_focus_is_first_then_two_small_alternatives_without_model_or_extra_network(self):
        with patch("harness.decide", side_effect=AssertionError("No model")):
            decision, evidence = self.plan(control=self.control())
        self.assertEqual([r["sku"] for r in decision["buy"][:2]], [ACE, SIMMONS])
        self.assertEqual(sum(r["qty"] for r in decision["buy"]), 4)
        self.assertEqual(len([c for c in evidence["choices"] if c["reason"] == "adaptive_public_demand_pilot"]), 2)
        self.assertTrue(all(r["qty"] == 1 for r in decision["buy"]))
        self.assertGreaterEqual(evidence["projected_min_balance"], 2000000)
        self.assertLessEqual(evidence["budgeted_cost"], 2200000)

    def test_missing_diverse_recent_evidence_food_surge_and_cost_limits_are_not_pilots(self):
        original = deepcopy((self.public, self.inv, self.catalog))
        for variant in ("few_sales", "one_buyer", "one_shop", "stale", "food", "surge", "cost"):
            self.public, self.inv, self.catalog = deepcopy(original)
            for event in self.public["events"]:
                if event["sku"] == ALT1:
                    if variant == "one_buyer": event["buyer"] = "same"
                    if variant == "one_shop": event["shop"] = "same"
            if variant == "few_sales":
                self.public["events"] = [e for e in self.public["events"] if not (e["sku"] == ALT1 and e["buyer"] == "buyer-2")]
            if variant == "stale": self.public["updated_at"] -= 3601
            if variant == "food": self.catalog[ALT1]["kind"] = "food"
            if variant == "surge": self.inv["surge"] = [{"sku": ALT1, "factor": 1.11, "units": 105}]
            if variant == "cost": self.catalog[ALT1]["list_price"] = 2320000
            with self.subTest(variant=variant):
                self.assertNotIn(ALT1, self.pool())

    def test_one_bad_local_cost_refund_or_missing_unit_blocks_more_of_that_sku(self):
        for metric in ({"unknown": 1}, {"refunded": 1}, {"mature": 1, "net_estimate": -1}):
            self.assertNotIn(ALT1, self.pool(economics={ALT1: metric}))
        self.assertNotIn(ALT1, self.pool(paused={ALT1}))
        self.inv["units"] = [unit(unit="held", sku=ALT1, category="furniture", cost=100000)]
        self.assertNotIn(ALT1, self.pool())

    def test_stock_pressure_and_turnover_can_beat_a_more_expensive_product(self):
        self.catalog[ALT1]["list_price"] = 800000
        self.public["market_stock"] = {"round": 3, "observed_at": self.now, "by_sku": {
            ALT1: {"units": 100, "shops": 10}, ALT2: {"units": 2, "shops": 2}, ALT3: {"units": 50, "shops": 5}},
            "shop_names_by_url": {f"https://shop-{j}.invalid": f"shop-{j}" for j in range(3)}}
        for row in self.public["events"]:
            if row["sku"] == ALT1: row["price"] = 600000
        self.assertEqual(next(iter(self.pool())), ALT2)

    def test_tunnel_aliases_or_missing_names_do_not_invent_seller_diversity(self):
        self.public["market_stock"]["shop_names_by_url"] = {
            f"https://shop-{j}.invalid": "one-shop" for j in range(3)}
        self.assertEqual(self.pool(), {})
        self.public["market_stock"].pop("shop_names_by_url")
        self.assertEqual(self.pool(), {})

    def test_fixed_cash_category_and_run_exposures_still_bound_combined_plan(self):
        self.inv["balance"] = 2225001
        decision, detail = self.plan()
        self.assertEqual(sum(r["qty"] for r in decision["buy"]), 1)
        self.assertGreaterEqual(detail["projected_min_balance"], 2000000)
        self.inv["balance"] = 5000000
        self.inv["units"] = [unit(unit="old", sku="Gffffffff", category="furniture", cost=2900000)]
        self.assertEqual(self.plan()[0]["buy"], [])

    def test_adaptive_stops_at_low_stock_target_and_during_wind_down(self):
        self.public["events"] = [e for e in self.public["events"] if e["sku"] not in (ACE, SIMMONS)]
        for count in range(4, 8):
            self.inv["units"] = [unit(unit=f"old-{i}", sku=f"Gfffffff{i}", category="other", cost=10000)
                                 for i in range(count)]
            with self.subTest(stock=count), patch("harness.decide", side_effect=AssertionError("No model")):
                decision, evidence = self.plan(control=self.control())
            self.assertEqual(sum(r["qty"] for r in decision["buy"]), min(2, 8 - count))
            self.assertTrue(all(r["qty"] == 1 and r["sku"] in (ALT1, ALT2, ALT3) for r in decision["buy"]))
            self.assertLessEqual(count + sum(r["qty"] for r in decision["buy"]), 8)
            self.assertGreaterEqual(evidence["projected_min_balance"], 2000000)
            self.assertEqual(evidence["adaptive_goods"]["low_stock_units"], 8)
            self.assertEqual(evidence["adaptive_goods"]["observed_stock_units"], count)
        self.inv["units"].append(unit(unit="old-7", sku="Gfffffff7", category="other", cost=10000))
        self.assertEqual(self.plan()[0]["buy"], [])
        self.inv["units"] = []
        horizon = {**self.policy["horizon"], "ends_at": datetime.fromtimestamp(
            self.now + 12 * 3600, timezone.utc).isoformat()}
        self.assertEqual(self.plan(horizon=horizon)[0]["buy"], [])

    def test_expanded_target_does_not_buy_without_evidence_or_override_fixed_stock_policy(self):
        self.inv["units"] = [unit(unit=f"old-{i}", sku=f"Gfffffff{i}", category="other", cost=10000)
                             for i in range(7)]
        decision, evidence = self.plan(control=self.control())
        self.assertEqual([r["sku"] for r in decision["buy"]], [ACE, SIMMONS])
        self.assertFalse(any(c["reason"] == "adaptive_public_demand_pilot" for c in evidence["choices"]))
        # Eight is an adaptive top-up threshold; the existing overall cap and
        # proven fixed-SKU replenishment policy remain independent.
        self.assertEqual(self.cfg["max_stock_units"], 20)
        self.public["events"] = []
        with patch("harness.decide", side_effect=AssertionError("No model")):
            self.assertEqual(self.plan()[0]["buy"], [])

    def test_expanded_policy_bound_rejects_unapproved_larger_target(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root = Path(directory)
            atomic_json(root / "shop-policy.json", self.policy)
            self.assertEqual(policy_at(root)["restock"]["adaptive_goods"]["low_stock_units"], 8)
            self.rules["low_stock_units"] = 61
            atomic_json(root / "shop-policy.json", self.policy)
            with self.assertRaisesRegex(ValueError, "adaptive inventory policy: low_stock_units"):
                policy_at(root)

    def test_successive_openings_expand_four_held_skus_to_eight_without_duplicates(self):
        extra = ("G00000004", "G00000005", "G00000006")
        for i, sku in enumerate(extra):
            self.catalog[sku] = {"name": sku, "list_price": 200000, "category": "furniture", "kind": "goods"}
            self.public["market_stock"]["by_sku"][sku] = {"units": 10, "shops": 3}
            self.public["events"].extend(
                {"seq": 100 + i * 10 + j, "ts": self.now - 30, "kind": "settle", "sku": sku,
                 "buyer": f"buyer-{j}", "shop": f"https://shop-{j}.invalid", "price": 150000,
                 "grade": "A", "warranty_hours": 6} for j in range(3))
        self.inv["units"] = [unit(unit=f"held-{sku}", sku=sku, category="furniture", cost=100000)
                             for sku in (ACE, SIMMONS, ALT1, ALT2)]
        acquired = []
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root, md = Path(directory), Path(directory) / ".market"
            atomic_json(root / "shop-policy.json", self.policy)
            atomic_json(md / "catalog.json", self.catalog)
            atomic_json(md / "demand.json", self.demand)
            with patch("restock.refresh_market", return_value=self.public) as refresh, \
                    patch("harness.decide", side_effect=AssertionError("No model")):
                for step in range(3):
                    current = self.now + 1200 * step
                    self.inv["restock"] = {"open": True, "opens_at": current - 30, "closes_at": current + 150}
                    atomic_json(md / "inventory.json", self.inv)
                    os.utime(md / "demand.json", (current, current))
                    before = {u["sku"] for u in self.inv["units"]}
                    decision = restock.run_once(root, current)
                    self.assertEqual(sum(r["qty"] for r in decision["buy"]), 2 if step < 2 else 0)
                    for row in decision["buy"]:
                        self.assertEqual(row["qty"], 1)
                        self.assertNotIn(row["sku"], before)
                        acquired.append(row["sku"])
                        self.inv["units"].append(unit(unit=f"bought-{row['sku']}", sku=row["sku"],
                            category="furniture", list_price=200000, cost=150000, supplied_at=current))
                        self.inv["balance"] -= 150000
                    self.assertLessEqual(len(self.inv["units"]), 8)
                    self.assertGreaterEqual(self.inv["balance"], 2000000)
                self.assertEqual(refresh.call_count, 3)  # Existing single refresh per opening, no added polling.
            controls = load_json(md / "restock-control.json")
            self.assertEqual(sum(r["planned_qty"] for r in controls["adaptive_supply"].values()), 4)
        self.assertEqual(len(acquired), len(set(acquired)))
        self.assertEqual(len({u["sku"] for u in self.inv["units"]}), 8)

    def test_retiring_candidate_does_not_forget_unconfirmed_purchase_or_repeat_forever(self):
        controls = self.control(pool={ALT1: {}})
        controls[ALT1]["planned_qty"] = 1
        controls = self.control(controls, pool={ALT2: {}}, next_window=True)
        self.assertIn(ALT1, controls)
        self.assertEqual(remaining_credit(controls[ALT1]), 1)
        controls[ALT1]["planned_qty"] += 1
        again = self.control(controls, pool={ALT1: {}, ALT2: {}}, next_window=True)
        self.assertEqual(remaining_credit(again[ALT1]), 0)
        blocked = self.control({ALT1: {**controls[ALT1], "retry_credit": 0}}, pool={ALT1: {}},
                               next_window=True, unresolved_receipts=True)
        self.assertEqual(remaining_credit(blocked[ALT1]), 0)

    def test_full_pending_history_refuses_new_pilots_and_confirmed_inactive_history_can_retire(self):
        original = {f"G{i:08x}": {"planned_qty": 1, "settled_qty": 0, "last_ledger": 0,
                    "retry_credit": 0, "observed_at_plan": 0} for i in range(self.rules["max_retained_skus"])}
        result = self.control(original, pool={ALT1: {}})
        self.assertEqual(len(result), self.rules["max_retained_skus"])
        self.assertNotIn(ALT1, result)
        self.assertEqual(original[next(iter(original))]["settled_qty"], 0)
        settled = deepcopy(original)
        first = next(iter(settled))
        settled[first]["settled_qty"] = 1
        result = self.control(settled, pool={ALT1: {}})
        self.assertIn(ALT1, result)
        self.assertNotIn(first, result)
        with self.assertRaises(ValueError): self.control({**original, ALT1: original[first]}, pool={})

    def test_open_window_persists_separate_supply_credits_and_cannot_duplicate_buy(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root, md = Path(directory), Path(directory) / ".market"
            atomic_json(root / "shop-policy.json", self.policy)
            atomic_json(md / "catalog.json", self.catalog)
            atomic_json(md / "demand.json", self.demand)
            atomic_json(md / "inventory.json", self.inv)
            with patch("restock.refresh_market", return_value=self.public):
                decision = restock.run_once(root, self.now)
                self.assertEqual(sum(row["qty"] for row in decision["buy"]), 4)
                self.assertEqual(restock.run_once(root, self.now + 30), {"buy": []})
            controls = load_json(md / "restock-control.json")
            self.assertEqual(sum(r["planned_qty"] for r in controls["focused_supply"].values()), 2)
            self.assertEqual(sum(r["planned_qty"] for r in controls["adaptive_supply"].values()), 2)

    def test_market_schedule_handles_fast_host_without_changing_local_freshness(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root, md = Path(directory), Path(directory) / ".market"
            atomic_json(root / "shop-policy.json", self.policy)
            atomic_json(md / "catalog.json", self.catalog)
            atomic_json(md / "demand.json", self.demand)  # Local filesystem mtime remains local.
            shifted = {**self.inv, "restock": {"open": True,
                       "opens_at": self.now - 630, "closes_at": self.now - 450}}
            atomic_json(md / "inventory.json", shifted)
            def market_clock(_md, _round, *, local_now):
                return {"now": local_now - 600, "status": "synced", "offset_seconds": -600,
                        "uncertainty_seconds": 1}
            with patch("restock.schedule_now", side_effect=market_clock), \
                    patch("restock.refresh_market", return_value=self.public):
                self.assertEqual(sum(r["qty"] for r in restock.run_once(root, self.now)["buy"]), 4)
            import json
            event = json.loads((root / "logs/restock-decisions.jsonl").read_text().splitlines()[-1])
            self.assertEqual(event["window"]["schedule_clock"], {"status": "synced", "offset_seconds": -600})
            self.assertGreaterEqual(event["time"], self.now)

    def test_adaptive_only_preparation_counts_as_usable_without_extra_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as directory:
            root, md = Path(directory), Path(directory) / ".market"
            atomic_json(root / "shop-policy.json", self.policy)
            atomic_json(md / "catalog.json", self.catalog)
            atomic_json(md / "demand.json", self.demand)
            opens = self.now + 120
            atomic_json(md / "inventory.json", {**self.inv, "restock": {"open": False,
                        "opens_at": opens, "closes_at": None}})
            self.public["events"] = [e for e in self.public["events"] if e["sku"] not in (ACE, SIMMONS)]
            with patch("restock.refresh_market", return_value=self.public) as refresh:
                self.assertEqual(restock.run_once(root, self.now), {"buy": []})
                self.assertEqual(restock.run_once(root, self.now + 30), {"buy": []})
                refresh.assert_called_once()
            self.assertTrue(load_json(md / "restock-control.json")["prepared_demand_usable"])


if __name__ == "__main__":
    unittest.main()
