"""Offline checks: python -m unittest discover -s transaction -p 'after_transaction_test.py'."""

import json
from pathlib import Path
import tempfile
import unittest

from after_transaction_record import load_records, record_result, validate, write_json
from after_transaction_analyze import analyze


def example(task="t1", **changes):
    return {"task_id": task, "shop": "test-shop", "item": "test-sku",
            "currency": "market-credit", "status": "completed", "price": 120,
            "cost": 80, **changes}


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="after_transaction_test_", dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.ledger = Path(self.directory.name) / "after_transaction_records.json"

    def test_outcomes_and_export(self):
        record_result(example(), self.ledger)
        record_result(example("t2", status="rejected", price=None, reason="below reserve"), self.ledger)
        record_result(example("t3", status="canceled", price=None), self.ledger)
        result = analyze(load_records(self.ledger))
        group = result["groups"][0]
        self.assertEqual(group["outcome_counts"], {"completed": 1, "rejected": 1, "cancelled": 1})
        self.assertAlmostEqual(group["outcome_rates"]["completed"], 1 / 3)
        self.assertEqual(group["known_revenue"], 120)
        self.assertEqual(group["known_gross_profit"], 40)
        output = self.ledger.with_name("after_transaction_analysis.json")
        write_json(output, result)
        self.assertEqual(json.loads(output.read_text())["record_count"], 3)

    def test_retry_and_conflict(self):
        record_result(example(), self.ledger)
        record_result(example(), self.ledger)
        with self.assertRaises(ValueError):
            record_result(example(status="rejected"), self.ledger)
        self.assertEqual(len(load_records(self.ledger)), 1)

    def test_missing_money_and_separate_currencies(self):
        record_result(example(price=None, cost=None), self.ledger)
        record_result(example("t2", currency="KRW", price=200, cost=150), self.ledger)
        result = analyze(load_records(self.ledger))
        groups = {g["currency"]: g for g in result["groups"]}
        self.assertIsNone(groups["market-credit"]["known_revenue"])
        self.assertIsNone(groups["market-credit"]["known_gross_profit"])
        self.assertEqual(groups["KRW"]["known_revenue"], 200)

    def test_invalid_values(self):
        for changes in ({"price": float("nan")}, {"cost": -1}, {"price": True},
                        {"status": "pending"}, {"quantity": 0}, {"turns": -1},
                        {"recorded_at": "2026-01-01"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                record_result(example(**changes), self.ledger)

    def test_corrupt_and_duplicate_input_preserved(self):
        self.ledger.write_text("broken JSON")
        with self.assertRaises(ValueError):
            record_result(example(), self.ledger)
        self.assertEqual(self.ledger.read_text(), "broken JSON")
        row = validate({**example(), "recorded_at": "2026-01-01T00:00:00Z"})
        write_json(self.ledger, {"schema_version": 1, "records": [row, row]})
        with self.assertRaises(ValueError):
            load_records(self.ledger)

    def test_empty_history(self):
        result = analyze([])
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["groups"], [])


if __name__ == "__main__":
    unittest.main()
