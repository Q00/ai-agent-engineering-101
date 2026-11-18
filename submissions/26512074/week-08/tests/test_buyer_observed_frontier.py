import time
import unittest
from unittest.mock import patch,AsyncMock
import buyer_r3


class ObservedFrontierTests(unittest.TestCase):
    def test_uses_positive_all_not_only_highest_raw_quality(self):
        rows=[(0,dict(url=x,name=x),'x',100) for x in ('a','b')]
        memory={x:{'quotes':[dict(sku='x',ask=p,at=time.time(),grade='A')]} for x,p in [('a',40),('b',50)]}
        with patch('patch36.outcome',side_effect=lambda v,p,*a:1-p/100),patch('patch36.saved_objective',side_effect=lambda q,r:q*r):
            r=buyer_r3.observed_frontier(rows,dict(budget=100),{'x':dict(list_price=100)},[dict(shop='a',paid=False)]*20,memory)
        self.assertEqual(r['shop'],'b')

    def test_stale_overbudget_missing_food_lifetime_and_negative_all_excluded(self):
        row=[(0,dict(url='a',name='a'),'x',100)];q=dict(sku='x',ask=50,at=time.time(),grade='A');memory={'a':{'quotes':[q]}}
        self.assertIsNone(buyer_r3.observed_frontier(row,dict(budget=40),{},[],memory))
        q['at']=time.time()-31
        self.assertIsNone(buyer_r3.observed_frontier(row,dict(budget=100),{},[],memory))
        q['at']=time.time()
        self.assertIsNone(buyer_r3.observed_frontier(row,dict(budget=100,kind='food',use_within_hours=3),{},[],memory))
        with patch('patch36.outcome',return_value=.8),patch('patch36.saved_objective',return_value=-.01):
            self.assertIsNone(buyer_r3.observed_frontier(row,dict(budget=100),{'x':dict(list_price=100)},[],memory))


class FrontierIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_strong_attempt_then_available_frontier_settles(self):
        def task(state,text=''):
            return dict(id='t',status=dict(state=state,message=dict(parts=[dict(text=text)])))
        done=task('TASK_STATE_COMPLETED');done['artifacts']=[dict(parts=[dict(data=dict(task='t',item='x',price=70,claim='A',sig_seller='test'))])]
        scripts={'a':[task('TASK_STATE_INPUT_REQUIRED','asking 100, grade A'),task('TASK_STATE_REJECTED')],
                 'b':[task('TASK_STATE_INPUT_REQUIRED','asking 70, grade A'),done]};bids=[];targets=[]
        class Client:
            def __init__(self,url,**kw):self.url=url
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):pass
            async def cancel(self,*a):pass
            async def send(self,text,*a):
                if text.isdecimal():bids.append((self.url,text))
                return scripts[self.url].pop(0)
        def ceiling(*args):
            targets.append(args[-1]);return 20 if args[-1] is not None and args[-1]>=.9 else 100
        rows=[(0,dict(url=s,name=s),'x',100) for s in ('a','b')];memory={'b':{'quotes':[dict(sku='x',ask=70,grade='A',at=time.time())]}}
        with patch.object(buyer_r3,'A2AClient',Client),patch.object(buyer_r3,'candidates',return_value=rows),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda r:r)),patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda r,*a,**kw:r)),patch.object(buyer_r3,'price_ceiling',side_effect=ceiling),patch.object(buyer_r3,'offer_for',side_effect=lambda audit,ask,limit,*a:limit),patch('patch36.outcome',side_effect=lambda v,p,*a:1-p/200),patch('patch36.saved_objective',return_value=.001):
            r=await buyer_r3.buy([],dict(budget=100,acceptable=['x'],expires=time.time()+200),{'x':dict(list_price=200,name='X',kind='goods')},memory=memory)
        self.assertEqual(r['receipt']['price'],70)
        self.assertGreaterEqual(targets[0],.9);self.assertAlmostEqual(targets[1],.65,places=4)
        self.assertEqual(bids,[('a','20'),('b','70')])
