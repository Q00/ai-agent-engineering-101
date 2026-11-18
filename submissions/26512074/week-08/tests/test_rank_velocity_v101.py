import json,math,unittest
from unittest.mock import patch
from pathlib import Path
from tests.test_integrated_strategy import Case


class AccountingTests(unittest.TestCase):
    def test_board_sample_records_response_not_collection_start(self):
        import io
        from demand_strategy import fetch_market_sample
        response=io.BytesIO(b'{"buyers":[]}');response.headers={'Date':'source-date','Age':'2'}
        with patch('demand_strategy.urlopen',return_value=response),patch('demand_strategy.time.time',side_effect=[100.,104.]):
            data,sample=fetch_market_sample('/api/board?round=3')
        self.assertEqual(sample,dict(requested_at=100.,response_at=104.,server_date='source-date',cache_age='2'))
        self.assertEqual(data,{'buyers':[]})
    def test_607_and_786_are_different_snapshot_endpoints(self):
        from round3_metrics import reconcile_buyer_counts
        events=[dict(seq=i,ts=i,kind='settle',data=dict(order=f'o{i}',unit=f'u{i}',buyer='totato2203-r3')) for i in range(786)]
        before=dict(at=0,buyer=dict(deals=20887,saved=.633))
        middle=dict(at=606,buyer=dict(deals=21494,saved=.6364))
        end=dict(at=786,buyer=dict(deals=21673,saved=.6376))
        m=reconcile_buyer_counts(events,before,middle,0,606)
        e=reconcile_buyer_counts(events,before,end,0,786)
        mixed=reconcile_buyer_counts(events,before,middle,0,786)
        self.assertEqual((m['official_deals_delta'],m['public_unique_orders']),(607,607))
        self.assertEqual((e['official_deals_delta'],e['public_unique_orders']),(786,786))
        self.assertEqual(mixed['count_residual'],-179)
        self.assertAlmostEqual(e['inverse_net_contribution_estimate'],.76042468193384)

    def test_duplicate_order_not_counted_twice_and_old_refund_not_attributed_to_new(self):
        from round3_metrics import reconcile_buyer_counts
        old=dict(seq=1,ts=-10,kind='settle',data=dict(order='old',unit='old-u',buyer='totato2203-r3'))
        sale=dict(seq=2,ts=10,kind='settle',data=dict(order='new',unit='new-u',buyer='totato2203-r3'))
        refund=dict(seq=3,ts=20,kind='refund',data=dict(unit='old-u',amount=10))
        r=reconcile_buyer_counts([old,sale,sale,refund],dict(buyer=dict(deals=1,saved=.5)),dict(buyer=dict(deals=2,saved=.4)),0,30)
        self.assertEqual(r['duplicate_order_events'],1);self.assertEqual(r['public_unique_orders'],1)
        self.assertEqual(r['prior_purchase_result_changes'],1)
        self.assertFalse(r['inverse_is_new_purchase_final_mean']);self.assertIsNone(r['historical_score_delta'])

    def test_event_order_key_precedes_internal_database_id(self):
        from round3_metrics import order_key
        self.assertEqual(order_key(dict(order='o-real',id='internal')),'o-real')

    def test_saved_crossing_is_strict(self):
        from score_coordination import saved_trades
        k=saved_trades(.5,0,.75,1.)
        self.assertGreater((.5*100+k)/(100+k),.75)

    def test_peer_only_marginal_has_no_artificial_own_score_kink(self):
        from score_coordination import all_value
        t=dict(at=100,peer_only_marginal=True,components=dict(buyer=dict(saved=dict(current=.6,values=[.59,.6,.66]))))
        up=all_value(t,'buyer',dict(saved=.0001),100);down=all_value(t,'buyer',dict(saved=-.0001),100)
        self.assertAlmostEqual(up,-down)


class BuyerTests(unittest.TestCase):
    def test_opening_can_adapt_without_an_unconditional_095_target(self):
        from buyer_time_policy import learned_opening
        p=dict(n=10,settled_n=5,explicit_refusal_rate=.8,floor_ratio_median=.8,avoidable_response_seconds=2)
        price,profile=learned_opening(dict(opening_profiles={'url':p}),'url','x','A',6,60,100)
        self.assertEqual(price,80);self.assertIsNotNone(profile)
        self.assertEqual(learned_opening(dict(opening_profiles={'url':dict(p,settled_n=1)}),'url','x','A',6,60,100)[0],60)

    def test_profiles_count_a_counter_not_every_failed_checkout_as_refusal(self):
        from round3_metrics import opening_profiles
        trace=dict(order_id='o',decisions=[dict(action='offer',first_offer=True,shop='s',sku='x',grade='A',warranty_hours=6,ask=100,offer=60),dict(action='compare',sku='x',response_seconds=2)])
        p=opening_profiles([trace],{'o':dict(data=dict(shop='s',sku='x',amount=80))},{})['s|x|A|6']
        self.assertEqual(p['explicit_refusal_rate'],1);self.assertEqual(p['floor_ratio_median'],.8)


class DemandTests(unittest.TestCase):
    def test_posterior_tail_matches_geometric_predictive(self):
        from capital_policy import count_tail
        self.assertAlmostEqual(count_tail(1,1,1,1),.5)
        self.assertAlmostEqual(count_tail(1,1,1,2),.25)
        self.assertEqual(count_tail(1,1,0,1),0)

    def test_each_additional_unit_and_pending_stock_reduce_sale_probability(self):
        from capital_policy import marginal_sale_probability
        post={'A':dict(shape=1,exposure_hours=1)};w={'A':1.}
        p1=marginal_sale_probability(post,{'A':0},0,w,1,1)['A']
        p2=marginal_sale_probability(post,{'A':0},0,w,2,1)['A']
        pending=marginal_sale_probability(post,{'A':0},1,w,1,1)['A']
        self.assertGreater(p1,p2);self.assertEqual(p2,pending)

    def test_unsold_goods_retain_cost_but_food_can_expire(self):
        from capital_policy import marginal_curve,ROUND_END
        v=dict(kind='goods',estimated_cost=100000,expected_sale=120000,demand_posterior={'*':dict(shape=1,exposure_hours=1)},acquisition_weights={'*':1},held_by_grade={'*':100},next_supply_hours=1/3,shelf_hours=.5)
        goods=marginal_curve(v,dict(storage_per_day=.01),ROUND_END-3600,1)[0]
        food=marginal_curve(dict(v,kind='food'),dict(storage_per_day=.01),ROUND_END-3600,1)[0]
        self.assertGreater(goods['book'],-100);self.assertLess(goods['book'],0)
        self.assertLess(food['book'],-99999)

    def test_existing_eligible_stock_covers_next_window_not_just_total_sku(self):
        from capital_policy import cover_satisfied
        v=dict(next_supply_hours=1/3,acquisition_weights={'A':.5,'B':.5},demand_posterior={'A':dict(shape=3,exposure_hours=1),'B':dict(shape=3,exposure_hours=1)})
        self.assertTrue(cover_satisfied(v,{'A':2,'B':2},0))
        self.assertFalse(cover_satisfied(v,{'A':0,'B':20},0))

    def test_next_real_opening_is_not_forced_to_twenty_minutes(self):
        from capital_policy import replenish_hours
        self.assertEqual(replenish_hours(dict(restock=dict(open=False,opens_at=1300)),1000,1200),300/3600)

    def test_cash_shadow_is_zero_when_observed_alternatives_are_funded(self):
        from capital_policy import cash_shadow_price
        v=dict(worst_cost=100,funding_priority=1,marginal_values=[dict(book=10,all_gain=.01),dict(book=5,all_gain=.005)])
        self.assertEqual(cash_shadow_price([v],300,10),0)
        self.assertGreater(cash_shadow_price([v],50,10),0)


class ContractTests(Case):
    def test_warranty_sessions_are_separate_and_original_champion_survives(self):
        from price_learning import quote_episode,remember_episode
        self.policy.update(contract_price_learning_enabled=True,price_exploration_fraction=0)
        unit=dict(unit='u',sku='x',grade='A')
        arm,epoch,_,_=quote_episode(self.md,'a',unit,self.now,1.25,[1.,1.25],self.policy,False,{'warranty_hours':0})
        remember_episode(self.md,unit,epoch,arm,100,self.now,hours=0)
        _,_,locked,_=quote_episode(self.md,'b',unit,self.now,1.25,[1.,1.25],self.policy,False,{'warranty_hours':6})
        self.assertIsNone(locked);self.assertEqual(arm,1.25)

    def test_contract_learning_does_not_turn_immature_refunds_into_final_loss(self):
        from optimizer import db
        from strategy_policy import database
        from price_learning import update_contracts
        with db(self.md):pass
        r=dict(task='t',unit='u',sku='x',grade='A',warranty=6,at=self.now-60,settled_at=self.now-50,paid=100,cost=80,refunds=100,storage=0,expiry_loss=0,inquiry=0,arm=1.)
        with database(self.md,True) as c:update_contracts(self.md,[r],self.now,self.policy,{},c)
        d=json.loads((self.md/'price-hierarchy.json').read_text());self.assertEqual(d['contract_funnels']['x:A:6']['immature'],1)
        self.assertNotIn('x:A:6',d['contract_diagnostics'])

    def test_contract_groups_do_not_mix_zero_and_six_hour_warranties(self):
        from optimizer import db
        from strategy_policy import database
        from price_learning import update_contracts
        with db(self.md):pass
        rows=[dict(task=str(h),unit=str(h),sku='x',grade='A',warranty=h,at=self.now-30000,settled_at=self.now-29000,paid=100,cost=80,refunds=0,storage=0,expiry_loss=0,inquiry=0,arm=1.) for h in [0,6]]
        with database(self.md,True) as c:update_contracts(self.md,rows,self.now,self.policy,{},c)
        d=json.loads((self.md/'price-hierarchy.json').read_text());self.assertEqual(set(d['contract_diagnostics']),{'x:A:0','x:A:6'})


class ActivationTests(Case):
    def test_prepared_path_buys_profitable_uncovered_stock_and_preserves_funds(self):
        from capital_policy import prepared_orders,ROUND_END
        from strategy_policy import database
        self.now=ROUND_END-3600
        self.policy.update(marginal_demand_allocation_enabled=True,terminal_slot_allocation_enabled=True)
        self.inv['restock'].update(opens_at=self.now,closes_at=self.now+180)
        v=dict(sku='x',kind='goods',target=3,expected_net=50000,expected_sale=150000,estimated_cost=50000,
            actual_recovery=dict(n=4,median_hours=.05),
            terminal_book_per_unit=10000,acquisition_weights={'A':1.},demand_posterior={'A':dict(shape=10,exposure_hours=1)},
            next_supply_hours=1/3,grade_targets={'A':3},grade_costs={'A':50000},grade_expected_sale={'A':150000})
        self.write('procurement-prepared.json',dict(at=self.now,candidates=[v]))
        first=prepared_orders(self.md,self.inv,self.catalog,self.now,self.policy)
        second=prepared_orders(self.md,self.inv,self.catalog,self.now+1,self.policy)
        self.assertEqual(first['buy'],[dict(sku='x',qty=3)]);self.assertEqual(second['buy'],[])
        with database(self.md) as c:self.assertEqual(c.execute('select sum(qty) from funds').fetchone()[0],3)
        self.inv['units']=[dict(unit=str(i),sku='x',grade='A',cost=50000) for i in range(10)]
        self.assertEqual(prepared_orders(self.md,self.inv,self.catalog,self.now+2,self.policy)['buy'],[])

    def test_new_cohort_tick_preserves_old_maturity(self):
        import batch_strategy_review as r
        (self.md/'logs').mkdir();md=self.md/'.market';md.mkdir()
        old=dict(status='maturing',controller='existing_market_worker',start=10,cohort_end=20,review_at=100,version='rank-velocity-v10')
        new=dict(status='scheduled',controller='existing_market_worker',start=60,cohort_end=150,review_at=250,version='rank-velocity-v10.1')
        (md/'strategy-v9-review.json').write_text(json.dumps(old));(md/'strategy-v101-review.json').write_text(json.dumps(new))
        with patch.multiple(r,ROOT=self.md,MD=md),patch.object(r,'review',return_value={'changes':[]}) as run:
            self.assertEqual(r.tick(90)['v10_status'],'maturing');run.assert_not_called()
            self.assertEqual(r.tick(100)['v10_status'],'joint_review_complete');run.assert_called_once()
            self.assertEqual(run.call_args.args[0]['version'],'rank-velocity-v10')
        self.assertEqual(json.loads((md/'strategy-v101-review.json').read_text())['cohort_end'],150)
