"""High-value pilots, bounded uncertain supply, and real opening deadlines."""
from copy import deepcopy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from feedback import atomic_json, load_json
from focused_stock import observe_control, epoch_control
import restock
from shop_policy import policy_at
from supply_pricing import surge_factors, worst_cost
from test_inventory import unit

ROOT = Path(__file__).resolve().parent
ACE, SIMMONS, LACLOUD = "G8095ddd6", "Gb5d0dde7", "G03d0199f"


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        # These fixtures exercise normal restocking, independently of the live round deadline.
        self.policy["horizon"]["ends_at"] = datetime.fromtimestamp(
            self.now + 30 * 86400, timezone.utc).isoformat()
        self.cfg = self.policy["restock"]
        # Keep the original focused-pilot cash/depth boundary scenarios.
        # Current higher buying limits have separate integration coverage.
        self.cfg.update(max_units_per_run=4, max_run_cost_percent=44, max_sku_cost_percent=26)
        self.focus = self.cfg["focused_goods"]
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": [], "surge": [],
                    "restock": {"open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {s: {"name": s, "list_price": price, "category": "furniture", "kind": "goods"}
                        for s, price in ((ACE, 851400), (SIMMONS, 1094400), (LACLOUD, 1611000))}
        self.catalog["G00000000"] = {"name": "cheap distraction", "list_price": 1000,
                                    "category": "furniture", "kind": "goods"}
        self.memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": []}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [
            {"seq": i * 10 + j + 1, "ts": self.now - 30, "kind": "settle", "sku": sku,
             "buyer": f"buyer-{j}", "shop": f"shop-{j}", "price": 700000,
             "grade": "A", "warranty_hours": 6}
            for i, sku in enumerate((ACE, SIMMONS, LACLOUD)) for j in range(3)]}
        self.control = observe_control(None, self.memory, self.focus)

    def plan(self, **kwargs):
        return restock.plan(self.inv, self.catalog, [{"category": "furniture"}], self.memory,
                            [], self.cfg, self.now, self.public, focus_control=self.control, **kwargs)

    def sale(self, idx, **changes):
        return {"ledger": idx + 1, "unit": str(idx), "sku": ACE, "category": "furniture", "kind": "goods",
                "grade": "A", "cost": 500000, "price": 700000, "warranty_hours": 6,
                "supplied_at": self.now - 8 * 3600, "settled_at": self.now - 7 * 3600, **changes}

    def test_two_primary_pilots_fit_maximum_surge_and_ignore_cheap_products(self):
        with patch("harness.decide", side_effect=AssertionError("No model")):
            decision, detail = self.plan()
        self.assertEqual(decision["buy"], [{"sku": ACE, "qty": 1}, {"sku": SIMMONS, "qty": 1}])
        self.assertEqual(detail["budgeted_cost"], 2189027)
        self.assertEqual(detail["projected_min_balance"], 2810973)
        self.assertEqual(self.inv["units"], [])

    def test_cash_floor_and_category_exposure_still_bound_expensive_goods(self):
        self.inv["balance"] = 2957825
        self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["balance"] = 5000000
        self.inv["units"] = [unit(unit="old", sku="Gold00000", category="furniture", cost=2100000)]
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_missing_malformed_and_high_surge_defer_instead_of_assuming_free(self):
        for raw in (None, {}, True, [{"sku": ACE, "units": 1, "factor": True}],
                    [{"sku": ACE, "units": 1, "factor": 1.6}]):
            self.inv["surge"] = raw
            self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["surge"] = [{"sku": ACE, "units": 125, "factor": 1.15}]
        self.assertEqual(self.plan()[0]["buy"], [{"sku": SIMMONS, "qty": 1}])
        self.inv["surge"] = {"surge": []}
        self.assertEqual(len(self.plan()[0]["buy"]), 2)
        self.assertEqual(worst_cost(101), 115)

    def test_initial_pilot_needs_one_actual_recent_buyer_not_only_catalog(self):
        self.public["events"] = self.public["events"][:1]
        self.assertEqual(self.plan()[0]["buy"], [{"sku": ACE, "qty": 1}])
        self.public["events"][0].pop("buyer")
        self.assertEqual(self.plan()[0], {"buy": []})
        self.public["events"][0]["buyer"] = "buyer"
        self.public["updated_at"] -= 3601
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_emitting_plan_does_not_create_sale_or_automatic_replenishment(self):
        for sku in (ACE, SIMMONS):
            self.control[sku]["planned_qty"] = 1
        self.assertEqual(self.plan()[0], {"buy": []})
        self.memory["sales"] = [self.sale(1, settled_at=self.now - 30)]
        self.control = observe_control(self.control, self.memory, self.focus)
        self.assertEqual(self.plan()[0]["buy"], [{"sku": ACE, "qty": 1}])
        self.public["events"] = self.public["events"][:1]
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_held_unpaid_units_and_unresolved_missing_cost_block_rebuy(self):
        self.inv["units"] = [unit(unit=sku, sku=sku, category="furniture", cost=500000)
                             for sku in (ACE, SIMMONS)]
        self.assertEqual(self.plan()[0], {"buy": []})
        self.inv["units"] = []
        lifecycle = {"units": {"missing": {"sku": ACE, "category": "furniture", "kind": "goods",
            "cost": 500000, "state": "missing", "missing_reason": "unconfirmed"}}, "archived": {}}
        self.assertEqual(self.plan(lifecycle=lifecycle)[0]["buy"], [{"sku": SIMMONS, "qty": 1}])

    def test_secondary_needs_proven_primary_plus_fresh_public_demand_and_capital(self):
        self.inv["units"] = [unit(unit=sku, sku=sku, category="furniture", cost=500000)
                             for sku in (ACE, SIMMONS)]
        self.assertEqual(self.plan()[0], {"buy": []})
        self.memory["sales"] = [self.sale(i) for i in range(3)]
        self.assertEqual(self.plan()[0]["buy"], [{"sku": LACLOUD, "qty": 1}])
        self.memory["sales"].append(self.sale(5, refunds=[{"amount": 10000}]))
        self.assertEqual(self.plan()[0], {"buy": []})

    def test_single_uncertain_supply_retry_is_bounded_and_never_erases_observed_loss(self):
        self.control[ACE]["planned_qty"] = 1
        kw = {"lifecycle_stats": {}, "held": {}, "next_window": True}
        retried = observe_control(self.control, self.memory, self.focus, **kw)
        self.assertEqual(retried[ACE]["retry_credit"], 1)
        retried[ACE]["planned_qty"] += 1
        again = observe_control(retried, self.memory, self.focus, **kw)
        self.assertEqual(again[ACE]["retry_credit"], 1)
        self.control = again
        self.assertNotIn(ACE, [b["sku"] for b in self.plan()[0]["buy"]])
        prior = deepcopy(retried)
        prior[ACE]["retry_credit"] = 0
        kw["lifecycle_stats"] = {ACE: {"observed_units": 1, "unresolved_cost": 500000}}
        self.assertEqual(observe_control(prior, self.memory, self.focus, **kw)[ACE]["retry_credit"], 0)

    def test_historical_sales_do_not_authorize_repeated_unconfirmed_supply(self):
        self.memory["sales"] = [self.sale(i) for i in range(3)]
        initialized = observe_control(None, self.memory, self.focus)
        self.assertEqual(initialized[ACE]["settled_qty"], 0)
        self.assertEqual(observe_control(initialized, self.memory, self.focus), initialized)

    def test_stock_pressure_prioritizes_scarcer_target_and_unknown_reverts_to_config(self):
        self.cfg["max_units_per_run"] = 1
        self.public["market_stock"] = {"round": 3, "observed_at": self.now, "by_sku": {
            ACE: {"units": 100, "shops": 10}, SIMMONS: {"units": 1, "shops": 1}}}
        self.assertEqual(self.plan()[0]["buy"], [{"sku": SIMMONS, "qty": 1}])
        self.cfg["max_units_per_run"] = 2
        self.assertEqual(self.plan()[0]["buy"], [{"sku": SIMMONS, "qty": 1}, {"sku": ACE, "qty": 1}])
        self.cfg["max_units_per_run"] = 1
        self.public["market_stock"]["observed_at"] -= 3601
        self.assertEqual(self.plan()[0]["buy"], [{"sku": ACE, "qty": 1}])

    def test_a_single_observed_loss_or_unknown_cost_stops_further_exposure(self):
        for changes in ({"price": 400000}, {"cost": None}, {"refunds": [{"amount": 1000}]}):
            self.memory["sales"] = [self.sale(1, **changes)]
            self.assertNotIn(ACE, [b["sku"] for b in self.plan()[0]["buy"]])

    def test_proven_depth_is_two_including_held_units_and_never_bypasses_money_cap(self):
        self.memory["sales"] = [self.sale(i) for i in range(3)]
        self.control[SIMMONS]["planned_qty"] = 1
        self.control[ACE]["planned_qty"] = 1
        self.inv["units"] = [unit(unit="held", sku=ACE, category="furniture", cost=340560, grade="C")]
        decision, detail = self.plan()
        self.assertEqual(decision["buy"], [{"sku": ACE, "qty": 1}])
        self.assertEqual(detail["depth_limits"][ACE], 2)
        self.inv["units"][0]["cost"] = 550000
        self.assertNotIn(ACE, [b["sku"] for b in self.plan()[0]["buy"]])

    def test_malformed_persistent_control_is_refused_not_reset_into_another_pilot(self):
        for previous in ({ACE: {"planned_qty": False}}, {"unknown": {}}, []):
            with self.assertRaises(ValueError):
                observe_control(previous, self.memory, self.focus)

    def test_confirmed_same_round_reset_rebases_but_missing_or_stale_epoch_cannot(self):
        self.control[ACE].update(planned_qty=10, settled_qty=9, last_ledger=2000000)
        prior, epoch = epoch_control(self.control, None, {**self.memory, "reset_at": self.now})
        self.assertIsNone(prior)
        fresh_memory = {**self.memory, "reset_at": self.now, "sales": [self.sale(0)]}
        fresh = observe_control(prior, fresh_memory, self.focus)
        self.assertEqual(fresh[ACE]["last_ledger"], 1)
        self.assertEqual(fresh[ACE]["planned_qty"], 0)
        fresh[ACE]["planned_qty"] = 1
        fresh_memory["sales"].append(self.sale(1))
        self.assertEqual(observe_control(fresh, fresh_memory, self.focus)[ACE]["settled_qty"], 1)
        self.assertEqual(epoch_control(fresh, epoch, self.memory), (fresh, epoch))
        with self.assertRaises(ValueError):
            epoch_control(fresh, epoch, {"reset_at": epoch - 1})


class FocusWindowTests(unittest.TestCase):
    def setUp(self):
        FocusTests.setUp(self)
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / ".market"
        atomic_json(self.root / "shop-policy.json", self.policy)
        atomic_json(self.md / "catalog.json", self.catalog)
        atomic_json(self.md / "demand.json", [{"category": "furniture"}])
        atomic_json(self.md / "inventory.json", self.inv)

    def test_prepare_once_before_server_opening_and_emit_once_while_open(self):
        opens = self.now + 120
        closed = {**self.inv, "restock": {"open": False, "opens_at": opens, "closes_at": None}}
        atomic_json(self.md / "inventory.json", closed)
        with patch("restock.refresh_market", return_value=self.public) as refresh:
            self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
            self.assertEqual(restock.run_once(self.root, self.now + 30), {"buy": []})
            refresh.assert_called_once()
            self.assertEqual(refresh.call_args.kwargs["deadline_seconds"], 8)
            atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
                "open": True, "opens_at": opens, "closes_at": opens + 180}})
            os.utime(self.md / "demand.json", (opens, opens))
            self.assertEqual(len(restock.run_once(self.root, opens)["buy"]), 2)
            self.assertIsNone(refresh.call_args.kwargs["opening_retry"])
            self.assertEqual(restock.run_once(self.root, opens + 60), {"buy": []})
        state = load_json(self.md / "restock-control.json")
        self.assertEqual(state["focused_supply"][ACE]["planned_qty"], 1)
        self.assertEqual(state["focused_supply"][ACE]["settled_qty"], 0)

    def test_failed_or_empty_preparation_requests_one_bounded_opening_retry(self):
        for preparation in (None, {**self.public, "events": []}):
            with self.subTest(preparation=preparation is None):
                control = self.md / "restock-control.json"
                if control.exists():
                    control.unlink()
                opens = self.now + 120
                atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
                    "open": False, "opens_at": opens, "closes_at": None}})
                with patch("restock.refresh_market", side_effect=[preparation, self.public]) as refresh:
                    self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
                    self.assertFalse(load_json(control)["prepared_demand_usable"])
                    atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
                        "open": True, "opens_at": opens, "closes_at": opens + 180}})
                    os.utime(self.md / "demand.json", (opens, opens))
                    self.assertEqual(len(restock.run_once(self.root, opens)["buy"]), 2)
                    self.assertEqual(refresh.call_args.kwargs["opening_retry"], opens)
                    self.assertEqual(restock.run_once(self.root, opens + 30), {"buy": []})
                    self.assertEqual(refresh.call_count, 2)

    def test_paid_stale_reservation_does_not_block_another_sku_supply_retry(self):
        original = observe_control(None, self.memory, self.focus)
        for sku in (ACE, SIMMONS):
            original[sku]["planned_qty"] = 1
        control = {"round": 3, "attempts": [], "focused_supply": original,
                   "last_window": {"opens_at": self.now - 1230, "closes_at": self.now - 1050}}
        atomic_json(self.md / "receipt-reservations.json", {"paid-other": "old-task"})
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                atomic_json(self.md / "restock-control.json", control)
                memory = {**self.memory, "sales": ([{"unit": "paid-other", "ledger": 10,
                    "sku": "Gother", "category": "other"}] if confirmed else [])}
                with patch("restock.refresh_market", return_value=self.public), \
                        patch("restock.update_sales", return_value=memory), \
                        patch("restock.update_stock", return_value={"units": {}, "archived": {}}), \
                        patch("restock.update_openings"):
                    result = restock.run_once(self.root, self.now)
                self.assertEqual(len(result["buy"]), 2 if confirmed else 0)
                self.assertEqual(load_json(self.md / "receipt-reservations.json"), {"paid-other": "old-task"})

    def test_late_opening_allows_only_one_line_with_ten_second_handoff(self):
        atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
            **self.inv["restock"], "closes_at": self.now + 25}})
        with patch("restock.refresh_market", return_value=self.public):
            self.assertEqual(restock.run_once(self.root, self.now)["buy"], [{"sku": ACE, "qty": 1}])

    def test_rechecks_live_cash_after_evidence_reads(self):
        def change_wallet(*args, **kwargs):
            atomic_json(self.md / "inventory.json", {**self.inv, "balance": 2000000})
            return self.public
        with patch("restock.refresh_market", side_effect=change_wallet):
            self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})


if __name__ == "__main__":
    unittest.main()
