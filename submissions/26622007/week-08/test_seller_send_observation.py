"""Public Seller.execute send seam; synthetic URL keys, isolated stock and RPC."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from a2a.types import Message
import seller
import seller_send_observation as observation
import shop_memory
from shop_stock import StockBook


class SendObservation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for patch in (mock.patch.dict(os.environ, MARKET_DIR=str(self.root / 'market')),
                      mock.patch.object(shop_memory, 'MEMORY', str(self.root / 'memory.json')),
                      mock.patch.object(seller, 'load_shop_key', return_value=None),
                      mock.patch.object(seller, 'log')):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(seller.flush_memory)
        self.task = 'https://fixture.invalid/tasks/12345678-aaaa'
        self.unit = 'https://fixture.invalid/units/12345678-aaaa'
        self.deal = dict(id='Gb2392fa9', unit=self.unit, name='fixture item', kind='food',
                         reserve=8042, ask=13295, open=13706, rounds=1, warranty=None,
                         history=[('you asked', 13295)])
        self.inventory = [dict(unit=self.unit, sku='Gb2392fa9', grade='A')]
        self.book = StockBook(load=lambda: dict(round=3, units=self.inventory))
        self.shop = seller.Seller(None, 'fixture', stock=self.book)
        self.calls = []

    def rows(self):
        path = self.root / 'market/measure/seller_send.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_turn(self, *, task=None, unit=None, fail=None, error=None, probe=None):
        task, unit = task or self.task, unit or self.unit
        deal = copy.deepcopy(self.deal)
        deal['unit'] = unit
        self.inventory[:] = [dict(unit=unit, sku=deal['id'], grade='A')]
        self.shop.open[task] = deal
        async def sent(phase, *args, **kwargs):
            self.calls.append(phase)
            self.assertEqual(self.book.agreed[unit]['task'], task)
            if probe:
                probe(phase)
            if phase == fail:
                raise error
        up = SimpleNamespace(new_agent_message=lambda **kw: kw,
                             add_artifact=lambda **kw: sent('artifact', **kw),
                             complete=lambda *args: sent('complete', *args),
                             reject=mock.AsyncMock(), failed=mock.AsyncMock())
        ctx = SimpleNamespace(current_task=SimpleNamespace(id=task, context_id='fixture-context'),
                              message=Message(role='ROLE_USER', message_id='fixture-message',
                                              parts=[seller.new_text_part('13295')]))
        with mock.patch.object(seller, 'TaskUpdater', return_value=up):
            result = asyncio.run(self.shop.execute(ctx, None))
        self.assertFalse(up.reject.called)
        self.assertFalse(up.failed.called)
        seller.flush_memory()
        return result

    def test_public_send_records_before_and_returned_for_both_awaits(self):
        def probe(phase):
            self.assertEqual(self.rows()[-1]['phase'], phase + '_before')
        self.assertIsNone(self.run_turn(probe=probe))
        self.assertEqual(self.calls, ['artifact', 'complete'])
        self.assertEqual([r['phase'] for r in self.rows()],
                         ['artifact_before', 'artifact_returned', 'complete_before', 'complete_returned'])
        self.assertNotIn(self.task, self.shop.open)
        self.assertEqual(json.loads((self.root / 'memory.json').read_text())['negotiations'][-1]['result'], 'agreed')

    def test_each_rpc_error_keeps_identity_and_stops_following_send_and_memory(self):
        for phase in ('artifact', 'complete'):
            with self.subTest(phase=phase):
                error = RuntimeError('private@example.invalid response https://private.invalid')
                prior = len(self.rows())
                self.calls.clear()
                with self.assertRaises(RuntimeError) as caught:
                    self.run_turn(fail=phase, error=error)
                self.assertIs(caught.exception, error)
                rows = self.rows()[prior:]
                self.assertEqual(rows[-1]['phase'], phase + '_raised')
                self.assertEqual(rows[-1]['error_enum'], 'exception')
                self.assertEqual(self.calls, ['artifact'] if phase == 'artifact' else ['artifact', 'complete'])
                self.assertIn(self.task, self.shop.open)
                self.assertEqual(self.book.holder(self.unit), self.task)
                self.assertEqual(self.shop._task_turns, {})
                self.assertFalse((self.root / 'memory.json').exists())

    def test_each_rpc_cancellation_propagates_same_instance_without_release(self):
        for phase in ('artifact', 'complete'):
            with self.subTest(phase=phase):
                error = asyncio.CancelledError('private cancellation')
                self.calls.clear()
                with self.assertRaises(asyncio.CancelledError) as caught:
                    self.run_turn(fail=phase, error=error)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.rows()[-1]['phase'], phase + '_raised')
                self.assertEqual(self.rows()[-1]['error_enum'], 'cancelled')
                self.assertEqual(self.calls, ['artifact'] if phase == 'artifact' else ['artifact', 'complete'])
                self.assertEqual(self.book.holder(self.unit), self.task)
                self.assertIn(self.task, self.shop.open)
                self.assertFalse((self.root / 'memory.json').exists())

    def test_append_failure_keeps_success_error_and_cancellation_contracts(self):
        real_open = os.open
        def faulting(operation):
            def fail(*args, **kwargs):
                if operation == 'open' and not str(args[0]).endswith('seller_send.jsonl'):
                    return real_open(*args, **kwargs)
                raise PermissionError('private filesystem details')
            return fail
        for operation in ('open', 'write', 'fchmod'):
            for phase, error in ((None, None), ('artifact', RuntimeError('private')),
                                 ('complete', asyncio.CancelledError('private'))):
                with self.subTest(operation=operation, phase=phase):
                    self.calls.clear()
                    with mock.patch.object(observation.os, operation, side_effect=faulting(operation)):
                        if error is None:
                            self.assertIsNone(self.run_turn())
                        else:
                            with self.assertRaises(type(error)) as caught:
                                self.run_turn(fail=phase, error=error)
                            self.assertIs(caught.exception, error)
                    self.assertEqual(self.calls, ['artifact'] if phase == 'artifact' else ['artifact', 'complete'])

    def test_private_fields_excluded_and_existing_permissions_repaired(self):
        private = 'private@example.invalid https://private.invalid/account secret warranty'
        self.deal.update(name=private, warranty=private, account=private, holder=private)
        self.run_turn()
        path = self.root / 'market/measure/seller_send.jsonl'
        path.chmod(0o644)
        self.run_turn()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for row in self.rows():
            self.assertEqual(set(row), {'at', 'phase', 'error_enum', 'sku', 'attempt', 'task_sha256', 'unit_sha256'})
            self.assertEqual(row['task_sha256'], hashlib.sha256(self.task.encode()).hexdigest())
            self.assertEqual(row['unit_sha256'], hashlib.sha256(self.unit.encode()).hexdigest())
            self.assertRegex(row['attempt'], '^[0-9a-f]{32}$')
            for value in (private, self.task, self.unit, '13295'):
                self.assertNotIn(value, json.dumps(row))

    def test_full_keys_and_repeated_attempts_keep_task_and_unit_boundaries(self):
        keys = [(self.task, self.unit), (self.task, self.unit),
                (self.task + '-other', self.unit), (self.task, self.unit + '-other')]
        for task, unit in keys:
            self.book = StockBook(load=lambda: dict(round=3, units=self.inventory))
            self.shop = seller.Seller(None, 'fixture', stock=self.book)
            self.run_turn(task=task, unit=unit)
        rows = self.rows()
        groups = [rows[i:i + 4] for i in range(0, len(rows), 4)]
        self.assertEqual(len(groups), 4)
        self.assertEqual(len({g[0]['attempt'] for g in groups}), 4)
        for group in groups:
            self.assertEqual(len({r['attempt'] for r in group}), 1)
        self.assertEqual(groups[0][0]['task_sha256'], groups[1][0]['task_sha256'])
        self.assertNotEqual(groups[0][0]['task_sha256'], groups[2][0]['task_sha256'])
        self.assertEqual(groups[0][0]['unit_sha256'], groups[2][0]['unit_sha256'])
        self.assertNotEqual(groups[0][0]['unit_sha256'], groups[3][0]['unit_sha256'])

    def test_symlink_is_not_followed(self):
        target = self.root / 'protected'
        target.write_text('unchanged')
        path = self.root / 'market/measure/seller_send.jsonl'
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        self.run_turn()
        self.assertEqual(target.read_text(), 'unchanged')

    def test_invalid_observation_identity_does_not_change_send(self):
        self.deal['id'] = 'private@example.invalid'
        self.run_turn()
        self.assertEqual(self.calls, ['artifact', 'complete'])
        self.assertEqual(self.rows(), [])

    def test_cached_current_shape_replay_is_not_a_historical_cause_reconstruction(self):
        cached = json.loads((Path(__file__).parent / 'testdata/seller_terminal_cached.json').read_text())
        self.task, self.unit = cached['task'], cached['unit']
        self.deal.update(id=cached['sku'], unit=self.unit, name=cached['item'],
                         cost=cached['cost'], reserve=cached['reserve'], open=cached['open'],
                         ask=cached['asks'][-1], arm=cached['arm'], warranty=cached['warranty'],
                         history=[('you asked', p) for p in cached['asks']])
        self.run_turn()
        self.assertEqual(self.rows()[-1]['phase'], 'complete_returned')
        self.assertEqual(self.rows()[-1]['sku'], 'Gef4d3036')

    def test_actual_task_cancel_while_rpc_waits_preserves_original_contract(self):
        for phase in ('artifact', 'complete'):
            with self.subTest(phase=phase):
                entered = asyncio.Event()
                async def waiting(*args, **kwargs):
                    entered.set()
                    await asyncio.Future()
                async def scenario():
                    deal = copy.deepcopy(self.deal)
                    self.shop.open[self.task] = deal
                    up = SimpleNamespace(new_agent_message=lambda **kw: kw,
                                         add_artifact=mock.AsyncMock(), complete=mock.AsyncMock())
                    setattr(up, 'add_artifact' if phase == 'artifact' else 'complete', waiting)
                    ctx = SimpleNamespace(current_task=SimpleNamespace(id=self.task, context_id='fixture-context'),
                                          message=Message(role='ROLE_USER', message_id='fixture-message',
                                                          parts=[seller.new_text_part('13295')]))
                    with mock.patch.object(seller, 'TaskUpdater', return_value=up):
                        task = asyncio.create_task(self.shop.execute(ctx, None))
                        await entered.wait()
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    if phase == 'artifact':
                        up.complete.assert_not_awaited()
                asyncio.run(scenario())
                self.assertEqual(self.rows()[-1]['phase'], phase + '_raised')
                self.assertEqual(self.rows()[-1]['error_enum'], 'cancelled')
                self.assertIn(self.task, self.shop.open)
                self.assertEqual(self.book.holder(self.unit), self.task)
                self.assertFalse((self.root / 'memory.json').exists())


if __name__ == '__main__':
    unittest.main()
