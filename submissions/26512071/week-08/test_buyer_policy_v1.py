"""RED: buyer bounded-v1 policy. Simulated JSON-RPC seller, in-memory fixture keys, no model or market I/O."""
import argparse,base64,json,os,re,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import buyer_r3

SHOP='http://zeta.test'
FOOD={'acceptable':['GLOCAL'],'kind':'food','use_within_hours':7}

def pubkey(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()

def signed(key,price,task,sku='GLOCAL'):
    body={'shop':SHOP,'item':sku,'price':price,'task':task}
    canon=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return dict(body,sig_seller=base64.b64encode(key.sign(canon)).decode())

def seller(script,key):
    """Scripted seller: ('ask',price,task) or ('done',price,task[,signer]). Records every buyer text."""
    seen={'texts':[],'cancel':0}
    def handle(request):
        body=json.loads(request.content)
        if body['method']=='CancelTask':
            seen['cancel']+=1;return httpx.Response(200,json={'result':{}})
        seen['texts'].append(' '.join(p.get('text','') for p in body['params']['message']['parts']))
        step=script[len(seen['texts'])-1] if len(seen['texts'])<=len(script) else ('fail',0,script[-1][2])
        kind,price,task=step[:3]
        if kind=='ask':
            t={'id':task,'status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':f'asking {price} credits'}]}}}
        elif kind=='done':
            t={'id':task,'status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':signed(step[3] if len(step)>3 else key,price,task)}]}]}
        else:
            t={'id':task,'status':{'state':'TASK_STATE_FAILED'}}
        return httpx.Response(200,json={'result':{'task':t}})
    return handle,seen

def offers(seen):
    # texts[0] is the opening request; later texts are the buyer's numeric offers.
    return [int(re.search(r'\d+',t).group()) for t in seen['texts'][1:]]

async def negotiate(script,key,budget,order=FOOD,pub=None):
    handle,seen=seller(script,key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        out=await buyer_r3.negotiate(c,{'url':SHOP},'GLOCAL',budget,pub or pubkey(key),order,rpc_url=SHOP+'/a2a')
    return out,seen


class NegotiationPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_counter_once_then_meet_unchanged_quote_with_signed_receipt(self):
        key=Ed25519PrivateKey.generate()
        out,seen=await negotiate([('ask',100,'t1'),('ask',100,'t1'),('done',100,'t1')],key,120)
        self.assertEqual(offers(seen),[90,100])  # one discount, then meet the unchanged ask; no repeated discount
        self.assertEqual(out['receipt']['price'],100)
        self.assertIs(type(out['receipt']['price']),int)
        self.assertEqual(out['receipt']['task'],'t1')
        self.assertEqual(out['accept'],{'cert':True,'grades':['A','B'],'min_hours_left':7})
        self.assertEqual(buyer_r3.policy({'kind':'goods'}),{'cert':True,'grades':['A','B'],'warranty_hours':0})

    async def test_lowered_raised_invalid_budget_task_pin_and_signature_guards(self):
        key=Ed25519PrivateKey.generate();other=Ed25519PrivateKey.generate()
        out,seen=await negotiate([('ask',100,'t1'),('ask',95,'t1'),('done',95,'t1')],key,120)
        self.assertEqual(offers(seen),[90,95])
        self.assertEqual(out['receipt']['price'],95)
        out,seen=await negotiate([('ask',100,'t1'),('ask',110,'t1'),('done',110,'t1'),('done',110,'t1')],key,120)
        self.assertEqual(offers(seen)[:1],[90])
        self.assertTrue(all(o<=100 for o in offers(seen)),offers(seen))  # never chase a raised quote
        self.assertIsNone(out['receipt'])
        for budget in (float('nan'),float('inf'),0,-5):
            out,seen=await negotiate([('ask',100,'t1'),('ask',100,'t1'),('done',100,'t1')],key,budget)
            self.assertIsNone(out['receipt'],budget)
            self.assertEqual(offers(seen),[],budget)
        out,_=await negotiate([('ask',100,'t1'),('done',100,'substituted')],key,120)
        self.assertIsNone(out['receipt'])  # task pinned to the original id
        out,_=await negotiate([('ask',100,'t1'),('ask',100,'t1'),('done',100,'t1',other)],key,120)
        self.assertIsNone(out['receipt'])  # registry key only; other signer refused


class EvidencePreferenceTests(unittest.TestCase):
    def test_paid_grade_ab_honest_warranty_evidence_outranks_reviews_and_unknowns(self):
        def paid(ledger,shop,**settled):
            return dict(order=f'o{ledger}',shop=shop,sku='G1',price=50,ledger=ledger,sources=[{'file':'payments.jsonl','line':ledger}],**settled)
        memory={'purchases':{
            '1':paid(1,'http://alpha.test'),  # grade unknown
            '2':paid(2,'http://alpha.test',grade='A',claim_honest=False,warranty_hours=24),
            '11':paid(11,SHOP,grade='A',claim_honest=True,warranty_hours=24),
            '12':paid(12,SHOP,grade='B',claim_honest=True,warranty_hours=12)},
            'refusals':{},'broken_unresolved':{'x':{'observed':{'name':'Fixture','broke_at':5},'status':'unresolved'}}}
        catalog={'G1':{'list_price':100,'category':'cat'}}
        shops=[{'name':'alpha','url':'http://alpha.test','offers':[{'sku':'G1','qty':1}],'reviews':[{'stars':5,'text':'best ever'}]*50,'break_prob':None},
               {'name':'zeta','url':SHOP,'offers':[{'sku':'G1','qty':1}],'reviews':[],'break_prob':None}]
        order={'acceptable':['G1'],'kind':'goods'}
        self.assertEqual(buyer_r3.candidates(shops,order,memory,catalog)[0][0]['name'],'zeta')
        pref=buyer_r3.evidence_preference(memory,catalog)
        self.assertEqual(pref['sample'],'local-biased')
        self.assertEqual(sorted(pref['shops'][SHOP]['quality_evidence_ids']),['11','12'])
        self.assertEqual(pref['shops'].get('http://alpha.test',{}).get('quality_evidence_ids',[]),[])


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_journals_decision_under_market_only(self):
        key=Ed25519PrivateKey.generate();pub=pubkey(key)
        secret=base64.b64encode(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())).decode()
        rpc,_=seller([('ask',100,'t1'),('ask',100,'t1'),('ask',100,'t1'),('done',100,'t1')],key)  # v2: list 125 -> offers 87, 90, 100
        def handle(request):
            if request.url.host=='market.ourolabs.ai':return httpx.Response(200,json=[{'url':SHOP,'pubkey':pub}])
            if request.url.path=='/.well-known/agent-card.json':
                return httpx.Response(200,json={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':SHOP+'/a2a'}]})
            return rpc(request)
        real=httpx.AsyncClient
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                market=Path('.market');market.mkdir()
                (market/'payments.jsonl').write_text(json.dumps({'order':'o7','shop':SHOP,'item':'GLOCAL','price':100,'paid':True,'ledger':7})+'\n')
                (market/'deal.json').write_text(json.dumps({'order':'o7','receipt':{'price':100,'warranty_hours':24},
                    'settled':{'ledger':7,'sku':'GLOCAL','shop':SHOP,'grade':'A','claim_honest':True,'warranty_hours':24}}))
                (market/'catalog.json').write_text(json.dumps({'GLOCAL':{'list_price':125,'category':'cat'}}))
                Path('shops.json').write_text(json.dumps([{'name':'zeta','url':SHOP,'offers':[]}]))
                args=argparse.Namespace(order=None,item='GLOCAL',budget=120.0,shops='shops.json',url=None,decide=None)  # explicit deterministic replay; live default is Terra
                with patch.object(buyer_r3.httpx,'AsyncClient',lambda *a,**kw:real(transport=httpx.MockTransport(handle))):
                    result=await buyer_r3.run(args)
                self.assertEqual(result['receipt']['price'],100)
                self.assertEqual(sorted(os.listdir('.')),['.market','shops.json'])  # journal lives under .market only
                text=(market/'buyer-decisions.jsonl').read_text(encoding='utf-8')
                self.assertNotIn(secret,text)
                row=json.loads(text.splitlines()[-1])
                self.assertEqual(row['policy_version'],'bounded-v2-list')
                self.assertEqual(row['attempts'][0]['offers'],[87,90,100])
                self.assertEqual(row['source_order'],[SHOP])
                self.assertIn('7',row['memory_ids'])
                self.assertEqual(row['sample'],'local-biased')
                self.assertEqual((row['decision'],row['shop'],row['task']),('accepted',SHOP,'t1'))
            finally:os.chdir(old)

if __name__=='__main__':unittest.main()
