import tempfile,json,unittest,hashlib
from pathlib import Path
from optimizer import db
from extreme_quotes import terms,is_extreme,negotiation,decide,evaluate,settled_review_notes
from purchase_guidance import quote_suffix
from policy import read_price

def identity(high):
    return next(str(i) for i in range(1000) if (int.from_bytes(hashlib.sha256(('round3-price-v1:'+str(i)).encode()).digest()[:8],'big')%100<20)==high)

class ExtremeTests(unittest.IsolatedAsyncioTestCase):
    def configure(self,d):
        Path(d,'extreme-quote-policy.json').write_text(json.dumps(dict(enabled=True,allocation=20,experiment_started_at=1000)))

    async def test_stable_price_and_guidance_unchanged_and_no_concession(self):
        with tempfile.TemporaryDirectory() as d:
            self.configure(d);u=dict(sku='x',unit='u',grade='A');task=identity(True)
            r=terms(d,task,u,100000,6,True,lambda _:True,1001)
            self.assertEqual(r['effective_quote'],5000000)
            self.assertEqual(negotiation(r,4999999)['price'],5000000)
            self.assertEqual(negotiation(r,5000000)['action'],'accept')
            self.assertEqual(terms(d,task,u,999,0,False,lambda _:False,1002),r)
            self.assertIsNone(terms(d,'old',u,100,0,False,lambda _:True,1002))
            normal=terms(d,identity(False),u,100,0,True,lambda _:True,1002)
            self.assertEqual(normal['effective_quote'],100)

    async def test_warranty_fallback_and_exact_parser_json_price(self):
        from purchase_guidance import purchase_guidance
        with tempfile.TemporaryDirectory() as d:
            self.configure(d);u=dict(sku='x',unit='u',grade='B')
            r=terms(d,identity(True),u,100,6,True,lambda _:False,1001)
            self.assertFalse(is_extreme(r));self.assertEqual(r['effective_quote'],100)
            self.assertIn('x50_infeasible',r['reason'])
            text='Counteroffer: 5000000.'+purchase_guidance(u,5000000,6)
            self.assertEqual(await read_price(text,role='ask',allow_model=False),5000000)
            self.assertEqual(json.loads(text[text.index('{'):])['seller_offer_guidance']['quoted_price'],5000000)

    async def test_deadlines_disable_only_new_quotes_and_keep_history(self):
        with tempfile.TemporaryDirectory() as d:
            self.configure(d);u=dict(sku='x',unit='u',grade='A');task=identity(True)
            first=terms(d,task,u,100,0,True,lambda _:True,1001)
            self.assertEqual(decide(30,0,0,10,None,2),'CONTINUE_EXPERIMENT')
            self.assertEqual(decide(29,0,0,10,None,6),'EXTEND_OBSERVATION')
            self.assertEqual(decide(29,0,0,10,None,12),'DISABLE_X50_INSUFFICIENT_DATA')
            self.assertEqual(decide(30,0,0,10,None,6),'DISABLE_X50_NO_SETTLEMENT')
            self.assertEqual(decide(30,3,12,10,True,6,True),'KEEP_X50_CANDIDATE')
            self.assertEqual(terms(d,task,u,100,0,False,lambda _:True,44200)['effective_quote'],first['effective_quote'])
            self.assertFalse(json.loads(Path(d,'extreme-quote-policy.json').read_text())['enabled'])

    async def test_evaluation_six_hour_disable_writes_policy_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as d:
            self.configure(d);u=dict(sku='x',unit='u',grade='A')
            for i in range(30):
                task='high'+str(i)
                with db(d) as c:
                    c.execute('insert into trials(task,sku,unit,at,cost,inquiry) values(?,?,?,?,?,0)',(task,'x','u',1001,100))
                quote_suffix(d,task,u,5000,0,True,1001)
                terms(d,task,u,100,0,True,lambda _:True,1001)
                with db(d) as c:c.execute("update extreme_quotes set price_arm='x50',effective_quote=5000,requested_quote=5000,requested_multiplier=50 where task=?",(task,))
            r=evaluate(d,22601)
            self.assertEqual(r['decision'],'DISABLE_X50_NO_SETTLEMENT')
            self.assertFalse(json.loads(Path(d,'extreme-quote-policy.json').read_text())['enabled'])
            self.assertTrue(Path(d,'pricing_x50_v1-checkpoint-6h.json').exists())

    async def test_x2_new_version_preserves_x50_and_separates_results(self):
        with tempfile.TemporaryDirectory() as d:
            self.configure(d);u=dict(sku='x',unit='u',grade='A');task=identity(True)
            old=terms(d,task,u,100,0,True,lambda _:True,1001)
            Path(d,'extreme-quote-policy.json').write_text(json.dumps(dict(enabled=True,allocation=100,multiplier=2,experiment='pricing_x2_v2',experiment_started_at=2000)))
            self.assertEqual(terms(d,task,u,200,0,False,lambda _:True,2001)['effective_quote'],old['effective_quote'])
            new=terms(d,'new',u,100,0,True,lambda _:True,2001)
            self.assertEqual(new['effective_quote'],200);self.assertEqual(new['experiment'],'pricing_x2_v2')
            self.assertEqual(negotiation(new,199)['price'],200)
            self.assertEqual(negotiation(new,199,8)['action'],'reject')
            self.assertEqual(negotiation(new,200,8)['action'],'accept')
            for identity_,at in ((task,1001),('new',2001)):
                quote_suffix(d,identity_,u,200,0,True,at)
                with db(d) as c:c.execute('insert into trials(task,sku,unit,at,cost,inquiry) values(?,?,?,?,?,0)',(identity_,'x','u',at,50))
            result=evaluate(d,2100)
            self.assertEqual(result['premium']['quotes'],1)
            self.assertEqual(result['actual_premium_only']['quotes'],1)

    async def test_review_note_requires_market_settlement_and_preserves_receipt(self):
        from a2a.server.tasks import InMemoryTaskStore
        from a2a.server.context import ServerCallContext
        from a2a.types import Task
        from google.protobuf.json_format import ParseDict,MessageToDict
        with tempfile.TemporaryDirectory() as d:
            self.configure(d);task=identity(False)
            terms(d,task,dict(sku='x',unit='u',grade='A'),100,0,True,lambda _:True,1001)
            with db(d) as c:c.execute('insert into trials(task,sku,unit,at) values(?,?,?,?)',(task,'x','u',1001))
            store=InMemoryTaskStore();ctx=ServerCallContext()
            saved=ParseDict({'id':task,'contextId':'c','status':{'state':'TASK_STATE_COMPLETED','message':{'messageId':'m','role':'ROLE_AGENT','parts':[{'text':'Deal: x at 100.'}]}},'artifacts':[{'artifactId':'a','parts':[{'data':{'price':100,'task':task,'sig_seller':'unchanged'}}]}]},Task())
            original=MessageToDict(saved)['artifacts'];await store.save(saved,ctx)
            await settled_review_notes(d,store,1002)
            self.assertEqual(len((await store.get(task,ctx)).status.message.parts),1)
            with db(d) as c:c.execute('update trials set settled_at=1002 where task=?',(task,))
            await settled_review_notes(d,store,1003);await settled_review_notes(d,store,1004)
            updated=await store.get(task,ctx)
            self.assertEqual(len(updated.status.message.parts),2)
            self.assertEqual(MessageToDict(updated)['artifacts'],original)
