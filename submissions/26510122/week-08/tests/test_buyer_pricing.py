"""Identity linkage and tentative buyer pricing, with no external trades."""
import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import buyer_identity as identity
import buyer_r3
import counterparty_memory as memory
import market_memory
import seller_r3


def proof(key, name='alice'):
    signed={'shop':seller_r3.NAME,'item':'G1','price':120,'task':'past','order':'o1'}
    return {'seq':17,'buyer_name':name,'signed':signed,
            'sig_buyer':base64.b64encode(key.sign(identity.canon(signed))).decode()}


def iso(at):
    return datetime.fromtimestamp(at,timezone.utc).isoformat()


def trade(task, at, *, name='alice', price=120, ask=120, offers=None,
          counters=(), grade='A', hours=None, outcome='SETTLED'):
    common={'at':iso(at),'task_id':task}
    expiry=None if hours is None else at+hours*3600
    rows=[dict(common,event='opening',sku='G1',unit=task,grade=grade,list_price=100,
               ask=ask,expires_at=expiry)]
    rows += [dict(common,event='offer',price=p) for p in (offers if offers is not None else [price])]
    rows += [dict(common,event='counter',ask=p) for p in counters]
    if outcome in ('SETTLED','RECEIPT_PENDING'):
        rows.append(dict(common,event='receipt_signed',sku='G1',unit=task,grade=grade,
                         price=price,expires_at=expiry))
    if name:
        rows.append(dict(common,event='buyer_link',buyer_name=name,source='market_verified_result'))
    if outcome=='SETTLED':
        rows.append(dict(common,event='settled',price=price,grade=grade,ledger=len(task)))
    elif outcome in ('REJECTED','CANCELLED'):
        rows.append(dict(common,event=outcome.lower()))
    return rows


VERIFIED={'buyer_name':'alice','status':'market_linked_signature','verified':True}
UNIT={'sku':'G1','unit':'u1','name':'Lamp','kind':'goods','grade':'A','list_price':100,
      'cost':70,'expires_at':None,'category':'goods','supplied_at':1}


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.envelope=identity.sign(self.key,'alice','shop','m1',None,'I want G1',now=1000)

    def identify(self, envelope=None, proofs=None, **overrides):
        args=dict(recipient='shop',message_id='m1',task_id=None,message='I want G1',now=1000,
                  proofs=[proof(self.key)] if proofs is None else proofs)
        args.update(overrides)
        return identity.identify({'buyer_identity':self.envelope if envelope is None else envelope},**args)

    def test_signed_request_must_match_market_linked_key(self):
        self.assertTrue(self.identify()['verified'])
        self.assertFalse(self.identify(proofs=[])['verified'])
        self.assertFalse(self.identify(proofs=[proof(Ed25519PrivateKey.generate())])['verified'])
        self.assertFalse(self.identify(proofs=[proof(self.key,'someone_else')])['verified'])
        claimed=identity.identify({'buyer_name':'alice'},recipient='shop',message_id='m1',
                                  task_id=None,message='I want G1',proofs=[proof(self.key)])
        self.assertEqual(claimed['status'],'claimed_only')
        self.assertFalse(claimed['verified'])

    def test_tampering_replay_context_and_expiry_fail(self):
        for change in ({'message':'I want G2'},{'message_id':'different'},
                       {'task_id':'other-task'},{'recipient':'other-shop'},{'now':1301}):
            with self.subTest(change=change):
                self.assertFalse(self.identify(**change)['verified'])
        # A2A's protobuf metadata converts integer JSON timestamps to floats.
        self.assertTrue(self.identify(envelope={**self.envelope,'at':1000.0})['verified'])
        self.assertFalse(self.identify(envelope={**self.envelope,'at':1000.1})['verified'])

    def test_outgoing_buyer_signs_exact_message_without_budget(self):
        captured=[]
        def reply(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={'result':{'task':{'id':'t1'}}})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            buyer_r3.send(client,'https://shop','I want G1',time.monotonic()+30,identity={
                'key':self.key,'name':'alice','recipient':'shop'})
        params=captured[0]['params'];msg=params['message']
        observed=identity.identify(params['metadata'],recipient='shop',message_id=msg['messageId'],
                                   task_id=None,message=msg['parts'][0]['text'],proofs=[proof(self.key)])
        self.assertTrue(observed['verified'])
        self.assertNotIn('budget',json.dumps(captured))


class ProfileTests(unittest.TestCase):
    NOW=1800000000

    def view(self,rows,unit=UNIT,who=VERIFIED):
        return memory.profile(who,unit,history=rows,now=self.NOW)

    def test_one_vs_repeated_calm_high_payments_give_small_optional_probes(self):
        rows=trade('old',self.NOW-3600)
        first=self.view(rows)
        self.assertEqual(first['price_candidates'],[120,126])
        self.assertEqual(first['baseline_source'],'unvalidated_grade_anchor_prior')
        repeated=self.view(rows+trade('new',self.NOW-1800))
        self.assertEqual(repeated['price_candidates'],[120,126,132])
        self.assertEqual(repeated['calm_high_paid'],2)

    def test_pending_haggled_low_and_unverified_are_not_high_payment_evidence(self):
        cases=[trade('pending',self.NOW-1,outcome='RECEIPT_PENDING'),
               trade('haggled',self.NOW-1,offers=[100,120],counters=[120]),
               trade('discount',self.NOW-1,ask=150),
               trade('cheap',self.NOW-1,price=90,ask=90)]
        for rows in cases:
            with self.subTest(task=rows[0]['task_id']):
                self.assertFalse(self.view(rows)['eligible_for_price_probe'])
        self.assertFalse(self.view(trade('high',self.NOW-1),who={**VERIFIED,'verified':False})['eligible_for_price_probe'])

    def test_comparison_preserves_grade_age_buyer_and_food_expiry(self):
        invalid=[trade('grade',self.NOW-3600,grade='C'),
                 trade('stale',self.NOW-15*86400),trade('other',self.NOW-3600,name='bob')]
        self.assertEqual(self.view(sum(invalid,[]))['confirmed_comparable'],0)
        food={**UNIT,'kind':'food','expires_at':self.NOW+10*3600}
        rows=trade('clearance',self.NOW-3600,hours=2)
        self.assertFalse(self.view(rows,food)['eligible_for_price_probe'])
        rows+=trade('fresh',self.NOW-1800,hours=12)
        self.assertEqual(self.view(rows,food)['calm_high_paid'],1)
        self.assertEqual(self.view(rows,{**food,'expires_at':None})['status'],'current_expiry_unknown')

    def test_other_buyers_form_baseline_and_counterexamples_remain_visible(self):
        rows=trade('alice',self.NOW-100,price=120)
        for n in range(3):
            rows+=trade(f'other{n}',self.NOW-90+n,name='bob',price=120)
        result=self.view(rows)
        self.assertFalse(result['eligible_for_price_probe'])
        self.assertEqual(result['baseline_source'],'other_comparable_settlements')
        rows+=trade('alice-low',self.NOW-1,price=80,ask=80)
        self.assertEqual(self.view(rows)['non_high_or_bargained_count'],2)
        self.assertEqual(self.view(rows)['examples'][-1]['price'],80)

    def test_failed_unresolved_and_discounted_probes_stop_repeated_uplifts(self):
        for outcome,price,status in [('REJECTED',126,'last_price_probe_failed'),
                                     ('CANCELLED',126,'last_price_probe_failed'),
                                     ('OPEN',126,'price_probe_unresolved'),
                                     ('RECEIPT_PENDING',126,'price_probe_unresolved'),
                                     ('SETTLED',110,'last_price_probe_required_discount')]:
            with self.subTest(outcome=outcome):
                rows=trade('good',self.NOW-3600)+trade('probe',self.NOW-60,ask=126,price=price,outcome=outcome)
                rows.append({'at':iso(self.NOW-60),'event':'buyer_price_probe','buyer_name':'alice',
                             'sku':'G1','grade':'A','expiry_bucket':'unknown','task_id':'probe','price':126})
                self.assertEqual(self.view(rows)['status'],status)
                self.assertFalse(self.view(rows)['eligible_for_price_probe'])
                if outcome in ('REJECTED','CANCELLED','SETTLED'):
                    self.assertTrue(self.view(rows+trade('new-evidence',self.NOW-1))['eligible_for_price_probe'])

    def test_verified_public_receipt_link_does_not_count_as_payment(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl'
            rows=trade('past',self.NOW-1,name=None,outcome='RECEIPT_PENDING')
            signed=proof(Ed25519PrivateKey.generate())
            entry={'seq':17,'kind':'result','data':{'verified':True,'buyer':'alice',
                   'receipt':{k:v for k,v in signed['signed'].items() if k!='order'},
                   'order':'o1','sig_buyer':signed['sig_buyer']}}
            self.assertEqual(memory.ingest([{**entry,'data':{**entry['data'],'verified':False}}],
                             own_name=seller_r3.NAME,history=rows,path=path),[])
            memory.ingest([entry],own_name='other-shop',history=rows,path=path)
            self.assertEqual(market_memory.events(path),[])
            memory.ingest([entry],own_name=seller_r3.NAME,history=rows,path=path)
            linked=market_memory.events(path)
            self.assertEqual(len(linked),1)
            memory.ingest([entry],own_name=seller_r3.NAME,history=rows+linked,path=path)
            self.assertEqual(len(market_memory.events(path)),1)
            self.assertFalse(self.view(rows+linked)['eligible_for_price_probe'])

    def test_cached_public_result_reconciles_late_receipt_even_when_network_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl';cache=Path(directory)/'cache.json'
            p=proof(Ed25519PrivateKey.generate())
            entry={'seq':17,'kind':'result','data':{'verified':True,'buyer':'alice',
                   'receipt':{k:v for k,v in p['signed'].items() if k!='order'},
                   'order':'o1','sig_buyer':p['sig_buyer']}}
            cache.write_text(json.dumps({'polled_at':self.NOW-100,'rows':[entry],'proofs':[p]}))
            for row in trade('past',self.NOW-1,name=None,outcome='RECEIPT_PENDING'):
                market_memory.record(row.pop('event'),path=path,**row)
            with patch.object(memory.httpx,'get',side_effect=httpx.ReadTimeout('offline')) as get:
                self.assertEqual(memory.refresh(seller_r3.NAME,cache_path=cache,event_path=path,now=self.NOW),[p])
                memory.refresh(seller_r3.NAME,cache_path=cache,event_path=path,now=self.NOW+1)
                self.assertEqual(get.call_count,1)
            self.assertEqual(len([r for r in market_memory.events(path) if r['event']=='buyer_link']),1)


class SellerIdentityIntegrationTests(unittest.TestCase):
    def test_quote_speed_arms_use_the_same_sku_grade_and_price(self):
        now = time.time()
        sku = 'Gbf98024f'
        unit = {**UNIT, 'sku': sku, 'kind': 'goods', 'list_price': 270_000,
                'cost': 160_000, 'supplied_at': now}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trial.json'
            path.write_text(json.dumps({'starts_at': now - 1, 'ends_at': now + 3600,
                                        'prices': {sku: {unit['grade']: 243_000}}}))
            results = [seller_r3.speed_trial_quote(str(i), [sku], [unit], [], {}, now, path)
                       for i in range(12)]
            self.assertEqual({arm for arm, _, _ in results}, {'instant', 'control'})
            self.assertEqual({(u['sku'], u['grade'], price) for _, u, price in results},
                             {(sku, unit['grade'], 243_000)})
            self.assertIsNone(seller_r3.speed_trial_quote('1', [sku], [unit], [{'unit': unit['unit']}], {}, now, path))
            costly = {**unit, 'cost': 270_000}
            self.assertIsNone(seller_r3.speed_trial_quote('1', [sku], [costly], [], {}, now, path))
            self.assertIsNone(seller_r3.speed_trial_quote('1', [sku], [unit], [], {}, now + 3600, path))

    def test_actual_a2a_metadata_reaches_model_and_anonymous_falls_back(self):
        key=Ed25519PrivateKey.generate()
        prompts=[]
        async def exchange():
            app=seller_r3.build(9999)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                async def send(signed,mid=None):
                    mid=mid or str(uuid.uuid4());text='I want G1'
                    params={'message':{'role':'ROLE_USER','messageId':mid,'parts':[{'text':text}]}}
                    if signed:
                        params['metadata']={'buyer_identity':identity.sign(key,'alice',seller_r3.NAME,mid,None,text)}
                    result=(await client.post('/',json={'jsonrpc':'2.0','id':mid,'method':'SendMessage','params':params},
                                              headers={'A2A-Version':'1.0'})).json()
                    self.assertNotIn('error',result)
                    return result['result']['task']
                mid=str(uuid.uuid4())
                first=await send(True,mid)
                self.assertIn('asking 126',first['status']['message']['parts'][0]['text'])
                await send(True,mid)
                await send(False)

        def choose(_role,prompt,_schema):
            prompts.append(prompt)
            eligible=any(p['eligible_for_price_probe'] for p in prompt['buyer_price_evidence'].values())
            unit=prompt['unit_options'][0]['unit']
            return {'action':'ask','price':126 if eligible else 100,'unit_id':unit,'reason':'test evidence'}

        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                Path('.market').mkdir()
                Path('.market/inventory.json').write_text(json.dumps({'round':3,'units':[
                    {**UNIT,'supplied_at':time.time()},
                    {**UNIT,'unit':'u2','supplied_at':time.time()},
                    {**UNIT,'unit':'u3','supplied_at':time.time()}]}))
                for row in trade('past',time.time()-3600):
                    market_memory.record(row.pop('event'),**row)
                with patch.object(memory,'load_cache',return_value={'proofs':[proof(key)]}), \
                     patch.object(memory,'refresh',side_effect=AssertionError('quotes must not poll public ledger')), \
                     patch.object(seller_r3,'decide_json',side_effect=choose):
                    asyncio.run(exchange())
                self.assertTrue(prompts[0]['buyer_identity']['verified'])
                self.assertIn(126,prompts[0]['unit_options'][0]['buyer_price_candidates'])
                self.assertFalse(prompts[1]['buyer_identity']['verified'])
                self.assertEqual(prompts[1]['buyer_identity']['status'],'replayed_identity_message')
                self.assertNotIn('buyer_price_candidates',prompts[1]['unit_options'][0])
                self.assertEqual(prompts[2]['buyer_identity']['status'],'anonymous')
                self.assertEqual(len([e for e in market_memory.events() if e['event']=='buyer_price_probe']),1)
            finally:
                os.chdir(previous)


if __name__=='__main__':
    unittest.main()
