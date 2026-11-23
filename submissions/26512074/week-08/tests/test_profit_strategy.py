import json
from pathlib import Path
import tempfile
import unittest
from profit_strategy import opening,capital,replenish
from optimizer import choose_quote,db,negotiate


class ResponsePricingTests(unittest.TestCase):
    def setUp(self):
        self.unit={'unit':'u','sku':'x','cost':40,'list_price':100,'grade':'B','kind':'goods'}
        self.base={'floor':41,'ask':100,'market_reference':100}

    def test_replies_and_settlements_support_higher_opening_then_failures_lower_it(self):
        initial=opening(self.unit,self.base,[])
        successful=[{'ask':initial['ask'],'reference':100,'responded':1,'settled_at':100} for _ in range(12)]
        promoted=opening(self.unit,self.base,successful)
        self.assertGreater(promoted['ask'],initial['ask'])
        abandoned=[{'ask':initial['ask'],'reference':100,'responded':0,'settled_at':None} for _ in range(12)]
        reduced=opening(self.unit,self.base,abandoned)
        self.assertLess(reduced['ask'],initial['ask'])
        self.assertGreaterEqual(reduced['pricing_evidence']['continuation_estimate'],.55)

    def test_quote_only_requests_never_become_abandonment_training(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'units':[self.unit]}
            first=choose_quote(directory,'baseline',self.unit,inv,self.base,1000,inquiry=True)
            for i in range(12):
                choose_quote(directory,'q'+str(i),self.unit,inv,self.base,1000,inquiry=True)
            later=choose_quote(directory,'later',self.unit,inv,self.base,5000)
            self.assertEqual(first['ask'],later['ask'])
            with db(directory) as connection:
                self.assertEqual(connection.execute('select count(*) from trials where inquiry=1').fetchone()[0],13)

    def test_unknown_message_does_not_reduce_an_existing_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            plan=choose_quote(directory,'t',self.unit,{'units':[self.unit]},self.base,1000)
            deal={'unit':self.unit,'offer':None,'ask':plan['ask'],'lines':['buyer: delivery tomorrow?']}
            self.assertEqual(negotiate(directory,'t',deal,plan,1001)['price'],plan['ask'])


class CapitalTests(unittest.TestCase):
    def test_budget_scales_with_capital_and_reserves_warranty(self):
        inv={'balance':6000000,'bankrupt_line':500000,'units':[]}
        normal=capital(inv)
        self.assertGreater(normal['budget'],250000)
        self.assertGreater(normal['inventory_cap'],1500000)
        constrained=capital(inv,5500000)
        self.assertEqual(constrained['budget'],0)

    def events(self,sku,price,count=4,shop='competitor'):
        return [{'seq':sku+str(i)+shop,'kind':'settle','round':3,'ts':10000,
                 'data':{'sku':sku,'amount':price,'shop':shop}} for i in range(count)]

    def test_public_demand_alone_cannot_spend_cash_on_unproven_skus(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog={str(i):{'list_price':10000,'kind':'goods'} for i in range(15)}
            ledger=[e for sku in catalog for e in self.events(sku,10000)]
            inv={'balance':6000000,'restock':{'open':True},'units':[]}
            plan=replenish(inv,catalog,ledger,10001,directory)
            self.assertEqual(plan['buy'],[])

    def test_fresh_market_prices_can_overrule_old_low_own_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog={'x':{'list_price':10000,'kind':'goods'}}
            inv={'balance':3000000,'restock':{'open':True},'units':[]}
            ledger=self.events('x',4000,shop='totato2203-r3')+self.events('x',10000,20)
            plan=replenish(inv,catalog,ledger,10001,directory)
            self.assertTrue(plan['buy'])
            preview=json.loads((Path(directory)/'procurement-preview.json').read_text())
            self.assertEqual(preview['candidates'][0]['expected_sale'],10000)

    def test_negative_expected_net_is_still_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'balance':3000000,'restock':{'open':True},'units':[]}
            ledger=self.events('x',4000)+self.events('x',10000,shop='totato2203-r3')
            plan=replenish(inv,{'x':{'list_price':10000,'kind':'goods'}},ledger,10001,directory)
            self.assertEqual(plan['buy'],[])

    def test_sales_fraction_does_not_block_funded_restock(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'balance':6000000,'restock':{'open':True},'units':[]}
            ledger=self.events('x',10000,shop='totato2203-r3')
            ledger.append({'seq':'supply','kind':'supply','round':3,'ts':10000,
                           'data':{'shop':'totato2203-r3','sku':'x','total':30000,'qty':4}})
            plan=replenish(inv,{'x':{'list_price':10000,'kind':'goods'}},ledger,10001,directory)
            self.assertEqual(plan['buy'],[{'sku':'x','qty':8}])
            limits=json.loads((Path(directory)/'procurement-preview.json').read_text())['limits']
            self.assertEqual(limits['sales_cash_2h'],40000)
            self.assertEqual(limits['purchases_2h'],30000)
            self.assertGreater(limits['budget'],0)
            self.assertFalse(limits['sales_fraction_cap_applied'])
            self.assertGreater(limits['regular_budget'],0)

    def test_orders_retain_cash_and_skip_already_covered_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'balance':6000000,'restock':{'open':True},'units':[]}
            catalog={'x':{'list_price':10000,'kind':'goods'}}
            ledger=self.events('x',10000,8,shop='totato2203-r3')
            plan=replenish(inv,catalog,ledger,10001,directory)
            self.assertTrue(plan['buy'])
            self.assertLessEqual(sum(p['qty']*7500 for p in plan['buy']),capital(inv)['budget'])
            self.assertEqual(plan['buy'][0]['qty'],16)
            inv['units']=[{'sku':'x','cost':5000,'unit':str(i)} for i in range(16)]
            self.assertEqual(replenish(inv,catalog,ledger,10001,directory)['buy'],[])
