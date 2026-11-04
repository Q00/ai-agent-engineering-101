"""Offline Round 3 buyer fixtures: no model, key, sockets, or market writes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from r3_buyer import (acceptance, candidates_for, explicit_ask, load_order,
                      run, sanitized_order, validate_choice)
from r3_memory import Memory
from wire import ReceiptError, receipt as validate_receipt


def task(state, text='', data=None, task_id='task-1'):
    result = dict(id=task_id, status=dict(state=state, message=dict(parts=[dict(text=text)])))
    if data is not None:
        result['artifacts'] = [dict(parts=[dict(data=data)])]
    return result


class BuyerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.md = Path(self.temp.name)
        (self.md / 'policy.json').write_text(json.dumps({'fallback_enabled': False}))
        self.order = dict(order='order-1', item='milk', category='food>milk', budget=120,
                          acceptable=['F001'], kind='food', use_within_hours=2,
                          expires=time.time() + 600, mandates={'private': 'secret'}, nonce='secret')
        self.shops = [dict(name='Example', url='http://127.0.0.1:9999', reviews=[],
                           offers=[dict(sku='F001', name='Milk', qty=2)])]
        self.catalog = {'F001': dict(sku='F001', name='Milk', kind='food', category='food>milk', list_price=150)}
        self.received = dict(shop='Example', item='F001', price=100.0, task='task-1',
                             sig_seller='offline-signature', unit='unit-1',
                             cert={'cert': {'unit': 'unit-1'}, 'sig': 'certificate'},
                             claim='B', warranty_hours=0, additional={'keep': True})
        self.inputs, self.states, self.instances = [], [], []
        fixture = self

        class FakeWire:
            def __init__(self, url, deadline, context, allow_remote=False):
                fixture.instances.append(self)
                self.deadline, self.closed = deadline, False

            def discover(self):
                return dict(name='Example')

            def send(self, text, task_id=None):
                fixture.inputs.append((text, task_id))
                if task_id is None:
                    return task('TASK_STATE_INPUT_REQUIRED', 'Asking 100. Warranty 24 hours.')
                return task('TASK_STATE_COMPLETED', data=fixture.received)

            def cancel(self, task_id):
                return task('TASK_STATE_CANCELED')

            def close(self):
                self.closed = True

        self.wire = FakeWire
        self.write()

    def write(self):
        for filename, data in [('order.json', self.order), ('shops.json', self.shops),
                               ('catalog.json', self.catalog)]:
            (self.md / filename).write_text(json.dumps(data), encoding='utf-8')

    def model(self, system, state, validator, *args):
        self.states.append(state)
        if 'candidates' in state:
            return validator(dict(shop_name='Example', sku='F001', reason='Eligible stock fixture.'))
        return validator(dict(action='offer', price=100, summary='Offered 100 credits for the exact SKU.'))

    def execute(self, decision=None, wire=None):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            final = run(self.md / 'shops.json', 'milk', 120, self.md / 'order.json', self.md,
                        decision=decision or self.model, wire_factory=wire or self.wire)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), final)
        self.assertEqual(set(final), {'shop', 'receipt', 'stars', 'review', 'accept'})
        return final

    def test_exact_sku_private_budget_pending_full_receipt(self):
        before = time.monotonic()
        final = self.execute()
        self.assertIs(final['receipt'], self.received)
        self.assertEqual(self.inputs, [('I want to buy one unit of SKU F001', None), ('100', 'task-1')])
        self.assertNotIn('120', str(self.inputs))
        self.assertEqual(self.states[1]['current_ask'], 100)
        self.assertIsNone(final['stars'])
        self.assertEqual(final['review'], '')
        self.assertAlmostEqual(final['accept']['min_hours_left'], 2 + 5 / 60)
        self.assertLessEqual(self.instances[0].deadline - before, 241)
        self.assertTrue(self.instances[0].closed)
        memory = Memory(self.md)
        order_event = memory.events('order_seen')[0]['data']
        self.assertNotIn('mandates', order_event)
        self.assertNotIn('nonce', order_event)
        event = memory.events('negotiation')[0]['data']
        self.assertEqual(event['outcome'], 'receipt_pending')
        self.assertEqual(event['receipt'], self.received)
        self.assertEqual(event['sku'], 'F001')

    def test_only_exact_acceptable_positive_stock(self):
        self.shops[0]['offers'] = [dict(sku='milk', qty=20), dict(sku='F001', qty=0),
                                   dict(sku='F001', qty=True), dict(sku='F001', qty=-1)]
        self.write()
        final = self.execute()
        self.assertIsNone(final['receipt'])
        self.assertEqual(self.states, [])
        self.assertEqual(self.inputs, [])

    def test_category_receipt_is_preserved_and_reused_without_rewriting_signature(self):
        self.received['item'] = self.order['item']
        original = copy.deepcopy(self.received)
        first = self.execute()
        self.assertIs(first['receipt'], self.received)
        self.assertEqual(first['receipt'], original)
        requests, decisions = list(self.inputs), len(self.states)
        self.assertEqual(self.execute(), first)
        self.assertEqual(self.inputs, requests)
        self.assertEqual(len(self.states), decisions)
        with Memory(self.md) as memory:
            self.assertEqual(memory.get('negotiation', self.order['order'])['data']['receipt'], original)

    def test_other_order_acceptable_sku_receipt_is_preserved_on_live_and_replay_paths(self):
        self.order['acceptable'].append('F002')
        self.received['item'] = 'F002'
        self.write()
        first = self.execute()
        self.assertIs(first['receipt'], self.received)
        requests = list(self.inputs)
        self.assertEqual(self.execute(), first)
        self.assertEqual(self.inputs, requests)

    def test_category_receipt_does_not_bypass_price_task_or_signature_guards(self):
        self.received['item'] = self.order['item']
        original = copy.deepcopy(self.received)
        for index, (field, value) in enumerate([('price', 101), ('price', 121),
                                               ('task', 'other'), ('sig_seller', '')]):
            with self.subTest(field=field, value=value):
                self.order['order'] = 'category-guard-%s' % index
                self.write()
                self.received = copy.deepcopy(original)
                self.received[field] = value
                self.assertIsNone(self.execute()['receipt'])

    def test_replay_rejects_unallowed_selected_sku_or_receipt_item(self):
        for index, (selected_sku, signed_item) in enumerate([('F002', 'milk'), ('F001', 'F002'),
                                                            ('F001', 'food>milk')]):
            with self.subTest(selected_sku=selected_sku, signed_item=signed_item):
                self.order['order'] = 'replay-guard-%s' % index
                self.write()
                original = dict(self.received, item=signed_item)
                with Memory(self.md) as memory:
                    memory.record('order_seen', self.order['order'], sanitized_order(self.order))
                    memory.record('negotiation', self.order['order'],
                        dict(order=self.order['order'], shop='Example', shop_url=self.shops[0]['url'],
                             sku=selected_sku, budget=120, outcome='receipt_pending',
                             receipt=original, task_id='task-1'))
                self.assertIsNone(self.execute()['receipt'])
        self.assertEqual(self.inputs, [])
        self.assertEqual(self.states, [])

    def test_wire_legacy_exact_item_default_remains_strict(self):
        original = dict(self.received, item='milk')
        completed = task('TASK_STATE_COMPLETED', data=original)
        with self.assertRaises(ReceiptError):
            validate_receipt(completed, 'F001', 120, 100)
        self.assertIs(validate_receipt(completed, 'F001', 120, 100,
                                       allowed_items=['milk', 'F001']), original)

    def test_repeated_order_reuses_pending_receipt_without_negotiation(self):
        first = self.execute()
        requests = list(self.inputs)
        decisions = len(self.states)
        self.assertEqual(self.execute(), first)
        self.assertEqual(self.inputs, requests)
        self.assertEqual(len(self.states), decisions)
        memory = Memory(self.md)
        memory.record('payment', 'order-1', dict(order='order-1', paid=False, reason='Checkout failed'))
        self.assertIsNone(self.execute()['receipt'])
        self.assertEqual(self.inputs, requests)

    def test_reused_order_id_with_changed_details_cannot_negotiate(self):
        self.execute()
        requests = list(self.inputs)
        self.order['use_within_hours'] = 3
        self.write()
        self.assertIsNone(self.execute()['receipt'])
        self.assertEqual(self.inputs, requests)

    def test_offer_guard_applies_even_if_injected_model_skips_validator(self):
        def unsafe(system, state, validator, *args):
            if 'candidates' in state:
                return self.model(system, state, validator, *args)
            return dict(action='offer', price=121, summary='Invalid fixture')
        self.assertIsNone(self.execute(decision=unsafe)['receipt'])
        self.assertEqual(len(self.inputs), 1)
        self.assertEqual(Memory(self.md).events('negotiation')[0]['data']['outcome'], 'error')

    def test_receipt_identity_signature_and_offer_guards(self):
        for index, (field, value) in enumerate([('item', 'F002'), ('task', 'other'), ('price', 101),
                             ('price', True), ('sig_seller', ''), ('task', None)]):
            with self.subTest(field=field, value=value):
                self.order['order'] = 'receipt-case-%s' % index
                self.write()
                original = copy.deepcopy(self.received)
                self.received[field] = value
                self.assertIsNone(self.execute()['receipt'])
                self.received = original

    def test_model_failure_has_no_negative_public_rating_or_retry(self):
        (self.md / 'policy.json').write_text(json.dumps({'fallback_enabled': False}))
        def failure(*args):
            raise TimeoutError('Our model timed out')
        final = self.execute(decision=failure)
        self.assertIsNone(final['stars'])
        self.assertEqual(final['review'], '')
        self.assertEqual(self.inputs, [])

    def test_expired_order_never_contacts_model_or_shop(self):
        self.order['expires'] = time.time() - 1
        self.write()
        self.execute()
        self.assertEqual(self.states, [])
        self.assertEqual(self.inputs, [])

    def test_offer_limit_leaves_no_receipt_and_closes_wire(self):
        fixture = self

        class PersistentWire(self.wire):
            def send(self, text, task_id=None):
                fixture.inputs.append((text, task_id))
                return task('TASK_STATE_INPUT_REQUIRED', 'Counteroffer: 110.')

        final = self.execute(wire=PersistentWire)
        self.assertIsNone(final['receipt'])
        self.assertEqual(len(self.inputs), 7)
        self.assertTrue(self.instances[0].closed)
        self.assertEqual(Memory(self.md).events('negotiation')[0]['data']['reason'], 'turn_limit')

    def test_changed_task_id_is_rejected_before_another_offer(self):
        fixture = self

        class ChangedWire(self.wire):
            def send(self, text, task_id=None):
                fixture.inputs.append((text, task_id))
                return task('TASK_STATE_INPUT_REQUIRED', 'Asking 100.',
                            task_id='other' if task_id else 'task-1')

        self.assertIsNone(self.execute(wire=ChangedWire)['receipt'])
        self.assertEqual(len(self.inputs), 2)

    def test_fixed_order_cli_and_live_guard(self):
        with self.assertRaises(ValueError):
            load_order(self.md / 'order.json', 'milk', 121)
        with self.assertRaises(ValueError):
            load_order(self.md / 'order.json', 'other', 120)
        with patch.dict('os.environ', {'R3_ENABLE_LIVE': '0'}), self.assertRaises(RuntimeError):
            run(self.md / 'shops.json', 'milk', 120, self.md / 'order.json', self.md)

    def test_unknown_numbers_are_not_prices(self):
        for text in ['Warranty 24 hours', 'G00045, grade B, 48 h left', 'No price yet',
                     'Ask 10; price 12', 'Counteroffer: 12.5']:
            with self.subTest(text=text):
                self.assertIsNone(explicit_ask(text))
        self.assertEqual(explicit_ask('Counteroffer: 110. Warranty 72 hours.'), 110)
        self.assertEqual(explicit_ask('100.0'), 100)

    def test_bounded_candidates_retrieve_relevant_memory(self):
        memory = Memory(self.md)
        memory.record('settlement', '1', dict(shop='Example', sku='F001', order='older',
            shop_url=self.shops[0]['url'], outcome='settled', price=90))
        candidates = candidates_for(self.shops, self.order, self.catalog, memory)
        self.assertEqual(candidates[0]['list_price'], 150)
        self.assertTrue(candidates[0]['personal_records'])
        self.assertEqual(len(candidates_for(self.shops * 0, self.order, self.catalog, memory)), 0)
        with self.assertRaises(ValueError):
            validate_choice(dict(shop_name='Example', sku='wrong', reason='x'), candidates)

    def test_many_shops_heavy_history_has_bounded_diverse_selection_context(self):
        self.order['acceptable'] = ['F001', 'F002'] + ['F%05d' % n for n in range(1000)]
        self.catalog['F002'] = dict(self.catalog['F001'], sku='F002')
        self.shops = [dict(name='Example' if n == 0 else 'Shop-%02d' % n,
                          url='http://127.0.0.1:%s' % (10000 + n),
                          reviews=[dict(stars=4, review='Public observation. ' * 100)] * 10,
                          offers=[dict(sku=sku, name='Milk', qty=2) for sku in ('F001', 'F002')])
                      for n in range(60)]
        with Memory(self.md) as memory:
            for shop in self.shops:
                for n in range(6):
                    memory.record('settlement', '%s-%s' % (shop['name'], n),
                        dict(shop=shop['name'], sku='F001', budget=987654321,
                             order_details=dict(budget=987654321, acceptable=['x'] * 1000),
                             receipt=dict(item='F001', price=90 + n, sig_seller='blob' * 1000),
                             settled=dict(sku='F001', grade='B', value=130, ledger=n),
                             review=dict(stars=4, review='Private historical budget 987654321. ' * 100)),
                        ts=100 + n)
        self.write()
        self.execute()
        state = self.states[0]
        serialized = json.dumps(state, ensure_ascii=False)
        self.assertLessEqual(len(serialized), 36000)
        self.assertEqual(len(state['candidates']), 48)
        self.assertEqual(len({c['shop_name'] for c in state['candidates']}), 48)
        self.assertTrue(all(c['sku'] == 'F001' for c in state['candidates']))
        self.assertTrue(all(c['personal_records'] for c in state['candidates']))
        self.assertTrue(all(c['personal_records'][0]['price'] == 95 for c in state['candidates']))
        self.assertEqual(state['order']['budget'], 120)
        self.assertNotIn('987654321', serialized)
        self.assertNotIn('sig_seller', serialized)
        self.assertNotIn('acceptable', state['order'])
        self.assertNotIn('120', str(self.inputs))
        self.assertNotIn('987654321', str(self.inputs))
        self.assertEqual(len(Memory(self.md).events('settlement')), 360)

    def test_goods_warranty_is_configurable(self):
        order = dict(self.order, kind='goods')
        policy = acceptance(order, {'warranty_hours': 12})
        self.assertEqual(policy['warranty_hours'], 12)
        self.assertEqual(policy['min_hours_left'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
