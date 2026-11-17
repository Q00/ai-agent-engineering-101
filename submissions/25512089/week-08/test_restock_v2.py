"""Offline tests; no API calls, network or actual purchases."""
import json
import tempfile
import unittest
from pathlib import Path
from restock_v2 import evaluate, historical_metrics


class RestockV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / '.market'
        self.logs = self.root / 'logs'
        self.state.mkdir()
        self.logs.mkdir()
        self.catalog = {
            'Ggood': {'sku': 'Ggood', 'name': 'Profitable Throw', 'category': 'bedding', 'kind': 'goods', 'list_price': 30000},
            'Gbad': {'sku': 'Gbad', 'name': 'Unprofitable Sofa', 'category': 'bedding', 'kind': 'goods', 'list_price': 30000},
        }
        self.inventory = {'round': 3, 'balance': 4_900_000, 'units': [], 'restock': {'open': True}}
        self.write('catalog.json', self.catalog)
        self.write('inventory.json', self.inventory)
        self.write('demand.json', [{'category': 'bedding', 'item': 'bedding', 'kind': 'goods'}])
        (self.logs / 'market-kit.log').write_text('''15:00:00 [supply] 1 x Profitable Throw for 14000, balance 4900000
15:00:01 [acp] settled Profitable Throw at 19000, ledger #10, unit u-1 grade A
15:00:02 [acp] settled Profitable Throw at 18000, ledger #11, unit u-2 grade A
15:00:03 [supply] 1 x Unprofitable Sofa for 22000, balance 4800000
15:00:04 [acp] settled Unprofitable Sofa at 19000, ledger #12, unit u-3 grade A
15:00:05 [acp] settled Unprofitable Sofa at 20000, ledger #13, unit u-4 grade A
''', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, obj):
        (self.state / name).write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')

    def test_select_profitable_actual_seller(self):
        purchases, info = evaluate(self.state)
        self.assertEqual(purchases, [{'sku': 'Ggood', 'qty': 1}])
        self.assertEqual(info['reason'], 'candidate selected')

    def test_ignore_unprofitable_sku(self):
        purchases, _ = evaluate(self.state)
        self.assertNotIn('Gbad', [x['sku'] for x in purchases])

    def test_respects_closed_window(self):
        self.inventory['restock']['open'] = False
        self.write('inventory.json', self.inventory)
        self.assertEqual(evaluate(self.state)[0], [])
        self.assertEqual(evaluate(self.state, ignore_window=True)[0], [{'sku': 'Ggood', 'qty': 1}])

    def test_no_buy_if_stock_cap(self):
        self.inventory['units'] = [{'sku': 'X', 'grade': 'A', 'cost': 1000} for _ in range(8)]
        self.write('inventory.json', self.inventory)
        self.assertEqual(evaluate(self.state)[0], [])

    def test_no_buy_if_unsold_cost_high(self):
        self.inventory['units'] = [{'sku': 'X', 'grade': 'A', 'cost': 165000}]
        self.write('inventory.json', self.inventory)
        self.assertEqual(evaluate(self.state)[0], [])

    def test_no_buy_if_balance_low(self):
        self.inventory['balance'] = 4_500_000
        self.write('inventory.json', self.inventory)
        self.assertEqual(evaluate(self.state)[0], [])

    def test_no_buy_without_actual_seller_sales(self):
        (self.logs / 'market-kit.log').write_text('', encoding='utf-8')
        self.assertEqual(evaluate(self.state)[0], [])

    def test_deduplicates_ledger_ids(self):
        sales, _ = historical_metrics(['[acp] settled Profitable Throw at 19000, ledger #10, unit u-1 grade A', '[acp] settled Profitable Throw at 19000, ledger #10, unit u-1 grade A'], self.catalog)
        self.assertEqual(sales['counts']['Ggood'], 1)

    def test_ambiguous_product_name_not_guessed(self):
        self.catalog['Gother'] = {'name': 'Profitable Throw', 'category': 'bedding', 'kind': 'goods', 'list_price': 32000}
        self.write('catalog.json', self.catalog)
        self.assertEqual(evaluate(self.state)[0], [])


if __name__ == '__main__':
    unittest.main()
