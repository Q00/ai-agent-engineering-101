"""Synthetic local procurement and executable-plan regressions; no live inputs."""
import collections
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import restock
import seller_cash_policy as cash
import store

NOW = 1790000000
SKU = 'G8095ddd6'


class ProcurementGuardTests(unittest.TestCase):
    def setUp(self):
        self.info = {'kind':'goods', 'category':'beds', 'list_price':100000}
        self.catalog = {SKU:self.info}
        self.inv = {'balance':20000000, 'round':3, 'units':[],
                    'surge':[{'sku':SKU, 'units':0, 'factor':1}],
                    'restock':{'opens_at':NOW-20, 'closes_at':NOW+160}}
        self.hub = store.default_hub()
        self.hub['seller_cash'] = {'coverage':{'since':NOW-28800, 'through':NOW}, 'sales':{}}
        self.prices(100000)
        self.sales(A=20, B=10, C=10)

    def prices(self, amount, warranty=True):
        self.hub['demand_radar']['sku_comparables'] = {SKU:[
            dict({'grade':g, 'ts':NOW, 'amount':amount},
                 **({'warranty_hours':cash.warranty_hours(g)} if warranty else {}))
            for g in 'ABC' for _ in range(5)]}

    def sales(self, **counts):
        self.hub['seller_cash']['sales'] = {
            str(i):{'sku':SKU, 'grade':g, 'ts':NOW-60, 'own':True, 'amount':100000,
                    'order':str(i), 'warranty_hours':cash.warranty_hours(g)}
            for i,g in enumerate(g for g,n in counts.items() for _ in range(n))}

    def stock(self, **counts):
        self.inv['units'] = [{'sku':SKU, 'grade':g, 'cost':50000, 'kind':'goods',
                              'supplied_at':NOW-60} for g,n in counts.items() for _ in range(n)]

    def model(self):
        return cash.procurement(self.hub,SKU,self.info,self.inv,40,self.catalog,NOW)

    def guard(self):
        return self.model()['procurement_guard']

    def test_explicit_numeric_zero_only_and_shared_quote(self):
        unit = {'sku':SKU,'kind':'goods','grade':'C','cost':20000,'supplied_at':NOW-60}
        floor = cash.profit_floor(unit,self.hub,self.catalog,NOW)
        for value in (None,False,'0',float('nan'),True):
            with self.subTest(value=value):
                self.prices(100000)
                for row in self.hub['demand_radar']['sku_comparables'][SKU]:
                    row['warranty_hours'] = value
                self.assertIsNone(cash.reference_price(self.hub,SKU,'C',NOW))
                self.assertIsNone(cash.quote(unit,self.hub,self.catalog,NOW))
                self.assertEqual(cash.profit_floor(unit,self.hub,self.catalog,NOW),floor)
        self.prices(100000, warranty=False)
        self.assertIsNone(cash.reference_price(self.hub,SKU,'C',NOW))
        for zero in (0,0.0):
            for row in self.hub['demand_radar']['sku_comparables'][SKU]:
                row['warranty_hours'] = zero
            self.assertEqual(cash.reference_price(self.hub,SKU,'C',NOW),100000)
            self.assertEqual(cash.quote(unit,self.hub,self.catalog,NOW)['hard_floor'],floor)

    def test_food_and_unwatched_original_procurement(self):
        self.prices(100000,warranty=False)
        self.assertEqual(cash.reference_price(self.hub,SKU,'C',NOW,'food'),100000)
        for sku,info in [('ordinary',self.info),(SKU,dict(self.info,kind='food'))]:
            model = cash.procurement(self.hub,sku,info,self.inv,10,{sku:info},NOW)
            self.assertNotIn('procurement_guard',model)
            self.assertGreater(model['expected_unit_contribution'],0)
            self.assertEqual(model['projected_surge'],1 if sku==SKU else 1.02)

    def test_coverage_threshold_and_grade_accumulation(self):
        self.stock(A=10)
        self.sales(A=20)
        self.assertEqual(self.guard()['coverage_hours'],4)
        self.assertEqual(self.guard()['max_additional_qty'],0)
        self.stock(A=0)
        self.assertEqual(self.guard()['coverage_hours'],0)
        self.stock(B=2,C=7)
        self.sales(A=28,B=9)
        guard = self.guard()
        self.assertLess(guard['coverage_hours'],4)
        self.assertIn('C_accumulation',guard['reasons'])
        self.assertEqual(guard['max_additional_qty'],0)
        self.sales(A=100)
        self.assertIn('B_accumulation',self.guard()['reasons'])

    def test_own_only_eight_hour_and_missing_observation(self):
        self.stock(A=1)
        self.sales()
        rows = self.hub['seller_cash']['sales']
        rows['market'] = {'sku':SKU,'grade':'A','ts':NOW,'own':False}
        rows['stale'] = {'sku':SKU,'grade':'A','ts':NOW-28801,'own':True}
        rows['future'] = {'sku':SKU,'grade':'A','ts':NOW+1,'own':True}
        self.assertEqual(self.guard()['own_sales_8h'],{})
        self.assertIn('held_without_own_sales',self.guard()['reasons'])
        self.sales(A=20,B=10,C=10)
        self.hub['seller_cash']['coverage'] = {}
        self.assertEqual(self.guard()['max_additional_qty'],0)
        self.stock()
        self.assertEqual(self.guard()['max_additional_qty'],1)

    def test_average_positive_stress_negative(self):
        self.prices(76000)
        guard = self.guard()
        self.assertGreater(guard['scenarios']['average']['weighted_contribution'],0)
        self.assertLess(guard['scenarios']['stress']['weighted_contribution'],0)
        self.assertEqual(guard['max_additional_qty'],0)

    def test_all_five_observed_inventory_shapes_hold_without_blacklisting(self):
        # Supplied observation shapes only, not a live profitability measurement.
        snapshots = [('G8095ddd6',(3,5,5),(19,3,0)),
                     ('G24f5f979',(5,8,5),(32,0,0)),
                     ('G2698c6f8',(0,2,7),(28,9,0)),
                     ('Gbec2b68f',(2,2,0),(2,0,0)),
                     ('Gf6b70da8',(1,2,4),(1,0,0))]
        self.assertEqual(set(cash.config()['procurement_watchlist']),{s for s,_,_ in snapshots})
        for sku,inventory,sales in snapshots:
            with self.subTest(sku=sku):
                self.stock(**dict(zip('ABC',inventory)))
                self.sales(**dict(zip('ABC',sales)))
                for u in self.inv['units']:
                    u['sku'] = sku
                for row in self.hub['seller_cash']['sales'].values():
                    row['sku'] = sku
                guard = cash.procurement(self.hub,sku,self.info,self.inv,40,{sku:self.info},NOW)['procurement_guard']
                self.assertEqual(guard['max_additional_qty'],0)
                self.assertEqual(guard['own_sales_8h'].get('C',0),0)

    def test_b_risk_minimum_applies_even_if_estimator_returns_zero(self):
        with patch.object(cash,'refund_probability',return_value=0):
            self.assertEqual(self.model()['break_rates']['B'],.15)

    def test_unproven_c_cannot_offset_a_b_stress_losses(self):
        self.sales(A=28,B=9)
        self.prices(70000)
        for row in self.hub['demand_radar']['sku_comparables'][SKU]:
            if row['grade'] == 'C':
                row['amount'] = 200000
        guard = self.guard()
        stress = guard['scenarios']['stress']
        raw_mix = sum(guard['assumed_grade_weights'][g]*r['raw_contribution']
                      for g,r in stress['grades'].items())
        self.assertGreater(raw_mix,0)
        self.assertLess(stress['weighted_contribution'],0)
        self.assertEqual(guard['max_additional_qty'],0)

    def test_no_imaginary_grade_profit_storage_minimum_and_losses(self):
        self.sales(A=20,B=10)
        guard = self.guard()
        c = guard['scenarios']['average']['grades']['C']
        self.assertGreater(c['raw_contribution'],0)
        self.assertEqual(c['contribution'],0)
        self.prices(100000,warranty=False)
        grades = self.guard()['scenarios']['average']['grades']
        self.assertTrue(all(r['contribution']<=0 for r in grades.values()))
        self.assertEqual(grades['A']['price_basis'],'estimated_list_prior')
        self.prices(10000)
        c = self.guard()['scenarios']['stress']['grades']['C']
        self.assertEqual(c['contribution'],c['raw_contribution'])
        self.assertLess(c['contribution'],0)
        self.prices(100000)
        row = self.guard()['scenarios']['stress']['grades']['B']
        self.assertAlmostEqual(row['future_storage'],row['cost']*.01/3)
        self.assertEqual(row['minimum_contribution'],3000)
        self.assertAlmostEqual(row['raw_contribution'],100000*.85-row['cost']-row['future_storage']-3000)

    def test_depletion_and_new_evidence_conditionally_reallow_one(self):
        self.stock(C=7)
        self.sales(A=20)
        self.assertEqual(self.guard()['max_additional_qty'],0)
        self.stock()
        self.prices(100000,warranty=False)
        self.assertEqual(self.guard()['max_additional_qty'],0)
        self.prices(100000)
        self.sales(A=20,B=10,C=10)
        model = self.model()
        self.assertEqual(model['procurement_guard']['max_additional_qty'],1)
        self.assertEqual(model['projected_surge'],1)

    def test_allocator_restoration_and_priority_share_one_limit(self):
        guard = self.guard()
        models = {SKU:{'own60':20,'score20':100000,'procurement_guard':guard}}
        previous = {}
        args = ([('hot',(SKU,40,100000,10000))],models,collections.Counter(),
                10000000,10000000,collections.Counter(),previous)
        orders,spent = cash.allocate(*args)
        self.assertEqual(orders,[{'sku':SKU,'qty':1}])
        self.assertEqual(spent,100000)
        self.assertEqual(cash.allocate(*args),([],0))
        models[SKU]['procurement_guard'] = dict(guard,max_additional_qty=0)
        previous.clear()
        self.assertEqual(cash.allocate(*args),([],0))

    def plan(self, growth):
        stream = io.StringIO()
        with patch.object(restock.time,'time',return_value=NOW), \
                patch.object(restock,'ROUND_END_TS',NOW+10000000), \
                patch.object(cash,'ready',return_value=growth), \
                patch.object(restock,'queue_availability_notice'), \
                patch.object(store.urllib.request,'urlopen',side_effect=AssertionError('network forbidden')), \
                contextlib.redirect_stderr(stream):
            result = restock.plan_with_hub(self.hub,Path('unused'),self.catalog,self.inv,[],inventory_fresh=True)
        return result,json.loads(stream.getvalue().splitlines()[-1])

    def test_actual_growth_legacy_repeat_restored_state_and_next_window(self):
        for growth in (False,True):
            with self.subTest(growth=growth):
                self.hub['restock_windows'] = {}
                self.hub['demand_radar']['sku_inquiries'] = {SKU.lower():[{'ts':NOW,'miss':True}]*12}
                result,diag = self.plan(growth)
                self.assertEqual(result['buy'],[{'sku':SKU,'qty':1}])
                self.assertEqual(diag['procurement_guards'][SKU]['max_additional_qty'],1)
                state = next(iter(self.hub['restock_windows'].values()))
                self.assertEqual(state['planned_skus'][SKU],1)
                reserved = state['reserved']
                self.hub = json.loads(json.dumps(self.hub))
                repeat,diag = self.plan(growth)
                self.assertEqual(repeat['buy'],[])
                self.assertEqual(diag['procurement_guards'][SKU]['window_remaining_qty'],0)
                self.assertEqual(next(iter(self.hub['restock_windows'].values()))['reserved'],reserved)
                self.inv['restock']['opens_at'] += 1
                self.assertEqual(self.plan(growth)[0]['buy'],[{'sku':SKU,'qty':1}])

    def test_actual_orders_blocked_even_with_growth_and_popularity(self):
        self.stock(C=7)
        self.sales(A=100)
        self.hub['demand_radar']['sku_inquiries'] = {SKU.lower():[{'ts':NOW,'miss':True}]*40}
        for growth in (False,True):
            result,diag = self.plan(growth)
            self.assertEqual(result['buy'],[])
            self.assertIn('C_accumulation',diag['procurement_guards'][SKU]['reasons'])


if __name__ == '__main__':
    unittest.main()
