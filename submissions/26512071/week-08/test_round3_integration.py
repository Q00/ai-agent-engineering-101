"""Exercise real A2A routing; only model output replaced, no paid/network calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import httpx
import seller

class Integration(unittest.IsolatedAsyncioTestCase):
    async def test_adaptive_opening_and_event_attribution(self):
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(seller, MARKET=True, NICK='test',
                TALKS=Path(tmp)/'talks.jsonl', EVENTS=Path(tmp)/'events.jsonl'), patch.dict('os.environ', {'SHOP_POLICY':'adaptive'}), \
                patch.object(seller, 'decide', AsyncMock(side_effect=[('ask',999,'Here is my price.'), ('accept',999,'Thank you.')])), \
                patch.object(seller, 'check_reviews', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build(9999)), base_url='http://test') as c:
                msg={'role':'ROLE_USER','messageId':'test-open','parts':[{'text':'I want to buy a lamp'}]}
                r=await c.post('/', headers={'A2A-Version':'1.0'},json={'jsonrpc':'2.0','id':'1','method':'SendMessage','params':{'message':msg}})
                data=r.json()['result']; task=data.get('task',data)
                reply=task['status']['message']['parts'][0]['text']
                self.assertIn('103', reply)
                msg.update(taskId=task['id'], messageId='test-counter', parts=[{'text':'80'}])
                r=await c.post('/', headers={'A2A-Version':'1.0'},json={'jsonrpc':'2.0','id':'2','method':'SendMessage','params':{'message':msg}})
                data=r.json()['result']; task=data.get('task',data)
                self.assertIn('100',task['status']['message']['parts'][0]['text'])
                await c.post('/',headers={'A2A-Version':'1.0'},json={'jsonrpc':'2.0','id':'3','method':'CancelTask','params':{'id':task['id']}})
            row=json.loads((Path(tmp)/'talks.jsonl').read_text())
            self.assertEqual(row['policy'],'adaptive')
            self.assertEqual(row['reserve'],100)
            self.assertEqual(row['outcome'],'buyer canceled')

    async def test_invalid_initial_accept_still_uses_adaptive_quote(self):
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(seller, MARKET=True, NICK='test',
                TALKS=Path(tmp)/'talks.jsonl', EVENTS=Path(tmp)/'events.jsonl'), patch.dict('os.environ', {'SHOP_POLICY':'adaptive'}), \
                patch.object(seller, 'decide', AsyncMock(return_value=('accept',0,'Thank you.'))), \
                patch.object(seller, 'check_reviews', AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build(9999)), base_url='http://test') as c:
                msg={'role':'ROLE_USER','messageId':'test-invalid','parts':[{'text':'I want to buy a lamp'}]}
                r=await c.post('/',headers={'A2A-Version':'1.0'},json={'jsonrpc':'2.0','id':'invalid','method':'SendMessage','params':{'message':msg}})
                data=r.json()['result']; task=data.get('task',data)
                self.assertIn('103',task['status']['message']['parts'][0]['text'])
                await c.post('/',headers={'A2A-Version':'1.0'},json={'jsonrpc':'2.0','id':'cleanup','method':'CancelTask','params':{'id':task['id']}})
                import asyncio
                await asyncio.sleep(0)

if __name__ == '__main__': unittest.main()
