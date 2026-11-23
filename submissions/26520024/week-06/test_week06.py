"""Offline tests only: scripted model decisions are never experimental evidence."""
import contextlib
import io
import json
from pathlib import Path
import time
import unittest

from core import Catalog, decide, last_price, strict_json, validate_buyer, validate_plan, validate_seller

ROOT = Path(__file__).resolve().parent


class Guards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = json.loads((ROOT / 'catalog.json').read_text())
        cls.catalog = Catalog(cls.products)

    def test_catalog_size(self):
        self.assertEqual(len(self.products), 3016)

    def test_description_retrieval(self):
        for case in json.loads((ROOT / 'challenge_cases.json').read_text()):
            candidates = self.catalog.search(case['query'])
            self.assertLessEqual(len(candidates), 12)
            self.assertIn(case['expected_id'], [p['id'] for p in candidates])

    def test_generic_retrieval(self):
        for name in ('lamp', 'chair', 'bookshelf'):
            self.assertEqual(self.catalog.search('I want to buy a ' + name)[0]['id'], name)

    def test_strict_json(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ValueError):
                strict_json(text)

    def test_reserve_guard(self):
        for price in (99, -1, True, 100.0):
            with self.assertRaises(ValueError):
                validate_seller(dict(action='ask', item_id='lamp', price=price, summary=''),
                                self.products, None, None)

    def test_identity_guard(self):
        with self.assertRaises(ValueError):
            validate_seller(dict(action='ask', item_id='chair', price=200, summary=''),
                            self.products, 'lamp', 100)

    def test_accept_guard(self):
        for price, selected, offer in ((110, 'lamp', 100), (100, None, 100), (100, 'lamp', None)):
            with self.assertRaises(ValueError):
                validate_seller(dict(action='accept', item_id='lamp', price=price, summary=''),
                                self.products, selected, offer)

    def test_valid_accept(self):
        a = dict(action='accept', item_id='lamp', price=100, summary='')
        self.assertEqual(validate_seller(a, self.products, 'lamp', 100), a)

    def test_budget_guard(self):
        for price in (121, -1, True, 120.0):
            with self.assertRaises(ValueError):
                validate_buyer(dict(action='offer', price=price, summary=''), 120)

    def test_plan_guard(self):
        for purchases in ([dict(item='lamp', shop_id='a', budget=121)],
                          [dict(item='lamp', shop_id='bad', budget=100)]):
            with self.assertRaises(ValueError):
                validate_plan(dict(purchases=purchases, summary=''), ['lamp'], ['a'], 120)
        with self.assertRaises(ValueError):
            validate_plan(dict(purchases=[dict(item='lamp', shop_id='a', budget=50)] * 2, summary=''),
                          ['lamp', 'chair'], ['a'], 120)

    def test_price_parser(self):
        self.assertEqual(last_price('offer 90, counteroffer 100'), 100)
        self.assertEqual(last_price('-20'), -20)
        self.assertIsNone(last_price('12.5'))

    def test_corrective_feedback(self):
        calls = []
        def scripted(system, history, log):
            calls.append(list(history))
            return json.dumps(dict(action='offer', price=121 if len(calls) == 1 else 110, summary='')), dict(input_tokens=1, output_tokens=1)
        with contextlib.redirect_stdout(io.StringIO()):
            a = decide('offline', {}, lambda a: validate_buyer(a, 120), 'buyer',
                       time.monotonic() + 5, transport=scripted)
        self.assertEqual(a['price'], 110)
        self.assertEqual(len(calls), 2)
        self.assertIn('error', calls[1][-1]['content'])

    def test_no_fallback(self):
        def invalid(*args):
            return 'bad JSON', dict(input_tokens=1, output_tokens=1)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            decide('offline', {}, lambda a: a, 'buyer', time.monotonic() + 5, transport=invalid)


class Protocol(unittest.TestCase):
    def test_sdk_task_lifecycle(self):
        from starlette.testclient import TestClient
        from seller import build
        import uuid
        def scripted(system, state, validator, *args):
            selected = state['item_id']
            return validator(dict(action='ask' if selected is None else 'accept', item_id='lamp',
                                  price=120 if selected is None else state['incoming_offer'], summary='offline fixture'))
        def send(client, text, task_id=None):
            msg = dict(role='ROLE_USER', messageId=uuid.uuid4().hex, parts=[dict(text=text)])
            if task_id:
                msg['taskId'] = task_id
            return client.post('/', headers={'A2A-Version': '1.0'}, json=dict(jsonrpc='2.0', id=uuid.uuid4().hex,
                        method='SendMessage', params=dict(message=msg))).json()
        with contextlib.redirect_stdout(io.StringIO()), TestClient(build(9999, decision=scripted)) as client:
            card = client.get('/.well-known/agent-card.json', headers={'A2A-Version': '1.0'}).json()
            self.assertEqual(card['supportedInterfaces'][0]['protocolVersion'], '1.0')
            first = send(client, 'lamp')['result']['task']
            self.assertEqual(first['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
            last = send(client, '100', first['id'])['result']['task']
            self.assertEqual(last['status']['state'], 'TASK_STATE_COMPLETED')
            self.assertIn('error', send(client, '110', first['id']))

    def test_receipt_rejects_overcharge(self):
        from wire import receipt
        task = dict(artifacts=[dict(parts=[dict(data=dict(item='lamp', price=121))])])
        with self.assertRaises(ValueError):
            receipt(task, 'lamp', 120, 120)


if __name__ == '__main__':
    unittest.main(verbosity=2)
