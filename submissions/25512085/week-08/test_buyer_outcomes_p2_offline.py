"""P2 regression fixtures; synthetic data and isolated hub/journal only."""
from concurrent.futures import ThreadPoolExecutor
import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import buyer_outcomes as outcomes
import restock
import store
from test_buyer_outcomes_offline import CAT, OWN, row


def events():
    return outcomes.normalize([row(), row('broken', 2, ts=200, refunded=60)], CAT, OWN, 300)[0]


class P2Tests(unittest.TestCase):
    def test_slow_fsync_does_not_hold_hub_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            hub_path = base / 'hub.json'
            entered, release, updated = threading.Event(), threading.Event(), threading.Event()

            def slow_fsync(fd):
                entered.set()
                if not release.wait(10):
                    raise AssertionError('test fsync release timeout')

            def producer():
                with store.mutate_hub(hub_path) as hub:
                    store.apply_ledger_rows(hub, [row()], CAT)

            def other_writer():
                with store.mutate_hub(hub_path) as hub:
                    hub['p2_marker'] = True
                updated.set()

            with patch.object(store, 'BASE_DIR', base), patch.object(outcomes.os, 'fsync', slow_fsync):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(producer)
                    try:
                        self.assertTrue(entered.wait(3))
                        second = pool.submit(other_writer)
                        self.assertTrue(updated.wait(1), 'journal fsync blocked an independent hub update')
                    finally:
                        release.set()
                    first.result(timeout=5)
                    second.result(timeout=5)
            self.assertTrue(store.load_hub(hub_path)['p2_marker'])

    def test_valid_json_invalid_schema_continues_file_and_iterable(self):
        invalid = [
            {'kind': 'settle', 'buyer': OWN, 'seq': 3, 'order': 'bad'},
            dict(events()[0], seq=[]),
            {'version': 1, 'kind': 'coverage', 'observed_at': 300, 'complete': False, 'unlinked': []},
        ]
        expected = outcomes.summarize(events(), OWN, 30000)
        for bad in invalid:
            with self.subTest(bad=bad):
                direct = outcomes.summarize([bad] + events(), OWN, 30000)
                self.assertEqual(direct['cohorts'], expected['cohorts'])
                self.assertEqual(direct['coverage']['corrupt_unknown'], 1)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'journal.jsonl'
                    path.write_text(''.join(json.dumps(e) + '\n' for e in [bad] + events()), encoding='utf-8')
                    self.assertEqual(outcomes.summarize(outcomes.read_events(path), OWN, 30000), direct)

    def test_truncated_prefix_explicit_unknown(self):
        with patch.object(outcomes, 'MAX_EVENTS', 1):
            result = outcomes.summarize(events(), OWN, 30000)
        self.assertTrue(result['truncated'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['cohorts'][0]['unknown_reason'], 'replay_truncated_unknown')
        self.assertIsNone(result['cohorts'][0]['known_broken_contribution'])

    def test_bad_rows_rejected_but_following_good_evidence_survives(self):
        sale = events()[0]
        invalid = [None, [], 3, True, 'sensitive-unknown-text', {},
                   dict(sale, version=True), dict(sale, version=0), dict(sale, version=3),
                   dict(sale, kind=[]), dict(sale, kind='unknown'),
                   dict(sale, seq=True), dict(sale, seq=-1), dict(sale, seq=10**400),
                   dict(sale, ts=True), dict(sale, ts=float('nan')),
                   dict(sale, ts=float('inf')), dict(sale, ts=-1),
                   dict(sale, observed_at=None), dict(sale, observed_at=-1),
                   dict(sale, order=[]), dict(sale, order='x' * 129),
                   dict(sale, buyer='bad buyer'), dict(sale, unit='bad/unit'),
                   dict(sale, sku='x' * 129), dict(sale, goods=1),
                   dict(sale, paid_amount=True), dict(sale, paid_amount=-1),
                   dict(sale, list_price=0), dict(sale, list_price_source=None),
                   dict(sale, grade=[]), dict(sale, warranty_hours=float('nan')),
                   dict(sale, catalog_kind='food'),
                   dict(events()[1], refunded_amount=-1),
                   dict(events()[1], refunded_amount=True)]
        expected = outcomes.summarize(events(), OWN, 30000)['cohorts']
        for bad in invalid:
            with self.subTest(bad=bad):
                result = outcomes.summarize([bad] + events(), OWN, 30000)
                self.assertEqual(result['cohorts'], expected)
                self.assertEqual(result['coverage']['corrupt_unknown'], 1)

    def test_coverage_exact_types_and_safe_unknown_fields(self):
        base = dict(version=1, kind='coverage', observed_at=300, complete=False, unlinked={})
        bads = [dict(base, unlinked=[]), dict(base, unlinked=None),
                dict(base, complete=0), dict(base, complete=True)]
        for value in (True, -1, [], '1', 1.5, float('nan'), 10**400):
            bads.append(dict(base, unlinked={'no_order': value}))
        for bad in bads:
            with self.subTest(bad=bad):
                result = outcomes.summarize([bad] + events(), OWN, 30000)
                self.assertEqual(result['coverage']['corrupt_unknown'], 1)
        good = dict(base, unlinked={'no_order': 2, 'SECRET': {'JWT': 'SECRET'}}, JWT='SECRET')
        clean = outcomes.validate_event(good)
        self.assertNotIn('SECRET', json.dumps(clean))
        self.assertEqual(clean['unlinked'], {'no_order': 2})

    def test_v1_compatibility_and_allowlist_do_not_mutate_input(self):
        fixture = events()
        fixture[0] = {k: v for k, v in fixture[0].items() if k not in (
            'item_kind', 'item_kind_source', 'catalog_kind', 'catalog_kind_state',
            'catalog_kind_observed_at', 'ledger_item_kind', 'ledger_kind_state')}
        fixture[0].update(version=1, goods=True)
        fixture[1]['version'] = 1
        fixture[0].update(JWT='SECRET', receipt={'sig': 'SECRET'}, budget=999)
        before = copy.deepcopy(fixture)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v1.jsonl'
            path.write_text(''.join(json.dumps(e) + '\n' for e in fixture), encoding='utf-8')
            clean = list(outcomes.read_events(path))
        self.assertNotIn('SECRET', json.dumps(clean))
        self.assertNotIn('budget', json.dumps(clean))
        self.assertEqual(outcomes.summarize(fixture, OWN, 30000), outcomes.summarize(clean, OWN, 30000))
        self.assertEqual(fixture, before)

    def test_deep_json_and_invalid_scalar_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'v1.jsonl'
            path.write_text('[' * 1500 + '0' + ']' * 1500 + '\nnull\n' +
                            ''.join(json.dumps(e) + '\n' for e in events()), encoding='utf-8')
            result = outcomes.summarize(outcomes.read_events(path), OWN, 30000)
        self.assertEqual(result['coverage']['corrupt_unknown'], 2)
        self.assertEqual(result['cohorts'][0]['known_broken_contribution'], 0)

    def test_invalid_iterable_or_failing_iterator_unknown(self):
        for value in (None, 5, True):
            with self.subTest(value=value):
                result = outcomes.summarize(value, OWN, 30000)
                self.assertEqual(result['coverage']['corrupt_unknown'], 1)

        def failing():
            yield events()[0]
            raise ValueError('SECRET')

        result = outcomes.summarize(failing(), OWN, 30000)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['coverage']['iterator_error_unknown'], 1)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_replay_exact_cap_is_not_truncated(self):
        with patch.object(outcomes, 'MAX_EVENTS', 2):
            result = outcomes.summarize(events(), OWN, 30000)
        self.assertFalse(result['truncated'])
        self.assertEqual(result['cohorts'][0]['known_broken_contribution'], 0)

    def test_eviction_explicit_truncated_unknown(self):
        fixture = events() + outcomes.normalize([row(seq=3, order='other')], CAT, OWN, 300)[0]
        result = outcomes.summarize(fixture, OWN, 30000, max_orders=1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['coverage']['pending_eviction_unknown'], 1)

    def test_nested_contexts_flush_after_outer_hub_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, 'BASE_DIR', base), patch.object(outcomes, 'append_events') as append:
                with store.mutate_hub(base / 'outer.json') as outer:
                    store.apply_ledger_rows(outer, [row()], CAT)
                    with store.mutate_hub(base / 'inner.json') as inner:
                        store.apply_ledger_rows(inner, [row('broken', 2, ts=200, refunded=60)], CAT)
                    append.assert_not_called()
                self.assertEqual(append.call_count, 2)
            self.assertIsNone(store._buyer_outcome_batches.get())

    def test_snapshot_copied_and_standalone_apply_has_no_io(self):
        fixture, catalog = [row()], copy.deepcopy(CAT)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            journal = base / 'runtime/buyer-outcomes-v1.jsonl'
            with patch.object(store, 'BASE_DIR', base):
                store.apply_ledger_rows(store.default_hub(), fixture, catalog)
                self.assertFalse(journal.exists())
                with store.mutate_hub(base / 'hub.json') as hub:
                    store.apply_ledger_rows(hub, fixture, catalog)
                    fixture[0]['data']['amount'] = 1
                    catalog['g00000001']['list_price'] = 200
                    self.assertFalse(journal.exists())
            result = outcomes.summarize(outcomes.read_events(journal), OWN, 30000)
            self.assertEqual(result['cohorts'][0]['surface_discount'], .4)

    def test_exception_exit_flushes_after_unlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(store, 'BASE_DIR', base):
                with self.assertRaisesRegex(ValueError, 'fixture'):
                    with store.mutate_hub(base / 'hub.json') as hub:
                        store.apply_ledger_rows(hub, [row()], CAT)
                        raise ValueError('fixture')
            self.assertTrue((base / 'runtime/buyer-outcomes-v1.jsonl').exists())
            self.assertIsNone(store._buyer_outcome_batches.get())

    def test_reentrant_journal_fails_closed_without_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            entered = []

            def reenter(fd):
                if not entered:
                    entered.append(True)
                    outcomes.observe([row(seq=3, order='nested')], CAT, OWN, 300, path)

            with patch.object(outcomes.os, 'fsync', reenter), patch.object(outcomes, 'diagnostic') as diag:
                outcomes.observe([row()], CAT, OWN, 300, path)
            diag.assert_called_once_with('journal_write_failed_unknown')
            self.assertEqual(len(outcomes.summarize(outcomes.read_events(path), OWN, 30000)['cohorts']), 1)

    def test_cross_process_journal_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            code = (
                'from pathlib import Path; import sys; import buyer_outcomes as b; '
                'b.observe([{"kind":"settle","seq":1,"ts":100,"data":'
                '{"order":"child","buyer":"mjforge","item":"g00000001","amount":60}}],'
                '{"g00000001":{"list_price":100}},"mjforge",300,Path(sys.argv[1]))'
            )
            with outcomes.exclusive(path):
                result = subprocess.run([sys.executable, '-B', '-c', code, str(path)],
                                        cwd=Path(__file__).resolve().parent,
                                        capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr.strip(), '[buyer-outcomes] journal_write_failed_unknown')
            self.assertFalse(path.exists())

    def test_restock_full_flow_flushes_only_after_hub_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / 'hub.json'
            append = outcomes.append_events
            persisted = []

            def check_after_save(batch, journal):
                persisted.append(store.load_hub(path)['ledger_seq'])
                append(batch, journal)

            with patch.object(store, 'BASE_DIR', base), \
                 patch.object(restock, 'ROUND_END_TS', float('inf')), \
                 patch.object(restock, 'load_catalog', return_value=CAT), \
                 patch.object(restock, 'load_inventory', return_value={'balance': 5000000, 'units': []}), \
                 patch.object(restock, 'load_hub', side_effect=lambda: store.load_hub(path)), \
                 patch.object(restock, 'mutate_hub', side_effect=lambda: store.mutate_hub(path)), \
                 patch.object(restock, 'fetch_ledger_rows', return_value=[row()]) as fetch, \
                 patch.object(restock, 'fetch_own_shop_urls', return_value=[]), \
                 patch.object(restock, 'fetch_public_buying_data', return_value=None), \
                 patch.object(outcomes, 'append_events', side_effect=check_after_save):
                result = restock.build_restock_plan(base)
            self.assertEqual(result, {'buy': []})
            self.assertEqual(persisted, [1])
            fetch.assert_called_once_with(0)
            summary = outcomes.summarize(outcomes.read_events(base / 'runtime/buyer-outcomes-v1.jsonl'), OWN, 30000)
            self.assertEqual(summary['cohorts'][0]['ledger'], 1)


if __name__ == '__main__':
    unittest.main()
