"""Persistent SKU break estimates and random-grade buying, no network."""
import unittest
import collections
from unittest.mock import patch

import seller_cash_policy as cash
import restock
from test_unified_buying_offline import BuyingFixture, NOW


SKU = 'G00000001'


def event(seq, kind='settle', order='o', ts=NOW, shop='own', grade='A', buyer='outside'):
    return {'seq':seq, 'ts':ts, 'round':3, 'kind':kind,
            'data':{'order':order, 'sku':SKU, 'shop':shop, 'grade':grade,
                    'buyer':buyer, 'amount':80000}}


class BreakStatsTests(unittest.TestCase):
    def setUp(self):
        self.hub = {}
        self.catalog = {SKU:{'kind':'goods','list_price':100000}}

    def observe(self, rows=(), now=NOW):
        cash.observe(self.hub,list(rows),self.catalog,now,{'own'},'mjforge')

    def test_maturity_matching_and_replay(self):
        rows = [event(1), event(2,'broken',ts=NOW+60)]
        self.observe(rows, NOW+60)
        stat = self.hub['break_stats'][SKU]['A']
        self.assertEqual((stat['sold'],stat['broken']),(0,1))
        self.observe(rows, NOW+6*3600-1)
        self.assertEqual(stat['sold'],0)
        self.observe(rows, NOW+6*3600)
        self.observe(rows+[event(3,order='o'),event(4,'broken')], NOW+6*3600)
        self.assertEqual((stat['sold'],stat['broken']),(1,1))
        self.observe(rows, NOW+8*86400)
        self.assertFalse(self.hub['break_observer']['orders'])
        self.observe(rows, NOW+8*86400)
        self.assertEqual(stat['sold'],1)

    def test_market_disjoint_and_buyer_excluded(self):
        self.observe([event(1),event(2,order='m',shop='other'),
                      event(3,order='buyer',shop='other',buyer='mjforge'),
                      event(4,'broken',order='m')],NOW+6*3600)
        self.assertEqual(self.hub['break_stats'][SKU]['A']['sold'],1)
        self.assertEqual(self.hub['market_break_stats'][SKU]['A']['sold'],1)
        self.assertEqual(self.hub['market_break_stats'][SKU]['A']['broken'],1)

    def test_break_before_settle_and_unmatched_expiry(self):
        self.observe([event(1,'broken'),event(2),event(3,'broken',order='unknown')])
        self.assertEqual(self.hub['break_stats'][SKU]['A']['broken'],1)
        self.observe(now=NOW+8*86400)
        self.assertFalse(self.hub['break_observer']['unmatched'])

    def test_seed_once_and_preserve_existing(self):
        self.hub['break_stats']={'G1515d951':{'A':{'sold':1,'broken':0,'last_ts':NOW}}}
        self.observe()
        self.assertEqual(self.hub['break_stats']['G1515d951']['A']['sold'],1)
        self.assertEqual(self.hub['break_stats']['G8ec51935']['B']['broken'],3)
        self.observe()
        self.assertEqual(self.hub['break_stats']['G8ec51935']['B']['sold'],8)

    def test_bounds(self):
        with patch.object(cash,'MAX_BREAK_SKUS',2), patch.object(cash,'MAX_BREAK_PENDING',3):
            self.catalog.update({str(i):{'kind':'goods'} for i in range(6)})
            rows=[]
            for i in range(6):
                e=event(i+1,order=str(i));e['data']['sku']=str(i);rows.append(e)
            self.observe(rows,NOW+6*3600)
            self.assertLessEqual(len(self.hub['break_stats']),2)
            self.assertLessEqual(len(self.hub['market_break_stats']),2)
            self.assertLessEqual(len(self.hub['break_observer']['orders']),3)
            self.observe([event(20+i,'broken',order='unknown'+str(i)) for i in range(6)])
            self.assertLessEqual(len(self.hub['break_observer']['unmatched']),3)
            self.observe()
            self.assertLessEqual(len(self.hub['break_stats']),2)

    def test_mixture_b_floor_and_zero_warranty(self):
        self.hub['break_stats']={SKU:{'A':{'sold':100,'broken':10},'B':{'sold':100,'broken':0}}}
        self.hub['market_break_stats']={SKU:{'A':{'sold':40,'broken':8}}}
        self.assertAlmostEqual(cash.refund_probability(self.hub,SKU,'A',self.catalog,NOW),14.6/140)
        self.assertEqual(cash.refund_probability(self.hub,SKU,'B',self.catalog,NOW),.15)
        self.hub['break_stats'][SKU]['B']['broken']=40
        self.assertAlmostEqual(cash.refund_probability(self.hub,SKU,'B',self.catalog,NOW),43/120)
        self.assertEqual(cash.refund_probability(self.hub,SKU,'C',self.catalog,NOW),0)

    def test_penalty_lowers_budget_priority_even_for_own_stockout(self):
        models={'risky':{'score20':100000,'own60':5,'break_penalty':True},
                'safe':{'score20':1,'own60':0,'break_penalty':False}}
        groups=[('hot',('risky',4,100,1000)),('hot',('safe',4,100,1))]
        orders,spent=cash.allocate(groups,models,collections.Counter(),100,100,
                                   collections.Counter(),{})
        self.assertEqual(orders,[{'sku':'safe','qty':1}])
        self.assertEqual(spent,100)


class BuyingBreakTests(BuyingFixture, unittest.TestCase):
    def prepare(self):
        sku=self.item(SKU,price=100000)
        self.inquiry(sku,4)
        self.hub['demand_radar']['sku_comparables']={sku:[
            {'grade':g,'amount':1000,'ts':NOW-i,'warranty_hours':0 if g=='C' else 72}
            for g in 'ABC' for i in range(5)]}
        return sku

    def test_negative_mixture_excluded_in_both_paths(self):
        for growth in (False,True):
            with self.subTest(growth=growth), patch.object(cash,'ready',return_value=growth):
                sku=self.prepare()
                self.assertEqual(self.plan()['buy'],[])
                d=self.diag()['break_rate']
                self.assertEqual(d['excluded'],1)
                self.assertEqual(d['negative_combinations'],3)
                self.assertFalse(d['grade_selectable'])

    def test_risky_b_grade_penalized_not_random_grade_ban(self):
        sku=self.prepare()
        self.hub['break_stats']={sku:{'A':{'sold':100,'broken':10,'last_ts':NOW}}}
        self.hub['demand_radar']['sku_comparables'][sku]=[
            {'grade':g,'amount':100000 if g!='B' else 1000,'ts':NOW-i,
             'warranty_hours':0 if g=='C' else 72} for g in 'ABC' for i in range(5)]
        model=cash.procurement(self.hub,sku,self.catalog[sku],self.inv,4,self.catalog,NOW)
        self.assertEqual(model['negative_grades'],['B'])
        self.assertTrue(model['break_penalty'])
        self.assertGreater(model['expected_unit_contribution'],0)
        self.plan()
        self.assertEqual(self.diag()['break_rate']['penalized'],1)


if __name__ == '__main__':
    unittest.main()
