"""Offline request-boundary, replay, and signed-stock reconciliation regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import r3_seller as seller
from r3_seller_lifecycle import MAX_BODY, RequestJournal, packed
import test_r3_seller as fixtures
from test_r3_seller import send


def request(text, task=None, message_id=None):
    message = dict(role='ROLE_USER', messageId=message_id or uuid.uuid4().hex,
                   parts=[dict(text=text)])
    if task is not None:
        message['taskId'] = task
    return dict(jsonrpc='2.0', id=uuid.uuid4().hex, method='SendMessage', params=dict(message=message))


def dispatch(client, body):
    return client.post('/', headers={'A2A-Version': '1.0'}, json=body).json()


class SellerLifecycleTests(unittest.TestCase):
    setUp = fixtures.InventorySellerTests.setUp
    unit = fixtures.InventorySellerTests.unit
    write = fixtures.InventorySellerTests.write
    client_for = fixtures.InventorySellerTests.client_for
    decision = staticmethod(fixtures.InventorySellerTests.decision)
    assert_state = fixtures.InventorySellerTests.assert_state
    assert_no_receipt = fixtures.InventorySellerTests.assert_no_receipt

    def slow(self, entered, release, delegate=None):
        def decide(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test did not release the offline decision')
            return (delegate or self.decision)(*args)
        return decide

    def test_concurrent_same_message_replays_one_create_and_survives_restart(self):
        entered, release = threading.Event(), threading.Event()
        self.choose.side_effect = self.slow(entered, release)
        body = request('G00001')
        duplicate = dict(body, id='second-rpc-id')
        other_app = self.client_for()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(dispatch, self.client, body)
            self.assertTrue(entered.wait(5))
            second = pool.submit(dispatch, other_app, duplicate)
            try:
                time.sleep(.06)
                self.assertFalse(second.done())
                self.assertEqual(self.choose.call_count, 1)
            finally:
                release.set()
            one, two = first.result(timeout=5), second.result(timeout=5)
        self.assertEqual(one['result'], two['result'])
        self.assertEqual(two['id'], 'second-rpc-id')
        self.assert_state(one['result']['task'], 'INPUT_REQUIRED')
        restarted = self.client_for()
        replay = dispatch(restarted, dict(body, id='after-restart'))
        self.assertEqual(replay['result'], one['result'])
        self.assertEqual(replay['id'], 'after-restart')
        self.assertEqual(self.choose.call_count, 1)

    def test_overlapping_different_offers_wait_for_sdk_cleanup(self):
        first = send(self.client, 'G00001')
        entered, release = threading.Event(), threading.Event()

        def choose(system, state, validator, *args):
            if state['incoming_offer'] == 90:
                entered.set()
                self.assertTrue(release.wait(5))
                return validator(dict(action='ask', price=105, summary='Offline counter'))
            return self.decision(system, state, validator, *args)

        self.choose.side_effect = choose
        with ThreadPoolExecutor(max_workers=2) as pool:
            counter = pool.submit(dispatch, self.client, request('90', first['id']))
            self.assertTrue(entered.wait(5))
            accepted = pool.submit(dispatch, self.client, request('105', first['id']))
            try:
                time.sleep(.06)
                self.assertFalse(accepted.done())
                self.assertEqual(self.choose.call_count, 2)
            finally:
                release.set()
            counter, accepted = counter.result(timeout=5), accepted.result(timeout=5)
        self.assert_state(counter['result']['task'], 'INPUT_REQUIRED')
        self.assert_state(accepted['result']['task'], 'COMPLETED')
        self.assertEqual(accepted['result']['task']['artifacts'][0]['parts'][0]['data']['price'], 105)
        self.assertEqual(self.choose.call_count, 3)

    def test_duplicate_accept_returns_original_receipt_after_quote_expiry(self):
        first = send(self.client, 'G00001')
        body = request('110', first['id'])
        entered, release = threading.Event(), threading.Event()
        self.choose.side_effect = self.slow(entered, release)
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(dispatch, self.client, body)
            self.assertTrue(entered.wait(5))
            two = pool.submit(dispatch, self.client, dict(body, id='retry'))
            try:
                time.sleep(.06)
                self.assertFalse(two.done())
            finally:
                release.set()
            one, two = one.result(timeout=5), two.result(timeout=5)
        self.assert_state(one['result']['task'], 'COMPLETED')
        self.assertEqual(one['result'], two['result'])
        self.now += 1000
        restarted = self.client_for()
        replay = dispatch(restarted, dict(body, id='late-retry'))
        self.assertEqual(replay['result'], one['result'])
        self.assertEqual(replay['id'], 'late-retry')
        self.assertEqual(self.choose.call_count, 2)

    def test_conflicting_reuse_of_message_id_does_not_mutate_task(self):
        body = request('G00001')
        first = dispatch(self.client, body)['result']['task']
        for altered in (request('G00002', message_id=body['params']['message']['messageId']),
                        request('110', first['id'], body['params']['message']['messageId'])):
            result = dispatch(self.client, altered)
            self.assertEqual(result['error']['code'], -32602)
            self.assertIn('different request content', result['error']['message'])
        self.assertEqual(self.choose.call_count, 1)
        self.assert_state(send(self.client, '110', first['id']), 'COMPLETED')

    def test_independent_tasks_can_run_while_one_decision_waits(self):
        self.inventory['units'].append(self.unit('u2', 'G00002'))
        self.write('inventory.json', self.inventory)
        entered, release = threading.Event(), threading.Event()

        def choose(system, state, validator, *args):
            if state['unit']['sku'] == 'G00001':
                entered.set()
                self.assertTrue(release.wait(5))
            return self.decision(system, state, validator, *args)

        self.choose.side_effect = choose
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(dispatch, self.client, request('G00001'))
            self.assertTrue(entered.wait(5))
            try:
                two = pool.submit(dispatch, self.client, request('G00002')).result(timeout=3)
                self.assert_state(two['result']['task'], 'INPUT_REQUIRED')
            finally:
                release.set()
            self.assert_state(one.result(timeout=5)['result']['task'], 'INPUT_REQUIRED')

    def test_cancel_waits_until_active_counter_finishes(self):
        first = send(self.client, 'G00001')
        entered, release = threading.Event(), threading.Event()
        self.choose.side_effect = self.slow(entered, release)
        cancel = dict(jsonrpc='2.0', id='cancel-rpc', method='CancelTask', params=dict(id=first['id']))
        with ThreadPoolExecutor(max_workers=2) as pool:
            counter = pool.submit(dispatch, self.client, request('90', first['id']))
            self.assertTrue(entered.wait(5))
            canceled = pool.submit(dispatch, self.client, cancel)
            try:
                time.sleep(.06)
                self.assertFalse(canceled.done())
            finally:
                release.set()
            self.assert_state(counter.result(timeout=5)['result']['task'], 'INPUT_REQUIRED')
            result = canceled.result(timeout=5)['result']
            self.assert_state(result.get('task', result), 'CANCELED')
        self.choose.side_effect = self.decision
        self.assert_state(send(self.client, 'G00001'), 'INPUT_REQUIRED')

    def test_nonblocking_and_oversized_requests_cannot_reach_sdk(self):
        body = request('G00001')
        body['params']['configuration'] = dict(returnImmediately=True)
        self.assertEqual(dispatch(self.client, body)['error']['code'], -32602)
        for configuration in (dict(return_immediately=True), dict(blocking=False)):
            body['params']['configuration'] = configuration
            self.assertEqual(dispatch(self.client, body)['error']['code'], -32602)
        for method in ('SendStreamingMessage', 'message/stream', 'send_streaming_message', 'sendStreamingMessage'):
            streaming = dict(request('G00001'), method=method)
            self.assertEqual(dispatch(self.client, streaming)['error']['code'], -32601)
        aliases = request('G00001')
        aliases['params']['message']['taskId'] = 'first-task'
        aliases['params']['message']['task_id'] = 'other-task'
        self.assertEqual(dispatch(self.client, aliases)['error']['code'], -32602)
        result = self.client.post('/', headers={'A2A-Version': '1.0'},
                                  content=b' ' * (MAX_BODY + 1))
        self.assertEqual(result.status_code, 413)
        self.choose.assert_not_called()

    def test_task_lock_timeout_before_execution_can_retry_same_message(self):
        first = send(self.client, 'G00001')
        body = request('110', first['id'])
        original = RequestJournal.lock

        @asynccontextmanager
        async def timeout_task(journal, key):
            if key.startswith('task:'):
                raise TimeoutError('Injected task lock timeout before SDK execution')
            async with original(journal, key):
                yield

        with patch.object(RequestJournal, 'lock', timeout_task):
            failed = dispatch(self.client, body)
        self.assertEqual(failed['error']['code'], -32001)
        self.assertEqual(self.choose.call_count, 1)
        accepted = dispatch(self.client, body)
        self.assert_state(accepted['result']['task'], 'COMPLETED')
        self.assertEqual(self.choose.call_count, 2)

    def test_unresolved_journal_attempt_is_not_executed_again(self):
        body = request('G00001')
        digest = hashlib.sha256(packed(dict(method='SendMessage', params=body['params'])).encode()).hexdigest()
        journal = RequestJournal(self.md)
        journal.start(body['params']['message']['messageId'], digest)
        result = dispatch(self.client, body)
        self.assertEqual(result['error']['code'], -32001)
        self.assertIn('unresolved outcome', result['error']['message'])
        self.choose.assert_not_called()

    def complete(self):
        first = send(self.client, 'G00001')
        final = send(self.client, '110', first['id'])
        self.assert_state(final, 'COMPLETED')
        return final['artifacts'][0]['parts'][0]['data']

    def record_settlement(self, seq, **changes):
        data = dict(source='public_ledger', seq=seq, shop='Round 3 Inventory Shop',
                    item='G00001', unit='u1', amount=110)
        data.update(changes)
        with seller.Memory(self.md) as memory:
            memory.record('market_settle', str(seq), data)

    def test_unpaid_receipt_quarantines_without_ttl_unlock_or_false_settlement(self):
        self.complete()
        executor = self.client.app.state.seller
        self.assertEqual(executor.reconcile()['signed_pending'], 1)
        self.now += 181
        self.write('inventory.json', self.inventory)
        with seller.Memory(self.md) as memory:
            memory.record('payment', 'other-buyer-order', dict(paid=False, unit='u1'))
        for seq, invalid in enumerate((dict(shop='another-shop'), dict(source='forum_claim'),
                                       dict(unit='another-unit'), dict(item='G00002'),
                                       dict(amount=109), dict(price=109), dict(task='another-task')), 1):
            self.record_settlement(seq, **invalid)
        report = executor.reconcile()
        self.assertEqual(report['quarantined'], 1)
        self.assertEqual(report['settled'], 0)
        self.assertEqual(report['blocked_in_inventory'], 1)
        self.assertFalse(report['safe_unlock_available'])
        self.assert_state(send(self.client, 'G00001'), 'REJECTED')
        self.assertEqual(json.loads((self.md / 'seller-stock-status.json').read_text())['quarantined'], 1)

    def test_exact_paid_ledger_marks_settled_but_absence_alone_does_not(self):
        receipt = self.complete()
        executor = self.client.app.state.seller
        self.write('inventory.json', dict(self.inventory, units=[]))
        report = executor.reconcile()
        self.assertEqual(report['quarantined'], 1)
        self.assertEqual(report['settled'], 0)
        self.record_settlement(123, task=receipt['task'], price=110)
        report = executor.reconcile()
        self.assertEqual(report['settled'], 1)
        self.assertEqual(report['blocked_in_inventory'], 0)
        self.assertEqual(report['units'][0]['settlement']['ledger'], 123)
        self.write('inventory.json', self.inventory)
        self.assert_state(send(self.client_for(), 'G00001'), 'REJECTED')

    def test_acceptance_receipt_survives_noncritical_memory_record_failure(self):
        first = send(self.client, 'G00001')
        original = seller.Memory.record

        def fail_accept(memory, kind, key, data, **kwargs):
            if kind == 'seller_action' and data['action'] == 'accept':
                raise sqlite3.OperationalError('Injected offline journal contention')
            return original(memory, kind, key, data, **kwargs)

        with patch.object(seller.Memory, 'record', fail_accept):
            final = send(self.client, '110', first['id'])
        self.assert_state(final, 'COMPLETED')
        report = self.client.app.state.seller.reconcile()
        self.assertEqual(report['signed_pending'], 1)

    def test_legacy_commitment_migrates_to_explicit_quarantine(self):
        with self.client.app.state.seller.reservations.transaction() as db:
            db.execute('INSERT INTO deals VALUES (?,?,?,?,?,?)',
                       ('old-task', 'u1', 'committed', self.now - 1000, None,
                        json.dumps(dict(sku='G00001', ask=110, rounds=1, summary='', recent=[]))))
        restarted = self.client_for()
        report = restarted.app.state.seller.reconcile()
        self.assertEqual(report['quarantined'], 1)
        self.assertEqual(report['units'][0]['reason'], 'legacy_receipt_not_recorded')
        self.assert_state(send(restarted, 'G00001'), 'REJECTED')


if __name__ == '__main__':
    unittest.main()

