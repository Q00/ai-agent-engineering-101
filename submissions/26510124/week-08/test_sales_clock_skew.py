"""Confirmed ledger identity survives clock skew; local maturity stays cautious."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from feedback import atomic_json
import sales_performance as perf

ROOT = Path(__file__).resolve().parent


class SalesClockSkewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 2000000000.0
        self.skew = 775.9
        self.sale = {"ledger": 100, "unit": "u-confirmed", "sku": "G8095ddd6",
                     "category": "furniture", "price": 650000,
                     "receipt_time": self.now - 50, "observed_at": self.now - 20,
                     "cost": 500000, "kind": "goods", "grade": "A", "warranty_hours": 6,
                     "supplied_at": self.now - self.skew - 3600}
        self.settle = {"seq": 100, "kind": "settle", "unit": self.sale["unit"],
                       "sku": self.sale["sku"], "price": self.sale["price"],
                       "shop": "https://our-shop.invalid", "ts": self.now - self.skew - 30}
        self.broken = {"seq": 101, "kind": "broken", "unit": self.sale["unit"],
                       "sku": self.sale["sku"], "shop": self.settle["shop"],
                       "refunded": self.sale["price"], "ts": self.settle["ts"] + 10}
        self.save()

    def save(self, sale=None):
        atomic_json(self.root / ".market/sales-performance.json",
                    {"schema": 1, "round": 3, "sales": [deepcopy(sale or self.sale)]})

    def update(self, events=None, now=None):
        result = perf.update(self.root, 3, self.now if now is None else now,
                             public={"round": 3, "events": [self.settle, self.broken]
                                     if events is None else events})
        return result["sales"][0]

    def test_immediate_refund_matches_confirmed_unit_despite_775_second_clock_skew(self):
        sale = self.update()
        self.assertEqual(sale["shop"], self.settle["shop"])
        self.assertEqual(sale["refunds"], [{"seq": 101, "amount": 650000, "ts": self.broken["ts"]}])
        self.assertEqual(perf.economics(sale, self.now)["observed_refund"], 650000)
        self.assertNotIn("settled_at", sale)
        self.assertEqual(sale["observed_at"], self.sale["observed_at"])

    def test_earlier_or_equal_ledger_refund_is_ignored_even_with_later_timestamp(self):
        for sequence in (99, 100):
            self.save()
            sale = self.update([self.settle, {**self.broken, "seq": sequence, "ts": self.now + 20}])
            self.assertEqual(sale["refunds"], [])

    def test_other_unit_or_sku_does_not_receive_this_refund(self):
        for changes in ({"unit": "u-other"}, {"sku": "G00000000"}):
            self.save()
            sale = self.update([self.settle, {**self.broken, **changes}])
            self.assertEqual(sale["refunds"], [])
            self.assertNotIn("ambiguous_refund", sale)

    def test_exact_confirmed_settlement_identity_required_for_shop_enrichment(self):
        for changes in ({"seq": 102}, {"unit": "u-other"}, {"unit": None},
                        {"sku": "G00000000"}, {"price": 649999}):
            self.save()
            sale = self.update([{**self.settle, **changes}])
            self.assertNotIn("shop", sale)
            self.assertNotIn("settled_at", sale)

    def test_duplicate_refund_is_idempotent_across_updates(self):
        first = self.update([self.settle, self.broken, dict(self.broken)])
        second = self.update([self.settle, self.broken, dict(self.broken)])
        self.assertEqual(first["refunds"], second["refunds"])
        self.assertEqual(len(second["refunds"]), 1)
        self.assertEqual(perf.economics(second, self.now)["observed_refund"], 650000)

    def test_missing_unit_marks_only_known_shop_ambiguous_without_assigning_money(self):
        no_unit = {k: v for k, v in self.broken.items() if k != "unit"}
        sale = self.update([self.settle, no_unit])
        self.assertTrue(sale["ambiguous_refund"])
        self.assertEqual(sale["refunds"], [])
        for changes in ({"shop": "https://other.invalid"}, {"seq": 99}):
            self.save()
            sale = self.update([self.settle, {**no_unit, **changes}])
            self.assertNotIn("ambiguous_refund", sale)
            self.assertEqual(sale["refunds"], [])
        self.save()
        self.assertNotIn("ambiguous_refund", self.update([no_unit]))

    def test_clock_skew_does_not_advance_warranty_maturity_or_create_price_evidence(self):
        sale = self.update([self.settle])
        prematurely_mature = self.settle["ts"] + 6 * 3600 + 1
        self.assertLess(prematurely_mature, self.sale["observed_at"] + 6 * 3600)
        self.assertEqual(perf.economics(sale, prematurely_mature)["status"], "pending_warranty")
        self.assertEqual(perf.economics(sale, self.sale["observed_at"] + 6 * 3600)["status"],
                         "mature_estimate")
        rows = [{**sale, "unit": f"unit-{i}", "ledger": i + 100} for i in range(3)]
        memory = {"schema": 1, "round": 3, "updated_at": self.now, "sales": rows}
        self.assertIsNone(perf.own_price(memory, {"sku": sale["sku"], "grade": "A"}, 6, 3, self.now))

    def test_normal_clock_still_adds_settlement_time_and_refund(self):
        sale = self.update([{**self.settle, "ts": self.now - 30},
                            {**self.broken, "ts": self.now - 20}])
        self.assertEqual(sale["settled_at"], self.now - 30)
        self.assertEqual(sale["shop"], self.settle["shop"])
        self.assertEqual(sale["refunds"][0]["amount"], 650000)

    def test_unconfirmed_public_admission_is_not_relaxed_by_clock_fix(self):
        atomic_json(self.root / ".market/sales-performance.json", {"schema": 1, "round": 3, "sales": []})
        (self.root / "logs").mkdir()
        import json
        receipt = {**self.sale, "time": self.sale["receipt_time"], "round": 3,
                   "outcome": "receipt_issued", "task": "unconfirmed-task"}
        (self.root / "logs/inventory-seller-events.jsonl").write_text(json.dumps(receipt) + "\n")
        result = perf.update(self.root, 3, self.now, public={"round": 3, "events": [self.settle]})
        self.assertEqual(result["sales"], [])


if __name__ == "__main__":
    unittest.main()
