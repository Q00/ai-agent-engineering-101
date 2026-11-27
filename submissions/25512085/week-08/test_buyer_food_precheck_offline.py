"""Step3 synthetic shelf-life hints; selected receipts are NOT paid trades."""
from contextlib import contextmanager
import base64
import time
import unittest
from unittest.mock import patch

import market_buyer as buyer
import test_buyer_price_offline as fixtures
import test_buyer_saved_risk_offline as risk


class FoodPrecheckTests(unittest.TestCase):
    setUp = fixtures.BuyerPriceTests.setUp
    receipt = fixtures.BuyerPriceTests.receipt
    run_buyer = fixtures.BuyerPriceTests.run_buyer

    def test_use_one_hour_rejects_short_and_accepts_exact_margin(self):
        self.order['use_within_hours'] = 1
        for hours, accepted in ((1.1, False), (1.25, True), (1.249, False), (2, True)):
            with self.subTest(hours=hours):
                self.hub['pending_deals'] = {}
                result, _, _, memory = self.run_buyer(receipt=self.receipt(
                    9000, expires_at=self.now + hours*3600))
                self.assertEqual(result['receipt'] is not None, accepted)
                if not accepted:
                    self.assertIn('food-lifetime-precheck', result['review'])
                    self.assertEqual(buyer.failure_kind(result['review']), 'receipt_policy')
                    self.assertFalse(self.hub.get('pending_deals'))
                    self.assertFalse(memory['shop']['dishonest'])
                    self.assertEqual(memory['shop']['recent'][-1]['outcome'], 'declined-price-policy')
                else:
                    self.assertEqual(result['accept']['min_hours_left'], 1.25)
                    self.assertIs(type(result['receipt']['price']), int)

    def test_short_hint_declines_on_every_completion_path(self):
        self.order['use_within_hours'] = 1
        for turn in range(5):
            with self.subTest(turn=turn):
                self.hub['pending_deals'] = {}
                result, _, _, _ = self.run_buyer(ask=9000, complete_after=turn,
                    receipt=self.receipt(9000, expires_at=self.now+1.1*3600))
                self.assertIsNone(result['receipt'])
                self.assertIn('food-lifetime-precheck', result['review'])
                self.assertFalse(self.hub.get('pending_deals'))

    def test_unknown_hints_defer_to_kit_not_certification(self):
        for extra in ({}, {'hours_left': .1}, *({'expires_at': v} for v in (
                None, float('nan'), float('inf'), True, False, -1, 0, '2026-11-09T00:00:00Z', [], {}))):
            with self.subTest(extra=extra):
                result, _, _, _ = self.run_buyer(receipt=self.receipt(9000, **extra))
                self.assertIsNotNone(result['receipt'])
                self.assertTrue(result['accept']['cert'])
                self.assertIn('verification pending', result['review'])

    def test_expired_and_explicit_certificate_false_decline(self):
        for extra in ({'expires_at': self.now-1}, {'expires_at': self.now}, {'cert': False}):
            with self.subTest(extra=extra):
                result, _, _, _ = self.run_buyer(receipt=self.receipt(9000, **extra))
                self.assertIsNone(result['receipt'])
                self.assertIn('food-lifetime-precheck', result['review'])

    def test_strengthened_active_accept_is_the_precheck_threshold(self):
        self.order['use_within_hours'] = 1
        original = buyer.food_price_policy
        def stronger(*args, **kwargs):
            cap, accept = original(*args, **kwargs)
            return cap, dict(accept, min_hours_left=3) if len(args) > 5 else accept
        with patch.object(buyer, 'food_price_policy', side_effect=stronger):
            for hours, accepted in ((2.99, False), (3, True)):
                result, _, _, _ = self.run_buyer(receipt=self.receipt(
                    9000, expires_at=self.now+hours*3600))
                self.assertEqual(result['receipt'] is not None, accepted)
                if accepted:
                    self.assertEqual(result['accept']['min_hours_left'], 3)

    def test_actual_sku_price_policy_and_catalog_name_are_preserved(self):
        self.order['use_within_hours'] = 1
        rec = dict(self.receipt(9000, expires_at=self.now+4500), item='f00000002')
        result, _, _, _ = self.run_buyer(receipt=rec)
        self.assertIsNotNone(result['receipt'])
        self.assertEqual(self.hub['pending_deals']['order-1']['name'], 'other')
        self.assertEqual(self.hub['pending_deals']['order-1']['list_price'], 10000)
        result, _, _, _ = self.run_buyer(receipt=dict(rec, price=9001))
        self.assertIsNone(result['receipt'])

    def test_internal_helper_none_is_compatible_and_not_a_certificate(self):
        policy = buyer.build_accept_policy(self.order, 9000, 16700)
        self.assertIsNone(buyer.food_lifetime_precheck({}, policy, self.now))
        self.assertIsNone(buyer.food_lifetime_precheck(
            {'expires_at': self.now+policy['min_hours_left']*3600}, policy, self.now))

    def test_short_first_shop_retries_to_selected_not_paid_second_shop(self):
        self.order['use_within_hours'] = 1
        shops = [dict(name=n, url='https://'+n+'.invalid') for n in ('short', 'fresh')]
        messages = []
        @contextmanager
        def local_hub():
            yield self.hub
        def pick(remaining, *args):
            return remaining[0], dict(self.catalog[self.sku], sku=self.sku), 'fixture'
        def rpc(url, method, params):
            self.assertEqual(method, 'SendMessage')
            messages.append(params['message']['parts'][0]['text'])
            hours = 1.1 if 'short' in url else 1.25
            return {'id': 'task-1', 'status': {'state': 'TASK_STATE_COMPLETED'},
                    'artifacts': [{'parts': [{'data': self.receipt(9000,
                        expires_at=self.now+hours*3600)}]}]}
        with patch.object(buyer, 'MEMORY_FILE', self.tmp/'retry-memory.json'), \
                patch.object(buyer, 'mutate_hub', local_hub), \
                patch.object(buyer, 'pick_round3_shop_and_sku', side_effect=pick), \
                patch.object(buyer.httpx, 'get', return_value=fixtures.SimpleNamespace(json=lambda: {})), \
                patch.object(buyer, 'rpc_call', side_effect=rpc):
            result = buyer.run_round3(shops, {}, self.hub, self.catalog, self.order,
                                      'food', 25000, time.monotonic()+90)
        self.assertEqual(result['shop'], 'https://fresh.invalid')
        self.assertIsNotNone(result['receipt'])  # pending fixture, NOT a settlement
        self.assertEqual(len(messages), 2)
        self.assertEqual(self.hub['pending_deals']['order-1']['shop_name'], 'fresh')
        events = self.hub['adaptive_strategy']['buyer_failures']
        self.assertEqual([(e['url'], e['reason']) for e in events],
                         [('https://short.invalid', 'price')])
        for text in messages:
            self.assertIn('at least 1.25h', text)
            self.assertIn('cert=true', text)
            self.assertNotIn('25000', text)
            self.assertNotIn('90%', text)

    def test_goods_ignores_food_hints_and_keeps_step2_gate(self):
        self.order['kind'] = 'goods'
        result, _, _, _ = self.run_buyer(receipt=self.receipt(9000, claim='A',
            expires_at=self.now-1, cert=False))
        self.assertIsNotNone(result['receipt'])
        self.assertNotIn('min_hours_left', result['accept'])

    def test_fourth_final_accept_still_rejects_negative_goods_saved(self):
        self.order['kind'] = 'goods'
        result, offers, _, _ = self.run_buyer(ask=13000, receipt=self.receipt(13000,
            claim='C', warranty_hours=0), complete_after=4)
        self.assertEqual(len(offers), 4)
        self.assertIsNone(result['receipt'])


class FoodKitContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        risk.KitAcceptContractTests.setUpClass.__func__(cls)

    def checkout(self, hours):
        key = risk.Ed25519PrivateKey.generate()  # ephemeral synthetic market only
        body = {'unit': 'unit', 'sku': 'sku', 'grade': 'A',
                'expires_at': risk.NOW+hours*3600}
        order = {'kind': 'food', 'market_pubkey': base64.b64encode(
            key.public_key().public_bytes(risk.Encoding.Raw, risk.PublicFormat.Raw)).decode()}
        co = {'unit': 'unit', 'item': 'sku', 'cert': {
            'cert': body, 'sig': base64.b64encode(key.sign(self.canon(body))).decode()}}
        return order, co

    def test_precheck_matches_real_kit_less_than_predicate_and_boundary(self):
        policy = buyer.build_accept_policy({'kind': 'food', 'use_within_hours': 1}, 9000, 16700)
        for minimum in (policy['min_hours_left'], 3, 38.4):
            policy['min_hours_left'] = minimum
            for hours in (minimum-.001, minimum, minimum+.001):
                with self.subTest(minimum=minimum, hours=hours):
                    order, co = self.checkout(hours)
                    hint = {'expires_at': co['cert']['cert']['expires_at']}
                    with patch.object(buyer.time, 'time', return_value=risk.NOW):
                        self.assertEqual(buyer.food_lifetime_precheck(hint, policy) is None,
                                         self.check(order, co, policy) is None)

    def test_missing_hint_still_needs_real_market_certificate(self):
        policy = buyer.build_accept_policy({'kind': 'food', 'use_within_hours': 1}, 9000, 16700)
        self.assertIsNone(buyer.food_lifetime_precheck({}, policy, risk.NOW))
        self.assertIn('no certificate', self.check({'kind': 'food'}, {}, policy))


if __name__ == '__main__':
    unittest.main()
