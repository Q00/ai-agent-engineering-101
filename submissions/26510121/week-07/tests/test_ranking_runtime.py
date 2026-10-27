"""Checkout read-only preflight and failed-order timing through the real adapter."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import httpx
from market_buyer import checkout_available
from negotiation.experience import Experience

spec=importlib.util.spec_from_file_location('ranking_runtime_test',Path(__file__).parents[1]/'agent-market'/'run-kit.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


class Log:
    def __init__(self):self.events=[]
    def record(self,event,**data):self.events.append({'event':event,**data})


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_only_reads_and_rejects_foreign_endpoint(self):
        selected={'name':'s','url':'https://s.example','pubkey':'public-key'}
        requests=[]
        for endpoint,valid in [('https://s.example/acp',True),('https://other.example/acp',False)]:
            def respond(request):
                requests.append(request)
                return httpx.Response(200,json={'protocol':'acp','endpoint':endpoint,
                    'capabilities':{'services':['checkout'],'payment_methods':['ap2-mandate']},
                    'tools':[{'name':'create_checkout_session'},{'name':'complete_checkout_session'}]})
            with tempfile.TemporaryDirectory() as folder:
                memory=Experience(Path(folder)/'db')
                self.assertEqual(await checkout_available(selected,memory,Log(),transport=httpx.MockTransport(respond)),valid)
                observed=memory.buyer_evidence('lamp',130,{'s':'public-key'})['s']
                self.assertEqual(observed['acp_available'],valid)
        self.assertTrue(all(r.method=='GET' and r.url.host=='s.example' for r in requests))

    async def test_transport_failure_is_recorded_without_any_binding_offer(self):
        calls=[]
        def respond(request):
            calls.append(request)
            raise httpx.ConnectError('test offline',request=request)
        valid=await checkout_available({'name':'s','url':'https://s.example','pubkey':'k'},None,Log(),transport=httpx.MockTransport(respond))
        self.assertFalse(valid)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].method,'GET')


class AdapterTimingTests(unittest.TestCase):
    def test_poll_wait_and_failed_no_receipt_order_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);memory=Experience(root/'db');market=SimpleNamespace()
            row={'name':'s','item':'lamp','budget':130,'outcome':'no_deal','price':None,
                 'task_id':None,'order_id':'o','pubkey':'k','seconds':20,'epoch':100,'sequence':1}
            memory.buyer_observation(row,round_number=3)
            with patch.object(runtime,'AGENT',root),patch.object(runtime,'experience',memory), \
                 patch.dict(os.environ,{'MARKET_ROUND':'3'}),patch.object(runtime,'original_call') as call, \
                 patch.object(runtime.time,'monotonic') as clock:
                clock.return_value=100;call.return_value=httpx.Response(429,json={'wait':10})
                runtime.market_call(market,'POST','/api/orders')
                clock.return_value=110;call.return_value=httpx.Response(200,json={'order':'o','round':3})
                runtime.market_call(market,'POST','/api/orders')
                clock.return_value=130;call.return_value=httpx.Response(200,json={'verified':False,'checks':{'paid':False},'ledger':4})
                runtime.market_call(market,'POST','/api/results',json={'order':'o','receipt':None})
            feedback=json.loads((root/'memory'/'market-verification.jsonl').read_text(encoding='utf-8'))
            self.assertEqual((feedback['total_seconds'],feedback['processing_seconds'],feedback['wait_seconds']),(30,20,10))
            stats=memory.buyer_evidence('lamp',130,{'s':'k'},round_number=3,now=130)['s']
            self.assertEqual((stats['finalized'],stats['successes'],stats['mean_cycle_seconds']),(1,0,30))
            self.assertIsNone(market._ranking_cycle_start)


if __name__=='__main__':unittest.main()
