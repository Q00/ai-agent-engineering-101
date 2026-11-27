"""Seller cash policy: monetary limits, forecast attribution, honest receipts; no network."""
import collections
import unittest
from unittest.mock import patch

import seller
import seller_cash_policy as cash
import store
import restock
from test_unified_buying_offline import BuyingFixture,NOW
import test_seller_food_offline as food_fixture


def sale(sku,ts,own=False,amount=80000,grade='A',order='order'):
    return {'sku':sku,'ts':ts,'own':own,'amount':amount,'grade':grade,'order':order,'warranty_hours':6}


class CashMathTests(unittest.TestCase):
    def setUp(self):
        self.catalog={'G00000001':{'category':'beds','kind':'goods','list_price':100000}}
        self.hub=store.default_hub()

    def test_observations_deduplicate_and_exclude_own_buyer(self):
        rows=[{'round':3,'kind':'settle','seq':1,'ts':NOW,'data':{'item':'G00000001','buyer':'outside','amount':80000,'grade':'A','shop':'https://own','order':'a'}},
              {'round':3,'kind':'settle','seq':2,'ts':NOW,'data':{'item':'G00000001','buyer':'mjforge','amount':80000,'shop':'other','order':'b'}}]
        cash.observe(self.hub,rows*2,self.catalog,NOW,{'https://own'},'mjforge')
        self.assertEqual(len(self.hub['seller_cash']['sales']),1)
        self.assertTrue(self.hub['seller_cash']['sales']['1']['own'])

    def test_only_mature_sales_estimate_breakage(self):
        rows=[{'round':3,'kind':'settle','seq':i,'ts':ts,
               'data':{'sku':'G00000001','amount':80000,'grade':'A','order':str(i),'shop':'other'}}
              for i,ts in [(1,NOW-7*3600),(2,NOW-60)]]
        cash.observe(self.hub,rows,self.catalog,NOW,set(),'mjforge')
        self.assertAlmostEqual(cash.refund_probability(self.hub,'G00000001','A',self.catalog,NOW),.6/20.5)

    def test_huge_market_is_not_assigned_to_our_shop(self):
        self.hub['seller_cash']={'sales':{str(i):sale('G00000001',NOW-i) for i in range(800)}}
        p=cash.forecast(self.hub,'G00000001',self.catalog['G00000001'],self.catalog,NOW)
        self.assertAlmostEqual(p['prediction20'],8)
        self.assertEqual(p['depth'],21)
        self.assertLess(p['prediction20'],800)

    def test_own_velocity_can_grow_beyond_old_eight_unit_cap(self):
        self.hub['seller_cash']={'sales':{str(i):sale('G00000001',NOW-i,own=i<8) for i in range(40)}}
        p=cash.forecast(self.hub,'G00000001',self.catalog['G00000001'],self.catalog,NOW)
        self.assertGreater(p['depth'],8)
        self.assertLessEqual(p['depth'],40)

    def test_actual_worst_case_reservations_cover_surge_races(self):
        v=cash.procurement(self.hub,'G00000001',self.catalog['G00000001'],
                           {'surge':[{'sku':'G00000001','units':233,'factor':1.366}]},20,self.catalog,NOW)
        self.assertAlmostEqual(v['projected_surge'],1.406)
        self.assertEqual(v['reservation_unit_cost'],112500)

    def test_budget_keeps_refund_cash_and_does_not_depend_on_stock_target_ratio(self):
        budget,reserve=cash.spending_budget(self.hub,{'balance':30000000},1500000,NOW)
        self.assertEqual(reserve,5000000)
        self.assertEqual(budget,21250000)
        self.assertGreaterEqual(30000000-budget,reserve)

    def test_restoration_precedes_whole_budget_concentration_and_repeats_are_blocked(self):
        models={'a':{'score20':1000000,'own60':10},'b':{'score20':100,'own60':2}}
        groups=[('hot',('a',40,100,100)),('popular',('b',10,100,10))]
        previous={};spending=collections.Counter()
        orders,spent=cash.allocate(groups,models,collections.Counter(),1000,1000,spending,previous)
        bought=collections.Counter()
        for o in orders:bought[o['sku']]+=o['qty']
        self.assertGreaterEqual(bought['b'],3)
        self.assertEqual(spent,1000)
        self.assertEqual(cash.allocate(groups,models,collections.Counter(),1000,1000,spending,previous),([],0))

    def test_supply_limits_apply_to_split_lines(self):
        groups=[('hot',(str(i),40,100,100)) for i in range(12)]
        models={str(i):{'score20':i,'own60':0} for i in range(12)}
        orders,spent=cash.allocate(groups,models,collections.Counter(),100000,100000,collections.Counter(),{})
        self.assertLessEqual(len(orders),10)
        self.assertTrue(all(1<=o['qty']<=20 for o in orders))

    def test_warmup_freshness_and_endgame_gate(self):
        self.hub['adaptive_ledger_coverage']={'since':NOW-1200,'through':NOW}
        self.hub['seller_cash']={'coverage':{'since':NOW-1200,'through':NOW}}
        inv={'round':3,'balance':10000000}
        self.assertTrue(cash.ready(self.hub,inv,NOW,True,'normal'))
        self.assertFalse(cash.ready(self.hub,inv,NOW,False,'normal'))
        self.assertFalse(cash.ready(self.hub,inv,NOW,True,'taper-1'))
        self.hub['adaptive_ledger_coverage']['since']=NOW-60
        self.assertFalse(cash.ready(self.hub,inv,NOW,True,'normal'))

    def test_old_shared_history_does_not_skip_new_observer_warmup(self):
        self.hub['adaptive_ledger_coverage']={'since':NOW-7200,'through':NOW}
        self.hub['seller_cash']={'coverage':{'since':NOW-60,'through':NOW}}
        self.assertFalse(cash.ready(self.hub,{'round':3,'balance':30000000},NOW,True,'normal'))


class CashRestockTests(BuyingFixture,unittest.TestCase):
    def test_live_growth_runs_through_real_planner_without_duplicate_buys(self):
        sku=self.item('G00000001',price=100000)
        self.inv.update(round=3,balance=30000000,surge=[])
        self.hub['adaptive_ledger_coverage']={'since':NOW-1200,'through':NOW}
        self.hub['seller_cash']={'sales':{str(i):sale(sku,NOW-i,own=i<8) for i in range(40)},
                                 'coverage':{'since':NOW-1200,'through':NOW}}
        p=restock.plan_with_hub(self.hub,self.path,self.catalog,self.inv,[],inventory_fresh=True)
        self.assertGreater(sum(x['qty'] for x in p['buy']),8)
        self.assertTrue(self.diag()['seller_cash_policy']['active'])
        self.assertLessEqual(self.state()['reserved'],21250000)
        self.assertEqual(restock.plan_with_hub(self.hub,self.path,self.catalog,self.inv,[],inventory_fresh=True)['buy'],[])


class CashQuoteTests(unittest.TestCase):
    # Reuse the existing isolated fake A2A fixture, without inheriting/duplicating its tests.
    setUp=food_fixture.FoodSafetyTests.setUp
    turn=food_fixture.FoodSafetyTests.turn
    receipt=food_fixture.FoodSafetyTests.receipt

    def prepare(self,cost=60000):
        sku='G00000001'
        self.catalog={sku:{'category':'beds','list_price':100000,'kind':'goods'}}
        unit={'unit':'goods-1','sku':sku,'name':'mattress','kind':'goods','grade':'A','cost':cost,
              'list_price':100000,'category':'beds','supplied_at':self.now-60,'cert':{'proof':'test'}}
        self.inventory['units']=[unit]
        hub={'demand_radar':{'sku_comparables':{sku:[{'grade':'A','amount':80000,'ts':self.now-i,'warranty_hours':6} for i in range(5)]}}}
        self.stack.enter_context(patch.object(seller,'load_hub',return_value=hub))
        return sku,unit,hub

    def test_first_quote_receipt_uses_same_price_unit_grade_and_warranty(self):
        sku,unit,hub=self.prepare()
        self.turn(sku)
        self.assertEqual(self.agent.open['task-1']['ask'],80000)
        receipt=self.receipt(self.turn('80000'))
        self.assertEqual((receipt['price'],receipt['unit'],receipt['claim'],receipt['warranty_hours']),(80000,'goods-1','A',72))

    def test_one_small_concession_then_rejects_low_offer(self):
        sku,unit,hub=self.prepare()
        self.turn(sku)
        first=self.turn('60000')
        self.assertIn('79200',str(first))
        second=self.turn('60000')
        self.assertIn('reject',[k for k,_ in second])
        self.assertNotIn('receipt',[k for k,_ in second])
        self.assertFalse(self.agent.open)

    def test_high_wholesale_cost_never_changes_catalog_value_or_claims_discount(self):
        sku,unit,hub=self.prepare(130000)
        quote=seller.compute_unit_pricing_and_terms(unit,hub)
        self.assertEqual(quote['list_price'],100000)
        self.assertEqual(quote['disc_pct'],0)
        text=str(self.turn(sku))
        self.assertIn('above catalog list',text)
        self.assertNotIn('% below',text)

    def test_unknown_grade_and_short_warranty_not_price_comparables(self):
        sku,unit,hub=self.prepare()
        obs=hub['demand_radar']['sku_comparables'][sku]
        obs += [{'grade':'B','amount':1,'ts':self.now,'warranty_hours':6}]*30
        obs += [{'grade':'A','amount':1,'ts':self.now,'warranty_hours':0}]*30
        self.assertEqual(cash.quote(unit,hub,self.catalog,self.now)['open_ask'],80000)

    def test_shop_post_uses_only_actual_certified_stock_and_same_quote(self):
        sku,unit,hub=self.prepare()
        hub['demand_radar']['sku_inquiries']={sku.lower():[{'ts':self.now,'miss':False}]}
        text=cash.stock_post([unit],hub,self.catalog,self.now)
        self.assertIn('재고 있음',text)
        self.assertIn(sku,text)
        self.assertNotIn('first_quote',text)
        self.assertNotIn('grade=',text)
        self.assertIsNone(cash.stock_post([{**unit,'cert':None}],hub,self.catalog,self.now))


if __name__=='__main__':
    unittest.main()
