import tempfile
from pathlib import Path
import unittest
from optimizer import db,atomic,choose_quote,negotiate
from premium import terms,review
from profit_strategy import replenish


class PremiumTests(unittest.TestCase):
    def test_old_sales_deficit_no_longer_blocks_funded_high_value_purchase(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'balance':6000000,'restock':{'open':True},'units':[]}
            cat={'x':{'list_price':300000,'kind':'goods'}}
            ledger=[{'seq':i,'round':3,'kind':'settle','ts':10000,
                     'data':{'shop':'s'+str(i%2),'buyer':'b'+str(i%3),'sku':'x','amount':270000}} for i in range(12)]
            ledger.append({'seq':20,'round':3,'kind':'supply','ts':10000,'data':{'shop':'totato2203-r3','sku':'old','total':1000000}})
            plan=replenish(inv,cat,ledger,10001,directory)
            self.assertEqual(plan['buy'],[{'sku':'x','qty':3}])
            import json
            limits=json.loads((Path(directory)/'procurement-preview.json').read_text())['limits']
            self.assertFalse(limits['sales_fraction_cap_applied'])
            self.assertGreaterEqual(inv['balance']-3*225000,limits['cash_reserve'])

    def test_bundle_is_bound_and_explicit_six_hours_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            unit={'sku':'x'}
            first=terms(directory,'fixed',unit,6,flexible=False)
            atomic(Path(directory)/'premium-policy.json',{'default_fee':.20})
            self.assertEqual(terms(directory,'fixed',unit,6,flexible=False),first)
            self.assertEqual(first['hours'],6)
            self.assertGreaterEqual(first['fee'],.03)
            self.assertLessEqual(first['fee'],.20)

    def test_premium_low_offer_is_countered_above_market_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            unit={'sku':'x','unit':'u','kind':'goods','grade':'A','cost':100000,'list_price':300000}
            plan=choose_quote(directory,'t',unit,{'units':[unit]}, {'ask':280000,'floor':101000,'market_median':200000},1000)
            with db(directory) as connection:
                connection.execute("update trials set strategy='premium-offensive-v1' where task='t'")
            move=negotiate(directory,'t',{'unit':unit,'ask':280000,'offer':170000,'lines':['buyer: 170000']*3},plan,1001)
            self.assertEqual(move['action'],'ask')
            self.assertGreaterEqual(move['price'],184000)

    def test_six_hour_review_prefers_realized_profit_after_refunds(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=[]
            with db(directory) as connection:
                connection.execute('create table premium_terms(task text primary key,sku text,hours real,fee real)')
                for i in range(16):
                    hours,fee,price=(3,.06,180000) if i<8 else (6,.20,200000)
                    connection.execute('insert into units values(?,?,?,?)',(str(i),'x',100000,1000))
                    connection.execute('insert into trials(task,unit,sku,at,cost,ask,settled_at,price,margin) values(?,?,?,?,?,?,?,?,?)',(str(i),str(i),'x',2000+i,100000,price,3000+i,price,price-100000))
                    connection.execute('insert into premium_terms values(?,?,?,?)',(str(i),'x',hours,fee))
                    if i>=8:
                        ledger.append({'seq':i,'kind':'broken','round':3,'ts':5000,'data':{'shop':'totato2203-r3','unit':str(i),'refunded':price}})
            result=review(directory,{'shops':[]},{'shops':[]},ledger,100000)
            self.assertEqual(result['changes']['x']['after']['hours'],3)
            self.assertEqual(result['changes']['x']['after']['fee'],.06)
