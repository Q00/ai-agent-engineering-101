import unittest
from profit_cohorts import summarize


class CohortsTest(unittest.TestCase):
    def test_refund_belongs_to_sale_not_refund_time_and_deduplicates(self):
        sale = dict(k='settle', seq=1, ts=100, sku='G', grade='B', amount=200, order='o1', warranty=6)
        rows = [sale, sale, dict(k='broken', seq=4, ts=20000, order='o1', sku='G'), dict(k='settle', seq=2, ts=29000, sku='G', grade='A', amount=300),
                dict(k='settle', seq=3, ts=100, sku='G', grade='A', amount=999)]
        mine = {'1': {'unit': 'u1'}, '2': {'unit': 'u2'}}
        mem = [dict(unit='u1', result='deal', cost=150), dict(unit='u2', result='deal', cost=100)]
        losses = [dict(kind='break_refund', ts=20000, detail='ledger #1 warranty 6 h', amount=200)]
        report = summarize(rows, mine, mem, losses, 30000)
        self.assertEqual(report['ledger_lag_seconds'], 1000)
        x = report['skus']['G']
        self.assertEqual(x['all']['net'], 50)
        self.assertEqual(x['mature_6h']['net'], -150)
        self.assertEqual(x['open_6h']['net'], 200)
        self.assertEqual(x['by_grade']['B']['mature_6h']['refunded_sales'], 1)
        self.assertEqual(summarize(rows, mine, mem, losses, 10000)['skus']['G']['all']['net'], 50)

    def test_agreed_missing_cost_and_future_rows_never_look_profitable(self):
        rows = [dict(k='settle', seq=1, ts=100, sku='G', amount=200),
                dict(k='settle', seq=2, ts=40000, sku='G', amount=999)]
        mem = [dict(unit='u', result='agreed', cost=1)]
        x = summarize(rows, {'1': {'unit': 'u'}, '2': {'unit': 'v'}}, mem, [], 30000)['skus']['G']
        self.assertEqual(x['all']['paid'], 1)
        self.assertEqual(x['mature_6h']['missing_cost'], 1)
        self.assertIsNone(x['mature_6h']['net'])
        self.assertIsNone(x['mature_6h']['net_per_sale'])
        self.assertEqual(summarize(rows, {'1': {'unit': 'u'}}, mem, [], 30000, start=200)['skus'], {})

    def test_old_wrong_refund_is_recomputed_by_order(self):
        rows = [dict(k='settle', seq=1, ts=100, sku='G', buyer='b', order='old', amount=200, warranty=6),
                dict(k='settle', seq=2, ts=200, sku='G', buyer='b', order='new', amount=300, warranty=6),
                dict(k='broken', seq=3, ts=300, sku='G', buyer='b', order='old')]
        mine = {'1': {'unit': 'u1'}, '2': {'unit': 'u2'}}
        mem = [dict(unit='u1', result='deal', cost=100), dict(unit='u2', result='deal', cost=100)]
        legacy = [dict(kind='break_refund', ts=300, detail='ledger #2', amount=300)]
        report = summarize(rows, mine, mem, legacy, 30000)
        self.assertEqual(report['skus']['G']['mature_6h']['net'], 100)
        self.assertEqual(report['refund_reconciliation']['changed_sale_seqs'], [1, 2])
        # Future breaks and wrong/missing orders never charge the latest similar sale.
        self.assertEqual(summarize(rows, mine, mem, legacy, 250)['skus']['G']['all']['refunds'], 0)
        rows[-1]['order'] = 'not-in-ledger'
        report = summarize(rows, mine, mem, legacy, 30000)
        self.assertEqual(report['skus']['G']['all']['refunds'], 0)
        self.assertEqual(report['refund_reconciliation']['unmatched_order_breaks'], 1)

    def test_outside_warranty_and_unknown_warranty(self):
        sale = dict(k='settle', seq=1, ts=100, sku='G', order='o', amount=200, warranty=1)
        rows = [sale, dict(k='broken', seq=2, ts=4000, sku='G', order='o')]
        mine = {'1': {'unit': 'u'}}
        mem = [dict(unit='u', result='deal', cost=100)]
        self.assertEqual(summarize(rows, mine, mem, [], 30000)['skus']['G']['all']['net'], 100)
        del sale['warranty']
        self.assertIsNone(summarize(rows, mine, mem, [], 30000)['skus']['G']['all']['net'])

    def test_sandbox_tool_reads_fixture_and_cannot_claim_unpaid_profit(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        import agent_tools
        with tempfile.TemporaryDirectory() as root:
            md = Path(root) / '.market'
            md.mkdir()
            result = agent_tools.execute('cohort_profit', {'sku': 'G'}, str(md))
            self.assertNotIn('error', result)
            self.assertEqual(result['skus'], {'G': None})
