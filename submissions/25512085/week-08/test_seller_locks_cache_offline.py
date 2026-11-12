"""Synthetic receipts, isolated state and deterministic lock contention only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import seller
import seller_cash_policy as policy
import store
import test_seller_food_offline as fixture


class ReservationTests(unittest.TestCase):
    setUp = fixture.FoodSafetyTests.setUp
    turn = fixture.FoodSafetyTests.turn
    receipt = fixture.FoodSafetyTests.receipt

    def stock(self):
        self.inventory['units'] = [dict(unit='unit-1', sku='G021aae64', name='sofa',
            category='goods>sofa', kind='goods', grade='C', cost=30000,
            list_price=100000, supplied_at=self.now-3600)]
        self.catalog['G021aae64'] = dict(kind='goods', list_price=100000)
        self.stack.enter_context(patch.object(seller, 'queue_inbound_inquiry', lambda *a, **k: None))

    def close(self, task='task-1'):
        return self.receipt(self.turn('G021aae64 offer 999999', task))

    def test_restart_and_old_120_seconds_do_not_release(self):
        self.stock()
        self.close()
        self.now += 121
        self.agent = seller.Seller(0)
        self.assertIn('unit-1', self.agent.committed_units)
        events = self.turn('G021aae64 offer 999999', 'other')
        self.assertNotIn('receipt', [k for k, _ in events])
        self.assertIn('unit-1', self.agent.committed_units)

    def test_inventory_disappearance_releases_disk_and_memory(self):
        self.stock()
        self.close()
        self.inventory['units'] = []
        self.agent.cleanup_reservations(self.inventory, self.now)
        self.assertFalse(self.agent.committed_units)
        self.assertFalse(seller.Seller(0).committed_units)

    def test_long_cap_releases_without_refreshing_on_read(self):
        self.stock()
        self.close()
        self.now += 899
        self.agent.cleanup_reservations(self.inventory, self.now)
        self.assertTrue(self.agent.committed_units)
        self.now += 2
        self.agent.cleanup_reservations(self.inventory, self.now)
        self.assertFalse(seller.Seller(0).committed_units)

    def test_failed_inventory_read_does_not_prove_settlement(self):
        self.stock()
        self.close()
        self.agent.cleanup_reservations({}, self.now)
        self.assertTrue(seller.Seller(0).committed_units)

    def test_signing_and_artifact_failure_roll_back_both_paths(self):
        self.stock()
        for negotiated in (False, True):
            for stage in ('sign', 'artifact'):
                with self.subTest(negotiated=negotiated, stage=stage):
                    if negotiated:
                        self.turn('G021aae64')
                    target, attr = ((seller, 'sign_receipt') if stage == 'sign' else (fixture.FakeUpdater, 'add_artifact'))
                    with patch.object(target, attr, side_effect=RuntimeError('pre-publication')):
                        with self.assertRaises(RuntimeError):
                            self.turn('999999' if negotiated else 'G021aae64 offer 999999')
                    self.assertFalse(self.agent.open)
                    self.assertFalse(self.agent.committed_units)
                    self.assertFalse(seller.Seller(0).committed_units)

    def test_completion_failure_keeps_both_paths_reserved(self):
        self.stock()
        for negotiated in (False, True):
            with self.subTest(negotiated=negotiated):
                if negotiated:
                    self.turn('G021aae64')
                with patch.object(fixture.FakeUpdater, 'complete', side_effect=RuntimeError('after artifact')):
                    with self.assertRaises(RuntimeError):
                        self.turn('999999' if negotiated else 'G021aae64 offer 999999')
                self.assertFalse(self.agent.open)
                self.assertIn('unit-1', seller.Seller(0).committed_units)
                self.now += 901
                self.agent.cleanup_reservations(self.inventory, self.now)

    def test_activity_sliding_ttl_and_absolute_cap(self):
        self.stock()
        self.turn('G021aae64')
        for _ in range(3):
            self.now += 80
            self.turn('hello')
            self.assertEqual(self.agent.open['task-1']['last_activity_at'], self.now)
        self.now += 91
        self.agent.cleanup_reservations(self.inventory, self.now)
        self.assertFalse(self.agent.open)
        self.turn('G021aae64')
        for _ in range(12):
            self.now += 80
            self.agent.cleanup_reservations(self.inventory, self.now)
            if self.agent.open:
                self.agent.open['task-1']['last_activity_at'] = self.now
        self.assertFalse(self.agent.open)

    def test_request_catalog_and_hub_read_once(self):
        self.stock()
        self.turn('G021aae64')
        with patch.object(seller, 'load_catalog', return_value=self.catalog) as cat, patch.object(seller, 'load_hub', return_value={}) as hub:
            self.turn('999999')
        self.assertEqual(cat.call_count, 1)
        self.assertEqual(hub.call_count, 1)


class LockAndCacheTests(unittest.TestCase):
    def test_timeout_never_enters_or_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'hub.json'
            path.write_text('{"sentinel":1}')
            before = path.read_bytes()
            with store.file_lock(path):
                with self.assertRaises(TimeoutError):
                    with store.mutate_hub(path, timeout=0):
                        self.fail('must not yield without lock')
            self.assertEqual(path.read_bytes(), before)

    def test_migration_lock_contention_returns_read_only_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'hub.json'
            path.write_text('{"sentinel":1}')
            before = path.read_bytes()
            with store.file_lock(path), patch.object(store.time, 'sleep', side_effect=AssertionError('event-loop blocked')):
                self.assertEqual(store.load_hub(path)['sentinel'], 1)
            self.assertEqual(path.read_bytes(), before)

    def test_analytics_timeout_skips_with_diagnostic(self):
        with patch.object(store, 'mutate_hub', side_effect=TimeoutError('busy')), patch('builtins.print') as diagnostic:
            self.assertIsNone(store.record_inbound_inquiry('x', True))
            self.assertIsNone(store.record_buyer_order('x'))
            self.assertTrue(diagnostic.called)

    def test_catalog_cache_invalidates_mtime_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory/'catalog.json'
            path.write_text('{"a":{"list_price":100}}')
            with patch.object(Path, 'read_text', autospec=True, wraps=Path.read_text) as read:
                # Use a forwarding side effect (MagicMock wraps alone loses binding).
                read.side_effect = lambda p, *a, **k: original_read(p, *a, **k)
                self.assertEqual(store.load_catalog(directory), store.load_catalog(directory))
                self.assertEqual(read.call_count, 1)
            stamp = path.stat().st_mtime_ns
            path.write_text('{"a":{"list_price":200}}')
            os.utime(path, ns=(stamp+1000000000, stamp+1000000000))
            self.assertEqual(store.load_catalog(directory)['a']['list_price'], 200)
            path.write_text('{"a":{"list_price":3000}}')
            os.utime(path, ns=(stamp+1000000000, stamp+1000000000))
            self.assertEqual(store.load_catalog(directory)['a']['list_price'], 3000)

    def test_policy_cached_quote_equivalence_and_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(policy, 'CONFIG_PATH', Path(tmp)/'policy.json'):
            policy.CONFIG_PATH.write_text('{"enabled":true}')
            unit = dict(sku='x', kind='goods', grade='C', cost=1000, supplied_at=100)
            hub = {'demand_radar': {'sku_comparables': {'x': [dict(ts=100, amount=2000, grade='C', warranty_hours=0)]*5}}}
            first = policy.quote(unit, hub, {}, 100)
            with patch.object(Path, 'read_text', side_effect=AssertionError('repeat parse')):
                self.assertEqual(policy.quote(unit, hub, {}, 100), first)
            old = policy.CONFIG_PATH.stat().st_mtime_ns
            policy.CONFIG_PATH.write_text('{"enabled":false}')
            os.utime(policy.CONFIG_PATH, ns=(old+1000000000, old+1000000000))
            self.assertIsNone(policy.quote(unit, hub, {}, 100))
            policy.CONFIG_PATH.write_text('{"enabled":true, "grade_warranty_hours":{"C":6}}')
            self.assertEqual(policy.warranty_hours('C'), 6)


original_read = Path.read_text
