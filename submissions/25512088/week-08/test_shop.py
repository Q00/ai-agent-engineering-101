"""Offline regressions for shop discovery and buyer demand accounting."""
import json
from pathlib import Path
import tempfile
import unittest

from market_intel import Intel
from seller_ledger import Program
from seller_offers import offers
from seller_strategy import sale_quote


class ShopDiscoveryTests(unittest.TestCase):
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
