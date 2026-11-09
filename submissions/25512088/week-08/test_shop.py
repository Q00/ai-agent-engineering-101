"""Offline regressions for shop discovery and buyer demand accounting."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from market_intel import Intel
from seller_ledger import Program
from seller_offers import offers
from seller_market import rank_products
from seller_strategy import protected_floor, sale_quote


class ShopDiscoveryTests(unittest.TestCase):
    def test_discovery_uses_checkout_preferences_without_bypassing_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = dict(unit='u', sku='sku', name='chair', grade='B', kind='goods',
                        list_price=20000, cost=8000, expires_at=None)
            (root/'inventory.json').write_text(json.dumps(dict(round=3, units=[unit])))
            intel = Intel(root)
            program = Program(root)
            try:
                baseline = sale_quote(unit, {}, program, intel, record=False)['price']
                for age, target in ((0, 12000), (181, 12000), (0, 1)):
                    with self.subTest(age=age, target=target):
                        preferences = dict(version=3, generated_at=time.time()-age,
                            buyers={str(i): dict(active=True, skus=[dict(
                                sku='sku', median_paid=target, price_samples=3)])
                                for i in range(3)})
                        (root/'buyer-preferences.json').write_text(json.dumps(preferences))
                        expected = sale_quote(unit, {}, program, intel, record=False,
                                              buyer_preferences=preferences)
                        preview = offers(root, program, intel, limit=1)[0]
                        self.assertEqual(preview['price'], expected['price'])
                        self.assertGreaterEqual(preview['price'], protected_floor(unit, program.p))
                        if age == 0:
                            self.assertLess(preview['price'], baseline)
                        else:
                            self.assertEqual(preview['price'], baseline)
                self.assertEqual(intel.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0], 0)
            finally:
                program.close()
                intel.close()

    def test_procurement_does_not_value_six_hour_stock_at_long_warranty_prices(self):
        now = 100000
        catalog = {'sku': dict(kind='goods', list_price=100000, category='chairs')}
        short = [dict(sku='sku', grade=grade, price=50000, warranty=6,
                      honest=1, ts=now-10, buyer='short-'+grade) for grade in 'ABC']
        premium = [dict(row, price=150000, warranty=hours, buyer=f'premium-{hours}-{i}')
                   for hours in (24, 48, 72) for i, row in enumerate(short)]
        # Premium cohorts must not turn unprofitable six-hour stock into a buy.
        self.assertEqual(rank_products(catalog, short+premium, [], now=now), [])
        self.assertEqual(rank_products(catalog, premium, [], now=now), [])

        profitable = [dict(row, price=95000) for row in short]
        baseline = rank_products(catalog, profitable, [], now=now)
        self.assertEqual(len(baseline), 1)
        self.assertEqual(rank_products(catalog, profitable+premium, [], now=now), baseline)
        self.assertEqual(baseline[0]['exact_grade_warranty_trades'], 3)

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
