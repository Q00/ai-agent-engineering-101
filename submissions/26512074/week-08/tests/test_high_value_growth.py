import json
import tempfile
from pathlib import Path
import unittest
from optimizer import db,atomic
from high_value_growth import update


class GrowthTests(unittest.TestCase):
    def setup_case(self,directory,stage=0):
        md=Path(directory)
        atomic(md/'high-value-growth.json',{'stage':stage,'activated_at':0,'evidence_after':0,'last_checked':0})
        catalog={sku:{'list_price':200000,'kind':'goods'} for sku in ('a','b')}
        events=[]
        with db(md) as connection:
            for i in range(5):
                connection.execute('insert into units values(?,?,?,?)',(str(i),'a' if i%2 else 'b',100000,1000))
                events.append({'seq':i,'kind':'settle','round':3,'ts':5000+i,
                               'data':{'shop':'totato2203-r3','sku':'a' if i%2 else 'b','unit':str(i),'amount':140000,'warranty_hours':3}})
        events.append({'seq':999,'kind':'settle','round':3,'ts':20000,
                       'data':{'shop':'other','sku':'a','amount':140000}})
        return md,catalog,events

    def test_paid_mature_profitable_sales_promote_once_not_repeatedly(self):
        with tempfile.TemporaryDirectory() as directory:
            md,cat,events=self.setup_case(directory)
            state=update(md,{'units':[]},cat,events,20000)
            self.assertEqual(state['stage'],1)
            self.assertEqual(state['share'],.4)
            self.assertEqual(update(md,{'units':[]},cat,events,24000)['stage'],1)

    def test_pending_warranty_and_unknown_cost_never_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            md,cat,events=self.setup_case(directory)
            self.assertEqual(update(md,{'units':[]},cat,events,10000)['stage'],0)
            events[4]['data']['unit']='unknown'
            self.assertEqual(update(md,{'units':[]},cat,events,20000)['stage'],0)

    def test_refund_losses_roll_back_one_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            md,cat,events=self.setup_case(directory,2)
            for i in range(3):
                events.append({'seq':100+i,'kind':'broken','round':3,'ts':17000,
                               'data':{'shop':'totato2203-r3','item':'a' if i%2 else 'b','unit':str(i),'refunded':140000}})
            state=update(md,{'units':[]},cat,events,20000)
            self.assertEqual(state['stage'],1)
            self.assertIn('rollback',state['reason'])

    def test_aged_unsold_inventory_rolls_back_even_without_paid_sales(self):
        with tempfile.TemporaryDirectory() as directory:
            md,cat,_=self.setup_case(directory,1)
            inv={'units':[{'sku':'a','cost':100000,'supplied_at':1000},{'sku':'b','cost':100000,'supplied_at':1000}]}
            events=[{'seq':100,'kind':'supply','round':3,'ts':1000,'data':{'shop':'totato2203-r3','sku':'a','total':200000}}]
            self.assertEqual(update(md,inv,cat,events,50000)['stage'],0)

    def test_losses_at_minimum_stage_pause_new_high_value_spending(self):
        with tempfile.TemporaryDirectory() as directory:
            md,cat,events=self.setup_case(directory,0)
            events.append({'seq':100,'kind':'broken','round':3,'ts':17000,
                           'data':{'shop':'totato2203-r3','item':'b','unit':'0','refunded':140000}})
            state=update(md,{'units':[]},cat,events,20000)
            self.assertEqual(state['share'],0)
            self.assertGreater(state['paused_until'],20000)
