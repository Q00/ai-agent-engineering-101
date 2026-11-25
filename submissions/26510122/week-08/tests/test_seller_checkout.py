import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from seller_state import SellerState
from seller_checkout import install

KIT=Path(__file__).resolve().parents[1]/'ops/vendor/market-kit-2026-11-03/market_kit.py'

class CheckoutReservationTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('checkout_kit_fixture_'+str(time.time_ns()),KIT)
        self.kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.kit)
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.md=self.root/'.market';self.md.mkdir();(self.md/'inventory.json').write_text('{"round":3}')
        self.state=SellerState(self.root/'state/seller.sqlite3');self.key=Ed25519PrivateKey.generate()
        self.units=[{'unit':'old-C','sku':'G','grade':'C','expires_at':None,'supplied_at':1,'cert':{'cert':{'unit':'old-C'}}},
                    {'unit':'reserved-A','sku':'G','grade':'A','expires_at':None,'supplied_at':2,'cert':{'cert':{'unit':'reserved-A'}}}]
        class Market:
            def __init__(s,outer):s.outer=outer;s.calls=[]
            def call(s,method,path,*args,**kwargs):
                import httpx
                s.calls.append((method,path,kwargs))
                if path=='/api/inventory':return httpx.Response(200,json={'round':3,'units':s.outer.units})
                return httpx.Response(200,json={'ledger':11,'mandate_receipt':{'unit':'reserved-A','ledger':11,'name':'fixture'}})
        self.market=Market(self);install(self.kit,self.state,event_path=self.root/'events.jsonl')
        self.shop=self.kit.AcpShop(self.key,self.market,self.md)
        self.rec=self.reserve('task-1')
    def tearDown(self):self.temp.cleanup()
    def reserve(self,task):
        now=time.time();unit=self.units[1]
        self.state.save_deal(task,{'unit':unit,'opened_at':now,'last_activity_at':now})
        rec={'shop':'own','item':'G','price':800,'task':task,'unit':'reserved-A','claim':'A','warranty_hours':6}
        import base64
        rec['sig_seller']=base64.b64encode(self.key.sign(self.kit.canon({k:rec[k] for k in self.kit.RECEIPT_KEYS}))).decode()
        self.assertTrue(self.state.commit_receipt(task,rec));return rec
    def body(self,rec=None):
        r=rec or self.rec
        return {'receipt':{k:r[k] for k in self.kit.RECEIPT_KEYS+('sig_seller',)},'line_items':[{'id':'G','quantity':1}]}
    def test_restart_or_lru_restores_exact_original_unit_and_warranty(self):
        async def run():
            response=await self.shop.create(self.body(),'https://own')
            self.assertEqual(response.status_code,201);data=json.loads(response.body)
            self.assertEqual(data['fulfillment']['unit'],'reserved-A')
            self.assertEqual(data['fulfillment']['warranty_hours'],6)
            self.assertEqual(data['fulfillment']['claim'],'A')
        asyncio.run(run())
    def test_duplicate_create_is_idempotent_and_does_not_extend_reservation(self):
        until=self.state.receipt_reservation('task-1')['until']
        async def run():
            one,two=await asyncio.gather(self.shop.create(self.body(),'https://own'),self.shop.create(self.body(),'https://own'))
            self.assertEqual(json.loads(one.body)['id'],json.loads(two.body)['id']);self.assertEqual(two.status_code,200)
            self.assertEqual(len(self.shop.sessions),1)
        asyncio.run(run());self.assertEqual(self.state.receipt_reservation('task-1')['until'],until)
    def test_expired_checkout_does_not_hold_inventory_and_stale_completion_is_refused(self):
        async def run():
            one=json.loads((await self.shop.create(self.body(),'https://own')).body)
            with self.state.connect() as db:db.execute('update leases set until=?',(time.time()-1,))
            two=self.reserve('task-2')
            response=await self.shop.create(self.body(two),'https://own')
            self.assertEqual(response.status_code,201);self.assertEqual(self.shop.sessions[one['id']]['status'],'expired')
            result=await self.shop.handle(SimpleNamespace(method='POST'),'acp/checkout_sessions/'+one['id']+'/complete',b'{}','https://own')
            self.assertEqual(result.status_code,409)
            self.assertFalse(any(path=='/api/settle' for _,path,_ in self.market.calls))
        asyncio.run(run())
    def test_cancel_retry_uses_same_original_terms_without_releasing_db_hold(self):
        async def run():
            one=json.loads((await self.shop.create(self.body(),'https://own')).body)
            until=self.state.receipt_reservation('task-1')['until']
            response=await self.shop.handle(SimpleNamespace(method='POST'),'acp/checkout_sessions/'+one['id']+'/cancel',b'{}','https://own')
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.state.receipt_reservation('task-1')['until'],until)
            retry=await self.shop.create(self.body(),'https://own');self.assertEqual(retry.status_code,201)
            self.assertEqual(json.loads(retry.body)['fulfillment']['unit'],'reserved-A')
        asyncio.run(run())
    def test_buyer_unit_override_is_ignored_and_signature_tampering_still_fails(self):
        async def run():
            body=self.body();body['receipt']['unit']='old-C';body['receipt']['warranty_hours']=0
            response=await self.shop.create(body,'https://own');self.assertEqual(response.status_code,201)
            self.assertEqual(json.loads(response.body)['fulfillment']['unit'],'reserved-A')
            bad=self.body();bad['receipt']['price']=1
            response=await self.shop.create(bad,'https://own');self.assertEqual(response.status_code,400)
        asyncio.run(run())
    def test_settled_receipt_cannot_allocate_a_different_unit(self):
        self.state.mark_settled({'reserved-A'})
        response=asyncio.run(self.shop.create(self.body(),'https://own'))
        self.assertEqual(response.status_code,409);self.assertFalse(self.shop.sessions)

    def test_unknown_inventory_and_expired_receipt_fail_closed(self):
        self.state.mark_settled({'reserved-A'});(self.md/'inventory.json').unlink()
        response=asyncio.run(self.shop.create(self.body(),'https://own'))
        self.assertEqual(response.status_code,409);self.assertFalse(self.shop.sessions)
    def test_ambiguous_legacy_signed_reservations_do_not_pick_an_arbitrary_unit(self):
        with self.state.connect() as db:
            db.execute('insert into leases values (?,?,?,?,?)',('old-C','task-1','receipt',time.time()+600,json.dumps(dict(self.rec,unit='old-C'))))
        response=asyncio.run(self.shop.create(self.body(),'https://own'))
        self.assertEqual(response.status_code,409);self.assertFalse(self.shop.sessions)
