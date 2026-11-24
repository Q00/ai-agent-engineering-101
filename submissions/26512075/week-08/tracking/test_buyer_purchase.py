import json
import tempfile
import unittest
from pathlib import Path
from record_buyer_purchase import record


class PurchaseTests(unittest.TestCase):
    def test_hook_records_authoritative_terms_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deal.json'
            path.write_text(json.dumps({'order': {'round': 3, 'order': 'o1'},
                'receipt': {'warranty_hours': 72},
                'settled': {'ledger': 12, 'shop': 'https://shop', 'sku': 'sku',
                            'name': 'lamp', 'unit': 'u1', 'grade': 'B', 'warranty_hours': 0}}))
            record(path)
            record(path)
            rows = (path.parent / 'buyer-purchases.jsonl').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])['warrantyHours'], 0)

    def test_not_a_settlement_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deal.json'
            path.write_text(json.dumps({'order': {'round': 3}, 'settled': {}}))
            with self.assertRaises(ValueError):
                record(path)
            self.assertFalse((path.parent / 'buyer-purchases.jsonl').exists())
