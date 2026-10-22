"""Boundary and integrity checks using synthetic memory only."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from strategy_metrics import compare, timestamp


class StrategyMetricsTests(unittest.TestCase):
    def setUp(self):
        cache = BASE / ".market"
        cache.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.seller = self.directory / "seller-memory.jsonl"
        self.buyer = self.directory / "buyer-memory.json"
        self.seller.write_text("", encoding="utf-8")
        self.buyer.write_text("[]", encoding="utf-8")
        self.now = timestamp("2026-10-22T02:00:00Z")

    def save(self, seller=(), buyer=()):
        self.seller.write_text("".join(json.dumps(r) + "\n" for r in seller), encoding="utf-8")
        self.buyer.write_text(json.dumps(list(buyer)), encoding="utf-8")

    def test_seller_cohort_uses_latest_outcome_and_keeps_pending_in_denominator(self):
        def row(task, time, outcome="open", amount=None):
            return {"task_id": task, "item": "lamp", "started_at": time,
                    "outcome": outcome, "sale_price": amount}
        previous = row("old", "2026-10-22T00:30:00Z")
        self.save(seller=[previous, row("new", "2026-10-22T01:00:00Z"),
                          row("pending", "2026-10-22T01:50:00Z"),
                          {**previous, "outcome": "deal", "sale_price": 120,
                           "updated_at": "2026-10-22T01:45:00Z"},
                          row("new", "2026-10-22T01:00:00Z", "deal", 100),
                          row("end", "2026-10-22T02:00:00Z", "deal", 110)])
        result = compare(self.directory, self.now)
        recent = result["current"]["seller"]["total"]
        prior = result["previous"]["seller"]["total"]
        self.assertEqual(recent, {"inquiries": 2, "sales": 1, "average_sale_price": 100,
                                  "completion_rate": 0.5, "pending": 1})
        self.assertEqual(prior["sales"], 1)
        self.assertEqual(prior["inquiries"], 1)
        self.assertEqual(result["change"]["seller"]["total"]["average_sale_price"], -20)

    def test_buyer_time_boundaries_and_invalid_receipts(self):
        def row(time, outcome="deal", amount=100):
            return {"item": "lamp", "time": time, "outcome": outcome, "price": amount}
        self.save(buyer=[row("2026-10-22T00:00:00Z", amount=110),
                         row("2026-10-22T00:59:59Z", "no_deal", None),
                         row("2026-10-22T10:00:00+09:00"),
                         row("2026-10-22T01:20:00Z", "invalid_receipt", 999),
                         row("2026-10-22T02:00:00Z")])
        result = compare(self.directory, self.now)
        current = result["current"]["buyer"]["total"]
        self.assertEqual(current, {"attempts": 2, "recorded_deals": 1,
                                   "average_purchase_price": 100, "success_rate": 0.5,
                                   "invalid_receipts": 1})
        self.assertEqual(result["previous"]["buyer"]["total"]["attempts"], 2)
        self.assertIsNone(result["current"]["seller"]["total"]["completion_rate"])

    def test_incomplete_line_is_reported_without_editing_memory(self):
        self.seller.write_text('{"task_id":', encoding="utf-8")
        self.buyer.write_text('[{"time":', encoding="utf-8")
        before = [p.read_bytes() for p in (self.seller, self.buyer)]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = compare(self.directory, self.now)
        self.assertEqual(result["data_quality"]["seller"]["omitted_rows"], 1)
        self.assertEqual(result["data_quality"]["buyer"], {"omitted_rows": None, "file_errors": 1})
        self.assertIn("invalid/incomplete JSON at line 1", stderr.getvalue())
        self.assertEqual(before, [p.read_bytes() for p in (self.seller, self.buyer)])

    def test_cli_emits_only_json_and_invalid_row_does_not_crash(self):
        self.save(buyer=[{"item": "chair", "time": "bad", "outcome": "deal", "price": 180}])
        result = subprocess.run([sys.executable, str(BASE / "strategy_metrics.py"),
                                 "--dir", str(self.directory), "--now", self.now.isoformat()],
                                check=True, capture_output=True, text=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["data_quality"]["buyer"]["omitted_rows"], 1)
        self.assertIn("warning:", result.stderr)

    def test_invalid_latest_seller_snapshot_does_not_resurrect_an_old_deal(self):
        row = {"task_id": "one", "item": "lamp", "started_at": "2026-10-22T01:00:00Z",
               "outcome": "deal", "sale_price": 100}
        self.save(seller=[row, {**row, "sale_price": None}])
        with redirect_stderr(io.StringIO()):
            result = compare(self.directory, self.now)
        self.assertEqual(result["current"]["seller"]["total"]["sales"], 0)
        self.assertEqual(result["data_quality"]["seller"]["omitted_rows"], 1)


if __name__ == "__main__":
    unittest.main()
