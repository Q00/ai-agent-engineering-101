import json,time,unittest
from unittest.mock import patch
from tests.test_integrated_strategy import Case


class ValueTests(unittest.TestCase):
    def test_supply_slot_prefers_large_real_profit_when_cash_is_available(self):
        from capital_policy import terminal_funding,ROUND_END
        inv=dict(balance=20000000,storage_per_day=.01)
        large=dict(need=1,worst_cost=1000000,estimated_cost=1000000,expected_sale=1100000,expected_net=95000,kind='goods',payback_hours=10)
        small=dict(need=20,worst_cost=10000,estimated_cost=10000,expected_sale=11000,expected_net=1000,kind='food',payback_hours=1/3)
        self.assertGreater(small['expected_net']*20/small['payback_hours'],large['expected_net']/large['payback_hours'])
        with patch('score_coordination.all_value',side_effect=lambda t,s,d,n:d['book']):
            a=terminal_funding(large,inv,{},ROUND_END-36*3600,10)
            b=terminal_funding(small,inv,{},ROUND_END-36*3600,10)
        self.assertGreater(a['funding_priority'],b['funding_priority'])

    def test_terminal_unsold_goods_keep_book_cost_and_exposure_is_discounted(self):
        from capital_policy import terminal_funding,ROUND_END
        v=dict(need=1,worst_cost=1000000,estimated_cost=1000000,expected_sale=1100000,expected_net=95000,kind='goods',payback_hours=10)
        with patch('score_coordination.all_value',return_value=1):r=terminal_funding(v,dict(balance=20000000,storage_per_day=.01),{},ROUND_END-3600,10)
        self.assertAlmostEqual(r['terminal_sell_probability'],.1)
        self.assertGreater(r['terminal_book_per_unit'],0)
        self.assertLess(r['terminal_book_per_unit'],10000)

    def test_buyer_does_not_rank_only_by_quote_ping(self):
        from buyer_time_policy import candidate_priority
        self.assertGreater(8/.05,10/.2)
        self.assertGreater(candidate_priority(10,.2,3),candidate_priority(8,.05,3))

    def test_dominated_mature_price_arm_does_not_consume_exploration(self):
        from price_learning import exploration_arm
        estimates={'1.0':dict(value=10,se=.1,n=50),'0.5':dict(value=1,se=.1,n=50)}
        self.assertTrue(all(exploration_arm([1.,.5],estimates,d,30,2)==1. for d in (0,500000,999999999)))

    def test_sparse_price_arm_is_not_declared_bad(self):
        from price_learning import exploration_arm
        estimates={'1.0':dict(value=10,se=.1,n=50),'0.5':dict(value=1,se=5,n=1)}
        outcomes={exploration_arm([1.,.5],estimates,d*10000,30,2) for d in range(0,1000000,10000)}
        self.assertIn(.5,outcomes)

    def test_disappeared_forum_post_still_matures_and_observation_is_frozen(self):
        from forum_value import mature_stored_posts
        p={'1':dict(role='buyer',ts=0,initial_author_forum=.8,initial_post_percentile=.3,initial_author_posts=10),'2':dict(role='shop',ts=0,initial_author_forum=.8)}
        mature_stored_posts(p,.9,12,.4,1600)
        self.assertAlmostEqual(p['1']['all_proxy'],.04)
        mature_stored_posts(p,.95,15,.5,1800)
        self.assertAlmostEqual(p['1']['all_proxy'],.04)
        self.assertNotIn('all_proxy',p['2'])

    def test_missed_day_old_forum_observation_is_not_fabricated(self):
        from forum_value import mature_stored_posts
        p={'1':dict(role='buyer',ts=0,initial_author_forum=.8)}
        mature_stored_posts(p,.9,12,.4,86400);self.assertNotIn('all_proxy',p['1'])


class IntegrationTests(Case):
    def test_supply_forecast_uses_observed_mean_not_upper_quantile(self):
        events=self.events()
        supplies=[e for e in events if e['kind']=='supply']
        for e,per_unit in zip(supplies,[10000,10000,10000,100000,100000,100000]):e['data']['total']=2*per_unit
        market=self.market(events)
        self.assertEqual(market['skus']['x']['supply_cost_mean'],55000)
        self.assertGreater(market['skus']['x']['supply_cost'],55000)

    def test_existing_inventory_can_recover_cash_without_terminal_book_loss(self):
        from strategy_feedback import market_price
        self.policy['terminal_slot_allocation_enabled']=True;self.save_policy()
        self.write('strategy-market.json',dict(at=self.now,skus={'x':dict(prices={'A':dict(n=10,median=100000)},own_rate=0,market_rate=0)}))
        self.write('inventory-plan.json',dict(at=self.now,groups={'x:A':dict(state='low_demand_excess',qty=10,target=1,expected_storage=3000,conversion=.1,expected_hours_to_sell=36,remaining_round_hours=36)}))
        unit=dict(unit='u',sku='x',grade='A',kind='goods',cost=70000,list_price=100000,supplied_at=self.now-6000)
        with patch('score_coordination.all_value',side_effect=lambda t,s,d,n:d['book']*1e-6+d['revenue']*1e-9) as value,patch('score_coordination.boundary_value',return_value=0):
            market_price(self.md,unit,self.inv,self.now)
        candidates=[call.args[2] for call in value.call_args_list if call.args[2]['revenue']<70000 and call.args[2]['book']>=0]
        self.assertTrue(candidates)

    def test_grade_targets_never_replace_class_score_targets(self):
        from capital_policy import prepared_orders
        self.write('score-targets.json',dict(components={'shop':{}},at=self.now))
        self.write('procurement-prepared.json',dict(at=self.now,candidates=[dict(sku='x',target=1,grade_targets={'A':1},loss_exception=True,expected_net=-1,expected_sale=90000)]))
        with patch('score_coordination.boundary_value',return_value=1) as boundary,patch('score_coordination.project_targets',side_effect=lambda t,*a:t):
            r=prepared_orders(self.md,self.inv,self.catalog,self.now,self.policy)
        self.assertEqual(len(r['buy']),1)
        self.assertIn('components',boundary.call_args.args[0])

    def test_contract_calibration_never_uses_uncovered_outcomes_for_covered_purchase(self):
        from patch36 import calibrated_probabilities
        data=dict(at=self.now,final_outcomes={'goods:A':dict(n=100,normal=50,refunded=0,failed=50,complete=True)},contract_outcomes={'goods:A:6plus':dict(n=80,normal=80,refunded=0,failed=0,complete=True),'goods:A:none':dict(n=80,normal=40,refunded=0,failed=40,complete=True)})
        with patch('patch36.document',return_value=data),patch('patch36.time.time',return_value=self.now),patch('strategy_policy.load',return_value={'contract_risk_calibration_enabled':True}):
            covered=calibrated_probabilities(self.md,'goods','A',.9,.1,0,6)
            uncovered=calibrated_probabilities(self.md,'goods','A',.9,0,.1,0)
        self.assertGreater(covered[0],uncovered[0]);self.assertEqual(covered[2],0)

    def test_collector_records_warranty_contract_outcomes(self):
        from round3_metrics import update
        self.catalog['x']['list_price']=100
        events=[]
        for i,(u,w,refund) in enumerate([('u1',6,70),('u2',0,0)]):
            events.extend([dict(seq=i*3+1,ts=self.now-31000,kind='supply',data=dict(sku='x',qty=1,total=60,units=[u])),dict(seq=i*3+2,ts=self.now-30000,kind='settle',data=dict(order=u,sku='x',unit=u,buyer='totato2203-r3',shop='other',grade='A',amount=70,warranty_hours=w)),dict(seq=i*3+3,ts=self.now-29000,kind='broken',data=dict(unit=u,shop='other',refunded=refund))])
        update(self.md,events,self.catalog,self.now)
        outcomes=json.loads((self.md/'buyer-cohort-calibration.json').read_text())['contract_outcomes']
        self.assertEqual(outcomes['goods:A:6plus']['refunded'],1)
        self.assertEqual(outcomes['goods:A:none']['failed'],1)
