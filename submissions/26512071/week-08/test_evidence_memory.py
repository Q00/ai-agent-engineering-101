"""Offline persistent-memory fixtures; never makes market/API/model calls."""
import importlib
import json
import tempfile
import unittest
from pathlib import Path


class MemoryTests(unittest.TestCase):
    def test_paid_and_refused_are_distinct_idempotent_facts(self):
        self.assertIsNotNone(importlib.util.find_spec('evidence_memory'), 'persistent memory module missing')
        from evidence_memory import sync
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            rows = [dict(order='o1', shop='https://s', item='G1', price=60, paid=True, ledger=42),
                    dict(order='o2', shop='https://s', item='G1', price=60, paid=False, reason='no certificate')]
            (p/'payments.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
            a = sync(p)
            self.assertEqual(len(a['purchases']), 1)
            self.assertEqual(len(a['refusals']), 1)
            purchase = a['purchases']['42']
            self.assertEqual(purchase['sku'], 'G1')
            self.assertEqual(purchase['price'], 60)
            self.assertNotIn('settled_at', purchase)
            self.assertNotIn('grade', purchase)
            self.assertEqual(a['refusals'][next(iter(a['refusals']))]['reason'], 'no certificate')
            self.assertEqual(sync(p), a)

    def test_deal_enrichment_and_safe_truncated_replay_missing_sources(self):
        from evidence_memory import sync
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            row = dict(order='o1', shop='https://s', item='G1', price=60, paid=True, ledger=42)
            (p/'payments.jsonl').write_text(json.dumps(row)+'\n'+ '{"order":')
            deal = dict(order='o1', receipt=dict(item='G1', price=60, cert=True, warranty_hours=6),
                        settled=dict(shop='https://s', sku='G1', name='same name', grade='B', ledger=42, warranty_hours=6))
            (p/'deal.json').write_text(json.dumps(deal))
            (p/'broken.jsonl').write_text(json.dumps(dict(name='same name', broke_at=123))+'\n')
            a = sync(p)
            self.assertEqual(a['purchases']['42'].get('grade'), 'B')
            self.assertIs(a['purchases']['42']['receipt_observed']['cert'], True)
            self.assertNotIn('settled_at', a['purchases']['42'])
            self.assertEqual(len(a['broken_unresolved']), 1)
            self.assertEqual(sync(p), a)
            (p/'payments.jsonl').write_text(json.dumps(row)+'\n')
            self.assertEqual(len(sync(p)['purchases']), 1)
            (p/'payments.jsonl').unlink()
            (p/'deal.json').unlink()
            self.assertEqual(len(sync(p)['purchases']), 1)
            (p/'evidence-memory.json').write_text('{truncated')
            with self.assertRaises(ValueError): sync(p)
            self.assertEqual((p/'evidence-memory.json').read_text(), '{truncated')

    def test_own_orders_are_separate_from_observed_settled_category_counts(self):
        import evidence_memory as memory
        self.assertTrue(hasattr(memory, 'summary'), 'observed denominator summary missing')
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            catalog = {'G1':dict(category='cat1',list_price=100)}
            order = dict(order='o1', category='cat1', acceptable=['G1'])
            a = memory.sync(p, order=order)
            a = memory.sync(p, order=order)
            s = memory.summary(a, catalog)
            self.assertEqual(s['requested_categories'], {'cat1':1})
            self.assertEqual(s['settled_categories'], {})
            self.assertEqual(s['settled_denominator'], 0)
            row = dict(order='o1',shop='https://s',item='G1',price=60,paid=True,ledger=42)
            (p/'payments.jsonl').write_text(json.dumps(row)+'\n')
            s = memory.summary(memory.sync(p), catalog)
            self.assertEqual(s['settled_categories'], {'cat1':1})
            self.assertEqual(s['settled_denominator'], 1)
            self.assertEqual(s['shops']['https://s']['ratios'], [.6])
            self.assertEqual(s['shops']['https://s']['paid'], 1)

    def test_buyer_ranking_uses_paid_evidence_not_unverified_review_volume(self):
        import buyer_r3
        import inspect
        self.assertIn('memory', inspect.signature(buyer_r3.candidates).parameters)
        shops = [dict(name='a',url='https://a',offers=[dict(sku='G1',qty=1)],reviews=[dict(stars=5)]*1000),
                 dict(name='b',url='https://b',offers=[dict(sku='G1',qty=1)],reviews=[dict(stars=1)])]
        memory = dict(purchases={'1':dict(shop='https://b',sku='G1',price=50,ledger=1)},
                      refusals={'r':dict(shop='https://a',reason='no certificate')})
        order = dict(acceptable=['G1'], kind='goods')
        result = buyer_r3.candidates(shops, order, memory, {'G1':dict(list_price=100)})
        self.assertEqual(result[0][0]['url'], 'https://b')
        self.assertEqual(buyer_r3.candidates(shops, dict(acceptable=['MISSING'],kind='goods'), memory, {}), [])
        self.assertTrue(buyer_r3.policy(order)['cert'])

    def test_restock_prioritizes_only_local_paid_categories_with_same_caps(self):
        import restock_r3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            catalog = {f'G{i}':dict(category=f'cat{i}',kind='goods',list_price=10000) for i in range(10)}
            (p/'inventory.json').write_text(json.dumps(dict(round=3,balance=5000000,start=5000000,units=[])))
            (p/'catalog.json').write_text(json.dumps(catalog))
            first = restock_r3.plan(p,now=100)
            selected = {r['sku'] for r in first['buy']}
            target = next(sku for sku in catalog if sku not in selected)
            (p/'restock-pending.json').unlink()
            row = dict(order='o1',shop='https://s',item=target,price=6000,paid=True,ledger=1)
            (p/'payments.jsonl').write_text(json.dumps(row)+'\n')
            learned = restock_r3.plan(p,now=100)
            self.assertEqual(learned['buy'][0]['sku'], target)
            self.assertLessEqual(len(learned['buy']), 3)
            self.assertTrue(all(r['qty']==1 for r in learned['buy']))
            self.assertTrue((p/'evidence-memory.json').exists())
            self.assertEqual(json.loads((p/'restock-observation.json').read_text())['settled_denominator'],1)

    def test_paid_review_hook_persists_before_overwritten_deal(self):
        import review_r3
        self.assertTrue(hasattr(review_r3, 'review_file'), 'review hook persistence missing')
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            deal = dict(order=dict(order='o1',category='cat1'),receipt=dict(price=60),
                        settled=dict(shop='https://s',sku='G1',ledger=1,grade='A'))
            (p/'deal.json').write_text(json.dumps(deal))
            self.assertEqual(review_r3.review_file(p/'deal.json')['stars'],5)
            self.assertEqual(len(json.loads((p/'evidence-memory.json').read_text())['purchases']),1)


class BuyerHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_buyer_hook_ingests_feedback_and_request_without_network(self):
        import argparse
        import buyer_r3
        import httpx
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p/'order.json').write_text(json.dumps(dict(order='o1',item='cat1',category='cat1',acceptable=['G1'],kind='goods')))
            (p/'shops.json').write_text('[]')
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[])))
            args=argparse.Namespace(order=str(p/'order.json'), shops=str(p/'shops.json'),item='cat1',budget=100)
            with patch.object(buyer_r3.httpx,'AsyncClient',return_value=client):
                result=await buyer_r3.run(args)
            self.assertIsNone(result['receipt'])
            self.assertTrue((p/'evidence-memory.json').exists(), 'buyer hook did not persist feedback')
            mem=json.loads((p/'evidence-memory.json').read_text())
            self.assertEqual(mem['requested_orders']['o1']['category'],'cat1')


if __name__ == '__main__':
    unittest.main()
