import time
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
import buyer_r3
from buyer_r3 import candidate_passes


class RecoveryPassTests(unittest.TestCase):
    def setup_rows(self):
        rows=[(0,dict(url=s,name=s),'x',100) for s in ('a','b')]
        memory={s:{'quotes':[dict(sku='x',ask=p,at=time.time(),grade='A',warranty_hours=6)]} for s,p in [('a',70),('b',60)]}
        return rows,memory

    def test_recovery_is_lazy_single_best_actual_quote(self):
        rows,memory=self.setup_rows()
        with patch('patch36.outcome',side_effect=lambda value,price,*args:1-price/100),patch('patch36.saved_objective',side_effect=lambda score,reliability:score):
            passes=candidate_passes(rows,dict(budget=100),{'x':dict(list_price=100)},[],memory)
            self.assertEqual(next(passes),(rows[0],False))
            self.assertEqual(next(passes),(rows[1],False))
            # Quotes learned during the first pass are authoritative.
            memory['a']['quotes'][-1]['ask']=50
            result=list(passes)
        self.assertEqual(len(result),1)
        self.assertTrue(result[0][1]);self.assertEqual(result[0][0][1]['url'],'a')
        self.assertEqual(result[0][0][1]['_comparison_quote']['grade'],'A')

    def test_expired_stale_overbudget_or_invalid_seller_not_retried(self):
        rows,memory=self.setup_rows()
        order=dict(budget=100,expires=time.time()+7)
        self.assertEqual(len(list(candidate_passes(rows,order,{'x':{}},[],memory))),2)
        memory['a']['avoid_until']=time.time()+100
        memory['b']['quotes'][-1]['ask']=101
        self.assertEqual(len(list(candidate_passes(rows,dict(budget=100),{'x':{}},[],memory))),2)

    def test_nonpositive_all_lead_is_not_recovered(self):
        rows,memory=self.setup_rows()
        with patch('patch36.outcome',return_value=.5),patch('patch36.saved_objective',return_value=-.001):
            self.assertEqual(len(list(candidate_passes(rows,dict(budget=100),{'x':dict(list_price=100)},[],memory))),2)


class RecoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_aggressive_pass_recovers_without_another_comparison(self):
        def task(state,text=''):
            return dict(id='t',status=dict(state=state,message=dict(parts=[dict(text=text)])))
        done=task('TASK_STATE_COMPLETED')
        done['artifacts']=[dict(parts=[dict(data=dict(task='t',item='x',price=70,claim='A',sig_seller='test'))])]
        replies=[task('TASK_STATE_INPUT_REQUIRED','asking 70, grade A'),task('TASK_STATE_REJECTED'),task('TASK_STATE_INPUT_REQUIRED','asking 70, grade A'),done]
        calls=[]
        class Client:
            def __init__(self,*a,**kw):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):pass
            async def cancel(self,*a):pass
            async def send(self,text,*a):calls.append(text);return replies.pop(0)
        def ceiling(*args):return 80 if args[-1] is None else 30
        memory={}
        with patch.object(buyer_r3,'A2AClient',Client),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda r:r)),patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda r,*a,**kw:r)) as compare,patch.object(buyer_r3,'price_ceiling',side_effect=ceiling),patch.object(buyer_r3,'offer_for',side_effect=lambda audit,ask,limit,*a:limit),patch('patch36.outcome',return_value=.8),patch('patch36.saved_objective',return_value=.001):
            result=await buyer_r3.buy([dict(url='shop',name='shop',offers=[dict(sku='x',qty=1)])],dict(budget=100,acceptable=['x'],expires=time.time()+100),{'x':dict(list_price=100,name='X',kind='goods')},memory=memory)
        self.assertEqual(result['receipt']['price'],70)
        self.assertEqual(compare.await_count,1)
        self.assertEqual(calls[1],'30');self.assertEqual(calls[3],'80')
        self.assertEqual(memory['shop']['recovery_attempts'][0]['receipt_task'],'t')
        self.assertNotIn('budget',memory['shop']['recovery_attempts'][0])
