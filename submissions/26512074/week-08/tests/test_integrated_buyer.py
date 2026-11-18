import unittest
from unittest.mock import patch,AsyncMock
import buyer_r3
from buyer_compare import compare


def task(state='TASK_STATE_REJECTED',text='',sku=None):
    r={'id':'t','status':{'state':state,'message':{'parts':[{'text':text}]}}}
    if sku:r['artifacts']=[{'parts':[{'data':dict(task='t',item=sku,price=4,sig_seller='test',cert=True)}]}]
    return r


class IntegratedBuyerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # These legacy-path fixtures mock buyer_r3's client. The time-aware
        # production path has separate offline tests and its own client.
        from strategy_policy import load
        legacy=dict(load(),buyer_time_policy_enabled=False)
        override=patch('strategy_policy.load',return_value=legacy)
        override.start();self.addCleanup(override.stop)
    def setup_client(self,responses):
        visits=[]
        class Client:
            def __init__(self,url,**kw):self.url=url
            async def __aenter__(self):visits.append(self.url);return self
            async def __aexit__(self,*a):pass
            async def discover(self):return {}
            async def cancel(self,*a):return {}
            async def send(self,*a):return responses[self.url].pop(0)
        return Client,visits
    async def test_search_reaches_seventh_shop(self):
        shops=[dict(url='http://s'+str(i),name=str(i),offers=[dict(sku='x',qty=1)]) for i in range(7)]
        responses={s['url']:[task()] for s in shops}
        responses[shops[-1]['url']]=[task('TASK_STATE_INPUT_REQUIRED','asking 80'),task('TASK_STATE_COMPLETED',sku='x')]
        client,visits=self.setup_client(responses)
        with patch.object(buyer_r3,'prioritize_clearance',side_effect=AssertionError('Unverified forum ads must not override ALL order')),patch.object(buyer_r3,'A2AClient',client),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda rows:rows)), \
             patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda rows,*a,**k:rows)):
            r=await buyer_r3.buy(shops,dict(acceptable=['x'],budget=100),{'x':dict(name='X',kind='goods',list_price=100)})
        self.assertEqual(len(visits),7);self.assertEqual(r['receipt']['price'],4)
    async def test_another_acceptable_sku_in_same_shop_is_explored(self):
        shops=[dict(url='http://s',name='s',offers=[dict(sku='x',qty=1),dict(sku='y',qty=1)])]
        client,visits=self.setup_client({'http://s':[task(),task('TASK_STATE_INPUT_REQUIRED','asking 80'),task('TASK_STATE_COMPLETED',sku='y')]})
        with patch.object(buyer_r3,'A2AClient',client),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda rows:rows)), \
             patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda rows,*a,**k:rows)):
            r=await buyer_r3.buy(shops,dict(acceptable=['x','y'],budget=100),{s:dict(name=s,list_price=100) for s in ('x','y')})
        self.assertEqual(r['receipt']['item'],'y');self.assertEqual(len(visits),2)
    def test_own_registered_shop_is_excluded(self):
        rows=buyer_r3.candidates([dict(name='totato2203-r3',url='http://own',offers=[dict(sku='x',qty=1)])],
                                 dict(acceptable=['x'],budget=100),{'x':dict(list_price=100)})
        self.assertEqual(rows,[])
