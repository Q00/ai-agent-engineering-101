"""Actual-unit accounting, uncertainty, restart dedup and bounded memory."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import stock_lifecycle as lifecycle


class StockLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 1800000000
        self.inv = {"round": 3, "start": 5000000, "balance": 4900000, "injected": 0, "units": []}
        self.sales = {"round": 3, "sales": []}

    def unit(self, uid="u1", **updates):
        return {"unit": uid, "sku": "F00001", "category": "food", "kind": "food", "cost": 100,
                "supplied_at": self.now - 60, "expires_at": self.now + 30, **updates}

    def sale(self, **updates):
        return {**self.unit(), "ledger": 10, "price": 120, "receipt_time": self.now + 5, **updates}

    def update(self, offset=0):
        state = lifecycle.update(self.root, self.inv, self.sales, self.now + offset)
        return state, lifecycle.metrics(state, self.now + offset)

    def test_expired_held_food_is_risk_not_disposal_and_missing_is_uncertain(self):
        self.inv["units"] = [self.unit()]
        _, stats = self.update()
        self.assertEqual(stats["F00001"]["held_expiring_cost"], 100)
        _, stats = self.update(31)
        self.assertEqual(stats["F00001"]["held_expired_cost"], 100)
        self.assertEqual(stats["F00001"]["unresolved_cost"], 0)
        self.inv["units"] = []
        state, stats = self.update(40)
        self.assertEqual(state["units"]["u1"]["state"], "missing")
        self.assertEqual(stats["F00001"]["expired_unconfirmed_cost"], 100)
        self.assertEqual(stats["F00001"]["unresolved_cost"], 100)
        self.assertEqual(stats["F00001"]["held_cost"], 0)
        self.assertEqual(stats["F00001"]["settled_count"], 0)
        raw = (self.root / "logs/stock-lifecycle.jsonl").read_text()
        self.assertNotIn("spoiled", raw)
        self.assertIn("missing_unconfirmed", raw)

    def test_late_settlement_reverses_estimate_without_double_charging_on_restart(self):
        self.inv["units"] = [self.unit()]
        self.update()
        self.inv["units"] = []
        self.update(40)
        self.sales["sales"] = [self.sale()]
        _, stats = self.update(60)
        g = stats["F00001"]
        self.assertEqual((g["observed_units"], g["observed_purchase_cost"]), (1, 100))
        self.assertEqual((g["unresolved_cost"], g["expired_unconfirmed_cost"]), (0, 0))
        self.assertEqual((g["settled_count"], g["settled_cost"]), (1, 100))
        self.sales["sales"] = []  # Bounded sales window/log tail no longer contains the sale.
        _, again = self.update(8 * 86400)
        self.assertEqual(again, stats)
        events = [json.loads(x) for x in (self.root / "logs/stock-lifecycle.jsonl").read_text().splitlines()]
        self.assertEqual(sum(e["event"] == "settlement_confirmed" for e in events), 1)

    def test_missing_goods_and_balance_changes_never_imply_a_sale(self):
        self.inv["units"] = [self.unit(kind="goods", expires_at=None)]
        self.update()
        self.inv.update(units=[], balance=4999999)
        _, stats = self.update(40)
        self.assertEqual(stats["F00001"]["settled_count"], 0)
        self.assertEqual(stats["F00001"]["unresolved_cost"], 100)
        self.assertEqual(stats["F00001"]["expired_unconfirmed_cost"], 0)

    def test_injection_disappearance_is_possible_seizure_not_expiry(self):
        self.inv["units"] = [self.unit()]
        self.update()
        self.inv.update(units=[], injected=4500000)
        state, stats = self.update(40)
        self.assertEqual(state["units"]["u1"]["missing_reason"], "reset_or_seizure_suspected")
        self.assertEqual(stats["F00001"]["seized_suspected_cost"], 100)
        self.assertEqual(stats["F00001"]["expired_unconfirmed_cost"], 0)
        self.assertEqual(stats["F00001"]["unresolved_cost"], 100)

    def test_reappearance_clears_missing_without_creating_second_purchase(self):
        unit = self.unit()
        self.inv["units"] = [unit]
        self.update()
        self.inv["units"] = []
        self.update(10)
        self.inv["units"] = [unit]
        _, stats = self.update(20)
        self.assertEqual(stats["F00001"]["observed_units"], 1)
        self.assertEqual(stats["F00001"]["held_cost"], 100)
        self.assertEqual(stats["F00001"]["unresolved_cost"], 0)

    def test_duplicate_actual_units_and_conflicting_settlements_refuse_without_write(self):
        self.inv["units"] = [self.unit()]
        self.update()
        path = self.root / ".market/stock-lifecycle.json"
        before = path.read_bytes()
        self.inv["units"].append(self.unit())
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.update(10)
        self.assertEqual(path.read_bytes(), before)
        self.inv["units"] = []
        self.sales["sales"] = [self.sale(cost=99)]
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.update(10)
        self.assertEqual(path.read_bytes(), before)
        self.sales["sales"] = [self.sale(sku="OTHER")]
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.update(10)

    def test_receipt_missing_cost_cannot_clear_unresolved_and_duplicate_sale_is_once(self):
        self.inv["units"] = [self.unit()]
        self.update()
        self.inv["units"] = []
        self.sales["sales"] = [self.sale(cost=None)]
        _, stats = self.update(40)
        self.assertEqual(stats["F00001"]["unresolved_cost"], 100)
        self.sales["sales"] = [self.sale(), self.sale()]
        _, stats = self.update(50)
        self.assertEqual(stats["F00001"]["settled_count"], 1)

    def test_sale_between_snapshots_is_observed_once_even_food_without_expiry(self):
        sale = self.sale()
        sale.pop("expires_at")
        self.sales["sales"] = [sale]
        _, stats = self.update(10)
        self.assertEqual(stats["F00001"]["observed_purchase_cost"], 100)
        self.assertEqual(stats["F00001"]["settled_count"], 1)
        _, again = self.update(20)
        self.assertEqual(stats, again)
        # A lagging inventory file cannot reopen an already-settled unit.
        self.inv["units"] = [self.unit()]
        _, again = self.update(25)
        self.assertEqual(stats, again)
        self.inv["units"] = []
        self.sales["sales"] = [sale, self.sale(unit="u2")]
        with self.assertRaisesRegex(ValueError, "ledger identity"):
            self.update(30)

    def test_confirmed_sale_beats_lagging_inventory_snapshot(self):
        self.inv["units"] = [self.unit()]
        self.update()
        self.sales["sales"] = [self.sale()]
        _, stats = self.update(10)
        self.assertEqual(stats["F00001"]["held_cost"], 0)
        self.sales["sales"] = []
        _, stats = self.update(20)
        self.assertEqual(stats["F00001"]["settled_count"], 1)

    def test_round_and_explicit_reset_exclude_cached_old_observations(self):
        self.inv["units"] = [self.unit()]
        self.update()
        self.inv.update(round=4, units=[])
        self.sales.update(round=4, sales=[])
        _, stats = self.update(10)
        self.assertEqual(stats, {})
        self.inv.update(round=3)
        self.sales["round"] = 3
        with self.assertRaisesRegex(ValueError, "Stale inventory round"):
            self.update(15)
        self.inv.update(round=4, units=[self.unit()])
        self.sales.update(round=4, reset_at=self.now + 20, sales=[self.sale()])
        _, stats = self.update(30)
        self.assertEqual(stats, {})
        self.inv["units"] = [self.unit("fresh", supplied_at=self.now + 21)]
        self.sales.pop("reset_at")
        state, stats = self.update(40)
        self.assertEqual(state["reset_at"], self.now + 20)
        self.assertEqual(stats["F00001"]["observed_units"], 1)

    def test_optional_injection_and_invalid_or_oversized_memory_fail_closed(self):
        self.inv.pop("injected")
        self.inv["units"] = [self.unit()]
        self.update()
        self.update(10)
        path = self.root / ".market/stock-lifecycle.json"
        for raw in (b"{broken", b"{}", b"x" * (lifecycle.MAX_BYTES + 1)):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.update(20)
            self.assertEqual(path.read_bytes(), raw)

    def test_capacity_refuses_without_pruning_unresolved_or_rewriting_logs(self):
        self.inv["units"] = [self.unit()]
        self.update()
        self.inv["units"] = []
        self.update(40)
        path = self.root / ".market/stock-lifecycle.json"
        log = self.root / "logs/stock-lifecycle.jsonl"
        state_before, events_before = path.read_bytes(), log.read_bytes()
        self.inv["units"] = [self.unit("u2")]
        with patch.object(lifecycle, "MAX_UNITS", 1), self.assertRaisesRegex(ValueError, "capacity"):
            self.update(50)
        self.assertEqual(path.read_bytes(), state_before)
        self.assertEqual(log.read_bytes(), events_before)

    def recent_sale(self, index):
        stamp = self.now + index * 10
        return self.sale(unit=f"sold-{index:03}", ledger=100 + index, expires_at=stamp + 1000,
                         supplied_at=stamp, receipt_time=stamp + 1)

    def test_unseen_sale_with_impossible_chronology_cannot_poison_saved_state(self):
        self.update()
        path = self.root / ".market/stock-lifecycle.json"
        before = path.read_bytes()
        self.sales["sales"] = [self.sale(supplied_at=self.now - 10, receipt_time=self.now - 20)]
        with self.assertRaisesRegex(ValueError, "predates acquisition"):
            self.update(30)
        self.assertEqual(path.read_bytes(), before)
        self.sales["sales"] = [self.sale()]
        _, stats = self.update(40)
        self.assertEqual(stats["F00001"]["settled_count"], 1)

    def test_long_run_rolls_up_settled_rows_but_preserves_totals_and_restart_dedup(self):
        with patch.object(lifecycle, "MAX_UNITS", 4), patch.object(lifecycle, "KEEP_SETTLED", 2):
            receipts = []
            for index in range(30):
                receipts.append(self.recent_sale(index))
                self.sales["sales"] = receipts[-3:]
                state, stats = self.update(index * 10 + 2)
                self.assertLessEqual(len(state["units"]), 2)
                self.assertEqual(stats["F00001"]["settled_count"], index + 1)
                self.assertEqual(stats["F00001"]["observed_purchase_cost"], (index + 1) * 100)
            self.assertEqual(state["archived"]["F00001"]["count"], 28)
            watermark = state["archive_watermark"]
            self.sales["sales"] = receipts  # Replay every archived sale after reading disk again.
            repeated, again = self.update(400)
            self.assertEqual(again, stats)
            self.assertEqual(repeated["archive_watermark"], watermark)
            self.assertLess((self.root / ".market/stock-lifecycle.json").stat().st_size, lifecycle.MAX_BYTES)
            events = [json.loads(x) for x in (self.root / "logs/stock-lifecycle.jsonl").read_text().splitlines()]
            self.assertEqual(sum(e["units"] for e in events if e["event"] == "settled_rollup"), 28)

    def test_old_missing_unit_survives_compaction_and_late_settlement_below_watermark(self):
        with patch.object(lifecycle, "MAX_UNITS", 4), patch.object(lifecycle, "KEEP_SETTLED", 2):
            self.inv["units"] = [self.unit()]
            self.update()
            self.inv["units"] = []
            for index in range(1, 12):
                self.sales["sales"] = [self.recent_sale(index)]
                state, stats = self.update(index * 10 + 2)
                self.assertEqual(state["units"]["u1"]["state"], "missing")
                self.assertEqual(stats["F00001"]["unresolved_cost"], 100)
            before = stats["F00001"]["observed_purchase_cost"]
            self.assertGreater(state["archive_watermark"][0], self.sale()["receipt_time"])
            self.sales["sales"] = [self.sale()]
            _, stats = self.update(150)
            self.assertEqual(stats["F00001"]["settled_count"], 12)
            self.assertEqual(stats["F00001"]["unresolved_cost"], 0)
            self.assertEqual(stats["F00001"]["observed_purchase_cost"], before)
            _, again = self.update(160)
            self.assertEqual(stats, again)

    def test_archived_reappearing_inventory_fails_closed_instead_of_new_purchase(self):
        with patch.object(lifecycle, "KEEP_SETTLED", 1):
            self.sales["sales"] = [self.recent_sale(1), self.recent_sale(2)]
            state, _ = self.update(30)
            self.assertNotIn("sold-001", state["units"])
            path = self.root / ".market/stock-lifecycle.json"
            before = path.read_bytes()
            self.inv["units"] = [self.recent_sale(1)]
            with self.assertRaisesRegex(ValueError, "may be archived stock"):
                self.update(40)
            self.assertEqual(path.read_bytes(), before)

    def test_legacy_settled_rows_wait_for_receipt_time_before_rollup(self):
        self.sales["sales"] = [self.recent_sale(1), self.recent_sale(2)]
        state, _ = self.update(30)
        for row in state["units"].values():
            row.pop("receipt_time")
        path = self.root / ".market/stock-lifecycle.json"
        path.write_text(json.dumps(state))
        self.sales["sales"] = []
        with patch.object(lifecycle, "KEEP_SETTLED", 1):
            state, _ = self.update(40)
            self.assertEqual(len(state["units"]), 2)
            self.assertNotIn("archived", state)
            self.sales["sales"] = [self.recent_sale(1)]
            state, stats = self.update(50)
            self.assertEqual(state["archived"]["F00001"]["count"], 1)
            self.assertEqual(stats["F00001"]["settled_count"], 2)


if __name__ == "__main__":
    unittest.main()
