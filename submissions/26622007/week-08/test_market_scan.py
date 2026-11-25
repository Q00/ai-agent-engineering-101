import json
import tempfile
import unittest
from pathlib import Path
from tools.auto_market_scan.tool import search
import agent_tools

CAT = {'G1': {'name': 'bed', 'category': 'furniture'}, 'G2': {'name': 'bed cover', 'category': 'bedding'}}
ROWS = [{'k': 'settle', 'seq': 1, 'ts': 1, 'sku': 'G1', 'shop': 'shop-a', 'buyer': 'shop-b'},
        {'k': 'result', 'seq': 2, 'ts': 2, 'sku': 'G1', 'shop': 'shop-a'},
        {'k': 'settle', 'seq': 3, 'ts': 3, 'sku': 'G2', 'shop': 'shop-b'}]

class MarketScanTest(unittest.TestCase):
    def test_sku_join_intersection_paid_and_dedup(self):
        got = search(CAT, ROWS + [ROWS[0]], {'query': 'bed', 'category': 'furniture'})
        self.assertEqual([r['sku'] for r in got['catalog']], ['G1'])
        self.assertEqual([r['seq'] for r in got['ledger']], [1])
        self.assertEqual(search(CAT, ROWS, {'query': 'G1'})['matched_paid'], 1)
        self.assertEqual(search(CAT, ROWS, {'query': 'missing'})['matched_paid'], 0)

    def test_shop_is_not_buyer_and_order_is_recent(self):
        self.assertEqual([r['seq'] for r in search(CAT, ROWS, {'shop': 'shop-b'})['ledger']], [3])
        self.assertEqual([r['seq'] for r in search(CAT, ROWS, {})['ledger']], [3, 1])

    def test_real_sandbox_contract(self):
        with tempfile.TemporaryDirectory() as d:
            md = Path(d)/'.market'; md.mkdir()
            (md/'catalog.json').write_text(json.dumps(CAT))
            (md/'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in ROWS))
            got = agent_tools.execute('auto_market_scan', {'category': 'furniture'}, str(md))
            self.assertNotIn('error', got)
            self.assertEqual(got['matched_paid'], 1)
            self.assertEqual(got['ledger'][0]['seq'], 1)
