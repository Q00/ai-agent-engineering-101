"""Offline checks for balanced growth v3; no network/market API."""
import json
import tempfile
import unittest
from pathlib import Path
from restock_v3 import evaluate, historical_metrics


class RestockV3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / '.market'
        self.logs = self.root / 'logs'
        self.state.mkdir()
        self.logs.mkdir()
        self.catalog = {
            'Ggood': {'sku': 'Ggood', 'name': 'Popular Throw', 'category': 'bedding', 'kind': 'goods', 'list_price': 27000},
            'Gsecond': {'sku': 'Gsecond', 'name': 'Popular Curtains', 'category': 'bedding', 'kind': 'goods', 'list_price': 30000},
            'Gbad': {'sku': 'Gbad', 'name': 'Low Margin Chair', 'category': 'furniture', 'kind': 'goods', 'list_price': 30000},
            'Gfood': {'sku': 'Gfood', 'name': 'Fresh Kimchi', 'category': 'food', 'kind': 'food', 'list_price': 30000},
        }
        self.inv = {'round': 3, 'balance': 4_950_000, 'units': [], 'restock': {'open': True}}
        self.write('catalog.json', self.catalog)
        self.write('inventory.json', self.inv)
        self.write('demand.json', [{'category': 'bedding'}, {'category': 'furniture'}, {'category': 'food'}])
        self.lines = '''14:00:00 [supply] 1 x Popular Throw for 15000, balance 4950000
14:00:01 [acp] settled Popular Throw at 21000, ledger #10, unit u-1 grade A
14:00:02 [acp] settled Popular Throw at 22000, ledger #11, unit u-2 grade A
14:00:03 [supply] 1 x Popular Curtains for 16500, balance 4950000
14:00:04 [acp] settled Popular Curtains at 19500, ledger #12, unit u-3 grade A
14:00:05 [acp] settled Popular Curtains at 20000, ledger #13, unit u-4 grade A
14:00:06 [supply] 1 x Low Margin Chair for 26000, balance 4950000
14:00:07 [acp] settled Low Margin Chair at 21000, ledger #14, unit u-5 grade A
14:00:08 [acp] settled Low Margin Chair at 20000, ledger #15, unit u-6 grade A
14:00:09 [supply] 1 x Fresh Kimchi for 10000, balance 4950000
14:00:10 [acp] settled Fresh Kimchi at 22000, ledger #16, unit u-7 grade A
14:00:11 [acp] settled Fresh Kimchi at 22000, ledger #17, unit u-8 grade A
'''
        (self.logs / 'market-kit.log').write_text(self.lines, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, obj):
        (self.state / name).write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')

    def test_selects_two_proven_goods(self):
        buys, info = evaluate(self.state)
        self.assertEqual({x['sku'] for x in buys}, {'Ggood', 'Gsecond'})
        self.assertEqual(info['reason'], 'candidates selected')

    def test_excludes_unprofitable_and_food(self):
        buys, _ = evaluate(self.state)
        self.assertNotIn('Gbad', [x['sku'] for x in buys])
        self.assertNotIn('Gfood', [x['sku'] for x in buys])

    def test_stock_cap_at_twelve(self):
        self.inv['units'] = [{'sku': 'X', 'grade': 'A', 'cost': 1000} for _ in range(12)]
        self.write('inventory.json', self.inv)
        self.assertEqual(evaluate(self.state)[0], [])

    def test_cost_cap_at_240k(self):
        self.inv['units'] = [{'sku': 'X', 'grade': 'A', 'cost': 240000}]
        self.write('inventory.json', self.inv)
        self.assertEqual(evaluate(self.state)[0], [])

    def test_cash_floor(self):
        self.inv['balance'] = 4_699_999
        self.write('inventory.json', self.inv)
        self.assertEqual(evaluate(self.state)[0], [])

    def test_closed_window(self):
        self.inv['restock']['open'] = False
        self.write('inventory.json', self.inv)
        self.assertEqual(evaluate(self.state)[0], [])
        self.assertTrue(evaluate(self.state, ignore_window=True)[0])

    def test_cannot_overfill_stock(self):
        self.inv['units'] = [{'sku': 'X', 'grade': 'A', 'cost': 1000} for _ in range(11)]
        self.write('inventory.json', self.inv)
        self.assertEqual(len(evaluate(self.state)[0]), 1)

    def test_history_missing_means_no_buy(self):
        (self.logs / 'market-kit.log').write_text('', encoding='utf-8')
        self.assertEqual(evaluate(self.state)[0], [])

    def test_ledger_id_deduplicated(self):
        stats, _ = historical_metrics([
            '[acp] settled Popular Throw at 21000, ledger #10, unit u-1 grade A',
            '[acp] settled Popular Throw at 21000, ledger #10, unit u-1 grade A',
        ], self.catalog)
        self.assertEqual(stats['counts']['Ggood'], 1)

    def test_ambiguous_name_skipped(self):
        self.catalog['Gother'] = {'name': 'Popular Throw', 'category': 'bedding', 'kind': 'goods', 'list_price': 27000}
        self.write('catalog.json', self.catalog)
        buys, _ = evaluate(self.state)
        self.assertNotIn('Ggood', [x['sku'] for x in buys])

    def test_same_sku_cap_three(self):
        self.inv['units'] = [{'sku': 'Ggood', 'grade': 'A', 'cost': 15000} for _ in range(3)]
        self.write('inventory.json', self.inv)
        buys, _ = evaluate(self.state)
        self.assertNotIn('Ggood', [x['sku'] for x in buys])

    def test_avoids_buying_beyond_cash_buffer(self):
        self.inv['balance'] = 4_710_000
        self.write('inventory.json', self.inv)
        self.assertEqual(evaluate(self.state)[0], [])


if __name__ == '__main__':
    unittest.main()
