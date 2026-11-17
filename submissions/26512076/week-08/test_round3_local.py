"""Offline contract/guard tests. No live model, market payment or forum requests."""
import asyncio
import base64
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import r3_common as c
import buyer_r3 as buyer
import seller_r3 as seller
import restock_r3 as restock
import review_r3 as review


class Contract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.patch=patch.multiple(c,ROOT=root,STATE=root/'.market',DB=root/'memory.sqlite3')
        self.patch.start();c.STATE.mkdir()
        self.cat={'SKU-1':{'name':'Test Chair','category':'chairs','list_price':1000,'kind':'goods'},
                  'SKU-2':{'name':'Test Food','category':'food','list_price':500,'kind':'food','shelf_hours':10}}
        self.unit={'unit':'unit-1','sku':'SKU-1','name':'Test Chair','category':'chairs','list_price':1000,
                   'kind':'goods','cost':600,'grade':'A','supplied_at':time.time(),'expires_at':None}
        self.inv={'round':3,'balance':5000000,'start':5000000,'injected':0,'bankruptcies_left':2,
                  'bankrupt_line':500000,'units':[self.unit]}
        (c.STATE/'catalog.json').write_text(json.dumps(self.cat))
        (c.STATE/'inventory.json').write_text(json.dumps(self.inv))
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_food_grade_is_current_not_stale(self):
        u={'sku':'SKU-2','kind':'food','grade':'A','expires_at':time.time()+3600}
        self.assertEqual(c.unit_view(u,self.cat)['grade'],'C')
        u['expires_at']=time.time()-1
        self.assertEqual(c.unit_view(u,self.cat)['grade'],'expired')

    def test_duplicate_stock_reservation(self):
        self.assertTrue(c.reserve('unit-1','task-a'))
        self.assertFalse(c.reserve('unit-1','task-b'))
        self.assertEqual(c.stock('task-b'),[])
        c.release('task-a');self.assertTrue(c.reserve('unit-1','task-b'))

    def test_receipt_budget_sku_task_and_offer_guards(self):
        r={'shop':'x','item':'SKU-1','price':700.0,'task':'t','sig_seller':'abc'}
        self.assertIsNone(c.receipt_problem(r,'t','SKU-1',700,800))
        for key,val in [('price',900),('price',True),('price',float('nan')),('item','SKU-2'),('task','z')]:
            self.assertIsNotNone(c.receipt_problem({**r,key:val},'t','SKU-1',700,800))

    def test_policy_has_food_window_and_certificate(self):
        p=buyer.policy({'kind':'food','use_within_hours':6},{'grades':['B','C']})
        self.assertTrue(p['cert']);self.assertGreater(p['min_hours_left'],6)
        with self.assertRaises(ValueError):buyer.policy({'kind':'goods'},{'warranty_hours':7})

    def test_candidate_filter_uses_stock_and_direct_evidence(self):
        o={'acceptable':['SKU-1']}
        shops=[{'name':'bad','url':'https://bad','offers':[{'sku':'SKU-1','qty':1}]},
               {'name':'good','url':'https://good','offers':[{'sku':'SKU-1','qty':1}]}]
        c.remember('mismatch',{'name':'bad'})
        self.assertEqual([r['name'] for r in c.candidate_offers(shops,o,self.cat)],['good'])
        shops[1]['offers'][0]['qty']=0;self.assertEqual(c.candidate_offers(shops,o,self.cat),[])

    def test_restock_enforces_balance_quantity_and_catalog(self):
        opts=restock.candidates(self.inv,self.cat)
        out,cap=restock.guard({'buy':[{'sku':'SKU-1','qty':2},{'sku':'SKU-1','qty':2},
                                        {'sku':'INVALID','qty':1},{'sku':'SKU-2','qty':99}]},opts,self.inv)
        self.assertEqual(out['buy'],[{'sku':'SKU-1','qty':2}])
        self.assertLessEqual(1500,cap)
        out,_=restock.guard({'buy':[{'sku':'SKU-1','qty':1}],'bankrupt':True},opts,self.inv)
        self.assertFalse(out['bankrupt'])
        poor={**self.inv,'balance':400000}
        out,_=restock.guard({'buy':[],'bankrupt':True},opts,poor)
        self.assertTrue(out['bankrupt'])

    def test_settlement_memory_is_idempotent_and_pending_is_not_paid(self):
        c.put('pending:o',{'shop_name':'shop'})
        self.assertEqual(c.purchase_rows(),[])
        d={'order':{'order':'o','kind':'goods','category':'chairs'},'receipt':{'item':'SKU-1','price':700},
           'settled':{'shop':'https://shop','sku':'SKU-1','name':'Test Chair','unit':'unit-1',
                      'grade':'A','claim_honest':True,'warranty_hours':6,'ledger':44,'value':1000}}
        review.ingest(d);review.ingest(d)
        self.assertEqual(len(c.purchase_rows()),1)
        self.assertEqual(c.purchase_rows()[0]['unit'],'unit-1')

    def test_forum_posts_are_grounded_deduplicated_and_throttled(self):
        r={'ledger':44,'price':700,'name':'Chair','sku':'SKU-1','grade':'A',
           'claim_honest':True,'warranty_hours':6}
        review.publish(r);review.publish(r);review.publish({**r,'ledger':45})
        posts=list(c.jsonl(c.STATE/'outbox.jsonl'))
        self.assertEqual(len(posts),1);self.assertEqual(posts[0]['refs'],[44])
        self.assertEqual(posts[0]['as'],'buyer')

    def test_ambiguous_broken_items_do_not_blame_single_shop(self):
        for i in range(2):c.save_purchase(str(i),{'order':str(i),'name':'same','at':time.time()-30})
        (c.STATE/'broken.jsonl').write_text(json.dumps({'name':'same','broke_at':time.time()})+'\n')
        c.sync_payments();r=c.recent('broken')[0]
        self.assertEqual(r['attribution'],'ambiguous');self.assertEqual(len(r['possible_orders']),2)

    def test_exact_sku_retrieval_avoids_substring(self):
        self.assertEqual(seller.matches('buy (SKU-10)',[self.unit]),[])
        self.assertEqual(seller.matches('buy (SKU-1)',[self.unit])[0]['sku'],'SKU-1')

    def test_kit_v4_accept_policy_function(self):
        kit=os.getenv('R3_KIT_PATH')
        if not kit:self.skipTest('Set R3_KIT_PATH for uploaded v4 integration')
        spec=importlib.util.spec_from_file_location('v4',kit);v4=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(v4)
        self.assertEqual(v4.KIT_VERSION,'4')
        key=Ed25519PrivateKey.generate();pub=key.public_key().public_bytes(serialization.Encoding.Raw,
                serialization.PublicFormat.Raw)
        body={'unit':'u','sku':'s','grade':'B'}
        cert={'cert':body,'sig':base64.b64encode(key.sign(c.canon(body))).decode()}
        o={'kind':'goods','market_pubkey':base64.b64encode(pub).decode()}
        co={'unit':'u','item':'s','cert':cert,'warranty_hours':6}
        self.assertIsNone(v4.check_accept(o,co,{'cert':True,'grades':['A','B'],'warranty_hours':6}))
        self.assertIsNotNone(v4.check_accept(o,{**co,'warranty_hours':0},{'warranty_hours':6}))

    def test_real_a2a_server_and_buyer_with_mocked_model(self):
        key=Ed25519PrivateKey.generate()
        (c.ROOT/'shop_key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        transcript=[]
        async def fake(role,system,payload):
            transcript.append((role,payload))
            if role=='buyer':return {'action':'offer','price':700,'reason':'accept suitable price'}
            first=payload['trusted']['first_turn']
            return {'action':'counter' if first else 'accept','unit':'unit-1','price':700,'warranty_hours':6}
        app=seller.build(9999)
        transport=httpx.ASGITransport(app=app)
        original=httpx.AsyncClient
        def local_client(**kwargs):
            kwargs['transport']=transport
            return original(**kwargs)
        async def scenario():
            shop={'name':'shop','url':'http://local','product':'Test Chair','sku':'SKU-1','list_price':1000}
            order={'order':'o','item':'chairs','budget':800}
            with patch.object(c,'ask',side_effect=fake),patch.object(httpx,'AsyncClient',side_effect=local_client):
                rec=await buyer.Negotiation(shop,order,{'cert':True,'grades':['A'],'warranty_hours':6}).run()
            self.assertEqual(rec['price'],700);self.assertEqual(rec['item'],'SKU-1')
            body={k:rec[k] for k in ('shop','item','price','task')};body['price']=int(body['price'])
            key.public_key().verify(base64.b64decode(rec['sig_seller']),c.canon(body))
            self.assertTrue(rec['cert']);self.assertEqual(rec['warranty_hours'],6)
        asyncio.run(scenario())
        self.assertEqual([x[0] for x in transcript],['seller','buyer','seller'])


if __name__=='__main__':unittest.main(verbosity=2)
