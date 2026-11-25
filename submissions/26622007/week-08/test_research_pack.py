"""Semantic checks through the real sandbox, not just valid JSON or exit zero."""
import json
from pathlib import Path
import tempfile
import unittest
import agent_tools
from tools.auto_market_research_pack.tool import research


class ResearchPackSemantics(unittest.TestCase):
    def run_pack(self, args):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixtures = {
                'catalog.json': {'G1': {'name': 'sofa', 'category': 'furniture'},
                                 'G2': {'name': 'sofa cover', 'category': 'bedding'}},
                'inventory.json': {'balance': 42, 'units': [
                    {'unit': 'u1', 'sku': 'G1'}, {'unit': 'u2', 'sku': 'G2'}]},
                'forum.json': {'posts': [{'text': 'sofa furniture'}, {'text': 'sofa bedding'}]},
            }
            for name, data in fixtures.items():
                (root / name).write_text(json.dumps(data))
            rows = [{'k': 'settle', 'seq': 1, 'ts': 1, 'sku': 'G1', 'seller': 's'},
                    {'k': 'settle', 'seq': 2, 'ts': 2, 'sku': 'G2', 'seller': 's'},
                    {'k': 'result', 'seq': 3, 'ts': 3, 'sku': 'G1'},
                    {'k': 'settle', 'seq': 1, 'ts': 1, 'sku': 'G1', 'seller': 's'}]
            (root / 'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return agent_tools.execute('auto_market_research_pack', args, td)

    def test_catalog_key_joins_paid_ledger_and_units(self):
        out = self.run_pack({'query': 'sofa', 'category': 'furniture', 'limit': 1})
        self.assertNotIn('error', out)
        self.assertEqual([p.get('sku') for p in out['catalog_matches']], ['G1'])
        self.assertEqual([r['seq'] for r in out['ledger_matches_recent']], [1])
        self.assertEqual(out['inventory_matches'], [{'unit': 'u1', 'sku': 'G1'}])
        self.assertEqual(out['forum_posts_recent'], [{'text': 'sofa furniture'}])

    def test_unknown_query_never_returns_unrelated_stock_or_unpaid_rows(self):
        out = self.run_pack({'query': 'missing'})
        for field in ['catalog_matches', 'ledger_matches_recent', 'inventory_matches', 'forum_posts_recent']:
            self.assertEqual(out[field], [], field)

    def test_presentation_limit_does_not_limit_the_sku_join(self):
        out = self.run_pack({'query': 'sofa', 'limit': 1})
        self.assertEqual(out['catalog_match_count'], 2)
        self.assertEqual([r['seq'] for r in out['ledger_matches_recent']], [2])

    def test_concentration_marks_single_buyer_and_unknown_identity(self):
        paid = [{'k': 'settle', 'seq': n, 'ts': n, 'sku': 'G', 'buyer': 'one'} for n in range(3)]
        args = {'limit': 20}
        out = research({}, paid, {'units': []}, None, args)
        self.assertEqual(out['demand_concentration']['largest_buyer_share'], 1)
        self.assertEqual(out['demand_concentration']['known_buyers'], 1)
        paid[0].pop('buyer')
        out = research({}, paid, {'units': []}, None, args)
        self.assertIsNone(out['demand_concentration']['largest_buyer_share'])
        self.assertEqual(out['demand_concentration']['unknown_buyer_rows'], 1)

    def test_empty_query_contains_only_unique_paid_rows(self):
        out = self.run_pack({})
        self.assertEqual([r['seq'] for r in out['ledger_matches_recent']], [2, 1])
        self.assertEqual(len(out['inventory_matches']), 2)


if __name__ == '__main__':
    unittest.main()
