"""Confirmed-cancel stock recovery with real kit routing and fake market/keys only."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor

import httpx

from r3_checkout import CheckoutGate
import test_r3_cash_recovery as fixtures


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.CashRecoveryTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.path = 'acp/checkout_sessions/' + self.fx.session['id']

    def cancel(self):
        return asyncio.run(self.fx.shop.handle(SimpleNamespace(method='POST'),
            self.path+'/cancel', b'{}', 'https://old-tunnel.example'))

    def test_cancel_before_quote_expiry_releases_present_stock_once(self):
        self.assertEqual(self.cancel().status_code, 200)
        fx=self.fx
        report=fx.reservations.reconcile(fx.inventory, [], 'shop1', fx.now+1, fx.key.public_key())
        self.assertEqual(report['canceled_released'],1)
        self.assertEqual(report['blocked_in_inventory'],0)
        with fx.reservations.transaction() as db:
            row=db.execute('SELECT * FROM deals WHERE task=?',('task1',)).fetchone()
            self.assertEqual(row['status'],'canceled')
            self.assertIsNone(row['unit'])
            self.assertEqual(json.loads(row['data'])['released_unit'],'u1')
        self.assertEqual(fx.reconcile([])['canceled_released'],0)

    def test_released_unit_can_issue_new_checkout_but_old_one_stays_closed(self):
        from r3_seller import sign_receipt
        fx=self.fx
        self.cancel();fx.reconcile([])
        _,data,token,_=fx.reservations.begin('task2',[fx.unit],{'u1'},fx.now+1,fx.now+180)
        receipt=sign_receipt(dict(shop='shop1',item='sku1',price=105,task='task2'),'u1',key=fx.key)
        data.update(receipt=receipt,checkout_guarded=True)
        fx.reservations.finish('task2',token,'signed_pending',data,fx.now+2)
        restarted=fx.shop_class(fx.key,fx.shop.market,fx.md)
        body=dict(receipt=receipt,line_items=[dict(id='sku1',quantity=1)])
        response=asyncio.run(restarted.create(body,'https://old-tunnel.example'))
        self.assertEqual(response.status_code,201)
        self.assertEqual(json.loads(response.body)['unit'],'u1')
        self.assertEqual(asyncio.run(restarted.create(fx.body,'https://old-tunnel.example')).status_code,409)
        self.assertEqual(asyncio.run(restarted.handle(SimpleNamespace(method='POST'),
            self.path+'/complete',b'{}','https://old-tunnel.example')).status_code,409)

    def test_missing_inventory_or_invalid_signature_never_releases(self):
        fx=self.fx;self.cancel()
        report=fx.reservations.reconcile({'units':[]},[],'shop1',fx.now+181,fx.key.public_key())
        self.assertEqual(report['canceled_released'],0)
        with fx.reservations.transaction() as db:
            row=db.execute('SELECT data FROM deals WHERE task=?',('task1',)).fetchone()
            data=json.loads(row['data'])
            data['checkout_session']['checkout_jwt']='bad.token.signature'
            db.execute('UPDATE deals SET data=? WHERE task=?',(json.dumps(data),'task1'))
        self.assertEqual(fx.reconcile([])['blocked_in_inventory'],1)

    def test_verified_paid_ledger_takes_precedence_over_cancel(self):
        fx=self.fx;self.cancel()
        report=fx.reconcile([fx.evidence()])
        self.assertEqual(report['settled'],1)
        self.assertEqual(report['canceled_released'],0)
        with self.assertRaises(ValueError):
            fx.reservations.begin('task2',[fx.unit],{'u1'},fx.now+182,fx.now+300)

    def test_recovery_and_new_reservations_cannot_allocate_twice(self):
        fx=self.fx;self.cancel()
        def reserve(i):
            fx.reconcile([])
            try:
                fx.reservations.begin('new'+str(i),[fx.unit],{'u1'},fx.now+182,fx.now+300)
                return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve,range(4))),1)

    def test_timeout_during_payment_stays_locked_across_restart(self):
        fx=self.fx
        class TimeoutMarket:
            def call(self,*args,**kwargs):raise httpx.ReadTimeout('fixture unknown outcome')
        fx.shop.market=TimeoutMarket()
        with self.assertRaises(httpx.ReadTimeout):
            asyncio.run(fx.shop.handle(SimpleNamespace(method='POST'),self.path+'/complete',
                b'{"payment_data":{}}','https://old-tunnel.example'))
        restarted=fx.shop_class(fx.key,None,fx.md)
        self.assertEqual(asyncio.run(restarted.handle(SimpleNamespace(method='POST'),
            self.path+'/cancel',b'{}','https://old-tunnel.example')).status_code,409)
        self.assertEqual(fx.reconcile([])['blocked_in_inventory'],1)
        with fx.reservations.transaction() as db:
            data=json.loads(db.execute('SELECT data FROM deals').fetchone()['data'])
            self.assertEqual(data['completion_state'],'in_flight')
            self.assertEqual(data['checkout_session']['status'],'ready_for_payment')

    def test_successful_payment_is_durable_and_cannot_be_canceled(self):
        fx=self.fx
        class PaidMarket:
            def call(self,method,path,role,**kwargs):
                if (method,path,role)==('POST','/api/settle','shop'):
                    return httpx.Response(200,json={'mandate_receipt':{'unit':'u1'},'ledger':123})
                fx.assertEqual((method,path,role),('GET','/api/inventory','shop'))
                return httpx.Response(200,json={'round':3,'units':[]})
        fx.shop.market=PaidMarket()
        result=asyncio.run(fx.shop.handle(SimpleNamespace(method='POST'),
            self.path+'/complete',b'{"payment_data":{}}','https://old-tunnel.example'))
        self.assertEqual(result.status_code,200)
        restarted=fx.shop_class(fx.key,None,fx.md)
        self.assertEqual(asyncio.run(restarted.handle(SimpleNamespace(method='POST'),
            self.path+'/cancel',b'{}','https://old-tunnel.example')).status_code,409)
        with fx.reservations.transaction() as db:
            data=json.loads(db.execute('SELECT data FROM deals').fetchone()['data'])
            self.assertEqual(data['completion_state'],'confirmed')
            self.assertEqual(data['checkout_session']['status'],'completed')
        report=fx.reconcile([fx.evidence()])
        self.assertEqual(report['settled'],1)
        self.assertEqual(report['canceled_released'],0)

    def test_bad_cancel_json_cannot_record_cancellation(self):
        fx=self.fx
        for body in (b'{invalid', b'[]', b'null'):
            response=asyncio.run(fx.shop.handle(SimpleNamespace(method='POST'),
                self.path+'/cancel',body,'https://old-tunnel.example'))
            self.assertEqual(response.status_code,400)
        self.assertEqual(fx.reconcile([])['blocked_in_inventory'],1)
        self.assertEqual(fx.shop.sessions[fx.session['id']]['status'],'ready_for_payment')

    def test_noncanonical_paths_cannot_bypass_durable_gate(self):
        fx=self.fx
        for path in (self.path+'/complete/extra',self.path+'/cancel/extra',
                     '/'+self.path+'/cancel',self.path+'/cancel/'):
            response=asyncio.run(fx.shop.handle(SimpleNamespace(method='POST'),
                path,b'{}','https://old-tunnel.example'))
            self.assertEqual(response.status_code,404)
        with fx.reservations.transaction() as db:
            data=json.loads(db.execute('SELECT data FROM deals').fetchone()['data'])
            self.assertNotIn('completion_state',data)
            self.assertEqual(data['checkout_session']['status'],'ready_for_payment')

    def test_completion_in_flight_blocks_concurrent_cancel_until_it_finishes(self):
        fx=self.fx
        async def exercise():
            entered=asyncio.Event();release=asyncio.Event()
            original=fx.kit.AcpShop.handle
            async def slow(base,req,path,body,public):
                if path.endswith('/complete'):
                    entered.set(); await release.wait()
                    raise httpx.ReadTimeout('fixture unknown outcome')
                return await original(base,req,path,body,public)
            from unittest.mock import patch
            with patch.object(fx.kit.AcpShop,'handle',slow):
                completing=asyncio.create_task(fx.shop.handle(SimpleNamespace(method='POST'),
                    self.path+'/complete',b'{}','https://old-tunnel.example'))
                await entered.wait()
                canceling=asyncio.create_task(fx.shop.handle(SimpleNamespace(method='POST'),
                    self.path+'/cancel',b'{}','https://old-tunnel.example'))
                await asyncio.sleep(.01)
                self.assertFalse(canceling.done())
                release.set()
                with self.assertRaises(httpx.ReadTimeout):await completing
                self.assertEqual((await canceling).status_code,409)
        asyncio.run(exercise())


if __name__=='__main__':unittest.main()
