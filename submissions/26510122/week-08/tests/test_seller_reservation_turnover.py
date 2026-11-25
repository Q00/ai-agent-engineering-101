import asyncio
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
import test_seller_persistence as fixture
import seller_r3
import seller_warranty
import seller_checkout_policy as policy
from seller_state import SellerState
import decision_audit as audit

class ReservationTurnoverTests(unittest.TestCase):
    setUp=fixture.PersistenceTests.setUp
    tearDown=fixture.PersistenceTests.tearDown
    write_inventory=fixture.PersistenceTests.write_inventory
    send=fixture.PersistenceTests.send
    client=fixture.PersistenceTests.client

    def test_exact_existing_quote_is_honoured_without_another_model(self):
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1')
                last=await self.send(c,'900',first['id'])
                self.assertEqual(last['status']['state'],'TASK_STATE_COMPLETED')
            self.assertEqual(SellerState().pending_receipt(first['id'])['price'],900)
        with patch.object(seller_r3,'decide_json',return_value={'action':'ask','price':900,'unit_id':'u1','reason':'quote'}) as model:
            asyncio.run(flow());self.assertEqual(model.call_count,1)

    def test_goods_quote_covers_penny_drift_and_keeps_live_floor(self):
        clock=[self.now]
        self.unit.update(kind='goods',grade='A',cost=239662,list_price=347850,supplied_at=self.now-600,expires_at=None)
        self.write_inventory()
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1 with 6 h warranty')
                ask=SellerState().restore_deals()[first['id']]['ask'];clock[0]+=30
                self.assertGreaterEqual(ask,seller_warranty.selling_floor(self.unit,now=clock[0],warranty_hours=6)['price_floor'])
                final=await self.send(c,str(ask),first['id'])
                self.assertEqual(final['status']['state'],'TASK_STATE_COMPLETED')
                self.assertEqual(SellerState().pending_receipt(first['id'])['price'],ask)
        with patch.object(seller_r3.time,'time',side_effect=lambda:clock[0]),patch.object(seller_r3,'decide_json',return_value={'action':'ask','price':1,'unit_id':'u1','reason':'low quote'}) as model:
            asyncio.run(flow());self.assertEqual(model.call_count,1)

    def test_old_quote_below_current_floor_cannot_bypass_model_or_cost_guard(self):
        clock=[self.now]
        self.unit.update(kind='goods',grade='A',cost=239662,list_price=347850,supplied_at=self.now-600,expires_at=None);self.write_inventory()
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1')
                ask=SellerState().restore_deals()[first['id']]['ask'];clock[0]+=180
                last=await self.send(c,str(ask),first['id'])
                self.assertNotEqual(last['status']['state'],'TASK_STATE_COMPLETED')
                self.assertIsNone(SellerState().pending_receipt(first['id']))
        with patch.object(seller_r3.time,'time',side_effect=lambda:clock[0]),patch.object(seller_r3,'decide_json',side_effect=[{'action':'ask','price':1,'unit_id':'u1','reason':'quote'},{'action':'accept','price':1,'unit_id':'u1','reason':'unsafe'}]):asyncio.run(flow())

    def executor_case(self, signed=False):
        state=SellerState();now=time.time()
        state.save_deal('task',{'unit':self.unit,'opened_at':now,'last_activity_at':now})
        seller=seller_r3.Seller(state)
        if signed:self.assertTrue(state.commit_receipt('task',{'unit':'u1','price':900}))
        async def stop(*args):
            audit.bind(task_id='task');raise asyncio.CancelledError
        ctx=SimpleNamespace(current_task=SimpleNamespace(id='task'),message=SimpleNamespace(message_id='message'))
        with patch.object(seller,'_execute',side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):asyncio.run(seller.execute(ctx,None))
        return state,seller

    def test_cancelled_execution_releases_unsigned_lease_immediately(self):
        state,seller=self.executor_case()
        self.assertEqual(state.active_leases(),[]);self.assertNotIn('task',seller.open)

    def test_cancelled_execution_never_releases_signed_receipt(self):
        state,seller=self.executor_case(signed=True)
        self.assertEqual(state.active_leases()[0][2],'receipt');self.assertEqual(state.pending_receipt('task')['price'],900)
        self.assertFalse(state.save_deal('other',{'unit':self.unit,'opened_at':time.time(),'last_activity_at':time.time()}))

    def test_silent_followup_reservation_expires_at_short_idle_deadline(self):
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1');await self.send(c,'700',first['id'])
            state=SellerState();deal=state.restore_deals()[first['id']]
            with state.connect() as db:until=db.execute('select until from leases where task=?',(first['id'],)).fetchone()[0]
            self.assertAlmostEqual(until-deal['last_activity_at'],90,places=2)
        with patch.object(seller_r3,'decide_json',side_effect=[{'action':'ask','price':900,'unit_id':'u1','reason':'quote'},{'action':'counter','price':900,'unit_id':'u1','reason':'counter'}]):asyncio.run(flow())

class QualityOptionTests(unittest.TestCase):
    def test_ab_units_survive_many_old_c_units_and_featured_unit_is_preserved(self):
        units=[{'unit':str(i),'grade':'C'} for i in range(20)]+[{'unit':'A','grade':'A'},{'unit':'B','grade':'B'}]
        options=policy.diverse_options(units,[{'unit':'12'}]);ids={u['unit'] for u in options}
        self.assertEqual(len(ids),12);self.assertTrue({'A','B','12'}<=ids)
        self.assertEqual(policy.diverse_options([{'unit':'B','grade':'B'}]),[{'unit':'B','grade':'B'}])

    def test_recent_exact_grade_receipts_distinguish_cancellation_pending_and_paid(self):
        now=time.time()
        def row(task,age,**kw):return dict(task_id=task,sku='G',grade='C',receipt_at=now-age,outcome='RECEIPT_PENDING',**kw)
        rows=[row('cancel',700,checkout_cancelled_at=now-690),row('pending',60),row('paid',100),row('old',50000),dict(row('other',700),sku='H')]
        rows[2]['outcome']='SETTLED';rows.append(dict(row('late_paid',800,checkout_cancelled_at=now-700),outcome='SETTLED'))
        got=policy.checkout_evidence(rows,'G','C',now)
        self.assertEqual(got['signed'],4);self.assertEqual(got['settled'],2)
        self.assertEqual(got['checkout_cancelled'],1);self.assertEqual(got['pending_unknown'],1);self.assertEqual(got['mature_unsettled_observed'],1)
