import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from strategy_policy import ROOT,load,active,database,portfolio,warranty_offer
from demand_strategy import observe,plan,record_order,substitution,collect
from strategy_feedback import market_price,quote,finalize_quote,reconcile_paid,evaluate


class Case(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.md=Path(self.temp.name)
        self.policy=load();self.policy['enabled']=True
        self.save_policy();self.now=1000000
        self.inv=dict(round=3,balance=100000000,units=[],restock=dict(open=True,opens_at=self.now,closes_at=self.now+180))
        self.catalog={'x':dict(kind='goods',category='goods/x',list_price=100000,name='X')}
        self.write('inventory.json',self.inv);self.write('catalog.json',self.catalog)
    def write(self,name,value):self.md.joinpath(name).write_text(json.dumps(value),encoding='utf-8')
    def save_policy(self):self.write('strategy-policy.json',self.policy)
    def events(self,sku='x',n=30,own=False,price=90000,cost=50000,kind='goods'):
        events=[dict(seq=sku+'sale'+str(i),kind='settle',ts=self.now-100+i,round=3,
                     data=dict(sku=sku,shop='totato2203-r3' if own else 's'+str(i%2),buyer='b'+str(i%5),
                               amount=price,grade='A',unit=sku+str(i),expires_at=self.now+86400 if kind=='food' else None)) for i in range(n)]
        events += [dict(seq=sku+'supply'+str(i),kind='supply',ts=self.now-50+i,round=3,
                        data=dict(sku=sku,shop='s'+str(i%2),qty=2,total=cost*2)) for i in range(6)]
        # Official seqs are numeric, distinct and stable for replay tests.
        for i,e in enumerate(events):e['seq']=10000*(1+sum(map(ord,sku)))+i
        return events
    def market(self,events=None,shops=()):
        return observe(self.md,self.inv,self.catalog,self.events() if events is None else events,list(shops),self.now)
    def limits(self,pending=(),holds=None):return portfolio(self.inv,self.catalog,holds or {},pending,self.now,self.policy,self.md)


class FundingTests(Case):
    def test_default_fractions(self):
        self.assertEqual([self.policy[k] for k in ('cash_reserve_fraction','inventory_fraction','sku_fraction','category_fraction')],[0,1,1,1])
    def test_invalid_fraction_rejected(self):
        self.policy['sku_fraction']=1.1;self.save_policy()
        with self.assertRaises(ValueError):load(self.md)
    def test_bool_fraction_rejected(self):
        self.policy['inventory_fraction']=True;self.save_policy()
        with self.assertRaises(ValueError):load(self.md)
    def test_no_old_fixed_purchase_cap(self):self.assertGreater(self.limits()['budget'],250000)
    def test_no_old_fixed_inventory_cap(self):self.assertGreater(self.limits()['inventory_cap'],1500000)
    def test_inventory_never_creates_cash_reserve(self):
        self.inv['units']=[dict(unit='u',sku='x',cost=10000000)]
        self.assertEqual(self.limits()['base_cash_reserve'],0)
    def test_pending_cash_cannot_be_spent_twice(self):
        p=[dict(id='p',sku='x',category='goods/x',qty=10,amount=1000000,until=self.now+60)]
        self.assertEqual(self.limits()['budget']-self.limits(p)['budget'],1000000)
    def test_expired_reservation_does_not_lock_funds(self):
        self.assertEqual(self.limits([dict(sku='x',category='goods/x',qty=1,amount=99999999,until=0)])['pending'],0)
    def test_warranty_largest_claim_does_not_sequester_cash(self):
        holds={'u':dict(price=4000000,expires_at=self.now+100,sku='x',grade='A',hours=6)}
        self.assertEqual(self.limits(holds=holds)['warranty_reserve'],0)
    def test_category_and_sku_exposures_include_pending(self):
        p=[dict(sku='x',category='goods/x',qty=1,amount=100,until=self.now+100)]
        r=self.limits(p);self.assertEqual(r['sku_exposure']['x'],100);self.assertEqual(r['category_exposure']['goods/x'],100)
    def test_pending_procurement_does_not_require_a_new_warranty_reserve(self):
        self.inv['balance']=1000000;self.write('inventory.json',self.inv)
        with database(self.md,True) as c:c.execute('insert into funds values(?,?,?,?,?,?,?)',('p','x','goods/x',1,800000,self.now,self.now+100))
        self.assertTrue(warranty_offer(self.md,dict(unit='u',sku='x',kind='goods',grade='A'),200000,1000000,self.now,6))
    def test_existing_stock_does_not_cap_cash_investment(self):
        self.inv['units']=[dict(unit='u',sku='x',cost=120000000)]
        self.assertEqual(self.limits()['budget'],100000000)


class DemandTests(Case):
    def test_stale_cursor_recovers_latest_tail_with_gap_label(self):
        old=dict(seq=1,kind='order',ts=self.now-100,data={})
        with database(self.md,True) as c:c.execute('insert into market_events values(?,?,?,?)',(1,old['ts'],'order',json.dumps(old)))
        newest=dict(seq=10000,kind='settle',round=3,ts=self.now-1,data=dict(sku='x',shop='s',buyer='b',amount=90000,grade='A'))
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return json.dumps(self.data).encode()
        def fetch(url,**kw):return Response([newest] if '/ledger?' in url else {'shops':[]})
        with patch('demand_strategy.urlopen',side_effect=fetch),patch('demand_strategy.time.time',return_value=self.now):
            result=collect(self.md,True)
        self.assertTrue(result['coverage']['gap_detected']);self.assertEqual(result['latest_event'],self.now-1)
    def test_refund_and_broken_are_not_double_charged(self):
        events=[dict(seq=1,kind='broken',ts=self.now-2,data=dict(shop='totato2203-r3',unit='u',refunded=100)),
                dict(seq=2,kind='refund',ts=self.now-1,data=dict(shop='totato2203-r3',unit='u',amount=100))]
        self.assertEqual(self.market(events)['financial']['refunds'],100)
    def test_food_expiring_before_next_window_does_not_block_fresh_stock(self):
        self.catalog['x'].update(kind='food',shelf_hours=24)
        self.inv['units']=[dict(sku='x',unit=str(i),cost=50000,grade='A',expires_at=self.now+10) for i in range(20)]
        self.market(self.events(own=True,kind='food'))
        self.assertTrue(plan(self.md,self.inv,self.catalog,self.now)['buy'])
    def test_real_settles_establish_demand(self):self.assertEqual(self.market()['skus']['x']['trades'],30)
    def test_replayed_events_not_counted_twice(self):
        self.market();self.assertEqual(self.market()['skus']['x']['trades'],30)
    def test_opaque_orders_do_not_establish_product_demand(self):
        e=[dict(seq=1,kind='order',ts=self.now,round=3,data=dict(item='x'))]
        self.assertEqual(self.market(e)['skus']['x']['trades'],0)
    def test_offline_competitor_keeps_effective_stock(self):
        m=self.market(shops=[dict(name='offline',url='http://offline',alive=False,stock=dict(x=40))])
        self.assertGreater(m['skus']['x']['competitor_stock'],0)
    def test_quantity_can_exceed_four(self):
        self.market(self.events(own=True));r=plan(self.md,self.inv,self.catalog,self.now)
        self.assertGreater(r['buy'][0]['qty'],4)
    def test_official_quantity_maximum_is_preserved(self):
        self.market(self.events(n=300,own=True));r=plan(self.md,self.inv,self.catalog,self.now)
        self.assertLessEqual(r['buy'][0]['qty'],20)
    def test_profitable_surge_above_ten_percent_is_allowed(self):
        self.inv['surge']=[dict(sku='x',factor=1.2,units=100)];self.market()
        self.assertTrue(plan(self.md,self.inv,self.catalog,self.now)['buy'])
    def test_second_planner_cannot_duplicate_pending_order(self):
        self.market();first=plan(self.md,self.inv,self.catalog,self.now);self.assertTrue(first['buy'])
        self.assertEqual(plan(self.md,self.inv,self.catalog,self.now+1)['buy'],[])
    def test_dry_run_does_not_reserve_cash(self):
        self.market();self.assertTrue(plan(self.md,self.inv,self.catalog,self.now,dry_run=True)['buy'])
        with database(self.md) as c:self.assertEqual(c.execute('select count(*) from funds').fetchone()[0],0)
    def test_existing_inventory_is_subtracted_from_expected_sales(self):
        self.inv['units']=[dict(unit=str(i),sku='x',cost=50000,grade='A') for i in range(1000)];self.market()
        self.assertEqual(plan(self.md,self.inv,self.catalog,self.now)['buy'],[])
    def test_stale_market_does_not_authorize_spending(self):
        self.market();self.assertEqual(plan(self.md,self.inv,self.catalog,self.now+1000)['buy'],[])
    def test_closed_supply_window_produces_no_order(self):
        self.market();self.inv['restock']['open']=False;self.assertEqual(plan(self.md,self.inv,self.catalog,self.now)['buy'],[])
    def test_actual_order_observation_does_not_log_budget(self):
        record_order(self.md,dict(order='o',acceptable=['x'],budget=999999,use_within_hours=12),self.now)
        with database(self.md) as c:raw=c.execute('select payload from orders').fetchone()[0]
        self.assertNotIn('budget',raw);self.assertIn('acceptable',raw)
    def test_acceptable_substitution_is_labelled_estimated(self):
        self.catalog['y']=dict(self.catalog['x']);record_order(self.md,dict(order='o',acceptable=['x','y']),self.now)
        m=self.market();_,source,_=substitution('y',self.catalog['y'],m,self.catalog,self.policy)
        self.assertEqual(source,'observed_acceptable_estimate')
    def test_financial_report_separates_cash_book_and_supply(self):
        m=self.market();f=m['financial'];self.assertIn('supply_spend',f);self.assertEqual(f['book'],f['cash_profit']+f['stock_cost'])
    def test_food_and_goods_can_share_supply_slots(self):
        self.catalog['f']=dict(kind='food',category='food',list_price=10000,name='F',shelf_hours=24)
        self.market(self.events(own=True)+self.events('f',own=True,price=9000,cost=5000,kind='food'))
        r=plan(self.md,self.inv,self.catalog,self.now)
        self.assertEqual({b['sku'] for b in r['buy']},{'f','x'})


class FeedbackTests(Case):
    def test_expiry_cost_is_allocated_once_across_quote_opportunities(self):
        self.market();u=self.unit();b=market_price(self.md,u,self.inv,self.now)
        quote(self.md,'first',u,self.inv,b,self.now);quote(self.md,'second',u,self.inv,b,self.now)
        e=dict(seq=99990,kind='expire',ts=self.now+1,data=dict(shop='totato2203-r3',unit='u',cost=50000))
        with database(self.md,True) as c:c.execute('insert into market_events values(?,?,?,?)',(e['seq'],e['ts'],e['kind'],json.dumps(e)))
        reconcile_paid(self.md,self.now+10);reconcile_paid(self.md,self.now+20)
        with database(self.md) as c:self.assertEqual(c.execute('select sum(amount) from price_losses').fetchone()[0],50000)
    def unit(self,**kw):return dict(dict(unit='u',sku='x',kind='goods',grade='A',cost=50000,list_price=100000,supplied_at=self.now-3600),**kw)
    def test_same_grade_market_price_is_used(self):
        self.market();p=market_price(self.md,self.unit(),self.inv,self.now);self.assertEqual(p['market_reference'],90000)
    def test_clearance_food_can_sell_below_cost(self):
        p=market_price(self.md,self.unit(kind='food',expires_at=self.now+1800),self.inv,self.now)
        self.assertLess(p['ask'],50000);self.assertEqual(p['floor'],1)
    def test_expired_food_has_no_quote(self):self.assertIsNone(market_price(self.md,self.unit(expires_at=1),self.inv,self.now))
    def test_quote_is_bound_once(self):
        self.market();u=self.unit();b=market_price(self.md,u,self.inv,self.now)
        first=quote(self.md,'t',u,self.inv,b,self.now);second=quote(self.md,'t',u,self.inv,dict(b,ask=999999),self.now+1)
        self.assertEqual(first['ask'],second['ask'])
    def test_quote_only_is_not_a_purchase(self):
        self.market();u=self.unit();quote(self.md,'q',u,self.inv,market_price(self.md,u,self.inv,self.now),self.now,True)
        with database(self.md) as c:r=c.execute('select * from price_quotes').fetchone()
        self.assertEqual(r['inquiry'],1);self.assertIsNone(r['paid'])
    def test_final_quote_tracks_warranty_total(self):
        self.market();u=self.unit();quote(self.md,'t',u,self.inv,market_price(self.md,u,self.inv,self.now),self.now)
        finalize_quote(self.md,'t',123456,6)
        with database(self.md) as c:r=c.execute('select * from price_quotes').fetchone()
        self.assertEqual(r['quote'],123456);self.assertEqual(r['warranty'],6)
    def seed_arms(self,arm_nets,champion=1.0,count=20):
        with database(self.md,True) as c:
            c.execute('insert into price_state values(?,?,?,?,?)',('x','A',champion,0,'fixture'))
            seq=1
            for arm,net in arm_nets.items():
                for i in range(count):
                    c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry,settle_seq,settled_at,paid) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (str(seq),str(seq),'x','A',self.now-1000,arm,100,100,50,0,seq,self.now-900,50+net));seq+=1
    def test_positive_opportunity_contribution_can_raise_price(self):
        self.seed_arms({1.0:10,1.1:30});r=evaluate(self.md,self.now,True)
        self.assertEqual(r['changes'][0]['after'],1.1)
    def test_worse_price_multiple_is_rolled_back(self):
        self.seed_arms({1.0:30,1.1:10},1.1);r=evaluate(self.md,self.now,True)
        self.assertEqual(r['changes'][0]['after'],1.0)
    def test_small_sample_does_not_change_champion(self):
        self.seed_arms({1.0:10,1.1:30},count=1);self.assertEqual(evaluate(self.md,self.now,True)['changes'],[])
    def test_mature_warranted_sales_remain_in_feedback_window(self):
        self.seed_arms({1.0:10,1.1:30})
        with database(self.md,True) as c:
            c.execute('update price_quotes set at=?,settled_at=?,warranty=72',(self.now-25200,self.now-25000))
        self.assertEqual(evaluate(self.md,self.now,True)['changes'][0]['after'],1.1)
    def test_one_paid_settlement_not_assigned_to_multiple_quotes(self):
        self.market();u=self.unit();b=market_price(self.md,u,self.inv,self.now)
        quote(self.md,'winner',u,self.inv,b,self.now);quote(self.md,'other',u,self.inv,b,self.now)
        events=[dict(seq=99901,kind='settle',ts=self.now+10,data=dict(shop='totato2203-r3',unit='u',sku='x',amount=80000,order='o')),
                dict(seq=99902,kind='result',ts=self.now+11,data=dict(shop='totato2203-r3',order='o',verified=True,receipt=dict(task='winner')))]
        with database(self.md,True) as c:
            for e in events:c.execute('insert into market_events values(?,?,?,?)',(e['seq'],e['ts'],e['kind'],json.dumps(e)))
        reconcile_paid(self.md,self.now+20);reconcile_paid(self.md,self.now+30)
        with database(self.md) as c:paid=c.execute('select task from price_quotes where paid is not null').fetchall()
        self.assertEqual([r[0] for r in paid],['winner'])
