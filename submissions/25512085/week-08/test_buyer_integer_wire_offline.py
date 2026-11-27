"""Strict integer-only synthetic seller; no real RPC, model, state or checkout."""
from contextlib import contextmanager, redirect_stderr
import io
import json
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import market_buyer as buyer
import test_buyer_price_offline as price_tests


class BuyerIntegerWireTests(unittest.TestCase):
    setUp = price_tests.BuyerPriceTests.setUp
    receipt = price_tests.BuyerPriceTests.receipt

    def simulate(self, asks=(100000, 100000, 100000, 80000), legacy=False):
        messages, offers = [], []
        invalid = []

        @contextmanager
        def hub():
            yield self.hub

        def rpc(url, method, params):
            if method == 'CancelTask':
                return {}
            self.assertEqual(method, 'SendMessage')
            message = params['message']
            text = message['parts'][0]['text']
            messages.append(message)
            done = False
            ask = asks[min(len(offers), len(asks)-1)]
            if len(messages) > 1:
                self.assertEqual(message['taskId'], 'task-1')
                if legacy:
                    match = re.fullmatch(r'I offer ([0-9]+) if you guarantee product quality and after-sales service support\.', text)
                else:
                    match = re.fullmatch(r'([0-9]+)', text)
                if match is None:
                    invalid.append(text)
                else:
                    offers.append(int(match[1]))
                    done = offers[-1] >= ask
                ask = asks[min(len(offers), len(asks)-1)]
            task = dict(id='task-1', status=dict(
                state='TASK_STATE_COMPLETED' if done else 'TASK_STATE_INPUT_REQUIRED',
                message=dict(parts=[{'text': f'Send your integer offer. ask {ask} cert warranty'}])))
            if done:
                task['artifacts'] = [{'parts': [{'data': dict(self.receipt(offers[-1]), item='lamp') if legacy
                                               else self.receipt(offers[-1])}]}]
            return task

        errors = io.StringIO()
        with patch.object(buyer, 'rpc_call', side_effect=rpc), \
             patch.object(buyer, 'mutate_hub', hub), \
             patch.object(buyer, 'update_memory', lambda *a: None), \
             patch.object(buyer, 'call_harness', return_value='OFFER 100'), \
             patch.object(buyer.httpx, 'get', return_value=SimpleNamespace(json=lambda: {})), \
             redirect_stderr(errors):
            result = buyer.run_attempt(dict(name='shop', url='https://offline.invalid'),
                dict(sku=self.sku, name='synthetic', list_price=103449), 'lamp' if legacy else 'food',
                {}, self.hub, self.catalog, {} if legacy else self.order, 'lamp' if legacy else 'food', 120000)
        events = [json.loads(json.loads(line)[8:]) for line in errors.getvalue().splitlines()
                  if json.loads(line).startswith('[buyer] {"event":"buyer_turn"')]
        self.assertEqual(invalid, [], 'strict seller rejects sentence-form offers')
        return result, messages, offers, events

    def test_all_counters_and_finalaccept_are_integer_only(self):
        self.catalog[self.sku]['list_price'] = 103449
        result, messages, offers, events = self.simulate()
        self.assertEqual(messages[0]['parts'][0]['text'],
            f'I want to buy synthetic ({self.sku}). Please quote your best price with market certificate (cert=true) and at least 12.25h shelf life.')
        self.assertNotIn('taskId', messages[0])
        self.assertEqual(offers, [60000, 74000, 93104, 80000])
        self.assertEqual(result['receipt']['price'], 80000)
        requests = [e for e in events if e['phase']=='offer' and e['boundary']=='request']
        self.assertEqual([e['policy_branch'] for e in requests], ['first', 'counter35', 'third', 'finalaccept'])
        self.assertEqual([e['offer_sent'] for e in requests], offers)
        self.assertEqual(len(events), 13)
        self.assertTrue(events[-1]['receipt_selected'])
        self.assertFalse(events[-1]['payment_confirmed'])

    def test_strict_seller_accepts_current_ask_on_first_offer(self):
        result, messages, offers, _ = self.simulate(asks=(9000,))
        self.assertEqual(offers, [9000])
        self.assertEqual(result['receipt']['price'], 9000)
        self.assertEqual(len(messages), 2)

    def test_goods_opening_keeps_warranty_and_certificate(self):
        self.order.pop('use_within_hours')
        self.order['kind'] = 'goods'
        _, messages, _, _ = self.simulate(asks=(9000,))
        self.assertEqual(messages[0]['parts'][0]['text'],
            f'I want to buy synthetic ({self.sku}). Please quote your best price with market certificate (cert=true) and warranty.')

    def test_legacy_sentence_and_opening_unchanged(self):
        result, messages, offers, events = self.simulate(asks=(200,), legacy=True)
        self.assertEqual(messages[0]['parts'][0]['text'],
            'I want to buy a lamp. Quality and trouble-free use matter: '
            'do you provide after-sales service, a quality warranty, or a flexible refund policy?')
        self.assertEqual(offers, [100, 155, 200])
        self.assertEqual(result['receipt']['price'], 200)
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
