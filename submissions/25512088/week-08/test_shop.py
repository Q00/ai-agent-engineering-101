"""Offline regressions for shop discovery and buyer demand accounting."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from market_intel import Intel
from seller_ledger import Program
from seller_offers import offers
from seller_strategy import sale_quote, protected_floor, LOSS_LEADER_TRIALS_PER_SKU


class ShopDiscoveryTests(unittest.TestCase):
    def test_margin_bypass_ends_after_own_sales_even_without_principal_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            program = Program(root)
            intel = Intel(root)
            unit = dict(unit='u', sku='sku', name='chair', grade='B', kind='goods',
                        list_price=20000, cost=9900, expires_at=None)
            try:
                # Exact-condition public evidence qualifies the existing trial.
                intel.ingest([dict(seq=n+1, ts=time.time(), kind='settle', round=3,
                    data=dict(shop='https://rival', sku='sku', grade='B', amount=10000,
                              warranty_hours=6, claim_honest=True, buyer=f'buyer-{n}'))
                    for n in range(10)])
                for n in range(LOSS_LEADER_TRIALS_PER_SKU):
                    quote = sale_quote(unit, {}, program, intel, record=False)
                    self.assertTrue(quote['loss_leader'])
                    self.assertEqual(quote['price'], 10000)
                    self.assertGreater(quote['price'], unit['cost'])
                    program.accept(f't{n}', f'u{n}', 'sku', unit['cost'], quote,
                                   inventory={'balance':10000000}, enroll=False)
                # Unknown settlements and refunds do not reset the trial.
                with program.db:
                    program.db.execute("UPDATE sales SET refunded=1 WHERE task='t0'")
                quote = sale_quote(unit, {}, program, intel, record=False)
                self.assertFalse(quote['loss_leader'])
                self.assertGreaterEqual(quote['price'], protected_floor(unit, program.p))
            finally:
                program.close()
                intel.close()

    def test_discovery_does_not_inflate_inquiries_or_trigger_conversion_discount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = dict(unit='u', sku='sku', name='chair', grade='B', kind='goods',
                        list_price=20000, cost=8000, expires_at=None)
            units = [dict(unit, unit=f'u{i}') for i in range(6)]
            (root/'inventory.json').write_text(json.dumps(dict(round=3, units=units)))
            intel = Intel(root)
            program = Program(root)
            try:
                intel.save_source('shops', {'shops': [dict(
                    name='rival', offers=[dict(sku='sku', qty=4)])]})
                quoted = sale_quote(unit, {}, program, intel)
                for _ in range(3):
                    preview = offers(root, program, intel, limit=1)
                    self.assertEqual(preview[0]['price'], quoted['price'])
                count = intel.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0]
                self.assertEqual(count, 1)
                # Actual buyer quotes still feed the existing demand accounting.
                next_quote = sale_quote(unit, {}, program, intel)
                self.assertEqual(next_quote['price'], quoted['price'])
                self.assertEqual(intel.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0], 2)
            finally:
                program.close()
                intel.close()


if __name__ == '__main__':
    unittest.main()
