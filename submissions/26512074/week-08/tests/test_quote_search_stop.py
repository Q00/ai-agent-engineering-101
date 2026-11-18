import asyncio
import unittest
from unittest.mock import patch
from buyer_compare import compare


class QuoteSearchStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_dominating_high_score_quote_stops_pending_search(self):
        cancelled=[]
        class Client:
            def __init__(self,url,**kw):self.url=url
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):pass
            async def cancel(self,*a):pass
            async def send(self,*a):
                if self.url=='slow':
                    try:await asyncio.sleep(10)
                    except asyncio.CancelledError:cancelled.append('slow');raise
                return dict(id='fast',status=dict(state='TASK_STATE_INPUT_REQUIRED',message=dict(parts=[dict(text='asking 50, grade A, 24 hours of shelf life left')])))
        rows=[(.1e-6,dict(url='fast'),'x',100),(0,dict(url='slow'),'x',100)]
        policy=dict(buyer_quote_parallelism=2,buyer_search_seconds=.2,buyer_search_ready_score=.95)
        real_wait=asyncio.wait
        with patch('buyer_compare.A2AClient',Client),patch('strategy_policy.load',return_value=policy),patch('buyer_compare.price_ceiling',return_value=100),patch('patch36.outcome',return_value=.99),patch('patch36.comparison_price',return_value=50),patch('patch36.saved_objective',side_effect=lambda s,r:s*r*1e-6),patch('buyer_compare.asyncio.wait',wraps=real_wait) as wait:
            result=await compare(rows,dict(kind='food',budget=100,use_within_hours=2),{'x':dict(name='X',kind='food',list_price=100)}, {},limit=None)
        self.assertEqual(wait.call_count,1);self.assertEqual(cancelled,['slow']);self.assertEqual(result[0][1]['url'],'fast')
