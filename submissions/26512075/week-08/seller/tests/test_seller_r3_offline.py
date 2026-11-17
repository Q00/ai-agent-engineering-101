import importlib
import json


import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def install_stubs():
    def module(name):
        result = types.ModuleType(name)
        sys.modules[name] = result
        return result
    for name in ('uvicorn', 'a2a', 'a2a.helpers', 'a2a.server', 'a2a.server.agent_execution',
                 'a2a.server.request_handlers', 'a2a.server.routes', 'a2a.server.tasks',
                 'a2a.types', 'starlette', 'starlette.applications', 'seller', 'storage'):
        module(name)
    helpers = sys.modules['a2a.helpers']
    helpers.get_message_text = lambda msg: msg
    helpers.new_text_part = lambda text: text
    helpers.new_data_part = lambda value: value
    helpers.new_task_from_user_message = lambda msg: SimpleNamespace(id='t1', context_id='c1')
    sys.modules['a2a.server.agent_execution'].AgentExecutor = object
    sys.modules['a2a.server.request_handlers'].DefaultRequestHandler = object
    sys.modules['a2a.server.routes'].create_agent_card_routes = lambda *a: []
    sys.modules['a2a.server.routes'].create_jsonrpc_routes = lambda *a: []
    class Updater:
        def __init__(self, event_queue, **_): self.q = event_queue
        def new_agent_message(self, **kw): return kw
        async def requires_input(self, msg): self.q.events.append(('input', msg))
        async def reject(self, msg): self.q.events.append(('rejected', msg))
        async def failed(self, msg): self.q.events.append(('failed', msg))
        async def add_artifact(self, **kw): self.q.events.append(('artifact', kw))
        async def complete(self, msg): self.q.events.append(('completed', msg))
        async def cancel(self): self.q.events.append(('canceled', None))
    sys.modules['a2a.server.tasks'].TaskUpdater = Updater
    for name in ('AgentCapabilities', 'AgentCard', 'AgentInterface', 'AgentSkill'):
        setattr(sys.modules['a2a.types'], name, object)
    sys.modules['starlette.applications'].Starlette = object
    seller = sys.modules['seller']
    seller.ExistingTaskContextBuilder = object
    seller._positive_int = lambda v: v if type(v) is int and v > 0 else (_ for _ in ()).throw(ValueError())
    seller.decide = AsyncMock()
    seller.followup_decision = AsyncMock()
    seller.sign_receipt = lambda rec: {**rec, 'sig_seller': 'TEST_ONLY'}
    sys.modules['storage'].stores = lambda: None


install_stubs()
r3 = importlib.import_module('seller_r3')


class Store:
    def __init__(self): self.data = {}
    async def get(self, task): return self.data.get(task)
    async def save(self, task, deal): self.data[task] = deal
    async def delete(self, task): self.data.pop(task, None)


class Queue:
    def __init__(self): self.events = []
    async def enqueue_event(self, event): pass


class SellerStockFlow(unittest.IsolatedAsyncioTestCase):
    async def test_turnover_opens_at_one_percent_floor_without_model(self):
        from strategy import Policy
        self.seller.policy = Policy(turnover_markup=.01)
        with patch.object(r3, 'decide', AsyncMock()) as decision:
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='buy G12345678'), self.q)
        decision.assert_not_awaited()
        deal = await self.store.get('t1')
        self.assertEqual((deal['ask'], deal['reserve']), (86, 86))
        self.assertGreater(deal['reserve'], self.row['cost'])

    def setUp(self):
        logger_patch = patch.object(r3.LOG, "exception")
        logger_patch.start()
        self.addCleanup(logger_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.market = Path(self.tmp.name)
        self.row = {'unit': 'u1', 'sku': 'G12345678', 'name': 'mirror',
                    'category': 'home>mirror', 'kind': 'goods', 'cost': 85,
                    'list_price': 150, 'grade': 'A', 'expires_at': None, 'supplied_at': time.time() - 3600}
        self.write([self.row])
        self.store, self.q = Store(), Queue()
        self.seller = r3.Round3Seller(self.store, 'Shop', self.market)
        self.task = SimpleNamespace(id='t1', context_id='c1')

    def write(self, units):
        (self.market / 'inventory.json').write_text(json.dumps({'round': 3, 'units': units}))

    async def test_aging_a_opens_at_floor_without_model_markup(self):
        self.row['supplied_at'] = time.time() - 20 * 3600
        self.write([self.row])
        with patch.object(r3, 'decide', AsyncMock()) as decision:
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='buy G12345678'), self.q)
        decision.assert_not_awaited()
        deal = self.store.data['t1']
        self.assertEqual(deal['ask'], deal['reserve'])
        self.assertEqual(deal['terms']['pricing_mode'], 'aged_floor')

    async def open_deal(self):
        with patch.object(r3, 'decide', AsyncMock(return_value={'action': 'offer', 'price': 100})) as decision:
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='buy G12345678'), self.q)
        self.assertEqual(decision.await_args.args[0]['limit'], 90)
        self.assertTrue({'grade','remaining_shelf_life_hours','offered_warranty_hours','pricing_mode','comps'} <= decision.await_args.args[0].keys())
        self.assertEqual(self.store.data['t1']['stock']['unit'], 'u1')
        self.assertEqual(self.q.events[-1][0], 'input')
        self.assertNotIn('reserve', self.q.events[-1][1]['metadata'])
        self.assertNotIn('comps', self.q.events[-1][1]['metadata'])
        self.assertEqual(self.q.events[-1][1]['metadata']['offered_warranty_hours'], 0)

    async def test_repeated_offer_history_survives_deal_serialization(self):
        from offer_validation import negotiate_followup
        await self.open_deal()
        model = AsyncMock(return_value={"action": "offer", "price": 95, "parsed_offer": 80})
        async def followup(request, text, reserve, ask):
            return await negotiate_followup(request, text, reserve, ask, model, r3.LOG)
        with patch.object(r3, 'followup_decision', followup):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='80'), self.q)
            self.assertEqual(self.store.data['t1']['seen_buyer_offers'], [80])
            self.store.data = json.loads(json.dumps(self.store.data))
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='80'), self.q)
        self.assertEqual(self.store.data['t1']['ask'], 95)
        model.assert_awaited_once()

    async def test_bc_and_old_open_at_floor_without_model(self):
        for grade, age in [('B', 3600), ('C', 3600), ('A', 86401)]:
            with self.subTest(grade=grade, age=age):
                self.store.data.clear()
                self.seller.holds.clear()
                self.row.update(grade=grade, supplied_at=time.time()-age)
                self.write([self.row])
                with patch.object(r3, 'decide', AsyncMock(side_effect=AssertionError('model called'))) as model:
                    await self.seller.execute(SimpleNamespace(current_task=self.task, message='G12345678'), self.q)
                model.assert_not_awaited()
                deal = self.store.data['t1']
                self.assertEqual(deal['ask'], deal['reserve'])
                self.assertEqual(deal['reserve'], 88)

    async def test_cost_becomes_reserve_and_receipt_reuses_selected_unit(self):
        await self.open_deal()
        with patch.object(r3, 'followup_decision', AsyncMock(return_value=({'action': 'accept', 'price': 100}, 100))):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='100'), self.q)
        receipt = next(event[1]['parts'][0] for event in self.q.events if event[0] == 'artifact')
        self.assertEqual((receipt['item'], receipt['unit'], receipt['price']), ('G12345678', 'u1', 100))
        self.assertEqual(self.q.events[-1][0], 'completed')

    async def test_missing_unit_cannot_complete(self):
        await self.open_deal()
        self.write([])
        await self.seller.execute(SimpleNamespace(current_task=self.task, message='100'), self.q)
        self.assertEqual(self.q.events[-1][0], 'failed')
        self.assertNotIn('artifact', [name for name, _ in self.q.events])

    async def test_clarification_keeps_deal_and_hold_then_accepts_explicit_price(self):
        from offer_validation import negotiate_followup
        await self.open_deal()
        model = AsyncMock(side_effect=[
            {'action': 'offer', 'price': 95, 'parsed_offer': 0},
            {'action': 'accept', 'price': 100, 'parsed_offer': 100}])
        async def followup(request, text, reserve, ask):
            return await negotiate_followup(request, text, reserve, ask, model, r3.LOG)
        with patch.object(r3, 'followup_decision', followup):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='Can you go lower?'), self.q)
            self.assertEqual(self.q.events[-1][0], 'input')
            self.assertEqual(self.q.events[-1][1]['metadata']['reason'], 'missing_buyer_offer')
            self.assertEqual(self.store.data['t1']['ask'], 100)
            self.assertIn('u1', self.seller.holds)
            self.assertNotIn('artifact', [name for name, _ in self.q.events])
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='100'), self.q)
        self.assertEqual(self.q.events[-1][0], 'completed')

    async def test_parsing_failure_reports_correct_reason_and_releases_hold(self):
        from offer_validation import OfferParsingError
        await self.open_deal()
        with patch.object(r3, 'followup_decision', AsyncMock(side_effect=OfferParsingError('bad extraction'))):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='100'), self.q)
        self.assertEqual(self.q.events[-1][0], 'failed')
        self.assertIn('parse or validate', self.q.events[-1][1]['parts'][0])
        self.assertNotIn('inventory', self.q.events[-1][1]['parts'][0])
        self.assertNotIn('t1', self.store.data)
        self.assertNotIn('u1', self.seller.holds)
        self.assertNotIn('artifact', [name for name, _ in self.q.events])

    async def test_acceptance_without_verified_offer_cannot_issue_receipt(self):
        await self.open_deal()
        with patch.object(r3, 'followup_decision', AsyncMock(return_value=({'action': 'accept', 'price': 100}, None))):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='yes'), self.q)
        self.assertEqual(self.q.events[-1][0], 'failed')
        self.assertNotIn('artifact', [name for name, _ in self.q.events])

    async def test_price_below_cost_cannot_complete(self):
        await self.open_deal()
        with patch.object(r3, 'followup_decision', AsyncMock(return_value=({'action': 'accept', 'price': 80}, 80))):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message='80'), self.q)
        self.assertEqual(self.q.events[-1][0], 'failed')
        self.assertNotIn('artifact', [name for name, _ in self.q.events])

    async def test_b_is_preferred_and_requested_grade_filters_first(self):
        self.write([self.row, {**self.row, 'unit':'u2', 'grade':'B'}])
        chosen = await self.seller._reserve('b', 'buy G12345678')
        self.assertEqual(chosen.unit, 'u2')
        chosen = await self.seller._reserve('a', 'buy G12345678', {'grades':['A']})
        self.assertEqual(chosen.unit, 'u1')

    async def test_food_grade_drop_during_model_call_cannot_issue_receipt(self):
        now = time.time()
        self.row.update(kind='food', grade='A', expires_at=now+9*3600, supplied_at=now-100)
        self.write([self.row])
        (self.market/'catalog.json').write_text(json.dumps({'G12345678':{'shelf_hours':10}}))
        await self.open_deal()
        async def changed(*args):
            self.row['expires_at'] = time.time()+7*3600
            self.write([self.row])
            return ({'action':'accept','price':200},200)
        with patch.object(r3,'followup_decision',changed):
            await self.seller.execute(SimpleNamespace(current_task=self.task,message='200'),self.q)
        self.assertEqual(self.q.events[-1][0],'failed')
        self.assertNotIn('artifact',[name for name,_ in self.q.events])

    async def test_positive_warranty_request_is_rejected_before_offer(self):
        class Message(str): pass
        message=Message('buy G12345678')
        message.metadata={'accept':{'warranty_hours':6}}
        with patch.object(r3,'decide',AsyncMock()) as decision:
            await self.seller.execute(SimpleNamespace(current_task=self.task,message=message),self.q)
        decision.assert_not_awaited()
        self.assertEqual(self.q.events[-1][0],'rejected')

    async def make_ad(self, activate=True):
        self.row['cert'] = {'cert':{'shop':'helenjeon101'}}
        self.write([self.row])
        ad = await self.seller.advertisement({'action':'preview','market':'https://market.example',
            'shop':'helenjeon101','unit':'u1','price':100,'ttl':1800})
        if activate:
            await self.seller.advertisement({'action':'activate','entry':ad})
        return ad

    async def test_ad_ledger_reference_is_generated_and_rechecked(self):
        import sqlite3
        with sqlite3.connect(self.market/'forum-analysis.sqlite3') as db:
            db.executescript("CREATE TABLE meta (key TEXT, value TEXT); "
                             "CREATE TABLE settlements (seq INTEGER, sku TEXT, grade TEXT, "
                             "warranty REAL, amount INTEGER, ts REAL);")
            db.execute('INSERT INTO meta VALUES (?,?)', ('market', 'https://market.example'))
            db.execute('INSERT INTO settlements VALUES (?,?,?,?,?,?)',
                       (12, 'G12345678', 'A', 0, 110, time.time()-60))
        ad = await self.make_ad(False)
        self.assertEqual(ad['refs'], [12])
        self.assertIn('원장 #12', ad['post'])
        await self.seller.advertisement({'action':'validate', 'entry':ad})
        with sqlite3.connect(self.market/'forum-analysis.sqlite3') as db:
            db.execute('UPDATE settlements SET warranty=24 WHERE seq=12')
        with self.assertRaises(ValueError):
            await self.seller.advertisement({'action':'validate', 'entry':ad})

    async def test_private_socket_preview_and_activation_survive_restart(self):
        import asyncio
        await self.make_ad(False)
        path=self.market/r3.shop_ads.SOCKET
        server=await asyncio.start_unix_server(self.seller._ad_client,path=str(path))
        try:
            ad=await asyncio.to_thread(r3.shop_ads.request,self.market,'preview',
                market='https://market.example',shop='helenjeon101',unit='u1',price=100,ttl=1800)
            await asyncio.to_thread(r3.shop_ads.request,self.market,'activate',entry=ad)
            restarted=r3.Round3Seller(self.store,'Shop',self.market)
            chosen=await restarted._reserve('restart','G12345678')
            self.assertEqual(chosen.unit,'u1')
            self.assertEqual(restarted.task_ads['restart'],ad)
        finally:
            server.close()
            await server.wait_closed()
            path.unlink(missing_ok=True)

    async def test_ad_atomic_concurrent_reservation_and_no_model(self):
        import asyncio
        ad = await self.make_ad()
        selected = await asyncio.gather(self.seller._reserve('one','G12345678'),
                                       self.seller._reserve('two','G12345678'))
        self.assertEqual(sum(u is not None for u in selected),1)
        self.assertEqual(len(self.seller.task_ads),1)
        with self.assertRaises(r3.InventoryError):
            await self.seller.advertisement({'action':'validate','entry':ad})

    async def test_ad_fixed_open_counter_and_signed_receipt(self):
        ad = await self.make_ad()
        with patch.object(r3,'decide',AsyncMock(side_effect=AssertionError('model used'))), \
             patch.object(r3,'followup_decision',AsyncMock(side_effect=AssertionError('model used'))):
            for message in ['G12345678','Can you go lower?','90','100']:
                await self.seller.execute(SimpleNamespace(current_task=self.task,message=message),self.q)
        quoted = [e[1]['metadata']['ask'] for e in self.q.events if e[0]=='input']
        self.assertEqual(quoted,[100,100,100])
        self.assertEqual(self.q.events[1][1]['metadata']['reason'], 'missing_buyer_offer')
        receipt = next(e[1]['parts'][0] for e in self.q.events if e[0]=='artifact')
        self.assertEqual(receipt['price'],100)
        self.assertEqual(receipt['unit'],'u1')
        with r3.shop_ads.database(self.market) as db:
            self.assertEqual(db.execute('SELECT ad_id FROM receipts').fetchone()[0],ad['id'])
        # Seller restart must not sell a unit again while its issued receipt could still settle.
        restarted = r3.Round3Seller(self.store,'Shop',self.market)
        self.assertIsNone(await restarted._reserve('again','G12345678'))

    async def test_ad_floor_drift_preserves_fixed_price_through_receipt(self):
        ad = await self.make_ad()
        original_pricing = r3.pricing
        def drifted(unit, policy, now):
            return {**original_pricing(unit, policy, now), "reserve": ad["floor"] + 1}
        with patch.object(r3, 'pricing', side_effect=drifted):
            for message in ('G12345678', '90', '100'):
                await self.seller.execute(SimpleNamespace(current_task=self.task, message=message), self.q)
        quotes = [value['metadata']['ask'] for event, value in self.q.events if event == 'input']
        self.assertEqual(quotes, [100, 100])
        receipt = next(value['parts'][0] for event, value in self.q.events if event == 'artifact')
        self.assertEqual(receipt['price'], ad['price'])

    async def test_ad_price_boundary_and_diagnostic(self):
        ad = await self.make_ad(False)
        self.seller._validate_ad_price(ad, ad['price'])
        with self.assertRaises(r3.InventoryError) as caught:
            self.seller._validate_ad_price(ad, ad['price'] + 1)
        for detail in ('sku=G12345678', f"ad_floor={ad['floor']}",
                       'current_floor=101', 'ad_price=100'):
            self.assertIn(detail, str(caught.exception))
        with self.assertRaises(r3.InventoryError):
            self.seller._validate_ad_price({**ad, 'warranty_hours': 1}, ad['floor'])

    async def test_ad_auto_price_and_grade_disclosure(self):
        self.row['cert'] = {'cert': {'shop': 'helenjeon101'}}
        for grade in ('A', 'B', 'C'):
            self.row['grade'] = grade
            self.write([self.row])
            ad = await self.seller.advertisement({'action': 'preview',
                'market': 'https://market.example', 'shop': 'helenjeon101', 'unit': 'u1'})
            self.assertGreaterEqual(ad['price'], ad['floor'])
            self.assertIn(f'{grade}등급', ad['post'])
            self.assertIn('재고 1개', ad['post'])
            self.assertIn('보증 0h', ad['post'])
        self.write([])
        with self.assertRaises(r3.InventoryError):
            await self.seller.advertisement({'action': 'preview',
                'market': 'https://market.example', 'shop': 'helenjeon101', 'unit': 'u1'})

    async def test_ad_higher_offer_honors_fixed_price(self):
        await self.make_ad()
        for message in ('G12345678', '120'):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message=message), self.q)
        receipt = next(e[1]['parts'][0] for e in self.q.events if e[0] == 'artifact')
        self.assertEqual(receipt['price'], 100)

    async def test_ad_missing_price_repeats_ask_and_keeps_deal(self):
        await self.make_ad()
        for message in ('G12345678', 'Can you go lower?'):
            await self.seller.execute(SimpleNamespace(current_task=self.task, message=message), self.q)
        event, message = self.q.events[-1]
        self.assertEqual(event, 'input')
        self.assertIn('Current ask: 100', message['parts'][0])
        self.assertIn('등급 A', message['parts'][0])
        self.assertIn('t1', self.store.data)
        self.assertFalse(any(e[0] == 'artifact' for e in self.q.events))

    async def test_ad_expiry_or_changed_conditions_terminates_deal(self):
        ad = await self.make_ad()
        await self.seller.execute(SimpleNamespace(current_task=self.task,message='G12345678'),self.q)
        with patch.object(r3.time,'time',return_value=ad['expires_at']+1), \
             patch.object(r3,'read_fresh_stock',return_value=[r3.InventoryUnit(**self.row)]):
            await self.seller.execute(SimpleNamespace(current_task=self.task,message='100'),self.q)
        self.assertEqual(self.q.events[-1][0],'failed')
        self.assertFalse(any(e[0]=='artifact' for e in self.q.events))

    async def test_ad_rejects_grade_cost_floor_or_owner_change(self):
        from dataclasses import replace
        ad = await self.make_ad(False)
        for field,value in [('grade','B'),('cost',86),('cert',{'cert':{'shop':'other'}})]:
            with self.subTest(field=field):
                self.write([{**self.row,field:value}])
                with self.assertRaises(r3.InventoryError):
                    await self.seller.advertisement({'action':'activate','entry':ad})
        self.write([self.row])
        self.seller.policy = replace(self.seller.policy,minimum_markup=.20,target_markup=.20)
        with self.assertRaises(r3.InventoryError):
            await self.seller.advertisement({'action':'activate','entry':ad})

    async def test_ad_preview_fixed_timestamp_and_goods_only(self):
        ad = await self.make_ad(False)
        checked = await self.seller.advertisement({'action':'validate','entry':ad})
        self.assertEqual(checked,ad)
        self.assertIn('[조건 한정]',ad['post'])
        self.row.update(kind='food',expires_at=time.time()+36000)
        self.write([self.row])
        (self.market/'catalog.json').write_text(json.dumps({'G12345678':{'shelf_hours':10}}))
        with self.assertRaises(r3.InventoryError):
            await self.seller.advertisement({'action':'preview','market':'https://market.example',
                'shop':'helenjeon101','unit':'u1','price':100,'ttl':1800})

    async def test_ad_receipt_exact_unit_settlement_attribution(self):
        ad = await self.make_ad()
        await self.seller.execute(SimpleNamespace(current_task=self.task,message='G12345678'),self.q)
        await self.seller.execute(SimpleNamespace(current_task=self.task,message='100'),self.q)
        completion = {'status':'completed','order':{'mandate_receipt':{'ledger':123,'unit':'u1'}},
                      'totals':[{'amount':100}]}
        r3.forum_effects.settled(self.market,{'units':[self.row]},completion,'https://shop.example')
        result = r3.shop_ads.effects(self.market)[0]
        self.assertEqual((result['ad_id'],result['unit'],result['ledger'],result['gross_spread']),
                         (ad['id'],'u1',123,15))
        self.assertIsNone(result['post_id'])
        self.assertIsNone(result['confirmed_net_margin'])
        (self.market/'forum-delivery.jsonl').write_text(json.dumps({**ad,'state':'sent','post_id':77,'sent_at':ad['checked_at']})+'\n')
        self.assertEqual(r3.shop_ads.effects(self.market)[0]['post_id'],77)
        other={**self.row,'unit':'different-unit'}
        r3.forum_effects.settled(self.market,{'units':[other]},
            {**completion,'order':{'mandate_receipt':{'ledger':124,'unit':'different-unit'}}},'https://shop.example')
        self.assertEqual(len(r3.shop_ads.effects(self.market)),1)
        completion['totals'][0]['amount'] = 99
        with self.assertRaises(ValueError):
            r3.forum_effects.settled(self.market,{'units':[self.row]},completion,'https://shop.example')

    def test_ambiguous_leaf_refused_and_sku_exact(self):
        u = r3.InventoryUnit(**self.row)
        second = r3.InventoryUnit(**{**self.row, 'unit': 'u2', 'sku': 'Gabcdef12', 'category': 'other>mirror', 'name': 'other'})
        self.assertEqual(r3.matching_units('buy mirror', [u, second]), [])
        self.assertEqual([v.unit for v in r3.matching_units('buy Gabcdef12', [u, second])], ['u2'])


if __name__ == '__main__': unittest.main()





