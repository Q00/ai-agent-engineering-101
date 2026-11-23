import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,MagicMock,patch
import buyer_r3
import seller_r3
from buyer_compare import compare,quoted_warranty
from profit_strategy import replenish,capital


class PilotTests(unittest.TestCase):
    def test_available_single_unit_pilots_stay_within_available_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'balance':6000000,'restock':{'open':True},'units':[]}
            catalog={name:{'list_price':200000,'kind':'goods'} for name in (f'p{i}' for i in range(9))}
            events=[{'seq':'fund','kind':'settle','round':3,'ts':10000,
                     'data':{'shop':'totato2203-r3','sku':'old-discontinued','amount':2000000}}]
            for sku in catalog:
                for i in range(12):
                    events.append({'seq':sku+str(i),'kind':'settle','round':3,'ts':10000,
                                   'data':{'sku':sku,'amount':180000,'buyer':'b'+str(i%3),'shop':'s'+str(i%2)}})
            plan=replenish(inv,catalog,events,10001,directory)
            self.assertEqual(len(plan['buy']),9)
            self.assertTrue(all(row['qty']==1 for row in plan['buy']))
            self.assertLessEqual(sum(row['qty']*150000 for row in plan['buy']),capital(inv)['budget'])
            state=json.loads((Path(directory)/'pilot-state.json').read_text())
            inv['units']=[{'unit':sku,'sku':sku,'cost':150000} for sku in state['skus']]
            self.assertEqual(replenish(inv,catalog,events,10001,directory)['buy'],[])

    def test_pilot_does_not_bypass_existing_purchase_debt(self):
        with tempfile.TemporaryDirectory() as directory:
            inv={'balance':6000000,'restock':{'open':True},'units':[]}
            events=[{'seq':'s','kind':'supply','round':3,'ts':10000,'data':{'shop':'totato2203-r3','total':1000000}}]
            self.assertEqual(replenish(inv,{},events,10001,directory)['buy'],[])


class BuyerComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_quotes_reorder_suppliers_without_binding_offers(self):
        rows=[(100,{'url':url,'name':url},'x',850000) for url in ('a','b','c')]
        memory={url:{'quotes':[{'sku':'x','at':1000,'ask':ask,'warranty_hours':w}]} for url,ask,w in [('a',800000,0),('b',500000,0),('c',510000,3)]}
        with patch('buyer_compare.time.time',return_value=1001),patch('buyer_compare.A2AClient',side_effect=AssertionError('cached quote should be reused')):
            result=await compare(rows,{'budget':1000000},{'x':{'list_price':1000000}},memory)
        self.assertEqual(result[0][1]['url'],'c')
        self.assertGreater(result[0][3],850000)
        self.assertLessEqual(result[0][3],980000)
        self.assertEqual(quoted_warranty('a six-hour warranty'),6)

    async def test_premium_offer_requires_warranty_in_receipt(self):
        self.assertIsNotNone(buyer_r3.terms_failure({'cert':True,'warranty_hours':0},{},3))
        self.assertIsNone(buyer_r3.terms_failure({'cert':True,'warranty_hours':3},{},3))

    async def test_premium_negotiation_carries_warranty_into_checkout_policy(self):
        def task(state,text='',warranty=None):
            result={'id':'t','status':{'state':state,'message':{'parts':[{'text':text}]}}}
            if warranty is not None:
                result['artifacts']=[{'parts':[{'data':{'shop':'s','item':'x','task':'t','price':880000,'warranty_hours':warranty,'cert':True}}]}]
            return result
        for warranty,expected in [(3,True),(0,False)]:
            client=AsyncMock();client.__aenter__.return_value=client
            client.send.side_effect=[task('TASK_STATE_INPUT_REQUIRED','asking 880000. 3-hour warranty') for _ in range(5)]+[task('TASK_STATE_COMPLETED',warranty=warranty)]
            with patch.object(buyer_r3,'A2AClient',return_value=client),patch.object(buyer_r3,'price_ceiling',side_effect=lambda value,budget,warranty,*args:900000 if warranty else 850000):
                result=await buyer_r3.buy([{'url':'s','name':'s','offers':[{'sku':'x','qty':1}]}],
                                         {'budget':1000000,'acceptable':['x'],'kind':'goods'},
                                         {'x':{'name':'X','kind':'goods','list_price':1000000}})
            self.assertEqual(bool(result['receipt']),expected)
            self.assertEqual(client.send.call_args_list[1].args[0],'308000')
            if expected:
                self.assertEqual(result['accept']['warranty_hours'],3)


class SellerWarrantyTests(unittest.IsolatedAsyncioTestCase):
    async def test_high_value_quote_and_receipt_include_grade_and_three_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            md=Path(directory)
            unit={'unit':'u','sku':'G11111111','name':'Mattress','category':'bed','grade':'A','kind':'goods',
                  'cost':100000,'list_price':300000,'supplied_at':1000,'expires_at':None}
            (md/'inventory.json').write_text(json.dumps({'round':3,'balance':6000000,'units':[unit]}))
            agent=seller_r3.Seller()
            signed=[]
            up=MagicMock();up.requires_input=AsyncMock();up.add_artifact=AsyncMock();up.complete=AsyncMock()
            context=SimpleNamespace(current_task=SimpleNamespace(id='t',context_id='c'),message=None)
            with patch.object(seller_r3,'MARKET_DIR',md),patch.object(seller_r3,'TaskUpdater',return_value=up),patch.object(seller_r3,'sign',side_effect=lambda r:signed.append(dict(r)) or r):
                with patch.object(seller_r3,'get_message_text',return_value='G11111111'):
                    await agent.execute(context,None)
                self.assertEqual(agent.open['t']['warranty_hours'],6)
                ask=agent.open['t']['ask']
                with patch.object(seller_r3,'get_message_text',return_value=str(ask)):
                    await agent.execute(context,None)
                up.complete.assert_awaited_once()
                self.assertEqual(signed[0]['claim'],'A')
                self.assertEqual(signed[0]['warranty_hours'],6)
                hold=json.loads((md/'warranty-holds.json').read_text())['u']
                self.assertTrue(hold['issued'])
                self.assertLess(hold['expires_at']-__import__('time').time(),6*3600+901)
