"""Correction pass RED->GREEN (2026-11-14): known-ineligible screening, price objective prompt, per-call input
telemetry, deterministic/live defaults, bounded CancelTask cleanup. Real Ed25519 signatures, injected backend, no network."""
import asyncio,base64,inspect,json,time,unittest
from unittest.mock import patch
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import buyer_r3,buyer_terra
from test_buyer_terra import (SKU,LIST,BUDGET,Backend,ok,pubkey,reply,negotiate,run_market,per_shop,reserve_seller)

GOODS1={'item':'소파','acceptable':[SKU],'kind':'goods'}

def signed_meta(key,price,task,shop,meta):
    """Signed core exactly as the seller signs it; `meta` is seller/kit-attached unsigned metadata (or none)."""
    body={'shop':shop,'item':SKU,'price':price,'task':task}
    canon=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return dict(body,sig_seller=base64.b64encode(key.sign(canon)).decode(),**meta)

def meta_seller(key,ask,reserve,shop,meta,task='t1'):
    seen={'texts':[],'cancel':0}
    def handle(request):
        body=json.loads(request.content)
        if body['method']=='CancelTask':
            seen['cancel']+=1;return httpx.Response(200,json={'result':{}})
        text=' '.join(p.get('text','') for p in body['params']['message']['parts'])
        seen['texts'].append(text)
        if len(seen['texts'])>1 and int(text)>=reserve:
            return reply(signed_meta(key,int(text),task,shop,meta),task)
        return reply(f'asking {ask} credits',task)
    return handle,seen

SHOPS=[{'name':n,'url':f'http://{n}.test','offers':[{'sku':SKU,'qty':1}]} for n in ('a','b')]


class KnownIneligibleTests(unittest.IsolatedAsyncioTestCase):
    async def check(self,second_meta,expect_shop,expect_reason):
        key=Ed25519PrivateKey.generate();pub=pubkey(key)
        first={'unit':'u-1','claim':'B','cert':True}
        sellers={'a.test':meta_seller(key,LIST,80000,'http://a.test',first),
                 'b.test':meta_seller(key,LIST,60000,'http://b.test',second_meta)}
        result,row=await run_market(pub,sellers,SHOPS,per_shop(80000,60000),order=GOODS1)
        self.assertEqual(result['shop'],expect_shop,second_meta)
        self.assertEqual(row['attempts'][1]['reason'],expect_reason,second_meta)
        return result,row

    async def test_cheap_known_claim_c_does_not_displace_earlier_valid(self):
        result,row=await self.check({'unit':'u-2','claim':'C','cert':True},'http://a.test','known_ineligible')
        self.assertEqual(result['receipt']['claim'],'B')                    # receipt fields untouched
        self.assertEqual([c['shop'] for c in row['comparison']['candidates']],['http://a.test'])

    async def test_cheap_known_cert_false_does_not_displace_earlier_valid(self):
        await self.check({'unit':'u-2','claim':'A','cert':False},'http://a.test','known_ineligible')

    async def test_unknown_metadata_stays_unknown_and_competes(self):
        # No claim/cert observed: not proof of quality, not ineligible; kit's final guard stays authoritative.
        result,row=await self.check({'unit':'u-2'},'http://b.test','signed_receipt')
        self.assertNotIn('claim',result['receipt']);self.assertNotIn('cert',result['receipt'])

    async def test_only_known_ineligible_receipt_is_not_handed_to_kit(self):
        key=Ed25519PrivateKey.generate();pub=pubkey(key)
        sellers={'a.test':meta_seller(key,LIST,60000,'http://a.test',{'unit':'u-2','claim':'C','cert':True})}
        result,row=await run_market(pub,sellers,SHOPS[:1],per_shop(60000),order=GOODS1)
        self.assertIsNone(result['receipt']);self.assertEqual(row['decision'],'no_deal')
        self.assertEqual(row['attempts'][0]['reason'],'known_ineligible')

    def test_screen_uses_policy_food_180h_unchanged(self):
        acc=buyer_r3.policy({'kind':'food','use_within_hours':180})
        self.assertEqual(acc['min_hours_left'],180.0)
        self.assertIsNone(buyer_r3.known_ineligible({'shop':'s','claim':'A','cert':True},acc))
        self.assertIsNone(buyer_r3.known_ineligible({'shop':'s'},acc))                 # unknown is unknown
        self.assertEqual(buyer_r3.known_ineligible({'claim':'C'},acc),'claim')
        self.assertEqual(buyer_r3.known_ineligible({'cert':False},acc),'cert')
        self.assertEqual(buyer_r3.known_ineligible({'cert':'true'},acc),'cert')       # only literal True counts


class PromptObjectiveTests(unittest.TestCase):
    def test_system_states_price_objective_and_contract_bounds_only(self):
        s=buyer_terra.SYSTEM
        for phrase in ('lowest negotiated price','list_price','not the official Saved','full ask',
                       'contract bounds','not this seller','firm','history'):
            self.assertIn(phrase,s,phrase)
        self.assertIn('0.55',s);self.assertIn('0.40',s)


class TelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_call_records_facts_timeout_raw_and_validated(self):
        key=Ed25519PrivateKey.generate()
        handle,seen=reserve_seller(key,LIST,76000,'http://zeta.test')
        b=Backend(ok({'action':'counter','offer':68000}),ok({'action':'counter','offer':500000}))
        out=await negotiate(handle,b,pub=pubkey(key))
        c0,c1=out['negotiation']['calls'][:2]
        self.assertEqual(c0['facts'],b.facts[0]);self.assertEqual(c0['timeout_s'],round(b.timeouts[0],3))
        self.assertEqual((c0['raw_action'],c0['raw_offer'],c0['validated']),('counter',68000,True))
        self.assertEqual((c1['raw_action'],c1['raw_offer'],c1['validated']),('counter',500000,False))
        self.assertNotIn('IGNORE',json.dumps(out['negotiation']));self.assertNotIn('sig',json.dumps(c0['facts']))

    async def test_raw_fields_are_bounded_for_hostile_text(self):
        key=Ed25519PrivateKey.generate()
        handle,seen=reserve_seller(key,LIST,0,'http://zeta.test')
        out=await negotiate(handle,Backend(ok({'action':'x'*500,'offer':'y'*500})),pub=pubkey(key))
        c=out['negotiation']['calls'][0]
        self.assertLessEqual(len(c['raw_action']),16);self.assertEqual(c['raw_offer'],'str');self.assertFalse(c['validated'])


class DefaultsTests(unittest.TestCase):
    def test_negotiate_default_deterministic_run_default_live_terra(self):
        self.assertIsNone(inspect.signature(buyer_r3.negotiate).parameters['decide'].default)
        self.assertIs(buyer_r3.DECIDE,buyer_terra.terra_decide)
        self.assertIn("getattr(args,'decide',DECIDE)",inspect.getsource(buyer_r3.run))


class CancelCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_shop_timeout_cleanup_is_bounded(self):
        key=Ed25519PrivateKey.generate();pub=pubkey(key);seen={'n':0,'cancel':0}
        async def hang(request):
            body=json.loads(request.content)
            if body['method']=='CancelTask':
                seen['cancel']+=1;await asyncio.sleep(8);return httpx.Response(200,json={'result':{}})
            seen['n']+=1
            if seen['n']==1:return reply('asking 100000 credits','t1')
            await asyncio.sleep(8);return reply('asking 100000 credits','t1')
        t0=time.monotonic()
        with patch.object(buyer_r3,'SHOP_S',1.5),patch.object(buyer_r3,'MIN_ATTEMPT_S',0):
            result,row=await run_market(pub,{'a.test':(hang,{})},SHOPS[:1],per_shop(70000),order=GOODS1)
        elapsed=time.monotonic()-t0
        self.assertLess(elapsed,1.5+buyer_r3.CANCEL_S+1.0)                 # per-shop + bounded cleanup, no 8 s hang
        self.assertLessEqual(buyer_r3.CANCEL_S,2)
        self.assertEqual(seen['cancel'],1)                                    # cancel still attempted
        self.assertEqual(row['attempts'][0]['reason'],'timeout')

    def test_total_bound_below_kit_deadline(self):
        # global search + one bounded cleanup + registry fetch stay under the kit's 300 s
        self.assertLess(buyer_r3.GLOBAL_S+buyer_r3.CANCEL_S+20,300)


if __name__=='__main__':unittest.main()
