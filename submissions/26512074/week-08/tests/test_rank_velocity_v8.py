import asyncio
import json
import math
import time
import unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from tests.test_integrated_strategy import Case
from strategy_policy import database
from buyer_time_policy import OrderState,action_values,search_ready,execute


class TimeDecisionTests(unittest.TestCase):
    def test_extra_negotiation_requires_more_gain_than_opportunity_cost(self):
        self.assertFalse(action_values(1,1.01,.5,2,.1)['continue_bargaining'])
        self.assertTrue(action_values(1,2,.9,1,.01)['continue_bargaining'])

    def test_frontier_discards_stale_and_out_of_stock_without_logging_budget(self):
        state=OrderState('o',100,200,'goods',['x'],12345)
        state.update('old',dict(at=70,stock=True),100,6)
        state.update('sold',dict(at=99,stock=False),100,6)
        state.update('new',dict(at=99,stock=True),100,6)
        self.assertEqual([x[0] for x in state.frontier],['new'])
        self.assertNotIn('budget',state.summary())

    def test_unattainable_fixed_095_is_not_needed_to_end_search(self):
        row=(0,dict(url='s'),'x',100)
        with patch('buyer_time_policy.outcome',side_effect=lambda value,price,*a:.910 if price==0 else .905),patch('buyer_time_policy.saved_objective',side_effect=lambda q,r:q):
            self.assertTrue(search_ready(row,dict(ask=60,grade='A',response_seconds=1),dict(budget=100),{'x':dict(list_price=100)},[],dict(all_per_second=.01),3,3))


class TimeExecutionTests(Case,unittest.IsolatedAsyncioTestCase):
    async def run_policy(self,overcharge=False,negative=False,claim='A',quote_text='asking 70, grade A',extra_order=None,send_delay=0):
        import buyer_r3
        def task(state,text=''):
            return dict(id='t',status=dict(state=state,message=dict(parts=[dict(text=text)])))
        done=task('TASK_STATE_COMPLETED');done['artifacts']=[dict(parts=[dict(data=dict(item='x',task='t',price=101 if overcharge else 70,claim=claim,sig_seller='test',cert=True))])]
        replies=[task('TASK_STATE_INPUT_REQUIRED',quote_text),done];calls=[]
        class Client:
            def __init__(self,*a,**kw):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):pass
            async def cancel(self,*a):pass
            async def send(self,text,*a):
                if send_delay:await __import__('asyncio').sleep(send_delay)
                calls.append(text);return replies.pop(0)
        rows=[(-.001 if negative else .01,dict(url='s',name='S'),'x',100)]
        from capital_policy import ROUND_END
        fixture_end=ROUND_END if ROUND_END>time.time() else time.time()+3600
        with patch('capital_policy.ROUND_END',fixture_end),patch('protocol.A2AClient',Client),patch.object(buyer_r3,'candidates',return_value=rows),patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda r,*a,**kw:r)),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda r:r)) as refresh,patch('buyer_time_policy.opportunity',return_value=dict(all_per_second=.00001)),patch('buyer_time_policy.outcome',return_value=.8),patch('buyer_time_policy.saved_objective',return_value=.001):
            order=dict(id='o',budget=100,acceptable=['x'],expires=time.time()+60);order.update(extra_order or {})
            result=await execute(buyer_r3,[],order,self.catalog,[],{},self.policy,self.md)
            self.stock_calls=refresh.await_count
        return result,calls

    async def test_fast_settlement_returns_exactly_one_authorized_receipt(self):
        r,calls=await self.run_policy();self.assertEqual(r['receipt']['price'],70)
        self.assertEqual(calls[-1],'70');self.assertEqual(len(calls),2)

    async def test_fresh_stock_does_not_make_redundant_checkout_snapshot_request(self):
        r,_=await self.run_policy();self.assertIsNotNone(r['receipt']);self.assertEqual(self.stock_calls,2)

    async def test_expired_stock_is_still_rechecked_before_checkout(self):
        self.policy['buyer_quote_ttl_seconds']=1e-9
        r,_=await self.run_policy(send_delay=.005);self.assertIsNotNone(r['receipt']);self.assertGreaterEqual(self.stock_calls,3)

    async def test_round_close_does_not_double_discount_already_risk_adjusted_deal(self):
        with patch('capital_policy.ROUND_END',time.time()+10):
            r,_=await self.run_policy()
        self.assertIsNotNone(r['receipt'])

    async def test_budget_violation_cannot_escape_as_receipt(self):
        r,_=await self.run_policy(overcharge=True);self.assertIsNone(r['receipt'])

    async def test_bad_goods_exits_before_contact_and_does_not_block_next_order(self):
        r,calls=await self.run_policy(negative=True);self.assertIsNone(r['receipt']);self.assertEqual(calls,[])

    async def test_wrong_grade_or_withdrawn_warranty_is_not_returned(self):
        r,_=await self.run_policy(claim='C');self.assertIsNone(r['receipt'])
        r,_=await self.run_policy(quote_text='asking 70, grade A, six-hour warranty');self.assertIsNone(r['receipt'])

    async def test_food_life_requirement_survives_fast_execution(self):
        self.catalog['x']['kind']='food'
        r,_=await self.run_policy(quote_text='asking 70, grade A, 1 h of shelf life left',extra_order=dict(kind='food',use_within_hours=3))
        self.assertIsNone(r['receipt'])


class CapitalAndCohortTests(Case):
    def test_cover_is_not_multiplied_by_active_sku_count(self):
        from capital_policy import replenish_hours,ROUND_END
        self.assertAlmostEqual(replenish_hours(self.inv,self.now,1200),1/3)
        self.assertAlmostEqual(replenish_hours(self.inv,ROUND_END-60,1200),1/60)

    def test_prepared_supply_subtracts_existing_grades_and_pending_orders(self):
        from capital_policy import prepared_orders
        self.write('procurement-prepared.json',dict(at=self.now,candidates=[dict(sku='x',target=1,grade_targets={'A':1})]))
        r=prepared_orders(self.md,self.inv,self.catalog,self.now,self.policy)
        self.assertEqual(r['buy'],[dict(sku='x',qty=1)])
        r=prepared_orders(self.md,self.inv,self.catalog,self.now+1,self.policy)
        self.assertEqual(r['buy'],[])

    def test_prepared_supply_honors_twenty_units_and_ten_lines(self):
        from capital_policy import prepared_orders
        candidates=[]
        for n in range(25):
            sku='x'+str(n);self.catalog[sku]=dict(self.catalog['x']);candidates.append(dict(sku=sku,target=30,grade_targets={'A':30}))
        self.write('procurement-prepared.json',dict(at=self.now,candidates=candidates))
        r=prepared_orders(self.md,self.inv,self.catalog,self.now,self.policy)
        self.assertEqual(len(r['buy']),10);self.assertTrue(all(b['qty']<=20 for b in r['buy']))

    def test_inventory_states_include_slow_excess_and_cash_recovery(self):
        from capital_policy import inventory_review
        self.inv['units']=[dict(sku='x',grade='C',cost=50000)]
        r=inventory_review(self.md,self.inv,self.catalog,{'skus':{}},self.now)
        self.assertEqual(r['groups']['x:C']['state'],'low_demand_excess')
        self.assertEqual(r['groups']['x:C']['excess'],1)

    def test_missing_ledger_is_unknown_but_official_break_is_failure(self):
        from round3_metrics import update
        self.catalog['x']['list_price']=100
        events=[dict(seq=1,kind='supply',ts=self.now-31000,data=dict(sku='x',qty=1,total=60,units=['u'])),
                dict(seq=2,kind='settle',ts=self.now-30000,data=dict(order='o',sku='x',buyer='totato2203-r3',shop='other',unit='u',grade='A',amount=70,warranty_hours=6))]
        events.append(dict(seq=99,kind='result',ts=self.now-100,data=dict(buyer='totato2203-r3',order='failed',receipt=None,verified=False)))
        update(self.md,events,self.catalog,self.now)
        r=json.loads((self.md/'buyer-cohort-calibration.json').read_text())
        self.assertEqual(r['windows']['12']['exact_cost'],1)
        self.assertEqual(r['windows']['12']['final_known'],0)
        self.assertIsNone(r['purchases'][0]['final_score'])
        events.append(dict(seq=3,kind='broken',ts=self.now-29000,data=dict(unit='u',shop='other',refunded=0)))
        update(self.md,events,self.catalog,self.now)
        r=json.loads((self.md/'buyer-cohort-calibration.json').read_text())
        self.assertEqual(r['purchases'][0]['final_score'],-1)

    def test_food_is_decided_at_settle_not_after_use_within_hours(self):
        from round3_metrics import update
        self.catalog['x'].update(kind='food',list_price=100)
        with database(self.md,True) as c:c.execute('insert into orders values(?,?,?)',('o',self.now-1100,json.dumps(dict(use_within_hours=100))))
        events=[dict(seq=1,kind='supply',ts=self.now-1200,data=dict(sku='x',qty=1,total=60,units=['u'])),
                dict(seq=2,kind='settle',ts=self.now-1000,data=dict(order='o',sku='x',buyer='totato2203-r3',shop='other',unit='u',grade='A',amount=70,expires_at=self.now-1000+101*3600))]
        update(self.md,events,self.catalog,self.now)
        row=json.loads((self.md/'buyer-cohort-calibration.json').read_text())['purchases'][0]
        self.assertTrue(row['mature']);self.assertAlmostEqual(row['final_score'],.75)

    def test_expected_normal_refund_failure_are_exclusive(self):
        from patch36 import outcome_components
        r=outcome_components(100,70,'x','C','goods',6,'shop',self.md)
        self.assertAlmostEqual(r['normal']+r['refunded']+r['failed'],1)
        self.assertAlmostEqual(r['expected_score'],r['normal']*r['raw_score']-r['failed'])


class RatingPriceForumTests(Case):
    def test_enabled_evaluator_reaches_hierarchy_in_active_path(self):
        from strategy_feedback import evaluate
        self.policy['hierarchical_pricing_enabled']=True;self.save_policy()
        with database(self.md,True) as c:c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry) values(?,?,?,?,?,?,?,?,?,?)',('t','u','x','A',self.now-1000,1.,80000,80000,50000,0))
        result=evaluate(self.md,self.now,True)
        self.assertEqual(result['version'],self.policy['version']);self.assertTrue((self.md/'price-hierarchy.json').exists())

    def test_account_average_marginal_and_anonymous_zero(self):
        from review_policy import marginal
        self.write('review-account-model.json',dict(at=self.now,accounts={'a':dict(mean=4,n=10),'b':dict(mean=5,n=1)},observed_account_mean=4.5,grade_reviews={'A':dict(n=100,mean=5)}))
        with patch('review_policy.time.time',return_value=self.now):
            old=marginal(self.md,'a','A');new=marginal(self.md,'new','A')
            self.assertGreater(new,old);self.assertGreater(old,0);self.assertEqual(marginal(self.md,None,'A'),0)

    def test_generic_negotiation_text_is_not_identity(self):
        from review_policy import identity
        buyer,confidence=identity(self.md,['I want to buy X. That is more than I want to pay. I can do 3.'],{})
        self.assertIsNone(buyer);self.assertEqual(confidence,'anonymous')

    def test_official_order_matched_claim(self):
        from review_policy import identity
        event=dict(data=dict(order='o',buyer='sergiobuilds',expires=self.now+50))
        with database(self.md,True) as c:c.execute('insert into market_events values(?,?,?,?)',(1,self.now-1,'order',json.dumps(event)))
        with patch('review_policy.time.time',return_value=self.now):
            name,confidence=identity(self.md,[],dict(buyer='sergiobuilds',order='o'))
        self.assertEqual(name,'sergiobuilds');self.assertIn('record_matched',confidence)

    def test_inquiry_and_purchase_share_episode_price_and_existing_task_total(self):
        from strategy_feedback import market_price,quote,finalize_quote
        self.policy['inquiry_price_episodes_enabled']=True;self.save_policy();self.market()
        unit=dict(unit='u',sku='x',grade='A',kind='goods',list_price=100000,cost=50000)
        base=market_price(self.md,unit,self.inv,self.now)
        inquiry=quote(self.md,'inq',unit,self.inv,base,self.now,True)
        purchase=quote(self.md,'buy',unit,self.inv,base,self.now+1,False)
        self.assertEqual(inquiry['ask'],purchase['ask'])
        finalize_quote(self.md,'buy',purchase['ask']+10,6)
        repeated=quote(self.md,'buy',unit,self.inv,base,self.now+2)
        self.assertEqual(repeated['locked_total'],purchase['ask']+10)

    def test_hierarchical_fallback_does_not_delete_high_champion(self):
        from price_learning import update
        self.catalog['peer']=dict(kind='goods',category=self.catalog['x']['category'],list_price=100000)
        self.write('catalog.json',self.catalog);self.inv['units']=[dict(sku='x',grade='A')];self.write('inventory.json',self.inv)
        rows=[dict(sku='peer',grade='A',at=self.now-1000,arm=1.2,paid=90000,cost=50000,refunds=0,storage=0,warranty=0,settled_at=self.now-900) for _ in range(30)]
        with database(self.md,True) as c:
            c.execute('insert into price_state values(?,?,?,?,?)',('x','A',2.,self.now-100000,'old'))
            update(self.md,rows,self.now,self.policy,{},c)
        r=json.loads((self.md/'price-hierarchy.json').read_text())['diagnostics']['x:A']
        self.assertTrue(r['revalidate']);self.assertEqual(r['fallback_arm'],1.2)
        with database(self.md) as c:self.assertEqual(c.execute('select arm from price_state where sku="x"').fetchone()[0],2.)

    def test_named_ban_kept_but_generic_pattern_not_refused(self):
        from buyer_access import blocked_buyer
        self.write('seller-access.json',dict(blocked_buyers=['q00','sergiobuilds']))
        with patch('buyer_access.ROOT',self.md),patch('strategy_policy.load',return_value={'verified_buyer_access_enabled':True}),patch('review_policy.identity',return_value=(None,'anonymous')):
            self.assertIsNone(blocked_buyer(['I want to buy X. That is more than I want to pay. I can do 5.']))
        with patch('buyer_access.ROOT',self.md),patch('strategy_policy.load',return_value={'verified_buyer_access_enabled':True}),patch('review_policy.identity',return_value=('sergiobuilds','official_order_record_matched_claim')):
            self.assertEqual(blocked_buyer([],dict(buyer='sergiobuilds')),'sergiobuilds')

    def test_new_joint_cohort_does_not_reset_or_duplicate_predecessor(self):
        import batch_strategy_review as batch
        old=self.md/'old.json';new=self.md/'strategy-v8-review.json'
        old.write_text(json.dumps(dict(status='maturing',controller='existing_market_worker',pid=1,start=0,cohort_end=1000,review_at=2000)))
        new.write_text(json.dumps(dict(status='scheduled',controller='existing_market_worker',pid=1,start=1500,cohort_end=23100,review_at=44700)))
        with patch.multiple(batch,STATE=old,MD=self.md,ROOT=self.md),patch.object(batch,'review',return_value={'changes':[]}) as review:
            result=batch.tick(2001)
        self.assertEqual(review.call_count,1);self.assertEqual(result['status'],'scheduled')
        self.assertEqual(json.loads(new.read_text())['start'],1500)
        self.assertEqual(json.loads(old.read_text())['status'],'complete')

    def test_forum_measures_rating_and_marks_unavailable_voters_unknown(self):
        from forum_value import measure
        self.write('forum.json',[dict(id=1,author='totato2203-r3',role='buyer',ts=self.now-1800,likes=10,dislikes=0,text='actual trade')])
        self.write('patch36-scoreboard.json',dict(buyer=dict(forum=.8,posts=10)))
        measure(self.md,self.now)
        self.write('patch36-scoreboard.json',dict(buyer=dict(forum=.81,posts=11)))
        r=measure(self.md,self.now+31);post=r['posts']['1']
        self.assertIsNone(post['unique_eligible_voters']);self.assertAlmostEqual(post['rating_delta'],.01)
        self.assertGreater(post['all_proxy'],0)

    def test_boundary_does_not_invent_a_rank_gain_between_peers(self):
        from score_coordination import boundary_value,all_value
        targets=dict(at=self.now,components={'shop':{'book':dict(current=100,values=[0,100,200]),'revenue':dict(current=100,values=[0,100,200])}})
        self.assertEqual(boundary_value(targets,'shop',dict(book=-1,revenue=1),self.now),0)
        self.assertGreater(all_value(targets,'shop',dict(book=0,revenue=1),self.now),0)

    def test_pending_projection_cannot_reuse_one_revenue_crossing(self):
        from score_coordination import boundary_value,project_targets
        targets=dict(at=self.now,components={'shop':{'book':dict(current=100,values=[0,100,1000]),'revenue':dict(current=100,values=[0,100,200])}})
        self.assertGreater(boundary_value(targets,'shop',dict(book=-1,revenue=101),self.now),0)
        projected=project_targets(targets,'shop',dict(book=-1,revenue=101))
        self.assertEqual(boundary_value(projected,'shop',dict(book=-1,revenue=1),self.now),0)
