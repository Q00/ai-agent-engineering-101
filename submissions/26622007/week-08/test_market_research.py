import json
import tempfile
import unittest
from pathlib import Path

import agent_tools
from tools.auto_market_research.tool import research


class MarketResearchTest(unittest.TestCase):
    def test_join_paid_deduplicated_and_personal_filters(self):
        catalog = {'G1': {'name': '김치', 'category': '식품>김치'},
                   'G2': {'name': '김치', 'category': 'other'}}
        rows = [{'k': 'settle', 'seq': 1, 'ts': 1, 'sku': 'G1', 'shop': 'seller', 'buyer': 'other'},
                {'k': 'result', 'seq': 2, 'sku': 'G1', 'shop': 'seller'},
                {'k': 'settle', 'seq': 3, 'sku': 'G2', 'shop': 'seller'}]
        trades = [{'order': 'a', 'item': 'G1', 'shop': 'seller', 'result': 'deal'},
                  {'order': 'b', 'item': 'G1', 'shop': 'seller', 'result': 'unpaid'},
                  {'order': 'c', 'item': 'G2', 'shop': 'seller', 'result': 'deal'}]
        got = research(catalog, rows + rows[:1], trades,
                       {'query': '김치', 'category': '식품', 'shop': 'seller', 'include_purchases': True})
        self.assertEqual(got['catalog_count'], 1)
        self.assertEqual([r['seq'] for r in got['paid_deals']], [1])
        self.assertEqual([t['order'] for t in got['personal_history']], ['a'])
        self.assertEqual(research(catalog, rows, trades, {'shop': 'other'})['paid_deal_count'], 0)

    def test_sandbox_search(self):
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / '.market'; md.mkdir()
            (md / 'catalog.json').write_text(json.dumps({'G1': {'name': '김치', 'category': '김치'}}))
            (md / 'ledger_rows.jsonl').write_text(json.dumps({'k': 'settle', 'seq': 5, 'ts': 1, 'sku': 'G1', 'shop': 's'}) + '\n')
            got = agent_tools.execute('auto_market_research', {'query': '김치', 'category': '김치'}, str(md))
            self.assertNotIn('error', got)
            self.assertEqual(got['paid_deals'][0]['seq'], 5)
