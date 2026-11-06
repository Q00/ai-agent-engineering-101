"""Offline checkout gate tests, including the installed kit with a fake market."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from r3_checkout import CheckoutGate, guarded_shop
from r3_market_kit import load_kit
from r3_seller import Reservations, sign_receipt


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.now = time.time()
        self.key = Ed25519PrivateKey.generate()
        self.unit = {'unit': 'u1', 'sku': 'sku1', 'grade': 'A', 'kind': 'goods',
                     'supplied_at': self.now, 'expires_at': None, 'cert': {'offline': True}}
        self.inventory = {'round': 3, 'units': [self.unit]}
        self.reservations = Reservations(self.md)
        _, data, token, _ = self.reservations.begin('task1', [self.unit], {'u1': self.unit}, self.now, self.now + 180)
        self.receipt = sign_receipt({'shop': 'shop1', 'item': 'sku1', 'price': 100, 'task': 'task1'}, 'u1', key=self.key)
        data.update(receipt=self.receipt, checkout_guarded=True)
        self.reservations.finish('task1', token, 'signed_pending', data, self.now)
        self.gate = CheckoutGate(self.md)

    def test_unissued_promise_revoked_then_old_receipt_rejected(self):
        status = self.reservations.reconcile(self.inventory, [], 'shop1', self.now + 181)
        self.assertEqual(status['revoked'], 1)
        self.assertEqual(status['blocked_in_inventory'], 0)
        with self.assertRaises(ValueError):
            self.gate.begin(self.receipt, self.now + 181)
        unit, *_ = self.reservations.begin('task2', [self.unit], {'u1': self.unit}, self.now + 181, self.now + 300)
        self.assertEqual(unit, 'u1')

    def test_issued_or_uncertain_checkout_never_ttl_unlocks(self):
        self.gate.begin(self.receipt, self.now)
        status = self.reservations.reconcile(self.inventory, [], 'shop1', self.now + 181)
        self.assertEqual(status['blocked_in_inventory'], 1)
        self.assertEqual(status['revoked'], 0)
        with self.assertRaises(ValueError):
            CheckoutGate(self.md).begin(self.receipt, self.now + 200)

    def test_no_checkout_issued_failure_can_expire(self):
        self.gate.begin(self.receipt, self.now)
        self.gate.finish('task1', no_checkout_issued=True)
        self.assertEqual(self.reservations.reconcile(self.inventory, [], 'shop1', self.now + 181)['revoked'], 1)

    def test_concurrent_checkout_claims_issue_only_once(self):
        def attempt():
            try:
                self.gate.begin(self.receipt, self.now)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: attempt(), range(4)))
        self.assertEqual(sum(results), 1)

    def test_tampered_receipt_does_not_reserve_or_unlock(self):
        for field, value in [('price', 99), ('task', 'other'), ('sig_seller', 'bad')]:
            with self.assertRaises(ValueError):
                self.gate.begin(dict(self.receipt, **{field: value}), self.now)
        self.gate.begin(self.receipt, self.now)

    def test_actual_kit_restores_terms_and_replays_same_checkout_after_restart(self):
        kit = load_kit()
        fixture = self
        class Market:
            def call(self, method, path, role, **kwargs):
                fixture.assertEqual((method, path, role), ('GET', '/api/inventory', 'shop'))
                return httpx.Response(200, json=fixture.inventory)
        cls = guarded_shop(kit.AcpShop)
        shop = cls(self.key, Market(), self.md)
        # Buyer extras must not override the seller's stored unit/cert/warranty.
        body = {'receipt': dict(self.receipt, unit='invented', cert=False, warranty_hours=6),
                'line_items': [{'id': 'sku1', 'quantity': 1}], 'acceptable': ['sku1']}
        response = asyncio.run(shop.create(body, 'https://offline.example'))
        self.assertEqual(response.status_code, 201)
        session = json.loads(response.body)
        self.assertEqual(session['unit'], 'u1')
        self.assertEqual(session['fulfillment']['cert'], self.unit['cert'])
        self.assertNotIn('warranty_hours', session['fulfillment'])
        restarted = cls(self.key, Market(), self.md)
        self.assertEqual(json.loads(asyncio.run(restarted.create(body, 'https://offline.example')).body), session)
        self.assertEqual(len(restarted.sessions), 1)

    def test_legacy_receipt_keeps_old_conservative_lock(self):
        with self.reservations.transaction() as db:
            data = json.loads(db.execute('SELECT data FROM deals').fetchone()['data'])
            data.pop('checkout_guarded')
            db.execute('UPDATE deals SET data=?', (json.dumps(data),))
        with self.assertRaises(ValueError):
            self.gate.begin(self.receipt, self.now)
        self.assertEqual(self.reservations.reconcile(self.inventory, [], 'shop1', self.now + 181)['blocked_in_inventory'], 1)

    def test_bad_checkout_body_does_not_poison_or_replay_reservation(self):
        cls = guarded_shop(load_kit().AcpShop)
        shop = cls(self.key, None, self.md)
        bodies = [None, [], {'receipt': self.receipt},
                  {'receipt': self.receipt, 'line_items': ['bad']},
                  {'receipt': self.receipt, 'line_items': [{'id': 'sku1', 'quantity': True}]},
                  {'receipt': self.receipt, 'line_items': [{'id': 'sku1', 'quantity': 2}]},
                  {'receipt': self.receipt, 'line_items': [{'id': 'sku1', 'quantity': 1}], 'acceptable': []}]
        for body in bodies:
            self.assertEqual(asyncio.run(shop.create(body, 'https://offline.example')).status_code, 400)
        # All invalid requests were rejected before the durable issuance marker.
        self.gate.begin(self.receipt, self.now)

    def test_replay_still_checks_items_and_acceptable_skus(self):
        self.gate.begin(self.receipt, self.now)
        session = {'id': 'cs_offline', 'task': 'task1', 'unit': 'u1',
                   'line_items': [{'id': 'sku1', 'quantity': 1}]}
        self.gate.finish('task1', session)
        shop = guarded_shop(load_kit().AcpShop)(self.key, None, self.md)
        body = {'receipt': self.receipt, 'line_items': [{'id': 'sku1', 'quantity': 1}],
                'acceptable': ['not-sku1']}
        self.assertEqual(asyncio.run(shop.create(body, 'https://offline.example')).status_code, 409)
        body['acceptable'] = ['sku1']
        body['line_items'][0]['id'] = 'not-sku1'
        self.assertEqual(asyncio.run(shop.create(body, 'https://offline.example')).status_code, 400)

    def test_fallback_accepts_ask_matching_already_reached_ceiling(self):
        from r3_strategy import buyer_fallback
        action = buyer_fallback({'budget': 100, 'list_price': 100, 'value_estimate': {'expected_value': 100},
                                 'last_offer': 95, 'current_ask': 95})
        self.assertEqual(action['action'], 'offer')
        self.assertEqual(action['price'], 95)
