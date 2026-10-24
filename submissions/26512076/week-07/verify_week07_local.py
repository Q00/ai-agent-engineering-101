"""Offline checks with fake model/A2A responses; NOT the official market test."""
import ast
import asyncio
import base64
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import market_support as support

HERE = Path(__file__).resolve().parent


def source_namespace(filename, **extra):
    # Execute the actual implementation definitions without optional SDK imports
    # or the CLI entry point. Transport/model behavior is explicitly mocked.
    tree = ast.parse((HERE / filename).read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))
                 and not isinstance(n, ast.If)]
    ns = dict(vars(support), asyncio=asyncio, base64=base64, logging=logging,
              serialization=serialization, AgentExecutor=object, __file__=str(HERE/filename), **extra)
    exec(compile(tree, filename, 'exec'), ns)
    return ns


class SafetyTests(unittest.TestCase):
    def test_old_mismatch_survives_recent_memory_window(self):
        ns=source_namespace('market_buyer.py')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'memory.jsonl'
            rows=[{'shop_name':'bad','result':'receipt_mismatch'}]
            rows += [{'shop_name':'good','result':'purchased'} for _ in range(250)]
            path.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            self.assertTrue(any(r['shop_name']=='bad' for r in ns['read_memory'](path)))

    def test_seller_uses_explicit_own_reviews_and_phase(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(support,'ROOT',Path(folder)):
            (Path(folder)/'shop_feedback.json').write_text(json.dumps({
                'name':'26512076','reviews':[{'stars':3,'review':'fixture only','round':2}]}))
            with patch.dict(os.environ,{'PRICE_PHASE':'after','MARKET_SHOP_NAME':'26512076'}):
                self.assertEqual(len(support.seller_feedback()['own_shop_reviews_untrusted']),1)
            with patch.dict(os.environ,{'PRICE_PHASE':'before'}):
                self.assertFalse(support.seller_feedback()['feedback_enabled'])

    def test_memory_excludes_changed_url_and_technical_errors_do_not(self):
        shops = [{'name':'bad','url':'https://new','reviews':[{'stars':5}]*30},
                 {'name':'good','url':'https://other','reviews':[]}]
        memory = [{'shop_name':'bad','result':'receipt_mismatch'},
                  {'shop_name':'good','result':'technical_failure'}]
        i, _, blocked = support.selection(shops, memory)
        self.assertEqual(i, 1)
        self.assertEqual(blocked, ['bad'])
        self.assertIsNone(support.selection(shops[:1], memory)[0])

    def test_bad_reviews_and_nonfinite_reviews(self):
        shops=[{'name':'bad','reviews':[{'stars':1},{'stars':2}]},
               {'name':'good','reviews':[{'stars':5},{'stars':4},{'stars':float('nan')}]}]
        self.assertEqual(support.selection(shops, [])[0], 1)

    def test_receipt_checks(self):
        rec={'shop':'shop','item':'lamp','price':110.0,'task':'task-1'}
        self.assertIsNone(support.receipt_problem(rec,'lamp',120,110,'task-1'))
        for change in [{'price':121}, {'price':111}, {'price':True}, {'price':float('nan')},
                       {'price':110.5}, {'item':'chair'}, {'task':'old'}, {'task':None}]:
            self.assertIsNotNone(support.receipt_problem({**rec,**change},'lamp',120,110,'task-1'))


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_seller_guard_signature_and_invalid_accept(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            key=Ed25519PrivateKey.generate()
            keypath=root/'shop_key.pem'
            keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            updates=[]
            class Updater:
                def __init__(self, **kwargs): self.parts=[]; updates.append(self)
                def new_agent_message(self, parts): return parts
                async def requires_input(self, message): self.state='input-required'; self.message=message
                async def add_artifact(self, parts, **kwargs): self.parts=parts
                async def complete(self, message): self.state='completed'
                async def reject(self, message): self.state='rejected'
                async def failed(self, message): self.state='failed'
            ns=source_namespace('seller.py', TaskUpdater=Updater,
                get_message_text=lambda message: message,
                new_data_part=lambda data:{'data':data}, new_text_part=lambda text:text)
            ns['ROOT']=root
            ns['seller_feedback']=lambda:{'phase':'after','feedback_enabled':True}
            with patch.dict(os.environ, {'SHOP_KEY_PATH':str(keypath)}):
                seller=object.__new__(ns['Seller']); seller.deals={}; seller.locks={}
                seller.decide=AsyncMock(return_value={'action':'counter','item':'bookshelf','price':240})
                context=types.SimpleNamespace(current_task=types.SimpleNamespace(id='t1',context_id='c1'),message='bookshelf')
                await seller.execute(context,None)
                self.assertEqual(updates[-1].message,['bookshelf: asking 260'])
                seller.decide=AsyncMock(return_value={'action':'accept','item':'bookshelf','price':260})
                context.message='260'
                await seller.execute(context,None)
                receipt=updates[-1].parts[0]['data']
                self.assertEqual(updates[-1].state,'completed')
                self.assertEqual(receipt['task'],'t1')
                self.assertIs(type(receipt['price']),int)
                key.public_key().verify(base64.b64decode(receipt['sig_seller']),
                    ns['canon']({k:receipt[k] for k in ('shop','item','price','task')}))
                context.current_task=types.SimpleNamespace(id='t2',context_id='c2')
                context.message='230'
                await seller.execute(context,None)
                self.assertEqual(updates[-1].state,'failed')
                self.assertFalse(updates[-1].parts)

    async def test_actual_buyer_rejects_changed_price_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            ns=source_namespace('market_buyer.py')
            ns['ROOT']=Path(folder)
            buyer=object.__new__(ns['Buyer'])
            buyer.url='https://fake'; buyer.item='lamp'; buyer.budget=120
            buyer.last_offer=None; buyer.task_id='t1'
            buyer.market_context={'shop_name':'bad'}
            card={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'https://fake/'}]}
            response=types.SimpleNamespace(raise_for_status=lambda:None,json=lambda:card)
            http=types.SimpleNamespace(get=AsyncMock(return_value=response))
            initial={'status':{'state':'TASK_STATE_INPUT_REQUIRED'}}
            final={'status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':{
                'shop':'bad','item':'lamp','price':115,'task':'t1'}}]}]}
            buyer.send=AsyncMock(side_effect=[initial,final])
            buyer.decide=AsyncMock(return_value={'action':'offer','price':110,'reason':'test'})
            with self.assertRaisesRegex(ValueError,'agreed offer'):
                await buyer.negotiate(http)
            rows=support.read_events(Path(folder)/'logs/challenge-2/avoidance.jsonl')
            self.assertEqual(rows[-1]['received_receipt']['price'],115)
            self.assertEqual(rows[-1]['last_offer'],110)

    async def test_provider_long_limit_does_not_repeat_calls(self):
        class Quota(Exception):
            status_code=429
            response=types.SimpleNamespace(headers={'retry-after':'120'})
        with tempfile.TemporaryDirectory() as folder, patch.object(support,'ROOT',Path(folder)):
            create=AsyncMock(side_effect=Quota('try again in 2m0s'))
            client=types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
            with self.assertRaises(Quota): await support.model_request(client,model='fake')
            with self.assertRaisesRegex(RuntimeError,'cooldown'): await support.model_request(client,model='fake')
            self.assertEqual(create.await_count,1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
