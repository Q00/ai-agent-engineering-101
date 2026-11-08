"""Local regression checks for seller conversion pricing."""
import json
import math
from pathlib import Path
import tempfile
import time
import unittest

from market_intel import Intel
from seller_ledger import Program
from seller_strategy import sale_quote


class ShopConversionPricingTests(unittest.TestCase):
    def test_partial_conversion_discount_requires_evidence_and_keeps_margin(self):
        cases = [
            # sales, quotes, competitor units, stale source, expected adjustment
            (1, 10, 4, False, .98),
            (2, 10, 4, False, 1.0),
            (0, 4, 4, False, 1.0),
            (1, 10, 0, False, 1.0),
            (1, 10, 4, True, 1.0),
        ]
        for sales, quotes, competitors, stale, expected in cases:
            for cost in (8000, 15000):
                with self.subTest(case=(sales, quotes, competitors, stale), cost=cost):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        program, intel = Program(root), Intel(root)
                        try:
                            unit = dict(unit='u', sku='sku', name='item', grade='B',
                                        kind='goods', list_price=20000, cost=cost)
                            catalog = {'sku': dict(category='chairs', list_price=20000),
                                       'alt': dict(category='chairs', list_price=18000)}
                            intel.save_source('shops', {'shops': [dict(name='rival',
                                offers=[dict(sku='alt', qty=competitors)])]})
                            if stale:
                                with intel.db:
                                    intel.db.execute('UPDATE sources SET ts=?', (time.time()-181,))
                            for _ in range(quotes):
                                intel.decision('seller_quote', {'sku': 'sku'})
                            with program.db:
                                for index in range(sales):
                                    program.db.execute(
                                        'INSERT INTO sales(task,sku,settled,refunded) VALUES(?,?,?,0)',
                                        (str(index), 'sku', time.time()))
                            quote = sale_quote(unit, catalog, program, intel)
                            body = json.loads(intel.db.execute(
                                "SELECT body FROM decisions WHERE role='seller_quote' "
                                'ORDER BY id DESC LIMIT 1').fetchone()[0])
                            self.assertEqual(body['market_adjustment'], expected)
                            self.assertEqual(quote['base'], max(math.ceil(16000*expected), body['floor']))
                            self.assertGreaterEqual(quote['price']*.85-quote['reward'],
                                                    cost+math.ceil(cost*.09))
                        finally:
                            intel.close()
                            program.close()


if __name__ == '__main__':
    unittest.main()
