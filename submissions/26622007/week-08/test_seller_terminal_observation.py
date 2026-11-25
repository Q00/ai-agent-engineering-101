"""Public paid/unpaid callback contract; no market or model calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import seller
import shop_memory
import seller_terminal_observation as observation
from shop_stock import StockBook


class TerminalObservation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, MARKET_DIR=str(self.root / '.market'))
        self.env.start()
        self.addCleanup(self.env.stop)
        memory = mock.patch.object(shop_memory, 'MEMORY', str(self.root / 'memory/seller_test_memory.json'))
        memory.start()
        self.addCleanup(memory.stop)
        cached = json.loads((Path(__file__).parent / 'testdata/seller_terminal_cached.json').read_text())
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        self.shop = seller.Seller(None, 'fixture', stock=StockBook(load=lambda: {'round': 3, 'units': []}))
        self.info = {'task': cached['task'], 'price': cached['price'], 'deal': {
            'id': cached['sku'], 'unit': cached['unit'], 'name': cached['item'],
            **{k: cached[k] for k in ('cost', 'reserve', 'open', 'arm', 'warranty')},
            'history': [('you asked', p) for p in cached['asks']] + [('buyer offered', p) for p in cached['offers']]}}

    def tearDown(self):
        seller.flush_memory()

    def rows(self):
        return [json.loads(x) for x in (self.root / '.market/measure/seller_terminal.jsonl').read_text().splitlines()]

    def test_unpaid_callback_adds_joinable_reason_without_changing_memory(self):
        original = copy.deepcopy(self.info)
        self.shop.unpaid(self.info, 'expired before payment')
        seller.flush_memory()
        row, = self.rows()
        memory = self.memory()[-1]
        self.assertEqual(row['task_sha256'], hashlib.sha256(memory['task'].encode()).hexdigest())
        self.assertEqual(row['unit_sha256'], hashlib.sha256(memory['unit'].encode()).hexdigest())
        self.assertEqual((row['sku'], row['local_outcome'], row['reason_enum'], row['price']),
                         ('Gef4d3036', 'unpaid', 'expired', 13295))
        self.assertEqual(memory['result'], 'unpaid')
        self.assertEqual(self.info, original)

    def memory(self):
        seller.flush_memory()
        return json.loads(Path(shop_memory.MEMORY).read_text())['negotiations']

    def test_timeout_then_late_paid_and_reused_unit_keep_distinct_tasks(self):
        self.shop.unpaid(self.info, 'not paid within 90 s (watched 10 min more)')
        self.shop.paid(dict(self.info, late=True))
        self.shop.paid(dict(self.info, task='second-task'))
        rows = self.rows()
        self.assertEqual([x['local_outcome'] for x in rows], ['unpaid', 'paid_late', 'paid'])
        self.assertEqual(rows[0]['reason_enum'], 'timeout')
        self.assertEqual(rows[1]['reason_enum'], 'local_inventory_observation')
        self.assertEqual(rows[0]['task_sha256'], rows[1]['task_sha256'])
        self.assertNotEqual(rows[1]['task_sha256'], rows[2]['task_sha256'])
        self.assertEqual(len({x['unit_sha256'] for x in rows}), 1)
        self.assertEqual([x['result'] for x in self.memory()], ['unpaid', 'deal', 'deal'])

    def test_cancel_refusal_is_combined_and_unknown_text_never_saved(self):
        self.shop.unpaid(self.info, 'checkout cancelled or refused')
        secret = 'private@example.invalid https://example.invalid/account warranty secret'
        info = copy.deepcopy(self.info)
        info['deal'].update(name=secret, warranty=secret)
        self.shop.unpaid(info, secret)
        rows = self.rows()
        self.assertEqual([r['reason_enum'] for r in rows], ['checkout_cancel_or_refusal', 'unknown'])
        for row in rows:
            self.assertEqual(set(row), {'at', 'sku', 'task_sha256', 'unit_sha256', 'local_outcome', 'reason_enum', 'price'})
            self.assertIsInstance(row['at'], (int, float))
            for key in ('task_sha256', 'unit_sha256'):
                self.assertRegex(row[key], '^[0-9a-f]{64}$')
            self.assertNotIn(secret, json.dumps(row))
        self.assertEqual(rows[0]['price'], 13295)

    def test_filesystem_and_serialization_failures_preserve_original_callbacks(self):
        for operation, error in [('open', PermissionError('private')), ('write', OSError('disk full'))]:
            with self.subTest(operation=operation), mock.patch.object(observation.os, operation, side_effect=error):
                self.shop.unpaid(self.info, 'expired before payment')
                self.shop.paid(self.info)
        real_dumps = json.dumps
        def serialize(value, *args, **kwargs):
            if isinstance(value, dict) and 'local_outcome' in value:
                raise TypeError('private')
            return real_dumps(value, *args, **kwargs)
        with mock.patch.object(observation.json, 'dumps', side_effect=serialize):
            self.shop.unpaid(self.info, 'expired before payment')
            self.shop.paid(self.info)
        self.assertEqual([r['result'] for r in self.memory()], ['unpaid', 'deal'] * 3)

    def test_malformed_payload_keeps_original_exception_and_learning_order(self):
        bad = dict(self.info, price=float('nan'))
        self.shop.unpaid(bad, object())
        self.assertFalse((self.root / '.market/measure/seller_terminal.jsonl').exists())
        with self.assertRaises(KeyError):
            self.shop.paid({'deal': {}})
        self.assertEqual(len(self.memory()), 1)

    def test_explicit_state_and_single_append_write(self):
        state = self.root / 'explicit'
        writes = []
        real_write = os.write
        def write(fd, data):
            writes.append(len(data))
            return real_write(fd, data)
        with mock.patch.object(observation.os, 'write', side_effect=write):
            self.assertTrue(observation.observe(self.info, 'unpaid', 'expired before payment', state=state))
        self.assertEqual(len(writes), 1)
        self.assertLess(writes[0], 1024)
        self.assertEqual(len((state / 'measure/seller_terminal.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
