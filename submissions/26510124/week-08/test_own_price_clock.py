"""Recent own prices use confirmed market time without moving accounting clocks."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import opening_strategy
import sales_performance as perf
from shop_policy import policy_at
from test_inventory import unit

ROOT = Path(__file__).resolve().parent


class OwnPriceClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.market_now = 1793929000
        self.local_now = self.market_now + 775.9
        self.product = unit(sku="G8095ddd6", grade="A", cost=540000, list_price=851400)
        self.cases = 0

    def memory(self, *, kit_confirmation=True, change_public=None):
        folder = self.root / str(self.cases)
        self.cases += 1
        (folder / "logs").mkdir(parents=True)
        receipts = [{"round": 3, "outcome": "receipt_issued", "task": f"t{i}",
                     "unit": f"u{i}", "sku": self.product["sku"], "category": "furniture",
                     "time": self.local_now - 90, "price": 650000 + 10000 * i,
                     "kind": "goods", "grade": "A", "warranty_hours": 6,
                     "cost": 540000, "supplied_at": self.market_now - 3600}
                    for i in range(3)]
        (folder / "logs/inventory-seller-events.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in receipts))
        if kit_confirmation:
            (folder / "logs/market-kit.log").write_text("".join(
                f"10:00:00 [acp] settled G8095ddd6 at {r['price']}, ledger #{i+1}, unit {r['unit']} grade A\n"
                for i, r in enumerate(receipts)))
        events = [{"seq": i+1, "kind": "settle", "unit": r["unit"], "sku": r["sku"],
                   "shop": "https://ours.invalid", "price": r["price"], "ts": self.market_now - 60,
                   "grade": "A", "warranty_hours": 6} for i, r in enumerate(receipts)]
        if change_public:
            events[-1].update(change_public)
        memory = perf.update(folder, 3, self.local_now, public={"round": 3, "events": events})
        return folder, memory

    def price(self, memory, **kwargs):
        return perf.own_price(memory, self.product, 6, 3, self.local_now, **kwargs)

    def test_confirmed_market_time_preserves_local_accounting_and_warranty_maturity(self):
        _, memory = self.memory()
        self.assertEqual(len(memory["sales"]), 3)
        for sale in memory["sales"]:
            self.assertEqual(sale["market_settled_at"], self.market_now - 60)
            self.assertEqual(sale["receipt_time"], self.local_now - 90)
            self.assertEqual(sale["observed_at"], self.local_now)
            self.assertNotIn("settled_at", sale)
            before = {k: v for k, v in sale.items() if k != "market_settled_at"}
            self.assertEqual(perf.economics(sale, self.local_now), perf.economics(before, self.local_now))
            self.assertEqual(perf.economics(sale, self.local_now + 6 * 3600 - 1)["status"], "pending_warranty")
            self.assertEqual(perf.economics(sale, self.local_now + 6 * 3600)["status"], "mature_estimate")
        self.assertIsNone(self.price(memory))
        self.assertEqual(self.price(memory, market_now=self.market_now)["price"], 660000)

    def test_public_timestamp_never_bypasses_exact_confirmed_identity(self):
        _, unpaid = self.memory(kit_confirmation=False)
        self.assertEqual(unpaid["sales"], [])
        for change in ({"seq": 99}, {"unit": "other"}, {"sku": "other"}, {"price": 1}):
            with self.subTest(change=change):
                _, memory = self.memory(change_public=change)
                self.assertEqual(sum("market_settled_at" in s for s in memory["sales"]), 2)
                self.assertIsNone(self.price(memory, market_now=self.market_now))

    def test_new_price_path_keeps_terms_duplicate_refund_round_and_reset_guards(self):
        _, memory = self.memory()
        changes = ({"grade": "B"}, {"warranty_hours": 3}, {"warranty_hours": True},
                   {"ledger": 0}, {"ledger": 1}, {"unit": "u0"}, {"sku": "other"},
                   {"refunds": [{"amount": 1}]}, {"ambiguous_refund": True})
        for change in changes:
            with self.subTest(change=change):
                invalid = deepcopy(memory)
                invalid["sales"][-1].update(change)
                self.assertIsNone(self.price(invalid, market_now=self.market_now))
        for change in ({"round": 2}, {"reset_at": self.local_now - 30}):
            self.assertIsNone(self.price({**memory, **change}, market_now=self.market_now))

    def test_recent_market_time_has_bounded_future_tolerance_and_expiry(self):
        _, memory = self.memory()
        for stamp in (self.market_now + 6, self.market_now - 86401, 0):
            with self.subTest(stamp=stamp):
                invalid = deepcopy(memory)
                invalid["sales"][-1]["market_settled_at"] = stamp
                self.assertIsNone(self.price(invalid, market_now=self.market_now))
        within_transport_error = deepcopy(memory)
        within_transport_error["sales"][-1]["market_settled_at"] = self.market_now + 3
        self.assertEqual(self.price(within_transport_error, market_now=self.market_now)["samples"], 3)

    def test_opening_uses_market_timestamp_only_with_a_synced_clock(self):
        for status in ("synced", "missing", "stale", "clock_changed", "invalid", "round_mismatch"):
            with self.subTest(status=status):
                folder, memory = self.memory()
                with patch("opening_strategy.schedule_now", return_value={"status": status, "now": self.market_now}):
                    result = opening_strategy.update(folder, {"round": 3, "units": [self.product]},
                                                     memory, None, policy_at(), self.local_now)
                entry = result["entries"]["G8095ddd6:A:6"]
                self.assertEqual(entry["source"], "own_settled_sales" if status == "synced" else "manual_pilot_policy")
                self.assertEqual(entry["target"], 660000 if status == "synced" else 690000)

    def test_legacy_valid_timestamps_work_without_market_clock(self):
        _, memory = self.memory()
        for sale in memory["sales"]:
            sale.pop("market_settled_at")
            sale["settled_at"] = self.local_now - 60
        self.assertEqual(self.price(memory)["price"], 660000)
        # A trusted market clock does not invent a timestamp for legacy rows.
        self.assertEqual(self.price(memory, market_now=self.market_now)["price"], 660000)
