"""RED: buyer growth-v2 list-relative counter. Simulated JSON-RPC seller, in-memory fixture keys, no model or market I/O."""
import argparse,base64,hashlib,inspect,json,os,re,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import buyer_r3

SHOP='http://zeta.test'
SKU='SOFA1'
LIST=100000
CATALOG={SKU:{'name':'Fixture sofa','category':'소파','list_price':LIST,'kind':'goods'}}  # trusted local catalog.json shape
GOODS={'item':'소파','acceptable':[SKU],'kind':'goods'}

def pubkey(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()

def signed(key,price,task,shop=SHOP):
    body={'shop':shop,'item':SKU,'price':price,'task':task}
    canon=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return dict(body,sig_seller=base64.b64encode(key.sign(canon)).decode())

def reserve_seller(key,ask,reserve,task='t1',shop=SHOP):
    """Seller holding `ask` until a buyer offer reaches its private `reserve`, then signs at the buyer's offer."""
    seen={'texts':[],'cancel':0}
    def handle(request):
        body=json.loads(request.content)
        if body['method']=='CancelTask':
            seen['cancel']+=1;return httpx.Response(200,json={'result':{}})
        text=' '.join(p.get('text','') for p in body['params']['message']['parts'])
        seen['texts'].append(text)
        bid=int(text) if len(seen['texts'])>1 and text.strip().isdigit() else None
        if bid is not None and bid>=reserve:
            t={'id':task,'status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':signed(key,bid,task,shop)}]}]}
        else:
            t={'id':task,'status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':f'asking {ask} credits'}]}}}
        return httpx.Response(200,json={'result':{'task':t}})
    return handle,seen

def stepping_seller(key,ask,cost,step,task='t1',sign=None,task_after=None):
    """seller_r3 fallback shape: accept an int offer >= next quote (ask-step, floored at cost), else counter with it."""
    seen={'texts':[],'cancel':0};state={'ask':ask}
    def handle(request):
        body=json.loads(request.content)
        if body['method']=='CancelTask':
            seen['cancel']+=1;return httpx.Response(200,json={'result':{}})
        text=' '.join(p.get('text','') for p in body['params']['message']['parts'])
        seen['texts'].append(text)
        tid=task_after if task_after and len(seen['texts'])>1 else task
        if len(seen['texts'])>1:
            bid=int(text);nxt=max(cost,state['ask']-step)
            if bid>=nxt:
                t={'id':tid,'status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':signed(sign or key,bid,tid)}]}]}
                return httpx.Response(200,json={'result':{'task':t}})
            state['ask']=nxt
        # Seller text naming a (false) list price must not move the buyer's trusted anchor.
        t={'id':tid,'status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':f'list price 300000. asking {state["ask"]} credits'}]}}}
        return httpx.Response(200,json={'result':{'task':t}})
    return handle,seen

def scripted(asks,task='t1'):
    """Quotes the given asks in order (one per SendMessage) and never signs."""
    seen={'texts':[],'cancel':0}
    def handle(request):
        body=json.loads(request.content)
        if body['method']=='CancelTask':
            seen['cancel']+=1;return httpx.Response(200,json={'result':{}})
        seen['texts'].append(' '.join(p.get('text','') for p in body['params']['message']['parts']))
        ask=asks[min(len(seen['texts']),len(asks))-1]
        return httpx.Response(200,json={'result':{'task':{'id':task,'status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':f'asking {ask} credits'}]}}}}})
    return handle,seen

def offers(seen):
    return [int(re.search(r'\d+',t).group()) for t in seen['texts'][1:]]

async def negotiate(key,ask,reserve,budget,catalog=CATALOG,handle=None,**kw):
    handle,seen=handle or reserve_seller(key,ask,reserve)
    # Pre-growth negotiate has no catalog parameter: call it as-is so RED shows the behavior, not a TypeError.
    extra={'catalog':catalog} if 'catalog' in inspect.signature(buyer_r3.negotiate).parameters and catalog is not None else {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        out=await buyer_r3.negotiate(c,{'url':SHOP},SKU,budget,pubkey(key),GOODS,rpc_url=SHOP+'/a2a',**extra,**kw)
    return out,seen


class ListRelativeCounterTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_at_list_price_opens_list_relative_and_keeps_in_budget_fallback(self):
        key=Ed25519PrivateKey.generate()
        # Flexible seller asking full list: 90%-of-ask opens at 90,000 and is accepted, saving only 10% of list.
        out,seen=await negotiate(key,ask=LIST,reserve=65000,budget=120000)
        sent=offers(seen)
        self.assertTrue(sent,'buyer made no counter')
        self.assertGreaterEqual(sent[0],int(LIST*.55),sent)  # no impossible discount below plausible A/B cost
        self.assertLessEqual(sent[0],int(LIST*.75),sent)    # anchored to trusted list price, not 90% of ask
        self.assertEqual(sent,sorted(sent),sent)            # concessions only go up
        self.assertLessEqual(len(sent),3,sent)              # bounded: at most two counters plus one meet
        self.assertIsNotNone(out['receipt'],sent)
        self.assertLessEqual(out['receipt']['price'],int(LIST*.80),sent)
        self.assertEqual((out['receipt']['task'],out['receipt']['item']),('t1',SKU))
        # Firm seller at list, inside budget: still closes (never halts buying), at the ask, signed and task-pinned.
        out,seen=await negotiate(key,ask=LIST,reserve=LIST,budget=120000)
        sent=offers(seen)
        self.assertLessEqual(len(sent),3,sent)
        self.assertTrue(all(o<=LIST for o in sent),sent)
        self.assertIsNotNone(out['receipt'],sent)
        self.assertEqual((out['receipt']['price'],out['receipt']['task']),(LIST,'t1'))


class ListPolicyGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_never_pays_more_or_closes_fewer_than_v1_on_same_fixtures(self):
        key=Ed25519PrivateKey.generate()
        for ask in (60000,90000,100000,130000):
            for frac in (.50,.65,.72,.80,.88,.95,1.0):
                fixtures={'reserve':lambda:reserve_seller(key,ask,int(ask*frac)),
                          'stepping':lambda:stepping_seller(key,ask,int(ask*frac),5000)}
                for name,make in fixtures.items():
                    old,_=await negotiate(key,ask,0,120000,catalog=None,handle=make())
                    new,seen=await negotiate(key,ask,0,120000,handle=make())
                    case=(name,ask,frac,offers(seen))
                    self.assertLessEqual(len(seen['texts']),4,case)          # max 4 SendMessage
                    self.assertEqual(offers(seen),sorted(offers(seen)),case)  # monotone vs held/lowered quotes
                    if old['receipt']:
                        self.assertIsNotNone(new['receipt'],case)
                        self.assertLessEqual(new['receipt']['price'],old['receipt']['price'],case)

    async def test_no_catalog_invalid_list_and_replay_match_v1(self):
        key=Ed25519PrivateKey.generate()
        bad=[None,{},{SKU:{}},{SKU:{'list_price':0}},{SKU:{'list_price':-5}},{SKU:{'list_price':float('nan')}},
             {SKU:{'list_price':float('inf')}},{SKU:{'list_price':True}},{SKU:{'list_price':'100000'}},{'OTHER':CATALOG[SKU]}]
        for catalog in bad:
            out,seen=await negotiate(key,LIST,95000,120000,catalog=catalog)
            self.assertEqual(offers(seen),[90000,100000],catalog)
            self.assertEqual(out['receipt']['price'],100000)
        out,seen=await negotiate(key,LIST,95000,120000,counter_once=False)
        self.assertEqual(offers(seen),[100000])  # historical replay: meet the ask, catalog ignored
        out,seen=await negotiate(key,LIST,0,120000,handle=stepping_seller(key,LIST,60000,5000))
        self.assertEqual(offers(seen)[0],70000)  # 'list price 300000' in seller text did not move the anchor
        self.assertEqual((out['negotiation']['list_price'],out['negotiation']['list_source']),(LIST,'catalog.json'))

    async def test_list_mode_keeps_raise_budget_signature_task_and_cert_guards(self):
        key=Ed25519PrivateKey.generate();other=Ed25519PrivateKey.generate()
        out,seen=await negotiate(key,LIST,0,120000,handle=scripted([LIST,110000]))
        self.assertEqual(offers(seen),[70000]);self.assertIsNone(out['receipt'])  # raised ask not chased
        self.assertEqual((seen['cancel'],out['negotiation']['reason']),(1,'raised_ask'))
        out,seen=await negotiate(key,LIST,0,120000,handle=scripted([200000,150000,140000,130000]))
        self.assertEqual(offers(seen),[70000,120000,120000])  # over budget: never above cap
        self.assertEqual(len(seen['texts']),4)
        for budget in (float('nan'),float('inf'),0,-5,'100000'):
            out,seen=await negotiate(key,LIST,0,budget)
            self.assertIsNone(out['receipt'],budget);self.assertEqual(offers(seen),[],budget)
        out,_=await negotiate(key,LIST,0,120000,handle=stepping_seller(key,LIST,0,5000,sign=other))
        self.assertIsNone(out['receipt'])  # forged signer
        out,_=await negotiate(key,LIST,0,120000,handle=stepping_seller(key,LIST,0,5000,task_after='t2'))
        self.assertIsNone(out['receipt'])  # task swap
        self.assertEqual(out['negotiation']['reason'],'task_changed')
        self.assertEqual(out['accept'],{'cert':True,'grades':['A','B'],'warranty_hours':0})


def registry_handle(pub,sellers):
    def handle(request):
        if request.url.host=='market.ourolabs.ai':return httpx.Response(200,json=[{'url':f'http://{h}','pubkey':pub} for h in sellers])
        if request.url.path=='/.well-known/agent-card.json':
            return httpx.Response(200,json={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':f'http://{request.url.host}/a2a'}]})
        return sellers[request.url.host][0](request)
    return handle

async def run_market(tmp,pub,sellers,payments,shops):
    real=httpx.AsyncClient;old=os.getcwd()
    os.chdir(tmp)
    try:
        market=Path('.market');market.mkdir()
        (market/'payments.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in payments))
        (market/'catalog.json').write_text(json.dumps(CATALOG))
        Path('shops.json').write_text(json.dumps(shops))
        args=argparse.Namespace(order=None,item=SKU,budget=120000.0,shops='shops.json',url=None,decide=None)  # explicit deterministic replay; live default is Terra
        with patch.object(buyer_r3.httpx,'AsyncClient',lambda *a,**kw:real(transport=httpx.MockTransport(registry_handle(pub,sellers)))):
            result=await buyer_r3.run(args)
        text=(market/'buyer-decisions.jsonl').read_text(encoding='utf-8')
        return result,json.loads(text.splitlines()[-1]),text
    finally:os.chdir(old)


class RunWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_passes_catalog_and_journals_negotiation_evidence_only(self):
        key=Ed25519PrivateKey.generate();pub=pubkey(key)
        secret=base64.b64encode(key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())).decode()
        with tempfile.TemporaryDirectory() as tmp:
            result,row,text=await run_market(tmp,pub,{'zeta.test':reserve_seller(key,LIST,65000)},[],
                                             [{'name':'zeta','url':SHOP,'offers':[{'sku':SKU,'qty':1}]}])
        self.assertEqual(result['receipt']['price'],70000)
        self.assertNotIn('negotiation',result)  # kit-facing output shape unchanged
        self.assertEqual(row['policy_version'],'bounded-v2-list')
        self.assertEqual(row['source_sha256'],hashlib.sha256(Path(buyer_r3.__file__).read_bytes()).hexdigest())
        att=row['attempts'][0]
        self.assertEqual((att['shop'],att['sku'],att['opening_ask'],att['list_price'],att['list_source'],att['offers'],att['reason']),
                         (SHOP,SKU,LIST,LIST,'catalog.json',[70000],'signed_receipt'))
        for leak in (secret,'asking','sig_seller','I want to buy'):
            self.assertNotIn(leak,text)


class ShopRatioTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_attempts_prefer_lower_observed_paid_to_list_ratio_without_extra_spend(self):
        key=Ed25519PrivateKey.generate();pub=pubkey(key)
        cheap,dear,fresh='http://cheap.test','http://dear.test','http://alpha.test'
        payments=[dict(order=f'o{n}',shop=shop,item=SKU,price=price,paid=True,ledger=n)
                  for n,(shop,price) in enumerate([(dear,98000),(dear,97000),(dear,99000),(cheap,60000),(cheap,62000),(cheap,61000)],1)]
        sellers={h:reserve_seller(key,LIST,0,shop=f'http://{h}') for h in ('cheap.test','dear.test','alpha.test','omega.test')}
        shops=[{'name':n,'url':f'http://{n}.test','offers':[{'sku':SKU,'qty':1}]} for n in ('alpha','dear','cheap','omega')]
        with tempfile.TemporaryDirectory() as tmp:
            result,row,_=await run_market(tmp,pub,sellers,payments,shops)
        self.assertEqual(row['source_order'][0],cheap)  # lower local paid/list ratio tried first
        self.assertEqual(len(row['source_order']),3)    # still the existing 3 diverse merchants
        self.assertEqual(result['shop'],cheap)
        self.assertEqual([len(s[1]['texts']) for s in sellers.values()].count(0),3)  # stop at first receipt: no extra spend
        self.assertEqual(row['shop_ratios'][cheap],{'n':3,'median':0.61})
        self.assertEqual(row['shop_ratios'][dear],{'n':3,'median':0.98})
        self.assertEqual(row['shop_ratios'][fresh],{'n':0,'median':None})


if __name__=='__main__':unittest.main()
