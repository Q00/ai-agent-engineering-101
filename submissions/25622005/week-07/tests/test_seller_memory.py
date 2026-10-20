"""Checks persistence and honest outcome counts, using synthetic records only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from seller_memory import SellerMemory


def row(task, outcome, price=None):
    return {"task_id": task, "item": "lamp", "outcome": outcome,
            "sale_price": price, "updated_at": "2026-10-20T00:00:00+00:00"}


class SellerMemoryTests(unittest.TestCase):
    def setUp(self):
        cache = BASE / ".market"
        cache.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "seller-memory.jsonl"

    def test_restart_counts_each_task_once(self):
        memory = SellerMemory(self.path)
        memory.record(row("one", "open"))
        memory.record(row("one", "open"))
        memory.record(row("one", "deal", 100))
        memory.record(row("two", "deal", 110))
        restored = SellerMemory(self.path).summary("lamp")
        self.assertEqual(restored["sales"], 2)
        self.assertEqual(restored["completed_negotiations"], 2)
        self.assertEqual(restored["average_sale_price"], 105)
        self.assertEqual(len(self.path.read_text().splitlines()), 4)

    def test_interrupted_task_is_not_counted_as_failure(self):
        memory = SellerMemory(self.path)
        memory.record(row("unfinished", "open"))
        memory.record(row("walked-away", "canceled"))
        memory.record(row("declined", "rejected"))
        summary = SellerMemory(self.path).summary("lamp")
        self.assertEqual(summary["completed_negotiations"], 2)
        self.assertEqual(summary["pending_or_interrupted"], 1)
        self.assertEqual(summary["canceled"], 1)
        self.assertEqual(summary["rejected"], 1)

    def test_concurrent_records_survive_reload(self):
        memory = SellerMemory(self.path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: memory.record(row(str(n), "deal", 100)), range(40)))
        self.assertEqual(SellerMemory(self.path).summary("lamp")["sales"], 40)

    def test_corrupt_memory_is_reported_without_overwriting_it(self):
        self.path.write_text('{"broken":', encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "line 1"):
            SellerMemory(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_rejects_sale_without_price(self):
        memory = SellerMemory(self.path)
        with self.assertRaises(ValueError):
            memory.record(row("bad", "deal"))
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
