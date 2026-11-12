"""Synthetic receipts, isolated state and deterministic lock contention only."""
import json
import os
from pathlib import Path
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

import seller
import seller_cash_policy as policy
import store
import test_seller_food_offline as fixture
import market_buyer as buyer
import restock
import review
import test_buyer_price_offline as buyer_fixture


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

    def test_food_followup_uses_one_catalog_snapshot(self):
        self.inventory['units'] = [fixture.FoodSafetyTests.food(self, 100)]
        self.stack.enter_context(patch.object(seller, 'queue_inbound_inquiry', lambda *a, **k: None))
        self.turn('f00000001 use within 72h')
        with patch.object(seller, 'load_catalog', return_value=self.catalog) as catalog:
            self.receipt(self.turn('offer 999999'))
        self.assertEqual(catalog.call_count, 1)

    def test_atomic_replace_failure_prevents_publication(self):
        self.stock()
        self.turn('G021aae64')
        with patch.object(seller.os, 'replace', side_effect=OSError('disk failure')), \
                patch.object(seller, 'sign_receipt') as sign:
            with self.assertRaises(OSError):
                self.turn('999999')
        sign.assert_not_called()
        self.assertFalse(self.agent.open)
        self.assertFalse(self.agent.committed_units)
        self.assertFalse(list(self.tmp.glob('*.tmp')))

    def test_corrupt_reservations_fail_closed(self):
        (self.tmp/'seller_reservations.json').write_text('{invalid')
        with self.assertRaises(ValueError):
            seller.Seller(0)

    def test_competing_request_during_artifact_publication_is_blocked(self):
        self.stock()
        original = fixture.FakeUpdater.add_artifact
        competing = []
        async def delayed(updater, parts, name):
            competing.extend(self.turn('G021aae64 offer 999999', 'other'))
            await original(updater, parts, name)
        with patch.object(fixture.FakeUpdater, 'add_artifact', delayed):
            self.close()
        self.assertEqual([kind for kind, _ in competing], ['reject'])
        self.assertTrue(seller.Seller(0).committed_units)


class LockAndCacheTests(unittest.TestCase):
    def test_catalog_transient_read_failure_retries_unchanged_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory/'catalog.json'
            path.write_text('{"a":{"list_price":100}}')
            signature = (path.stat().st_mtime_ns, path.stat().st_size)
            with patch.object(Path, 'read_text', side_effect=[PermissionError('busy'), original_read(path)]) as read:
                self.assertEqual(store.load_catalog(directory), {})
                self.assertEqual(store.load_catalog(directory), {'a': {'list_price': 100}})
                self.assertEqual(read.call_count, 2)
            self.assertEqual((path.stat().st_mtime_ns, path.stat().st_size), signature)

    def test_catalog_parse_or_shape_failure_is_not_cached(self):
        for failed in ('{invalid', 'null'):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                path = directory/'catalog.json'
                path.write_text('{"a":{}}')
                with patch.object(Path, 'read_text', side_effect=[failed, original_read(path)]) as read:
                    self.assertEqual(store.load_catalog(directory), {})
                    self.assertEqual(store.load_catalog(directory), {'a': {}})
                    self.assertEqual(read.call_count, 2)

    def test_valid_empty_catalog_is_cached(self):
        for contents in ('{}', '[]'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                (directory/'catalog.json').write_text(contents)
                with patch.object(Path, 'read_text', return_value=contents) as read:
                    self.assertEqual(store.load_catalog(directory), {})
                    self.assertEqual(store.load_catalog(directory), {})
                    self.assertEqual(read.call_count, 1)

    def test_policy_read_parse_or_shape_failure_retries_unchanged_file(self):
        for failed in (PermissionError('busy'), '{invalid', '[]'):
            with self.subTest(failed=repr(failed)), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(policy, 'CONFIG_PATH', Path(tmp)/'policy.json'):
                path = policy.CONFIG_PATH
                path.write_text('{"enabled":true}')
                signature = (path.stat().st_mtime_ns, path.stat().st_size)
                with patch.object(Path, 'read_text', side_effect=[failed, original_read(path)]) as read:
                    self.assertEqual(policy.config(), {'enabled': False})
                    self.assertEqual(policy.config(), {'enabled': True})
                    self.assertEqual(read.call_count, 2)
                self.assertEqual((path.stat().st_mtime_ns, path.stat().st_size), signature)

    def test_valid_empty_policy_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(policy, 'CONFIG_PATH', Path(tmp)/'policy.json'):
            policy.CONFIG_PATH.write_text('{}')
            with patch.object(Path, 'read_text', return_value='{}') as read:
                self.assertEqual(policy.config(), {})
                self.assertEqual(policy.config(), {})
                self.assertEqual(read.call_count, 1)

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
        with patch.object(store, 'mutate_hub', side_effect=store.HubLockTimeout('busy')), patch('builtins.print') as diagnostic:
            self.assertIsNone(store.record_inbound_inquiry('x', True))
            self.assertIsNone(store.record_buyer_order('x'))
            self.assertTrue(diagnostic.called)

    def test_save_timeout_propagates_without_writing(self):
        with patch.object(store, 'file_lock', side_effect=store.HubLockTimeout()), \
                patch.object(store, '_save_hub_unlocked') as save:
            with self.assertRaises(TimeoutError):
                store.save_hub({})
        save.assert_not_called()

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

    def test_policy_mtime_only_and_size_only_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(policy, 'CONFIG_PATH', Path(tmp)/'policy.json'):
            path = policy.CONFIG_PATH
            path.write_text('{"value":1}')
            self.assertEqual(policy.config()['value'], 1)
            old = path.stat().st_mtime_ns
            path.write_text('{"value":2}')
            os.utime(path, ns=(old+1000000000, old+1000000000))
            self.assertEqual(policy.config()['value'], 2)
            path.write_text('{"value":30}')
            os.utime(path, ns=(old+1000000000, old+1000000000))
            self.assertEqual(policy.config()['value'], 30)

    def test_catalog_cache_keeps_selection_and_quote_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            raw = {'G021aae64': dict(kind='goods', list_price=100000, category='goods>sofa')}
            (path/'catalog.json').write_text(json.dumps(raw))
            units = [dict(unit=g, sku='G021aae64', kind='goods', grade=g, name='sofa',
                cost=30000, list_price=100000, supplied_at=900, category='goods>sofa') for g in ('A', 'B', 'C')]
            inv = {'round': 3, 'units': units}
            def select_and_price(catalog):
                context = dict(hub={}, catalog=catalog, now=1000, clearance=1)
                unit, _ = seller.find_stock_unit('G021aae64', set(), inv, catalog, pricing_context=context)
                return unit['unit'], seller.compute_unit_pricing_and_terms(unit, **context)
            expected = select_and_price(raw)
            self.assertEqual(select_and_price(store.load_catalog(path)), expected)
            with patch.object(Path, 'read_text', side_effect=AssertionError('catalog reparse')):
                self.assertEqual(select_and_price(store.load_catalog(path)), expected)

    def test_inventory_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'inventory.json').write_text('{"units":[{"unit":"first"}]}')
            self.assertEqual(store.load_inventory(path)['units'][0]['unit'], 'first')
            (path/'inventory.json').write_text('{"units":[{"unit":"other"}]}')
            self.assertEqual(store.load_inventory(path)['units'][0]['unit'], 'other')

    def test_restock_contention_refuses_without_running_plan(self):
        inv = dict(balance=1000000, units=[], bankruptcies_left=0)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(restock, 'load_catalog', return_value={'x': {}}), \
                patch.object(restock, 'load_inventory', return_value=inv), \
                patch.object(restock, 'load_hub', return_value={}), \
                patch.object(restock, 'fetch_ledger_rows', return_value=[]), \
                patch.object(restock, 'fetch_own_shop_urls', return_value=[]), \
                patch.object(restock, 'fetch_public_buying_data', return_value=None), \
                patch.object(restock, 'ROUND_END_TS', restock.time.time()+1000000), \
                patch.object(restock, 'mutate_hub', side_effect=store.HubLockTimeout()), \
                patch.object(restock, 'plan_with_hub') as plan:
            self.assertEqual(restock.build_restock_plan(Path(tmp)), {'buy': []})
            plan.assert_not_called()

    def test_review_timeout_skips_evaluation_and_outputs_neutral_review(self):
        from contextlib import redirect_stdout
        output = io.StringIO()
        with patch.object(sys, 'argv', ['review', '--deal', 'synthetic-deal.json']), \
                patch.object(review, 'load_json_safe', return_value={}), \
                patch.object(review, 'mutate_hub', side_effect=store.HubLockTimeout()), \
                patch.object(review, 'evaluate_settled_deal') as evaluate, redirect_stdout(output):
            review.main()
        self.assertIsNone(json.loads(output.getvalue())['stars'])
        evaluate.assert_not_called()


class BuyerContentionTests(unittest.TestCase):
    setUp = buyer_fixture.BuyerPriceTests.setUp
    run_buyer = buyer_fixture.BuyerPriceTests.run_buyer
    receipt = buyer_fixture.BuyerPriceTests.receipt

    def test_pending_analytics_contention_preserves_valid_receipt(self):
        original = buyer._run_attempt
        def busy(*args, **kwargs):
            with patch.object(buyer, 'mutate_hub', side_effect=store.HubLockTimeout()):
                return original(*args, **kwargs)
        with patch.object(buyer, '_run_attempt', side_effect=busy):
            result, _, _, _ = self.run_buyer(receipt=self.receipt(9000))
        self.assertEqual(result['receipt']['price'], 9000)
        self.assertTrue(result['accept']['cert'])
        self.assertNotIn('pending_deals', self.hub)

    def test_cleanup_and_failure_analytics_contention_do_not_escape(self):
        def attempted(*args):
            args[-1].update(pending={'ts': 1}, failure='timeout', id='task', state='WORKING', url='fake')
            return {'receipt': None, 'review': 'No deal'}
        with patch.object(buyer, '_run_attempt', side_effect=attempted), \
                patch.object(buyer, 'mutate_hub', side_effect=store.HubLockTimeout()) as mutation, \
                patch.object(buyer, 'rpc_call', return_value={}) as cancel:
            result = buyer.run_attempt({'url': 'fake'}, {'sku': self.sku}, '', {}, {},
                self.catalog, self.order, 'food', 25000)
        self.assertIsNone(result['receipt'])
        self.assertEqual(mutation.call_count, 2)
        cancel.assert_called_once()


original_read = Path.read_text
