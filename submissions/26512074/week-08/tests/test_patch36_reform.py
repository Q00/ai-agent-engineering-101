import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from patch36 import margin_share,outcome,cost_estimate,observe_buyer_market,ceiling,target_price
from strategy_policy import portfolio,load,database,warranty_offer
from demand_strategy import plan
from buyer_negotiation import offer_for
from ledger_posts import attach


class ReformTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.md=Path(self.temp.name);self.now=time.time();self.policy=load()
        (self.md/'strategy-policy.json').write_text(json.dumps(self.policy))

    def test_all_cash_remains_deployable_with_large_refund_promises(self):
        inv=dict(balance=100000,units=[dict(sku='x',cost=999999)],storage_per_day=.01)
        holds={'u':dict(price=9000000,expires_at=self.now+21600)}
        p=dict(self.policy,cash_reserve_fraction=.9,inventory_fraction=.01,sku_fraction=.01,category_fraction=.01)
        limits=portfolio(inv,{},holds,[],self.now,p,self.md)
        self.assertEqual(limits['budget'],100000)
        for key in ('cash_reserve','base_cash_reserve','warranty_reserve','storage_reserve'):self.assertEqual(limits[key],0)

    def test_pending_supply_cannot_spend_same_cash_twice(self):
        pending=[dict(sku='x',category='x',amount=70000,qty=1,until=self.now+60)]
        self.assertEqual(portfolio(dict(balance=100000,units=[]),{}, {},pending,self.now,self.policy)['budget'],30000)

    def test_warranty_is_recorded_without_holding_cash(self):
        self.assertTrue(warranty_offer(self.md,dict(unit='u',sku='x',kind='goods',grade='A'),1000000,0,self.now,6))
        self.assertEqual(json.loads((self.md/'warranty-holds.json').read_text())['u']['price'],1000000)

    def test_margin_share_rewards_A_over_larger_C_list_discount(self):
        self.assertGreater(margin_share(100,70,65),margin_share(100,55,35))
        self.assertEqual(margin_share(100,20,35),1)

    def test_C_breakage_is_not_treated_as_zero_risk(self):
        self.assertGreater(outcome(100,70,grade='A',md=self.md),outcome(100,55,grade='C',md=self.md))
        self.assertGreater(outcome(100,55,grade='C',warranty=6,md=self.md),outcome(100,55,grade='C',md=self.md))

    def test_only_single_unit_public_supply_learns_individual_cost(self):
        events=[dict(kind='supply',data=dict(qty=2,total=20,units=['u','v'])),
                dict(kind='supply',data=dict(qty=1,total=70,units=['a'])),
                dict(kind='settle',ts=self.now-22000,data=dict(sku='x',grade='A',unit='a',shop='s',amount=75))]
        result=observe_buyer_market(self.md,events,{'x':dict(list_price=100,kind='goods')},self.now)
        self.assertEqual(result['costs']['x:A'],dict(n=1,ratio=.7,samples=[.7]))
        self.assertAlmostEqual(cost_estimate(100,'x','A',self.md),70)

    def test_authorized_budget_is_never_exceeded(self):
        self.assertLessEqual(ceiling(100,40,6,1,'s','x','A',md=self.md),40)
        self.assertLessEqual(offer_for({'margin_target':80},100,40,5,None),40)

    def test_margin_ladder_reaches_ceiling_without_old_twelve_round_delay(self):
        offers=[offer_for({'margin_target':60},100,80,i,None) for i in range(6)]
        self.assertEqual(offers,sorted(offers));self.assertEqual(offers[-1],80)

    def test_bulk_demand_uses_service_cycle_and_official_twenty_unit_limit(self):
        inv=dict(balance=10000000,units=[],restock=dict(open=True,opens_at=self.now,closes_at=self.now+180))
        sku=dict(kind='goods',category='x',list_price=100000,name='X')
        m=dict(trades=20,buyers=4,market_rate=10,own_rate=2,own_trades=4,competitors=2,competitor_stock=0,
               prices={'A':dict(n=20,median=90000,base_median=90000)},supply_cost=50000,supply_samples=20)
        (self.md/'strategy-market.json').write_text(json.dumps(dict(at=self.now,latest_event=self.now,skus={'x':m})))
        result=plan(self.md,inv,{'x':sku},self.now,dry_run=True)
        self.assertEqual(result['buy'],[dict(sku='x',qty=1)])
        m.update(market_rate=200,own_rate=200,own_trades=400,trades=400)
        (self.md/'strategy-market.json').write_text(json.dumps(dict(at=self.now,latest_event=self.now,skus={'x':m})))
        self.assertEqual(plan(self.md,inv,{'x':sku},self.now,dry_run=True)['buy'],[dict(sku='x',qty=20)])
        with database(self.md) as c:self.assertEqual(c.execute('select count(*) from funds').fetchone()[0],0)

    def test_both_roles_attach_real_settlements_without_likes_requests(self):
        with database(self.md,True) as c:
            for seq,role in [(1,'shop'),(2,'buyer')]:
                e=dict(seq=seq,round=3,kind='settle',ts=self.now-1,data=dict(shop='totato2203-r3' if role=='shop' else 'other',
                       buyer='totato2203-r3' if role=='buyer' else 'other',sku='x',amount=70,grade='A',warranty_hours=6))
                c.execute('insert into market_events values(?,?,?,?)',(seq,e['ts'],e['kind'],json.dumps(e)))
        for role,seq in [('shop',1),('buyer',2)]:
            post=attach(self.md,'x'*2500,role,now=self.now)
            self.assertLessEqual(len(post['post']),2000);self.assertEqual(post['refs'],[seq])
            self.assertIn(f'#{seq}',post['post']);self.assertNotIn('좋아요',post['post'])
            self.assertIn('70 credits',post['post'])

    def test_no_evidence_means_no_fabricated_forum_reference(self):
        with database(self.md):pass
        self.assertIsNone(attach(self.md,'body','shop',now=self.now))


if __name__=='__main__':unittest.main()
