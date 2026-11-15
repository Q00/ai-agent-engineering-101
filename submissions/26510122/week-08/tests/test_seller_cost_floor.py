import asyncio
import unittest
from unittest.mock import patch
import test_seller_persistence as fixtures
import seller_r3
from seller_state import SellerState
from seller_warranty import selling_floor

class CostFloorContractTests(unittest.TestCase):
    setUp=fixtures.PersistenceTests.setUp
    tearDown=fixtures.PersistenceTests.tearDown
    write_inventory=fixtures.PersistenceTests.write_inventory
    send=fixtures.PersistenceTests.send
    client=fixtures.PersistenceTests.client

    def test_low_open_counter_and_accept_cannot_sign_fresh_food_below_cost(self):
        answers=[{'action':a,'price':p,'unit_id':'u1','reason':'follow old median'}
                 for a,p in [('ask',300),('counter',300),('accept',300),('accept',501)]]
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1');task=first['id']
                self.assertEqual(SellerState().restore_deals()[task]['ask'],501)
                for _ in range(2):
                    reply=await self.send(c,'300',task)
                    self.assertEqual(reply['status']['state'],'TASK_STATE_INPUT_REQUIRED')
                    self.assertGreaterEqual(SellerState().restore_deals()[task]['ask'],501)
                    self.assertIsNone(SellerState().pending_receipt(task))
                last=await self.send(c,'501',task)
                self.assertEqual(last['status']['state'],'TASK_STATE_COMPLETED')
                self.assertEqual(SellerState().pending_receipt(task)['price'],501)
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())

    def test_restored_legacy_low_ask_is_protected_without_changing_signed_receipts(self):
        answers=[{'action':'ask','price':900,'unit_id':'u1','reason':'quote'},
                 {'action':'accept','price':300,'unit_id':'u1','reason':'agree'}]
        async def flow():
            async with self.client() as c:first=await self.send(c,'I want F1')
            state=SellerState();deal=state.restore_deals()[first['id']];deal['ask']=300;state.save_deal(first['id'],deal,create=False)
            async with self.client() as c:reply=await self.send(c,'300',first['id'])
            self.assertEqual(reply['status']['state'],'TASK_STATE_INPUT_REQUIRED')
            self.assertIsNone(SellerState().pending_receipt(first['id']))
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())

    def test_three_hour_boundary_and_unknown_expiry_are_conservative(self):
        u={'kind':'food','cost':500,'expires_at':100+3*3600}
        self.assertEqual(selling_floor(u,now=100)['price_floor'],0)
        self.assertEqual(selling_floor(u,now=99)['price_floor'],501)
        for expiry in (None,float('nan'),float('inf'),0):
            self.assertEqual(selling_floor(dict(u,expires_at=expiry),now=100)['price_floor'],501)
        self.assertGreater(selling_floor({'kind':'goods','cost':500,'supplied_at':0},now=100)['price_floor'],500)

    def test_near_expiry_loss_requires_explicit_model_accept(self):
        self.unit['expires_at']=self.now+2*3600;self.write_inventory()
        answers=[{'action':'ask','price':400,'unit_id':'u1','reason':'limit spoilage'},
                 {'action':'accept','price':300,'unit_id':'u1','reason':'limit spoilage'}]
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1');await self.send(c,'300',first['id'])
            self.assertEqual(SellerState().pending_receipt(first['id'])['price'],300)
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())
