import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from shop_evidence import analyze,render
from recommendations import purchase_exclusions,competitive_ad,render_slate,NAME
from resale import leads,inspect
from optimizer import choose_quote,negotiate


class ReportingTests(unittest.TestCase):
    def test_own_shop_excluded_even_when_not_recommended(self):
        now=1000
        quotes=[{'sku':'x','shop':NAME,'url':'https://ours','price':200,'at':now}]
        audits={'https://ours':{'avoid_until':2000,'reason':'receipt violation: example'}}
        shops=[{'name':NAME,'url':'https://ours'}]
        self.assertEqual(purchase_exclusions(quotes,{'x':{'median':100,'refs':[1]}},audits,shops,[],now),[])

    def test_repeat_observations_exclude_us_on_both_sides_and_include_items(self):
        board={'round':3,'shops':[]}
        shops=[{'name':NAME,'url':'https://ours'},{'name':'other','url':'https://other'}]
        events=[]
        for buyer,seller in [('totato2203-r3','https://other'),('them','https://ours'),('them','https://other')]:
            for _ in range(3):
                events.append({'seq':len(events),'kind':'settle','round':3,'ts':999,
                               'data':{'buyer':buyer,'shop':seller,'sku':'x','name':'Apples','amount':100}})
        evidence=analyze(board,shops,events,1000)
        self.assertEqual(len(evidence['repeat_pairs']),1)
        self.assertEqual(evidence['repeat_pairs'][0]['products'],[{'sku':'x','name':'Apples','count':3}])
        self.assertIn('Apples', '\n'.join(render(evidence,1000)[0]))

    def test_ad_uses_best_current_quote_for_live_inventory(self):
        inv={'units':[{'sku':'x','name':'X','cost':10},{'sku':'y','name':'Y','cost':20}]}
        quotes=[{'shop':NAME,'sku':sku,'price':price,'at':1000} for sku,price in [('x',90),('y',60)]]
        ad=competitive_ad({},inv,quotes,{'x':{'median':100},'y':{'median':100}},1000)
        self.assertIn('x X',ad)
        self.assertIn('90',ad)
        with patch('recommendations.time.time',return_value=1000):
            evidence={'leaders':[],'products':[],'repeat_pairs':[{'buyer':'a','shop':'b','count':3,
                       'buyer_total':3,'share':1,'reverse_count':0,'refs':[1],
                       'products':[{'sku':'x','name':'Apples','count':3}]}]}
            post=render_slate([],{},inv,'x'*2100,(),quotes,evidence,{'y':{'median':100}})
        self.assertLessEqual(len(post['post']),2000)
        self.assertIn('Apples',post['post'])
        self.assertIn('x X',post['post'])


class ResaleTests(unittest.TestCase):
    def setUp(self):
        self.sku='G12345678'
        self.shops=[{'name':'seller','url':'https://seller','stock':{self.sku:2}}]
        self.forum=[{'id':1,'ts':999,'author':'seller','role':'shop','text':self.sku+' clearance asking 40'}]

    def test_forum_claim_needs_actual_quote_and_does_not_move_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog={self.sku:{'name':'X'}}
            self.assertEqual(len(leads(self.forum,self.shops,catalog,1000)),1)
            events=[{'seq':i,'kind':'settle','round':3,'ts':999,'data':{'shop':'other','buyer':'b','sku':self.sku,'grade':'A','amount':100}} for i in range(3)]
            result=inspect(directory,self.forum,self.shops,catalog,{'units':[]},events,[],1000)
            self.assertEqual(result['opportunities'],[])
            quote={'shop':'seller','sku':self.sku,'price':40,'grade':'A','at':1000,'url':'https://seller'}
            result=inspect(directory,self.forum,self.shops,catalog,{'units':[]},events,[quote],1000)
            self.assertEqual(result['opportunities'][0]['planned_opening'],80)
            self.assertFalse(result['purchase_executed'])
            self.assertEqual(result['verified_inventory_units'],0)

    def test_double_cost_price_requires_unit_in_actual_shop_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            unit={'unit':'u','sku':self.sku,'name':'X','grade':'A','kind':'goods','cost':40,'list_price':100}
            purchase={'seq':10,'kind':'settle','round':3,'ts':999,'data':{'buyer':NAME,'shop':'other','unit':'u','sku':self.sku,'amount':40}}
            inspect(directory,[],[],{}, {'units':[]},[purchase],[],1000)
            self.assertEqual(json.loads((Path(directory)/'resale-units.json').read_text())['units'],{})
            inspect(directory,[],[],{}, {'units':[unit]},[purchase],[],1000)
            base={'ask':100,'floor':41}
            plan=choose_quote(directory,'t',unit,{'units':[unit]},base,1000)
            self.assertEqual(plan['ask'],80)
            self.assertEqual(plan['pricing_evidence']['strategy'],'verified-resale-100pct')
            move=negotiate(directory,'t',{'unit':unit,'offer':1,'ask':80,'lines':['buyer: 1']},plan,1001)
            self.assertGreaterEqual(move['price'],78)
