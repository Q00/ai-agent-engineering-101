"""Regression tests for real ledger URLs, aged evidence and supply-surge risk."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from r3_checkout import CheckoutGate, guarded_shop
from r3_common import DEFAULT_POLICY, atomic_json, read_json
from r3_market_kit import load_kit
from r3_memory import Memory
from r3_restock import blocked_inventory, bounded_proposals, plan_restock, surge_factors, wholesale_bound
from r3_seller import Reservations, sign_receipt
from test_r3_discovery import CATALOG, NOW
from test_r3_restock import analysis, inventory, unit


class CashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.key = Ed25519PrivateKey.generate()
        self.now = time.time()
        self.unit = dict(unit='u1', sku='sku1', cost=80, kind='goods', grade='A',
                         supplied_at=self.now, expires_at=None, cert={'fixture': True})
        self.inventory = dict(round=3, units=[self.unit])
        self.reservations = Reservations(self.md)
        _, data, token, _ = self.reservations.begin('task1', [self.unit], {'u1'}, self.now, self.now + 180)
        self.receipt = sign_receipt(dict(shop='shop1', item='sku1', price=100, task='task1'), 'u1', key=self.key)
        data.update(receipt=self.receipt, checkout_guarded=True)
        self.reservations.finish('task1', token, 'signed_pending', data, self.now)
        fixture = self

        class Market:
            def call(self, method, path, role, **kwargs):
                fixture.assertEqual((method, path, role), ('GET', '/api/inventory', 'shop'))
                return httpx.Response(200, json=fixture.inventory)

        self.kit = load_kit()
        self.shop_class = guarded_shop(self.kit.AcpShop)
        self.shop = self.shop_class(self.key, Market(), self.md)
        self.body = dict(receipt=self.receipt, line_items=[dict(id='sku1', quantity=1)])
        response = asyncio.run(self.shop.create(self.body, 'https://old-tunnel.example'))
        self.assertEqual(response.status_code, 201)
        self.session = json.loads(response.body)

    def evidence(self, **updates):
        data = dict(source='public_ledger', seq=123, shop='https://old-tunnel.example',
                    item='sku1', unit='u1', amount=100)
        data.update(updates)
        return dict(kind='market_settle', data=data)

    def reconcile(self, events):
        return self.reservations.reconcile(self.inventory, events, 'shop1', self.now + 181,
                                           self.key.public_key())

    def test_signed_checkout_url_matches_ledger_after_tunnel_changes(self):
        report = self.reconcile([self.evidence()])
        self.assertEqual(report['settled'], 1)
        self.assertEqual(report['units'][0]['settlement']['ledger'], 123)
        with self.assertRaises(ValueError):
            self.reservations.begin('task2', [self.unit], {'u1'}, self.now + 182, self.now + 300)

    def test_matching_price_and_unit_do_not_allow_another_shop(self):
        for updates in (dict(shop='https://another.example'), dict(item='other'), dict(amount=101),
                        dict(task='other'), dict(source='forum_claim'), dict(seq=True)):
            with self.subTest(updates=updates):
                self.assertEqual(self.reconcile([self.evidence(**updates)])['settled'], 0)

    def test_unsigned_or_tampered_url_cannot_match_ledger(self):
        altered = copy.deepcopy(self.session)
        head, body, sig = altered['checkout_jwt'].split('.')
        altered['checkout_jwt'] = head + '.' + body + '.' + ('A' if sig[0] != 'A' else 'B') + sig[1:]
        CheckoutGate(self.md).finish('task1', altered)
        self.assertEqual(self.reconcile([self.evidence()])['settled'], 0)

    def test_canceled_session_cannot_reopen_or_complete_after_restart(self):
        response = asyncio.run(self.shop.handle(SimpleNamespace(method='POST'),
            'acp/checkout_sessions/' + self.session['id'] + '/cancel', b'{}', 'https://old-tunnel.example'))
        self.assertEqual(response.status_code, 200)
        restarted = self.shop_class(self.key, None, self.md)
        self.assertEqual(asyncio.run(restarted.create(self.body, 'https://new-tunnel.example')).status_code, 409)
        response = asyncio.run(restarted.handle(SimpleNamespace(method='POST'),
            'acp/checkout_sessions/' + self.session['id'] + '/complete', b'{}', 'https://new-tunnel.example'))
        self.assertEqual(response.status_code, 409)
        with self.assertRaises(ValueError):
            CheckoutGate(self.md).finish('task1', self.session)
        # Confirmed cancellation follows the official kit's release policy. The old task stays closed.
        report = self.reconcile([])
        self.assertEqual(report['blocked_in_inventory'], 0)
        self.assertEqual(report['canceled_released'], 1)
        with self.assertRaises(ValueError):
            CheckoutGate(self.md).begin(self.receipt, self.now + 182)
        unit, *_ = self.reservations.begin('task2', [self.unit], {'u1'}, self.now + 182, self.now + 300)
        self.assertEqual(unit, 'u1')

    def test_cancel_is_serialized_with_inflight_completion(self):
        async def exercise():
            entered, release = asyncio.Event(), asyncio.Event()
            async def fake_handle(req, path, body, public):
                if path.endswith('/complete'):
                    entered.set()
                    await release.wait()
                else:
                    self.assertTrue(release.is_set())
                return None
            self.shop._handle = fake_handle
            base = 'acp/checkout_sessions/' + self.session['id']
            complete = asyncio.create_task(self.shop.handle(SimpleNamespace(method='POST'), base+'/complete', b'', ''))
            await entered.wait()
            cancel = asyncio.create_task(self.shop.handle(SimpleNamespace(method='POST'), base+'/cancel', b'', ''))
            await asyncio.sleep(.01)
            self.assertFalse(cancel.done())
            release.set()
            await asyncio.gather(complete, cancel)
        asyncio.run(exercise())

    def test_old_sale_found_beyond_global_last_10000_events(self):
        with Memory(self.md) as memory:
            memory.record('market_settle', '123', self.evidence()['data'])
            with memory.db:
                memory.db.executemany('INSERT INTO events(kind,event_key,ts,data) VALUES(?,?,?,?)',
                    [('market_settle', str(i), self.now, json.dumps(dict(unit='other', seq=i)))
                     for i in range(1000, 11001)])
            self.assertFalse(any(e['key'] == '123' for e in memory.events('market_settle', 10000)))
            events = memory.settlements_for_units(self.reservations.unsettled_units())
        self.assertEqual(self.reconcile(events)['settled'], 1)

    def test_stock_brake_counts_only_current_unsold_units(self):
        self.reconcile([])
        self.assertEqual(blocked_inventory(self.md, self.inventory), dict(units=1, cost=80, pause=True))
        self.assertEqual(blocked_inventory(self.md, dict(units=[])), dict(units=0, cost=0, pause=False))
        self.reconcile([self.evidence()])
        self.assertFalse(blocked_inventory(self.md, self.inventory)['pause'])

    def test_stock_brake_pauses_before_any_model_or_purchase(self):
        self.reconcile([])
        value = inventory(units=[dict(unit(), unit='u1')])
        atomic_json(self.md / 'inventory.json', value)
        atomic_json(self.md / 'catalog.json', CATALOG)
        import os
        os.utime(self.md / 'inventory.json', (NOW, NOW))
        def forbidden(*args, **kwargs):
            self.fail('Blocked inventory must pause before invoking the model')
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=forbidden), {'buy': []})
        self.assertEqual(read_json(self.md / 'restock-status.json')['status'], 'paused_blocked_inventory')


class SupplySurgeTests(unittest.TestCase):
    def test_maximum_surge_is_reserved_even_before_other_shops_purchase(self):
        inv = inventory()
        rows = bounded_proposals(CATALOG, inv, analysis(), [], DEFAULT_POLICY, NOW)
        self.assertTrue(rows)
        self.assertLessEqual(sum(wholesale_bound(CATALOG[r['sku']])*r['qty'] for r in rows), 100)
        self.assertEqual(wholesale_bound(dict(list_price=39900)), 44888)

    def test_known_unprofitable_surge_excludes_sku(self):
        inv = inventory()
        inv['surge'] = [dict(sku=sku, factor=1.5) for sku in CATALOG]
        self.assertEqual(bounded_proposals(CATALOG, inv, analysis(), [], DEFAULT_POLICY, NOW), [])

    def test_missing_surge_never_assumes_plain_cost(self):
        inv = inventory()
        inv.pop('surge')
        self.assertEqual(bounded_proposals(CATALOG, inv, analysis(), [], DEFAULT_POLICY, NOW), [])

    def test_invalid_surge_fails_closed(self):
        for surge in ({}, [None], [dict(sku='tea', factor=True)], [dict(sku='tea', factor=2)],
                      [dict(sku='tea', factor=float('nan'))]):
            with self.subTest(surge=surge), self.assertRaises(ValueError):
                surge_factors(dict(surge=surge))

    def test_pending_intents_reserve_max_surge_not_previous_plain_price(self):
        pending = [dict(sku='tea', qty=3), dict(sku='cup', qty=3)]
        self.assertEqual(bounded_proposals(CATALOG, inventory(balance=350), analysis(), pending,
                                          DEFAULT_POLICY, NOW), [])
