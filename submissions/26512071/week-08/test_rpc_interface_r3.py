"""RED regressions for advertised RPC and task binding; no external I/O."""
import asyncio,json,unittest
from unittest.mock import patch
import httpx
import buyer_r3

class InterfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_advertised_same_origin_jsonrpc_path(self):
        seen=[]
        def handle(r):
            seen.append(str(r.url))
            return httpx.Response(200,json={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'https://merchant.test/a2a/0'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            endpoint=await buyer_r3.rpc_endpoint(c,'https://merchant.test')
        self.assertEqual(endpoint,'https://merchant.test/a2a/0')
        self.assertEqual(seen,['https://merchant.test/.well-known/agent-card.json'])

    async def test_rejects_off_origin_credentials_and_redirects(self):
        for endpoint in ['http://127.0.0.1:1234/','https://evil.test/a2a/0','https://user:pass@merchant.test/a2a/0']:
            def handle(r):
                return httpx.Response(200,json={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':endpoint}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
                self.assertIsNone(await buyer_r3.rpc_endpoint(c,'https://merchant.test'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/'}))) as c:
            self.assertIsNone(await buyer_r3.rpc_endpoint(c,'https://merchant.test'))

    async def test_negotiation_posts_and_cancels_to_selected_path(self):
        seen=[]
        def handle(r):
            seen.append(r.url.path)
            return httpx.Response(200,json={'result':{'task':{'id':'t','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':'not a quote'}]}}}}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            out=await buyer_r3.negotiate(c,{'url':'https://merchant.test'},'G1',100,'nonempty',{'item':'category'},rpc_url='https://merchant.test/a2a/0')
        self.assertIsNone(out['receipt'])
        self.assertEqual(seen,['/a2a/0','/a2a/0'])

    async def test_changed_task_id_does_not_rebind_receipt_guard(self):
        n=0
        def handle(r):
            nonlocal n
            body=json.loads(r.content)
            if body['method']=='CancelTask':return httpx.Response(200,json={'result':{}})
            n+=1
            task=({'id':'original','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':'asking 80'}]}}} if n==1 else {'id':'substituted','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':{'item':'G1','task':'substituted','price':80,'sig_seller':'untrusted'}}]}]})
            return httpx.Response(200,json={'result':{'task':task}})
        with patch.object(buyer_r3,'valid_receipt',return_value={'item':'G1','task':'substituted','price':80}) as guard:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
                out=await buyer_r3.negotiate(c,{'url':'https://merchant.test'},'G1',100,'nonempty',{'item':'category'})
        self.assertIsNone(out['receipt'])
        guard.assert_not_called()

if __name__=='__main__':unittest.main()
