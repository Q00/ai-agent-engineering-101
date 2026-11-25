import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('brief', Path(__file__).parent / 'tools/auto_auto_market_brief/tool.py')
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)

class BriefTest(unittest.TestCase):
    def test_category_sku_join_paid_dedup_and_seller(self):
        catalog = {'G1': {'category': '침대', 'name': '매트'}, 'G2': {'category': '침구', 'name': '매트'}}
        rows = [
            {'k': 'settle', 'seq': 1, 'sku': 'G1', 'seller': 'shop', 'buyer': 'other'},
            {'k': 'settle', 'seq': 1, 'sku': 'G1', 'seller': 'shop'},
            {'k': 'result', 'seq': 2, 'sku': 'G1', 'seller': 'shop'},
            {'k': 'settle', 'seq': 3, 'sku': 'G2', 'seller': 'shop'},
            {'k': 'settle', 'seq': 4, 'sku': 'G1', 'seller': 'other', 'buyer': 'shop'},
        ]
        result = brief.search(catalog, rows, {'category': '침대', 'query': '매트', 'shop': 'shop'})
        self.assertEqual(result['matched_products'], 1)
        self.assertEqual([r['seq'] for r in result['ledger']], [1])
        self.assertEqual(result['catalog'][0]['sku'], 'G1')
