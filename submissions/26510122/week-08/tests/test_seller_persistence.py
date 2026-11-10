import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from a2a.types import Task, ListTasksRequest

import market_memory
import seller_requirements as requirements
import seller_r3
from seller_kit import instrument_settlement
from seller_state import SellerState, SQLiteTaskStore


class RequirementTests(unittest.TestCase):
    def test_explicit_terms_survive_numeric_turn_and_can_be_changed(self):
        view = requirements.initial('I want F1. It must last 84.0 hours, grade A or B.')
        self.assertEqual(view['min_hours_left'], 84)
        self.assertEqual(view['grades'], ['A', 'B'])
        self.assertEqual(requirements.update(view, '24000'), view)
        newer = requirements.update(view, 'I only need 48 hours now; any grade.')
        self.assertEqual(newer['min_hours_left'], 48)
        self.assertIsNone(newer['grades'])

    def test_mixed_offer_does_not_confuse_hours_with_money(self):
        cases = {'800, but only if it lasts 84 hours': 800,
                 '25,000 if it lasts 84 hours': 25000,
                 'I need 84 hours and offer 25000': 25000,
                 '25000원, 유통기한 84시간 이상': 25000,
                 '84 hours minimum': None, 'need 84 hours': None,
                 'offer 800 or pay 900': None, '25,00': None}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(requirements.offer(text), expected)

    def test_warranty_is_not_food_expiry_and_context_is_bounded(self):
        view = requirements.initial('Need 6 hours warranty, grade A.')
        self.assertEqual(view['warranty_hours'], 6)
        self.assertEqual(view['min_hours_left'], 0)
        for _ in range(100):
            view = requirements.update(view, 'long uncertain condition ' * 1000)
        self.assertLess(len(json.dumps(view).encode()), 4000)
        self.assertEqual(len(view['condition_messages']), 3)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.before = Path.cwd(); os.chdir(self.temp.name)
        self.now = time.time()
        self.unit = {'unit': 'u1', 'sku': 'F1', 'name': 'Example Food', 'category': 'food',
                     'kind': 'food', 'grade': 'B', 'cost': 500, 'list_price': 1000,
                     'supplied_at': self.now-100, 'expires_at': self.now+100*3600}
        Path('.market').mkdir()
        self.write_inventory()
        Path('.market/catalog.json').write_text(json.dumps({'F1': {'kind': 'food', 'name': 'Example Food',
                                                                'list_price': 1000, 'shelf_hours': 200}}))
        key = Ed25519PrivateKey.generate()
        Path('shop_key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.network = patch.object(seller_r3.counterparty_memory, 'refresh', return_value=[])
        self.network.start()

    def tearDown(self):
        self.network.stop(); os.chdir(self.before); self.temp.cleanup()

    def write_inventory(self):
        Path('.market/inventory.json').write_text(json.dumps({'round': 3, 'units': [self.unit]}))

    async def send(self, client, text, task=None):
        message = {'role': 'ROLE_USER', 'messageId': str(uuid.uuid4()), 'parts': [{'text': text}]}
        if task:
            message['taskId'] = task
        response = await client.post('/', json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage',
                                               'params': {'message': message}}, headers={'A2A-Version': '1.0'})
        body = response.json()
        self.assertNotIn('error', body)
        return body['result'].get('task', body['result'])

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)), base_url='http://testserver')

    def test_protocol_and_requirements_resume_after_rebuilding_the_server(self):
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1. It must last 84 hours, grade A or B.')
            state = SellerState()
            deal = state.restore_deals()[first['id']]
            self.assertTrue(deal['waiting_for_first_reply'])
            with state.connect() as db:
                until = db.execute('SELECT until FROM leases WHERE task=?', (first['id'],)).fetchone()[0]
            self.assertAlmostEqual(until-deal['last_activity_at'], seller_r3.FIRST_QUOTE_IDLE_SECONDS)
            async with self.client() as c:
                last = await self.send(c, '800', first['id'])
                self.assertEqual(last['status']['state'], 'TASK_STATE_COMPLETED')
                saved = SellerState().requirements_for(first['id'])
                self.assertEqual(saved['min_hours_left'], 84)
        answers = [{'action': 'ask', 'price': 900, 'unit_id': 'u1', 'reason': 'quote'},
                   {'action': 'accept', 'price': None, 'unit_id': 'u1', 'reason': 'accept'}]
        with patch.object(seller_r3, 'decide_json', side_effect=answers) as model:
            asyncio.run(flow())
        prompt = model.call_args.args[1]
        self.assertEqual(prompt['purchase_requirements']['min_hours_left'], 84)
        self.assertEqual(prompt['purchase_requirements']['grades'], ['A', 'B'])
        self.assertIn('must last 84 hours', prompt['purchase_requirements']['original_request'])

    def enable_promotion(self):
        Path('state').mkdir(exist_ok=True)
        Path('state/seller-opening-event.json').write_text(json.dumps({
            'id':'open-test','starts_at':self.now-100,'ends_at':self.now+3600,
            'discount_starts_at':self.now-1,'discount_bps':300}))

    def test_promotion_survives_restart_and_receipt_matches_acknowledged_price(self):
        self.enable_promotion()
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1')
            self.assertEqual(SellerState().restore_deals()[first['id']]['ask'],873)
            async with self.client() as c:
                last=await self.send(c,'873',first['id'])
                self.assertEqual(last['status']['state'],'TASK_STATE_COMPLETED')
            receipt=SellerState().pending_receipt(first['id'])
            self.assertEqual(receipt['price'],873)  # Not discounted a second time.
        answers=[{'action':'ask','price':900,'unit_id':'u1','reason':'ordinary quote'},
                 {'action':'accept','price':None,'unit_id':'u1','reason':'agreed'}]
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())

    def test_promotion_requires_buyer_ack_for_lower_counter_and_enforces_floor(self):
        self.enable_promotion()
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1')
                high=await self.send(c,'900',first['id'])
                self.assertEqual(high['status']['state'],'TASK_STATE_INPUT_REQUIRED')
                self.assertEqual(SellerState().restore_deals()[first['id']]['ask'],873)
                low=await self.send(c,'400',first['id'])
                self.assertEqual(low['status']['state'],'TASK_STATE_INPUT_REQUIRED')
                self.assertEqual(SellerState().restore_deals()[first['id']]['ask'],501)
                last=await self.send(c,'501',first['id'])
                self.assertEqual(last['status']['state'],'TASK_STATE_COMPLETED')
                self.assertEqual(SellerState().pending_receipt(first['id'])['price'],501)
        answers=[{'action':'ask','price':900,'unit_id':'u1','reason':'ordinary quote'}]+[
            {'action':'accept','price':None,'unit_id':'u1','reason':'model accepts'}]*3
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())

    def test_receipt_claim_matches_food_and_signature_still_verifies(self):
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1')
                await self.send(c, '800', first['id'])
            receipt = SellerState().pending_receipt(first['id'])
            self.assertEqual(receipt['claim'], 'B')
            import base64
            key = serialization.load_pem_private_key(Path('shop_key.pem').read_bytes(), password=None)
            key.public_key().verify(base64.b64decode(receipt['sig_seller']), seller_r3.canon({k:receipt[k] for k in ('shop','item','price','task')}))
            rows = market_memory.events()
            self.assertEqual(next(e for e in rows if e['event']=='receipt_signed')['claim'],'B')
            self.assertTrue(any('grade B at quote' in e.get('text','') for e in rows if e['event']=='seller_message'))
        with patch.object(seller_r3,'decide_json',side_effect=[
                {'action':'ask','price':900,'unit_id':'u1','reason':'quote'},
                {'action':'accept','price':None,'unit_id':'u1','reason':'agreed'}]):
            asyncio.run(flow())

    def test_signed_unit_remains_locked_across_restart_and_is_persisted_before_artifact(self):
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1')
                await self.send(c, '800', first['id'])
            async with self.client() as c:
                second = await self.send(c, 'I want F1')
                self.assertEqual(second['status']['state'], 'TASK_STATE_REJECTED')
        with patch.object(seller_r3, 'decide_json', side_effect=[
                {'action': 'ask', 'price': 900, 'unit_id': 'u1', 'reason': 'quote'},
                {'action': 'accept', 'price': None, 'unit_id': 'u1', 'reason': 'accept'}]) as model:
            asyncio.run(flow())
        self.assertEqual(model.call_count, 2)
        leases = SellerState().active_leases()
        self.assertEqual(leases[0][2], 'receipt')
        self.assertGreater(leases[0][3], time.time()+590)

    def test_wrong_expiry_is_filtered_before_model_and_cannot_receive_a_receipt(self):
        self.unit['expires_at'] = time.time()+36*3600; self.write_inventory()
        async def flow():
            async with self.client() as c:
                result = await self.send(c, 'I want F1. It must last 84 hours.')
                self.assertEqual(result['status']['state'], 'TASK_STATE_REJECTED')
        with patch.object(seller_r3, 'decide_json', side_effect=AssertionError('invalid unit must never reach model')):
            asyncio.run(flow())
        self.assertFalse(any(r['event'] == 'receipt_signed' for r in market_memory.events()))

    def test_mixed_message_updates_requirement_and_preserves_exact_price(self):
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1')
                last = await self.send(c, '800, but only if it lasts 84 hours', first['id'])
                self.assertEqual(last['status']['state'], 'TASK_STATE_COMPLETED')
        with patch.object(seller_r3, 'decide_json', side_effect=[
                {'action': 'ask', 'price': 900, 'unit_id': 'u1', 'reason': 'quote'},
                {'action': 'accept', 'price': None, 'unit_id': 'u1', 'reason': 'accept'}]) as model:
            asyncio.run(flow())
        self.assertEqual(model.call_args.args[1]['buyer_offer'], 800)
        self.assertEqual(model.call_args.args[1]['purchase_requirements']['min_hours_left'], 84)

    def test_expiry_is_checked_again_after_slow_model_decision(self):
        clock = [self.now]
        self.unit['expires_at'] = self.now + 84*3600 + 5; self.write_inventory()
        calls = []
        def decide(*args):
            calls.append(args)
            if len(calls) == 1:
                return {'action': 'ask', 'price': 900, 'unit_id': 'u1', 'reason': 'quote'}
            clock[0] += 10
            return {'action': 'accept', 'price': None, 'unit_id': 'u1', 'reason': 'accept'}
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1. It must last 84 hours.')
                last = await self.send(c, '800', first['id'])
                self.assertEqual(last['status']['state'], 'TASK_STATE_REJECTED')
        with patch.object(seller_r3.time, 'time', side_effect=lambda: clock[0]), patch.object(seller_r3, 'decide_json', side_effect=decide):
            asyncio.run(flow())
        self.assertFalse(any(r['event'] == 'receipt_signed' for r in market_memory.events()))

    def test_database_leases_are_atomic_and_old_receipt_logs_migrate(self):
        first = SellerState(); second = SellerState()
        deal = {'unit': self.unit, 'opened_at': self.now, 'last_activity_at': self.now, 'history': []}
        self.assertTrue(first.save_deal('one', deal))
        self.assertFalse(second.save_deal('two', deal))
        self.assertTrue(first.commit_receipt('one', {'unit': 'u1', 'task': 'one', 'price': 800}))
        self.assertFalse(second.commit_receipt('two', {'unit': 'u1', 'task': 'two', 'price': 900}))
        self.assertEqual(second.pending_receipt('one')['price'], 800)
        first.active_leases([{'event': 'settled', 'unit': 'u1'}])
        self.assertEqual(second.active_leases(), [])
        self.assertFalse(first.save_deal('one', deal, create=False))  # cancellation cannot be undone by a late model
        history = [{'event': 'receipt_signed', 'unit': 'u1', 'task_id': 'old',
                    'at': datetime.now(timezone.utc).isoformat()}]
        first.import_receipt_holds(history)
        self.assertEqual(second.active_leases()[0][2], 'receipt')

    def test_stale_snapshot_cannot_offer_paid_unit_but_can_sell_another_of_same_sku(self):
        second = {**self.unit, 'unit': 'u2'}
        Path('.market/inventory.json').write_text(json.dumps({'round': 3, 'units': [self.unit, second]}))
        market_memory.record('settled', ledger=7, unit='u1', price=800)
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1')
                last = await self.send(c, '800', first['id'])
            self.assertEqual(last['status']['state'], 'TASK_STATE_COMPLETED')
            self.assertEqual(SellerState().pending_receipt(first['id'])['unit'], 'u2')
        def choose(_role, prompt, _schema):
            if prompt['phase'] == 'opening':
                self.assertEqual([u['unit'] for u in prompt['unit_options']], ['u2'])
                return {'action': 'ask', 'price': 900, 'unit_id': 'u2', 'reason': 'available'}
            return {'action': 'accept', 'price': None, 'unit_id': 'u2', 'reason': 'agreed'}
        with patch.object(seller_r3, 'decide_json', side_effect=choose):
            asyncio.run(flow())
        self.assertEqual(SellerState().settled_units(), {'u1'})
        self.assertTrue(any(e['event']=='settled_unit_blocked' and e['unit']=='u1'
                            for e in market_memory.events()))

    def test_settlement_during_negotiation_blocks_new_receipt_in_stale_inventory(self):
        async def flow():
            async with self.client() as c:
                first = await self.send(c, 'I want F1')
                result = await self.send(c, '800', first['id'])
            self.assertEqual(result['status']['state'], 'TASK_STATE_REJECTED')
            self.assertIsNone(SellerState().pending_receipt(first['id']))
        def choose(_role, prompt, _schema):
            if prompt['phase'] == 'opening':
                return {'action': 'ask', 'price': 900, 'unit_id': 'u1', 'reason': 'available'}
            SellerState().mark_settled({'u1'})
            return {'action': 'accept', 'price': None, 'unit_id': 'u1', 'reason': 'agreed'}
        with patch.object(seller_r3, 'decide_json', side_effect=choose):
            asyncio.run(flow())
        self.assertFalse(any(e['event']=='receipt_signed' for e in market_memory.events()))

    def test_kit_marks_paid_unit_and_suppresses_saved_receipt_replay(self):
        state = SellerState()
        deal = {'unit': self.unit, 'opened_at': self.now, 'last_activity_at': self.now}
        self.assertTrue(state.save_deal('old', deal))
        self.assertTrue(state.commit_receipt('old', {'unit': 'u1', 'price': 800}))
        class Market:
            def call(self, method, path, **kwargs):
                return SimpleNamespace(status_code=kwargs['status'],
                    json=lambda: {'ledger': 7, 'mandate_receipt': {'unit': 'u1'}})
        kit = SimpleNamespace(Market=Market, log=lambda message: None)
        instrument_settlement(kit, state)
        market = Market()
        self.assertEqual(market.call('POST', '/api/settle', status=402).status_code, 402)
        self.assertIsNotNone(state.pending_receipt('old'))
        self.assertEqual(market.call('POST', '/api/settle', status=200).status_code, 200)
        self.assertIsNone(SellerState().pending_receipt('old'))
        self.assertFalse(state.save_deal('new', deal))
        state.import_receipt_holds([{'event': 'receipt_signed', 'unit': 'u1', 'task_id': 'old',
                                    'at': datetime.now(timezone.utc).isoformat()}])
        self.assertEqual(state.active_leases(), [])

    def test_receipt_committed_before_crash_replays_without_repricing_or_model(self):
        async def flow():
            with patch.object(seller_r3, 'decide_json', return_value={
                    'action': 'ask', 'price': 900, 'unit_id': 'u1', 'reason': 'quote'}):
                async with self.client() as c:
                    first = await self.send(c, 'I want F1. It must last 84 hours.')
            receipt = seller_r3.sign({'shop': seller_r3.NAME, 'item': 'F1', 'unit': 'u1',
                                      'price': 800, 'task': first['id']})
            self.assertTrue(SellerState().commit_receipt(first['id'], receipt))
            with patch.object(seller_r3, 'decide_json', side_effect=AssertionError('recovery must reuse exact receipt')):
                async with self.client() as c:
                    last = await self.send(c, '900', first['id'])
            self.assertEqual(last['status']['state'], 'TASK_STATE_COMPLETED')
            saved = SellerState().pending_receipt(first['id'])
            self.assertEqual(saved['price'], 800)
            self.assertEqual(saved['sig_seller'], receipt['sig_seller'])
        asyncio.run(flow())

    def test_protocol_task_store_preserves_owner_and_sdk_list_behavior(self):
        async def flow():
            a = SimpleNamespace(user=SimpleNamespace(user_name='a'))
            b = SimpleNamespace(user=SimpleNamespace(user_name='b'))
            first = SQLiteTaskStore(SellerState())
            await first.save(Task(id='t1', context_id='ctx'), a)
            restarted = SQLiteTaskStore(SellerState())
            self.assertIsNone(await restarted.get('t1', b))
            self.assertEqual((await restarted.get('t1', a)).id, 't1')
            self.assertEqual((await restarted.list(ListTasksRequest(), a)).total_size, 1)
            self.assertEqual((await restarted.list(ListTasksRequest(), b)).total_size, 0)
            await restarted.delete('t1', a)
            self.assertIsNone(await restarted.get('t1', a))
        asyncio.run(flow())


if __name__ == '__main__':
    unittest.main()
