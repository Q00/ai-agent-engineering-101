"""Supply openings and profitable SKU depth, with no model/network purchases."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from feedback import atomic_json, load_json
import restock
from shop_policy import policy_at
from test_inventory import unit

ROOT = Path(__file__).resolve().parent


class SupplyWindowTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / ".market"
        self.now = time.time()
        self.policy = policy_at()
        self.policy["restock"].update(max_units_per_run=5, max_units_per_sku=3,
            exploration_units=1, max_run_cost_percent=10, max_sku_cost_percent=10,
            max_category_cost_percent=25)
        self.policy["restock"]["focused_goods"] = {**self.policy["restock"]["focused_goods"], "enabled": False, "max_secondary_sku_cost_percent": 25}
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": [],
                    "restock": {"open": True, "opens_at": self.now - 30, "closes_at": self.now + 150}}
        self.catalog = {f"G{i:05}": {"name": str(i), "category": "goods", "kind": "goods", "list_price": 100000}
                        for i in range(1, 8)}
        self.demand = [{"category": "goods"}]
        atomic_json(self.root / "shop-policy.json", self.policy)
        atomic_json(self.md / "inventory.json", self.inv)
        atomic_json(self.md / "catalog.json", self.catalog)
        atomic_json(self.md / "demand.json", self.demand)
        self.control = self.md / "restock-control.json"
        self.log = self.root / "logs/restock-decisions.jsonl"
        no_network = patch("restock.refresh_market", return_value=None)
        self.network = no_network.start()
        self.addCleanup(no_network.stop)

    def status(self):
        return json.loads(self.log.read_text().splitlines()[-1])["status"]

    def test_closed_poll_is_cheap_and_does_not_hide_first_opening(self):
        closed = {**self.inv, "restock": {"open": False, "opens_at": self.now + 60, "closes_at": None}}
        atomic_json(self.md / "inventory.json", closed)
        atomic_json(self.control, {"round": 3, "attempts": [], "last_run": self.now - 5})
        for tick in (self.now, self.now + 20):
            self.assertEqual(restock.run_once(self.root, tick), {"buy": []})
        self.network.assert_not_called()
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        self.assertEqual(self.status(), "window_closed")
        self.assertEqual(load_json(self.control)["last_run"], self.now - 5)
        atomic_json(self.md / "inventory.json", self.inv)
        self.assertTrue(restock.run_once(self.root, self.now + 60)["buy"])
        self.network.assert_called_once()

    def test_missing_malformed_future_and_ended_openings_never_fetch(self):
        cases = [({}, "window_invalid"),
                 ({"open": 1}, "window_invalid"),
                 ({"open": True, "opens_at": "123", "closes_at": self.now + 100}, "window_invalid"),
                 ({"open": True, "opens_at": self.now + 100, "closes_at": self.now + 90}, "window_invalid"),
                 ({"open": True, "opens_at": self.now + 1, "closes_at": self.now + 100}, "window_closed"),
                 ({"open": True, "opens_at": self.now - 100, "closes_at": self.now}, "window_closed"),
                 ({"open": True, "opens_at": self.now - 100, "closes_at": self.now + 5}, "window_closing")]
        for window, status in cases:
            with self.subTest(window=window):
                atomic_json(self.md / "inventory.json", {**self.inv, "restock": window})
                self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
                self.assertEqual(self.status(), status)
                self.assertNotIn("last_window", load_json(self.control))
        self.network.assert_not_called()

    def test_restart_and_extended_same_opening_cannot_repeat_but_next_window_can(self):
        self.assertTrue(restock.run_once(self.root, self.now)["buy"])
        saved = load_json(self.control)["last_window"]
        extended = {**self.inv, "restock": {**self.inv["restock"], "closes_at": self.now + 1200}}
        atomic_json(self.md / "inventory.json", extended)
        self.assertEqual(restock.run_once(self.root, self.now + 901), {"buy": []})
        self.assertEqual(self.status(), "already_planned")
        self.assertEqual(load_json(self.control)["last_window"], saved)
        self.network.assert_called_once()
        # Even a server-scheduled opening less than the old 900s interval later
        # is independent, and bounded by the same hard cash/unit controls.
        later = self.now + 300
        atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
            "open": True, "opens_at": later - 30, "closes_at": later + 150}})
        os.utime(self.md / "demand.json", (later, later))
        self.assertTrue(restock.run_once(self.root, later)["buy"])
        self.assertEqual(self.network.call_count, 2)

    def test_network_elapsed_time_and_fresh_window_are_checked_before_emit(self):
        elapsed = [100.0]
        def delayed(*args, **kwargs):
            elapsed[0] += 151
            return None
        self.network.side_effect = delayed
        with patch("restock.time.monotonic", side_effect=lambda: elapsed[0]):
            self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
        self.assertEqual(self.status(), "window_closed")
        self.assertNotIn("last_window", load_json(self.control))
        self.network.side_effect = lambda *args, **kwargs: atomic_json(self.md / "inventory.json", {
            **self.inv, "restock": {"open": False}})
        self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
        self.assertEqual(self.status(), "window_closed")

    def test_changed_round_or_opening_is_not_used_with_previous_evidence(self):
        for update in ({"round": 4}, {"restock": {**self.inv["restock"], "opens_at": self.now - 10}}):
            atomic_json(self.md / "inventory.json", self.inv)
            self.network.side_effect = lambda *args, change=update, **kwargs: atomic_json(
                self.md / "inventory.json", {**self.inv, **change})
            self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
            self.assertEqual(self.status(), "window_changed")
            self.assertNotIn("last_window", load_json(self.control))

    def test_late_opening_limits_lines_to_remaining_ten_second_supply_slots(self):
        atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
            **self.inv["restock"], "closes_at": self.now + 35}})
        decision = restock.run_once(self.root, self.now)
        self.assertEqual(len(decision["buy"]), 2)
        self.assertEqual(sum(row["qty"] for row in decision["buy"]), 2)
        evidence = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual((evidence["window_unit_cap"], evidence["supply_lines"]), (2, 2))

    def test_planning_latency_cannot_emit_more_lines_than_remaining_window(self):
        atomic_json(self.md / "inventory.json", {**self.inv, "restock": {
            **self.inv["restock"], "closes_at": self.now + 35}})
        elapsed = [100.0]
        original = restock.plan
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            elapsed[0] += 20
            return result
        with patch("restock.time.monotonic", side_effect=lambda: elapsed[0]), patch("restock.plan", side_effect=delayed):
            self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
        self.assertEqual(self.status(), "window_closing")
        self.assertNotIn("last_window", load_json(self.control))

    def test_emit_is_reserved_even_if_logging_crashes_and_old_window_cannot_replay(self):
        with patch("restock.append_event", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                restock.run_once(self.root, self.now)
        self.assertIn("last_window", load_json(self.control))
        self.assertEqual(restock.run_once(self.root, self.now), {"buy": []})
        self.assertEqual(self.status(), "already_planned")
        self.network.assert_called_once()

    def test_malformed_dedup_record_does_not_reset_spending_protection(self):
        atomic_json(self.control, {"round": 3, "attempts": [], "last_window": {"opens_at": True, "closes_at": 0}})
        with self.assertRaisesRegex(ValueError, "window control"):
            restock.run_once(self.root, self.now)
        self.network.assert_not_called()


class ProvenDepthTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.policy = policy_at()
        self.policy["restock"].update(max_units_per_run=5, max_units_per_sku=3,
            exploration_units=1, max_run_cost_percent=10, max_sku_cost_percent=10,
            max_category_cost_percent=25)
        self.policy["restock"]["focused_goods"] = {**self.policy["restock"]["focused_goods"], "enabled": False, "max_secondary_sku_cost_percent": 25}
        self.inv = {"round": 3, "balance": 5000000, "start": 5000000, "units": []}
        self.catalog = {"G00001": {"name": "goods", "kind": "goods", "list_price": 100000, "category": "goods"}}
        self.sales = [self.sale(i) for i in range(3)]

    def sale(self, idx, **changes):
        return {"ledger": idx + 1, "unit": str(idx), "sku": "G00001", "category": "goods",
                "kind": "goods", "grade": "A", "cost": 70000, "price": 85000, "warranty_hours": 6,
                "supplied_at": self.now - 8 * 3600, "settled_at": self.now - 7 * 3600, **changes}

    def plan(self):
        return restock.plan(self.inv, self.catalog, [{"category": "goods"}], {"sales": self.sales}, [],
                            self.policy["restock"], self.now, horizon=self.policy["horizon"])

    def quantity(self):
        return sum(row["qty"] for row in self.plan()[0]["buy"])

    def test_mature_profit_keeps_two_unit_depth_with_new_warranty_exposure(self):
        self.assertEqual(self.quantity(), 3)
        self.sales.append(self.sale(4, settled_at=self.now - 60))
        self.assertEqual(self.quantity(), 2)
        self.inv["units"] = [unit(unit="held", sku="G00001", category="goods", supplied_at=self.now - 10)]
        self.assertEqual(self.quantity(), 1)  # Two including the currently held unit.
        self.inv["units"].append(unit(unit="held2", sku="G00001", category="goods", supplied_at=self.now - 10))
        self.assertEqual(self.quantity(), 0)

    def test_pending_only_unknown_cost_and_observed_refund_never_authorize_depth(self):
        self.sales = [self.sale(i, settled_at=self.now - 60) for i in range(10)]
        self.assertEqual(self.quantity(), 1)
        self.sales = [self.sale(i) for i in range(3)] + [self.sale(4, cost=None)]
        self.assertEqual(self.quantity(), 1)
        self.sales = [self.sale(i) for i in range(3)] + [self.sale(4, refunds=[{"amount": 1000}])]
        self.assertEqual(self.quantity(), 1)
        self.sales = [self.sale(i, price=60000) for i in range(3)]
        self.assertEqual(self.quantity(), 0)

    def test_depth_still_obeys_food_cash_exposure_and_total_stock_caps(self):
        self.inv["balance"] = 2000077
        self.assertEqual(self.quantity(), 0)
        self.inv["balance"] = 5000000
        self.catalog["G00001"]["list_price"] = 500000
        self.assertEqual(self.quantity(), 0)  # Maximum surge now exceeds the old 500k cap.
        self.catalog["G00001"]["list_price"] = 100000
        self.inv["units"] = [unit(unit=str(i), sku="G00009", category="other") for i in range(self.policy["restock"]["max_stock_units"] - 1)]
        self.assertEqual(self.quantity(), 1)
        self.inv["units"] = []
        self.catalog["G00001"]["kind"] = "food"
        self.sales = [self.sale(i, kind="food") for i in range(3)]
        self.assertEqual(self.quantity(), 1)
