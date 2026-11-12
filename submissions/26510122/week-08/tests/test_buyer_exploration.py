"""Explore beyond the ranked shortlist without changing checkout authority."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import buyer_exploration as exploration
import buyer_memory
import market_buyer_r3 as buyer


class FixedRandom:
    def __init__(self,draw=0.,index=-1):
        self.draw=draw;self.index=index;self.choice_calls=0
    def random(self):return self.draw
    def choice(self,rows):
        self.choice_calls+=1
        return rows[self.index]


class ExplorationPlanTests(unittest.TestCase):
    def test_probability_and_branch_boundaries(self):
        for value in (-.1,1.1,float('nan'),float('inf'),True,'bad'):
            with self.subTest(value=value),self.assertRaises((TypeError,ValueError)):
                exploration.probability(value)
        rows=[{'id':'0'},{'id':'1'}]
        for draw,epsilon,mode in ((.099,.1,'explore'),(.1,.1,'model'),(0.,0.,'model'),(.999,1.,'explore')):
            with self.subTest(draw=draw,epsilon=epsilon):
                rng=FixedRandom(draw)
                result=exploration.plan(rows,epsilon,rng)
                self.assertEqual(result['mode'],mode)
                self.assertEqual(rng.choice_calls,int(mode=='explore'))
        self.assertEqual(exploration.plan(rows,1,FixedRandom())['forced_candidate_id'],'1')

    def test_single_candidate_has_no_artificial_exploration(self):
        class NoRandom:
            def random(self):raise AssertionError('unnecessary random draw')
        result=exploration.plan([{'id':'0'}],1,NoRandom())
        self.assertEqual(result['mode'],'single_candidate')
        self.assertIsNone(result['draw'])


class BuyerExplorationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.previous=Path.cwd();os.chdir(self.directory.name)
        self.addCleanup(self.restore)
        self.order={'order':'o1','item':'food','kind':'food','budget':1200,'acceptable':['F1'],
                    'use_within_hours':4,'expires':time.time()+500}
        self.catalog={'F1':{'kind':'food','list_price':1000,'shelf_hours':24,'name':'Food'}}
        self.shops=[{'name':f'shop-{n:02d}','url':f'https://shop-{n:02d}',
                     'offers':[{'sku':'F1','qty':1}],'reviews':[]} for n in range(18)]

    def restore(self):
        os.chdir(self.previous);self.directory.cleanup()

    def outcome(self,price=700,task='t1'):
        return {'receipt':{'item':'F1','price':price,'task':task,'sig_seller':'test-signature'},
                'reason':'receipt received','offers':[price],'task_id':task}

    def test_full_pool_preserves_new_shops_and_deduplicates_offers(self):
        self.shops[0]['offers']*=3
        shortlist=buyer.candidates(self.order,self.shops,self.catalog,'own')
        full=buyer.candidates(self.order,self.shops,self.catalog,'own',full_pool=True)
        self.assertEqual(len(shortlist),16)
        self.assertEqual(len(full),18)
        self.assertNotIn('shop-17',[c['shop'] for c in shortlist])
        self.assertIn('shop-17',[c['shop'] for c in full])

    def test_exploration_uses_the_existing_eligibility_filters(self):
        bad=[{'name':'own','url':'https://own','offers':[{'sku':'F1','qty':1}]},
             {'name':'empty','url':'https://empty','offers':[{'sku':'F1','qty':0}]},
             {'name':'wrong','url':'https://wrong','offers':[{'sku':'G1','qty':1}]},
             {'name':'short','url':'https://short','offers':[{'sku':'F2','qty':1}]}]
        self.order['acceptable'].append('F2')
        self.catalog['F2']={'kind':'food','list_price':1000,'shelf_hours':1}
        eligible=buyer.candidates(self.order,bad+self.shops[:1],self.catalog,'own',full_pool=True)
        self.assertEqual([c['shop'] for c in eligible],['shop-00'])

    def test_random_target_cannot_be_replaced_by_model_and_price_is_capped(self):
        model={'candidate_id':'0','opening_offer':9999,'reason':'prefer familiar first shop'}
        with patch.object(buyer,'decide_json',return_value=model) as decide, \
             patch.object(buyer,'negotiate',return_value=self.outcome(722)) as negotiate:
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=1,rng=FixedRandom())
        self.assertEqual(result['shop'],'https://shop-17')
        self.assertEqual(negotiate.call_args.kwargs['opening_offer'],722)
        self.assertEqual(negotiate.call_args.kwargs['cap'],850)
        prompt=decide.call_args.args[1];schema=decide.call_args.args[2]
        self.assertEqual([c['id'] for c in prompt['candidate_shop_skus']],['17'])
        self.assertEqual(schema['properties']['candidate_id']['enum'],['17'])
        history=buyer_memory.read_events()
        opportunities=next(e for e in history if e['event']=='selection_opportunities')
        self.assertEqual(len(opportunities['eligible_candidates']),18)
        self.assertEqual(opportunities['mode'],'explore')
        self.assertEqual(opportunities['order_budget'],1200)
        self.assertEqual(opportunities['use_within_hours'],4)
        self.assertEqual(opportunities['eligible_candidates'][-1]['listed_shelf_hours'],24)
        selected=next(e for e in history if e['event']=='buyer_policy_selection')
        self.assertEqual(selected['model_candidate_id'],'0')
        self.assertEqual(selected['candidate_id'],'17')
        self.assertEqual(selected['model_reason'],'prefer familiar first shop')
        attempt=next(e for e in history if e['event']=='negotiation')
        self.assertEqual(attempt['policy_mode'],'explore')
        self.assertEqual(attempt['result'],'receipt_pending')
        self.assertEqual(buyer_memory.context('https://shop-17','F1')['same_sku_payment_outcomes'],{})

    def test_model_failure_still_uses_forced_candidate(self):
        with patch.object(buyer,'decide_json',side_effect=RuntimeError('offline')), \
             patch.object(buyer,'negotiate',return_value=self.outcome(680)) as negotiate:
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=1,rng=FixedRandom())
        self.assertEqual(result['shop'],'https://shop-17')
        self.assertEqual(negotiate.call_args.kwargs['opening_offer'],680)

    def test_zero_epsilon_keeps_model_selection_and_the_normal_shortlist(self):
        rng=FixedRandom()
        with patch.object(buyer,'decide_json',return_value={
                'candidate_id':'0','opening_offer':700,'reason':'firsthand evidence'}) as decide, \
             patch.object(buyer,'negotiate',return_value=self.outcome()):
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=0,rng=rng)
        self.assertEqual(result['shop'],'https://shop-00')
        self.assertEqual(len(decide.call_args.args[1]['candidate_shop_skus']),16)
        self.assertEqual(rng.choice_calls,0)

    def test_fallback_payment_is_not_attributed_to_exploration_target(self):
        missed={'receipt':None,'reason':'walked away','offers':[650],'task_id':'t1'}
        with patch.object(buyer,'decide_json',return_value={
                'candidate_id':'17','opening_offer':700,'reason':'explore'}), \
             patch.object(buyer,'negotiate',side_effect=[missed,self.outcome(700,'t2')]):
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=1,rng=FixedRandom())
        self.assertEqual(result['shop'],'https://shop-00')
        attempts=[e for e in buyer_memory.read_events() if e['event']=='negotiation']
        self.assertEqual([e['policy_mode'] for e in attempts],['explore','fallback'])
        self.assertEqual(attempts[0]['selection_id'],attempts[1]['selection_id'])
        Path('.market').mkdir()
        Path('.market/payments.jsonl').write_text(json.dumps({'order':'o1','shop':'https://shop-00',
            'item':'F1','price':700,'paid':True,'ledger':33})+'\n')
        buyer_memory.import_payments()
        self.assertEqual(buyer_memory.context('https://shop-00','F1')['same_sku_payment_outcomes'],{'paid':1})
        self.assertEqual(buyer_memory.context('https://shop-17','F1')['same_sku_payment_outcomes'],{})

    def test_slow_primary_leaves_time_for_guarded_fallback(self):
        clock=[1000.]
        deadlines=[]
        def negotiate(*args,**kwargs):
            deadlines.append(kwargs['deadline'])
            if len(deadlines)==1:
                clock[0]=kwargs['deadline']+.1
                return {'receipt':None,'reason':'first shop timed out','offers':[650],'task_id':'t1'}
            self.assertGreaterEqual(kwargs['deadline']-clock[0],80)
            return self.outcome(700,'t2')
        with patch.object(buyer.time,'monotonic',side_effect=lambda:clock[0]),              patch.object(buyer,'decide_json',return_value={
                 'candidate_id':'0','opening_offer':650,'reason':'compare'}),              patch.object(buyer,'negotiate',side_effect=negotiate):
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=0)
        self.assertEqual(len(deadlines),2)
        self.assertLess(deadlines[0],deadlines[1])
        self.assertEqual(result['receipt']['task'],'t2')
        self.assertTrue(result['accept']['cert'])
        self.assertEqual(result['accept']['min_hours_left'],4)

    def test_expired_order_logs_no_attempt_instead_of_a_failed_exploration(self):
        self.order['expires']=time.time()-1
        with patch.object(buyer,'decide_json',return_value={
                'candidate_id':'17','opening_offer':700,'reason':'explore'}), \
             patch.object(buyer,'negotiate') as negotiate:
            buyer.run(self.order,self.shops,self.catalog,'own',epsilon=1,rng=FixedRandom())
        negotiate.assert_not_called()
        history=buyer_memory.read_events()
        self.assertFalse(any(e['event']=='negotiation' for e in history))
        self.assertEqual(next(e for e in history if e['event']=='negotiation_skipped')['policy_mode'],'explore')


if __name__=='__main__':unittest.main()
