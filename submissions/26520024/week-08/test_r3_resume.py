"""Regression coverage for gaps found while recovering the interrupted strategy work."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_common import DEFAULT_POLICY
from r3_memory import Memory
from r3_portfolio import diversified, enrich, observe_offers
from r3_discovery import build_candidates
from r3_restock import bounded_proposals, wholesale_bound
from test_r3_discovery import CATALOG, NOW, event


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.memory = Memory(self.md)
        self.addCleanup(self.memory.close)

    def candidates(self):
        return build_candidates(CATALOG, [event('market_settle', '1',
                                {'seq': 1, 'item': 'tea', 'buyer': 'b1'})], NOW)

    def test_fallback_prefix_preserves_actual_substitute_before_related_guess(self):
        rows = [dict(sku=str(i), score=100-i, list_price=100, demand_status='observed_sale') for i in range(30)]
        rows += [dict(sku='related', score=90, list_price=100, demand_status='related_unseen'),
                 dict(sku='substitute', score=.01, list_price=100, demand_status='eligible_unseen')]
        self.assertIn('substitute', [r['sku'] for r in diversified(rows, 24)[:3]])

    def test_proven_stock_cannot_spend_pilot_reserve(self):
        catalog, candidates = {}, []
        for i in range(9):
            sku = 'sku'+str(i)
            catalog[sku] = dict(category=sku, kind='goods', list_price=100)
            candidates.append(dict(sku=sku, score=100-i, list_price=100, observed_sales=10,
                category_sales=10, observation_hours=4, order_groups=[], evidence_ids=['e'],
                portfolio=dict(scale_ready=i!=8, estimated_margin=10)))
        analysis = dict(status='ok', inputs=dict(candidates=candidates, initial_exploration=False),
                        hypotheses=[dict(sku=c['sku'], evidence_ids=['e']) for c in candidates])
        result = bounded_proposals(catalog, dict(balance=10000, units=[], surge=[]),
                                   analysis, [], DEFAULT_POLICY, NOW)
        self.assertIn({'sku':'sku8','qty':1}, result)
        self.assertLessEqual(sum(wholesale_bound(catalog[r['sku']])*r['qty']
                                 for r in result if r['sku']!='sku8'), 800)

    def test_supply_units_change_priority_and_truncation_is_unknown(self):
        def score_with(qty, order):
            observe_offers(self.memory, {'order':order,'category':'drinks'},
                           [{'name':'shop','offers':[{'sku':'tea','qty':qty}]}], NOW)
            row = next(r for r in enrich(self.candidates(), self.memory, {'surge':[]}, NOW)['candidates']
                       if r['sku']=='tea')
            return row
        one, many = score_with(1, 'one'), score_with(100, 'many')
        self.assertGreater(one['portfolio_score'], many['portfolio_score'])
        self.memory.record('offer_snapshot','truncated', dict(category='drinks', rows=[], truncated=True), ts=NOW)
        row=next(r for r in enrich(self.candidates(), self.memory, {'surge':[]}, NOW)['candidates'] if r['sku']=='tea')
        self.assertIsNone(row['portfolio']['competitor_shops'])

    def test_buyer_breadth_uses_distinct_settled_buyers(self):
        rows=[event('market_settle', str(i), {'seq':i,'item':'tea','buyer':buyer})
              for i,buyer in enumerate(['one','one','two'],1)]
        candidate=next(r for r in build_candidates(CATALOG, rows, NOW)['candidates'] if r['sku']=='tea')
        self.assertEqual(candidate['distinct_buyers'],2)

    def test_refund_or_unmatured_warranty_blocks_scaling(self):
        for i in range(2):
            self.memory.record('seller_settlement',str(i),dict(unit=str(i),sku='tea',cost=20,
                price=35,list_price=40,holding_hours=1,margin_after_storage=15,
                warranty_matures_at=NOW+60),ts=NOW)
        def row(now):
            return next(r for r in enrich(self.candidates(), self.memory, {'surge':[]}, now)['candidates']
                        if r['sku']=='tea')['portfolio']
        self.assertFalse(row(NOW)['scale_ready'])
        self.assertTrue(row(NOW+61)['scale_ready'])
        self.memory.record('market_broken','refund',dict(unit='0',refunded=True),ts=NOW+61)
        self.assertFalse(row(NOW+61)['scale_ready'])
        self.assertEqual(row(NOW+61)['observed_refunds'],1)

    def test_public_refund_is_retained_without_counting_as_sale(self):
        from r3_observe import refresh_public
        def get(path):
            if path.startswith('/api/ledger'):
                return [dict(seq=1,ts=NOW,kind='broken',data=dict(unit='u',item='tea',refunded=True))]
            return []
        with patch.dict(os.environ,{'R3_ENABLE_LIVE':'1'}):
            refresh_public(self.md,get=get,now=NOW)
        self.assertEqual(len(self.memory.events('market_broken')),1)
        self.assertFalse(self.memory.events('market_settle'))

    def test_locked_inventory_is_not_price_failure_evidence(self):
        from test_r3_trend_watch import TrendWatchTests
        from r3_trend_watch import propose
        fixture=TrendWatchTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        summary=fixture.summary(blocked_units={'u1'})
        self.assertEqual(summary['inventory']['blocked_cost'],20)
        self.assertIsNone(summary['held_category_price_ratio'])
        desired,_=propose(summary,DEFAULT_POLICY)
        self.assertEqual(desired['opening_price_fraction'],DEFAULT_POLICY['opening_price_fraction'])

    def test_checkout_audit_is_idempotent_and_never_stores_tokens(self):
        from test_r3_checkout import CheckoutTests
        from r3_checkout import guarded_shop
        from r3_market_kit import load_kit
        import httpx
        fixture=CheckoutTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        class Market:
            def call(self,*args,**kwargs):
                return httpx.Response(200,json=fixture.inventory)
        shop=guarded_shop(load_kit().AcpShop)(fixture.key,Market(),fixture.md)
        body=dict(receipt=fixture.receipt,line_items=[dict(id='sku1',quantity=1)])
        for _ in range(2):
            self.assertEqual(asyncio.run(shop.create(body,'https://offline.example')).status_code,201)
        with Memory(fixture.md) as memory:
            rows=memory.events('seller_checkout')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['data']['action'],'create')
        self.assertNotIn('checkout_jwt',json.dumps(rows))
        self.assertNotIn('sig_seller',json.dumps(rows))


if __name__=='__main__':
    unittest.main()
