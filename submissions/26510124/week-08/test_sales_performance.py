"""Actual own settlement prices and explicitly identified price rejections."""
import json
from pathlib import Path
import tempfile
import time
import unittest

import sales_performance as perf

ROOT = Path(__file__).resolve().parent


class OwnPriceTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.product = {"sku": "G8095ddd6", "grade": "A"}
        self.rows = [{"ledger": i + 1, "unit": f"unit-{i}", "sku": self.product["sku"],
                      "grade": "A", "warranty_hours": 6, "category": "furniture",
                      "price": 650000 + 10000 * i, "receipt_time": self.now - 120,
                      "settled_at": self.now - 90} for i in range(3)]

    def memory(self, rows=None, **updates):
        return {"schema": 1, "round": 3, "updated_at": self.now,
                "sales": self.rows if rows is None else rows, **updates}

    def price(self, memory=None):
        return perf.own_price(self.memory() if memory is None else memory, self.product, 6, 3, self.now)

    def test_exact_terms_median_deduplicates_unit_and_ledger(self):
        self.assertEqual(self.price()["price"], 660000)
        self.assertEqual(self.price()["samples"], 3)
        for key in ("ledger", "unit"):
            rows = [dict(s) for s in self.rows]
            rows[2][key] = rows[1][key]
            self.assertIsNone(self.price(self.memory(rows)))
        self.assertEqual(self.price(self.memory(self.rows[::-1]))["evidence_id"], self.price()["evidence_id"])

    def test_foreign_unknown_stale_or_unpaid_rows_never_make_a_third_observation(self):
        bad_rows = [dict(self.rows[2], grade="B"), dict(self.rows[2], sku="other"),
                    dict(self.rows[2], warranty_hours=None), dict(self.rows[2], warranty_hours=3),
                    dict(self.rows[2], warranty_hours=True), dict(self.rows[2], ledger=0),
                    dict(self.rows[2], settled_at=None), dict(self.rows[2], settled_at=self.now + 1),
                    dict(self.rows[2], settled_at=self.now - 86500),
                    dict(self.rows[2], receipt_time=self.now - 10),
                    dict(self.rows[2], refunds=[{"amount": 1}]), dict(self.rows[2], ambiguous_refund=True)]
        for bad in bad_rows:
            with self.subTest(bad=bad):
                self.assertIsNone(self.price(self.memory(self.rows[:2] + [bad])))
        for changes in ({"round": 2}, {"schema": 0}, {"updated_at": self.now - 86401},
                        {"reset_at": self.now - 100}, {"updated_at": self.now + 1}):
            self.assertIsNone(self.price(self.memory(**changes)))

    def test_only_receipts_matched_with_settlement_feed_own_price(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as temp:
            root = Path(temp)
            (root / "logs").mkdir()
            receipts = [{**s, "time": s["receipt_time"], "round": 3,
                         "outcome": "receipt_issued", "task": s["unit"]} for s in self.rows]
            for r in receipts:
                # Real receipt events never claim their own ledger confirmation.
                r.pop("ledger")
                r.pop("settled_at")
            (root / "logs/inventory-seller-events.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in receipts))
            empty = {"round": 3, "events": []}
            unpaid = perf.update(root, 3, self.now, public=empty)
            self.assertIsNone(self.price(unpaid))
            public = {"round": 3, "events": [
                {"seq": s["ledger"], "unit": s["unit"], "kind": "settle", "sku": s["sku"],
                 "shop": "https://ours.invalid", "ts": s["settled_at"], "price": s["price"],
                 "grade": s["grade"], "warranty_hours": s["warranty_hours"]} for s in self.rows]}
            matched = perf.update(root, 3, self.now, public=public)
            self.assertEqual(self.price(matched)["price"], 660000)
            self.assertEqual(self.price(matched)["samples"], 3)

    def test_funnel_only_explicit_price_rejections_can_drive_discounts(self):
        events = []
        cases = [("low", "rejected", "price_rejected"), ("legacy", "rejected", None),
                 ("policy", "rejected", "invalid_request"), ("unpaid", "receipt_issued", None),
                 ("lost", "expired", "negotiation_expired"), ("crash", "error", "model_error")]
        for task, outcome, reason in cases:
            opened = {"time": self.now - 60, "round": 3, "task": task, "sku": self.product["sku"],
                      "unit": task, "grade": "A", "warranty_hours": 6, "outcome": "opened"}
            events += [opened, {**opened, "outcome": "quote", "stage": "opening", "price": 690000},
                       {**opened, "outcome": outcome, "reason_code": reason}]
        result = perf.funnel(events, {}, 3, self.now)
        self.assertEqual(result["counts"]["rejected"], 3)
        group = result["groups"]["G8095ddd6:A:6"]
        self.assertEqual(group["rejected"], 1)
        self.assertEqual(group["unknown_exit"], 4)
        self.assertEqual(group["errors"], 1)


if __name__ == "__main__":
    unittest.main()
