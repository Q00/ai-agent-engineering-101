"""Public pricing/assignment seam; seller exposure seam is tested without sockets."""
import json
import asyncio
import contextlib
import os
from pathlib import Path
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
import uuid
import selectors
import sqlite3
import threading
import importlib
import importlib.util
import shutil
import sys
from unittest import mock

import sol_seller_margin as margin


def real_loop(coroutine):
    """Real asyncio scheduling/executors; only its socket wakeup is replaced.

    A bounded selector poll delivers worker callbacks without any socket call.
    No product coroutine/to_thread, future, lock or executor is mocked here.
    """
    class Poll(selectors.SelectSelector):
        def select(self, timeout=None):
            return super().select(.005 if timeout is None else min(timeout, .005))
    class Loop(asyncio.SelectorEventLoop):
        def _make_self_pipe(self): self._ssock = self._csock = None
        def _close_self_pipe(self): pass
    loop = Loop(Poll())
    try:
        return loop.run_until_complete(asyncio.wait_for(coroutine, 5))
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


def offline_await(coroutine, before=None):
    """Deterministic scheduler boundary; run real business/SQLite functions inline.

    Use the socket-free real loop for shielded abort observers. This inline seam
    is not concurrency evidence; the real_loop contention tests use real workers.
    """
    async def inline(function, *args, **kwargs):
        if before:
            before(function, args)
        return function(*args, **kwargs)
    with mock.patch.object(asyncio, 'to_thread', inline):
        return real_loop(coroutine)

class SourceEntryTest(unittest.TestCase):
    def test_main_alias_retains_entry_identity_code_and_mutation_checks(self):
        for case in ('main_alias', 'foreign_alias', 'unknown_alias', 'replaced_entry',
                     'changed_entry_disk', 'changed_entry_code', 'stale_bootstrap',
                     'preimported_local', 'preimported_foreign'):
            with self.subTest(case=case):
                self.entry_case(case)

    def test_local_pyc_cannot_replace_frozen_dependency_source(self):
        self.entry_case('stale_dependency_pyc')

    def entry_case(self, case):
        source=Path(margin.sol_source_load.__file__).read_bytes()
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(sys.modules), \
                mock.patch.object(sys,'meta_path',list(sys.meta_path)), \
                mock.patch.object(sys,'path',[td,*sys.path]):
            root=Path(td)
            boot_path=root/'sol_source_load.py'
            boot_path.write_bytes(source+(b'\nSTALE_DISK_FIXTURE=True\n' if case=='stale_bootstrap' else b''))
            entry=root/'entry_fixture.py'
            body=b'import sys, sol_source_load\nsol_source_load.install(sys._getframe().f_code,__name__)\nimport entry_dep\n'
            entry.write_bytes(body)
            dep=root/'entry_dep.py'
            dep.write_text("REVISION='new'\n")
            sys.modules.pop('__mp_main__',None)
            sys.modules.pop('entry_dep',None)
            boot=ModuleType('sol_source_load')
            boot.__file__=str(boot_path)
            sys.modules['sol_source_load']=boot
            exec(compile(source,str(boot_path),'exec',dont_inherit=True),boot.__dict__)
            if case in ('preimported_local','preimported_foreign'):
                old=ModuleType('entry_dep')
                old.__file__=str(dep if case=='preimported_local' else root/'foreign/entry_dep.py')
                old.REVISION='old'
                sys.modules['entry_dep']=old
            if case=='stale_dependency_pyc':
                from importlib._bootstrap_external import _code_to_timestamp_pyc
                cache=Path(importlib.util.cache_from_source(str(dep)))
                cache.parent.mkdir()
                cache.write_bytes(_code_to_timestamp_pyc(compile("REVISION='old'\n",str(dep),'exec'),
                    int(dep.stat().st_mtime),dep.stat().st_size))
            main=ModuleType('__main__')
            main.__file__=str(entry)
            sys.modules['__main__']=main
            code=body+(b'\nUNTRUSTED_ENTRY=True\n' if case=='changed_entry_code' else b'')
            exec(compile(code,str(entry),'exec',dont_inherit=True),main.__dict__)
            sys.modules['__mp_main__']=main
            if case=='foreign_alias':
                foreign=ModuleType('__mp_main__'); foreign.__file__=str(entry)
                sys.modules['__mp_main__']=foreign
            elif case=='unknown_alias':
                sys.modules['untracked_main_alias']=main
            elif case=='replaced_entry':
                other=ModuleType('__main__'); other.__file__=str(entry)
                sys.modules['__main__']=other
            elif case=='changed_entry_disk':
                entry.write_bytes(body+b'\nCHANGED_AFTER_ENTRY=True\n')
            expected=case in ('main_alias','stale_dependency_pyc')
            self.assertEqual(boot.proof()['valid'],expected)
            if expected:
                self.assertIs(boot._context.instances['__main__'],main)
                self.assertIn('__main__',boot._context.codes)
                self.assertEqual(main.entry_dep.REVISION,'new')


class MarginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'new-study'
        self.trial = margin.MarginStudy(self.root)
        self.unit = dict(unit='u-anonymous', sku='G-anonymous', grade='A', kind='goods',
                         cost=100000, list_price=500000)
        self.pricing = dict(price=dict(min_profit_share=0, goods_break=.04,
                            high_median_up=dict(min_list=300000, factor=.97),
                            high_price_trial=dict(share=0)), warranty=dict(max_break=.5))

    def enable(self, **updates):
        self.root.mkdir(exist_ok=True)
        baseline = margin.baseline_snapshot(self.pricing, updates.pop('others', ()))
        cfg = dict(enabled=True, version=margin.VERSION, study_id='sol-margin-new-001', epoch=1000,
                   start=1000, end=44200, salt='fresh-sol-salt-001', disk_snapshot_sha256=margin.DISK_SNAPSHOT_SHA256,
                   **margin.sol_source_load.proof()['claim'])
        cfg.update(baseline=baseline, baseline_sha256=margin.digest(baseline))
        cfg.update(updates)
        (self.root/'controller.json').write_text(json.dumps(cfg))
        return cfg

    def quote(self, task='task-anonymous', **kwargs):
        return self.trial.quote(task, self.unit, 300000, .04, (), self.pricing, now=1100, **kwargs)

    def test_default_off_preserves_price_and_creates_nothing(self):
        q, assignment = self.quote()
        self.assertEqual(q['open'], 291000)
        self.assertIsNone(assignment)
        self.assertFalse(self.root.exists())

    def test_preimported_old_dependency_cannot_certify_new_disk_snapshot(self):
        source = Path(margin.__file__).parent
        for preloaded in ('local', 'foreign', False):
            with self.subTest(preloaded=preloaded), tempfile.TemporaryDirectory() as td, \
                    mock.patch.dict(sys.modules), mock.patch.object(sys, 'meta_path', list(sys.meta_path)), \
                    mock.patch.object(sys, 'path', [td, *sys.path]):
                root = Path(td)
                for p in source.glob('*.py'): shutil.copyfile(p, root/p.name)
                names = {p.stem for p in root.glob('*.py')}
                for name in names: sys.modules.pop(name, None)
                profit = root/'profit_price.py'
                text = profit.read_text()
                if preloaded:
                    old_path = profit
                    if preloaded == 'foreign':
                        (root/'foreign').mkdir()
                        old_path = root/'foreign'/'profit_price.py'
                    old_path.write_text(text+"\nDISK_REVISION = 'old'\n")
                    spec = importlib.util.spec_from_file_location('profit_price', old_path)
                    old = importlib.util.module_from_spec(spec)
                    sys.modules['profit_price'] = old
                    spec.loader.exec_module(old)
                profit.write_text(text+"\nDISK_REVISION = 'new'\n")
                bootstrap = None
                if (root/'sol_source_load.py').exists():
                    bootstrap = importlib.import_module('sol_source_load')
                    bootstrap.install()
                m = importlib.import_module('sol_seller_margin')
                cfg = dict(enabled=True, version=m.VERSION, study_id='source-load-proof-test',
                           epoch=1000, start=1000, end=44200, salt='source-proof-test-salt')
                if hasattr(m, 'DISK_SNAPSHOT_SHA256'):
                    cfg.update(disk_snapshot_sha256=m.DISK_SNAPSHOT_SHA256,
                               **bootstrap.proof()['claim'])
                else:
                    cfg['source_sha256'] = m.SOURCE_BINDING
                baseline = m.baseline_snapshot(self.pricing)
                cfg.update(baseline=baseline, baseline_sha256=m.digest(baseline))
                trial = m.MarginStudy(root/'study')
                trial.root.mkdir()
                (trial.root/'controller.json').write_text(json.dumps(cfg))
                if preloaded:
                    self.assertEqual(m.profit_price.DISK_REVISION, 'old')
                    with self.assertRaises(m.StudyError):
                        trial.quote('fresh-task', self.unit, 300000, .04, (), self.pricing, now=1100)
                else:
                    self.assertEqual(m.profit_price.DISK_REVISION, 'new')
                    self.assertIsNotNone(trial.quote('fresh-task', self.unit, 300000, .04, (), self.pricing, now=1100)[1])

    def test_missing_or_unknown_grade_never_blocks_baseline_or_gets_b_price(self):
        self.enable()
        for kind in ('food', 'goods'):
            for grade in (None, '', 'unknown'):
                with self.subTest(kind=kind, grade=grade):
                    self.unit.update(kind=kind, grade=grade)
                    q, a = self.quote(task=f'grade-{kind}-{grade}')
                    self.assertEqual(q['open'], 315000 if kind == 'food' else 291000)
                    self.assertFalse(a['eligible'])
                    self.assertFalse(a['prepared_treatment'])
                    self.assertEqual(a['reason'], 'outside_target' if kind == 'food' else 'grade_unknown')

    def both_arms(self):
        found = {}
        for n in range(100):
            self.unit['sku'] = 'G-anonymous-' + str(n)
            q, a = self.quote(task='task-' + str(n))
            found.setdefault(a['arm'], (q, a))
            if len(found) == 2:
                return found
        self.fail('both fixed hash arms must be reachable')

    def test_fresh_fixed_assignment_changes_only_b_and_survives_reopen(self):
        self.enable()
        arms = self.both_arms()
        self.assertEqual(arms['A'][0]['open'], 291000)
        self.assertEqual(arms['B'][0]['open'], 300000)
        self.assertEqual(arms['A'][0]['floor'], arms['B'][0]['floor'])
        self.assertEqual(arms['A'][0]['b'], arms['B'][0]['b'])
        persisted = margin.MarginStudy(self.root).observations()
        self.assertIn(arms['B'][1], persisted)
        self.unit['sku'] = arms['B'][1]['sku']
        self.unit['unit'] = 'u-another'
        _, repeated = self.quote(task='task-repeat')
        self.assertEqual(repeated['arm'], 'B')
        self.assertEqual(repeated['cluster'], arms['B'][1]['cluster'])

    def test_clipped_and_missing_median_are_retained_in_itt(self):
        self.enable()
        self.unit['cost'] = 400000
        q, a = self.quote()
        self.assertTrue(a['eligible'])
        self.assertEqual(q['open'], q['floor'])
        self.assertFalse(a['prepared_treatment'])
        _, missing = self.trial.quote('task-missing', self.unit, None, .04, (), self.pricing, now=1100)
        self.assertTrue(missing['eligible'])
        self.assertEqual(missing['reason'], 'no_median')

    def test_caps_and_food_keep_baseline_policy(self):
        self.pricing['price']['ask_caps'] = {self.unit['sku']: {'A': 280000}}
        self.enable()
        q, a = self.quote()
        self.assertEqual((q['open'], a['candidate']), (280000, 280000))
        self.unit['kind'] = 'food'
        _, a = self.quote(task='task-food')
        self.assertFalse(a['eligible'])
        self.assertFalse(a['prepared_treatment'])

    def assert_scarce_excluded(self, fixed_ask, expected_baseline):
        self.enable()
        arms = self.both_arms()
        self.unit['sku'] = arms['B'][1]['sku']
        self.pricing['price']['scarce_asks'] = {self.unit['sku']: fixed_ask}
        # A distinct excluded input is still baseline-only, never a new price treatment.
        q, assignment = self.quote(task='scarce-case')
        self.assertEqual(assignment['arm'], 'B')
        self.assertEqual(q['open'], expected_baseline)
        self.assertEqual(assignment['selected'], expected_baseline)
        self.assertEqual(assignment['candidate'], expected_baseline)
        self.assertFalse(assignment['eligible'])  # logged exclusion, outside target ITT
        self.assertFalse(assignment['prepared_treatment'])
        self.assertEqual(assignment['reason'], 'scarce_fixed_ask')

    def test_valid_scarce_below_median_is_excluded_even_without_scarce_why(self):
        # Baseline .97*300000 = 291000; a valid 280000 fixed ask is hidden in why.
        self.assert_scarce_excluded(280000, 291000)

    def test_valid_scarce_above_median_is_excluded_from_target_itt(self):
        self.assert_scarce_excluded(310000, 310000)

    def test_warranty_refused_stays_in_eligible_itt_without_treatment(self):
        self.enable()
        q, a = self.trial.quote('refuse', self.unit, 300000, .8, (), self.pricing, now=1100)
        self.assertTrue(q['refuse'])
        self.assertTrue(a['eligible'])
        self.assertFalse(a['prepared_treatment'])
        self.assertEqual(a['reason'], 'warranty_refusal')

    def test_failed_persistence_never_returns_unrecorded_treatment(self):
        self.enable()
        with mock.patch('sqlite3.connect', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(margin.StudyError):
                self.quote()

    def test_epoch_reuse_or_stale_task_is_rejected(self):
        self.enable()
        self.quote()
        self.enable(epoch=1050, start=1050, end=44250, study_id='sol-margin-new-002')
        with self.assertRaises(margin.StudyError):
            self.quote(task='task-new')

    def test_baseline_price_and_warranty_drift_pause_same_epoch(self):
        self.enable()
        self.quote()
        import copy
        original = copy.deepcopy(self.pricing)
        for field in ('cap', 'warranty', 'markdown'):
            self.pricing = copy.deepcopy(original)
            if field == 'cap': self.pricing['price']['ask_caps'] = {self.unit['sku']: {'A': 280000}}
            if field == 'warranty': self.pricing['warranty']['max_hours'] = 6
            if field == 'markdown': self.pricing['markdown'] = {'min_floor_share': .2}
            with self.subTest(field=field), self.assertRaises(margin.StudyError):
                self.quote(task='changed-'+field)

    def test_ready_detects_common_policy_drift_and_retains_unknown_event(self):
        from seller_late_concession import LateConcession
        peer = LateConcession(Path(self.temp.name)/'peers')
        peer.root.mkdir(parents=True)
        (peer.root/'adopted').touch()
        cfg = self.enable(others=(peer,))
        _, a = self.quote(others=(peer,))
        self.assertEqual(a['baseline_sha256'], cfg['baseline_sha256'])
        self.assertEqual(margin.MarginStudy(self.root).observations()[0], a)
        (peer.root/'adopted').unlink()
        with self.assertRaises(margin.StudyError):
            self.trial.ready(a['task'], a, dict(ask=291000, reserve=119118), 1,
                             others=(peer,), now=1100, pricing=self.pricing)
        self.assertEqual(self.trial.events()[-1]['economic_status'], 'unknown')

    def test_changed_snapshot_and_source_are_rejected(self):
        self.enable()
        self.quote()
        self.unit['cost'] = 200000
        with self.assertRaises(margin.StudyError):
            self.quote()
        self.enable(disk_snapshot_sha256='0'*64)
        with self.assertRaises(margin.StudyError):
            self.quote(task='task-source')

    def test_reopened_controller_never_reconsumes_a_prepared_task(self):
        self.enable()
        self.quote()
        self.trial = margin.MarginStudy(self.root)  # process-local tombstones are gone
        with self.assertRaises(margin.StudyError):
            self.quote()

    def test_public_reader_rejects_active_journal_or_wal_without_touching_it(self):
        self.enable()
        self.quote()
        for suffix in ('-journal', '-wal'):
            sidecar = self.root/('journal.sqlite3'+suffix)
            sidecar.write_bytes(b'anonymous in-progress fixture')
            with self.subTest(suffix=suffix), self.assertRaises(margin.StudyError):
                self.trial.observations()
            self.assertEqual(sidecar.read_bytes(), b'anonymous in-progress fixture')
            sidecar.unlink()

    def test_epoch_boundary_off_and_rollback_do_not_reclassify_old_tasks(self):
        self.enable()
        self.trial.quote('before', self.unit, 300000, .04, (), self.pricing, now=999)
        self.assertEqual(self.trial.observations(), [])
        self.quote()
        self.enable(enabled=False)
        _, a = self.quote(task='after-off')
        self.assertIsNone(a)
        self.assertEqual(len(self.trial.observations()), 1)

    def test_peers_are_exclusive_including_adopted_policy(self):
        self.enable()
        peer_root = Path(self.temp.name)/'peer'
        peer_root.mkdir()
        class Peer:
            root = peer_root
            def config(self):
                return dict(start=1000, end=1200)
        self.assertFalse(self.trial.pre_pick_ok((Peer(),), now=1100))
        (peer_root/'disabled').touch()
        self.assertTrue(self.trial.pre_pick_ok((Peer(),), now=1100))

    def test_existing_adopted_common_policy_is_allowed_but_live_ab_is_not(self):
        from seller_late_concession import LateConcession
        peer = LateConcession(Path(self.temp.name)/'peers')
        peer.root.mkdir(parents=True)
        (peer.root/'adopted').touch()
        self.enable()
        self.assertTrue(self.trial.pre_pick_ok((peer,), now=1100))
        (peer.root/'adopted').unlink()
        (peer.root/'seller_concession_trial.json').write_text(json.dumps(dict(
            enabled=True, id='peer-live-ab-001', start=1000, end=1200, seed=1)))
        self.assertFalse(self.trial.pre_pick_ok((peer,), now=1100))

    def test_rollback_after_prepare_blocks_public_consumption(self):
        self.enable()
        _, a = self.quote()
        self.enable(enabled=False)
        with self.assertRaises(margin.StudyError):
            self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1, now=1100)

    def test_ready_off_between_config_read_and_peer_check_cannot_consume(self):
        self.enable()
        _, a = self.quote()
        original = self.trial.config
        reads = []
        def read(now=None):
            cfg = original(now)
            if not reads:
                (self.root/'controller.json').write_text('{"enabled":false}')
            reads.append(True)
            return cfg
        with mock.patch.object(self.trial, 'config', read), self.assertRaises(margin.StudyError):
            permit = self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1,
                                      now=1100, pricing=self.pricing)
            if permit is not None:
                with permit:
                    permit.consume()

    def test_expiry_after_durable_ready_before_transport_cannot_consume(self):
        self.enable()
        _, a = self.quote()
        clock = [1100]
        event = self.trial.event
        def after_write(*args, **kwargs):
            event(*args, **kwargs)
            if args[2] == 'quote_ready': clock[0] = 50000
        with mock.patch.object(margin.time, 'time', lambda: clock[0]), \
                mock.patch.object(self.trial, 'event', after_write), self.assertRaises(margin.StudyError):
            permit = self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1,
                                      now=1100, pricing=self.pricing)
            if permit is not None:
                with permit:
                    permit.consume()

    def test_real_off_writer_fences_consumption_and_preserves_already_admitted_send(self):
        self.enable()
        _, a = self.quote()
        requested, finished = threading.Event(), threading.Event()
        seen = []
        permit = self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1,
                                  now=1100, pricing=self.pricing)
        lock = self.trial._controller_lock
        def write_off():
            self.trial.write_controller({'enabled':False})
            seen.append(permit.consumed)
            finished.set()
        writer = threading.Thread(target=write_off)
        def notified_lock(wait=True):
            if threading.current_thread() is writer: requested.set()
            handle = lock(wait)
            if threading.current_thread() is not writer and not wait:
                try:
                    writer.start()
                    self.assertTrue(requested.wait(1))
                    self.assertFalse(finished.is_set())  # actual other fd's flock is fenced
                except BaseException:
                    handle.close()
                    raise
            return handle
        with mock.patch.object(margin.time, 'time', return_value=1100), \
                mock.patch.object(self.trial, '_controller_lock', notified_lock):
            with permit:
                self.assertEqual(permit.consume(), 1100)
            writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertEqual(seen, [True])
        self.assertIsNone(self.trial.config(now=1100))

    def test_real_off_writer_winning_gate_blocks_prepared_consumption(self):
        self.enable()
        _, a = self.quote()
        permit = self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1,
                                  now=1100, pricing=self.pricing)
        locked, release = threading.Event(), threading.Event()
        lock = self.trial._controller_lock
        writer = threading.Thread(target=lambda: self.trial.write_controller({'enabled':False}))
        def pause_writer(wait=True):
            handle = lock(wait)
            if threading.current_thread() is writer:
                locked.set()
                if not release.wait(2): handle.close(); raise AssertionError('fixture release timeout')
            return handle
        with mock.patch.object(self.trial, '_controller_lock', pause_writer):
            writer.start()
            try:
                self.assertTrue(locked.wait(1))
                with permit, self.assertRaises(margin.StudyError): permit.consume()
            finally:
                release.set()
                writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertFalse(permit.consumed)
        self.assertIsNone(self.trial.config(now=1100))

    def test_expiry_during_consume_intent_io_is_checked_after_the_wait(self):
        self.enable()
        _, a = self.quote()
        clock, event = [1100], self.trial.event
        def delayed(*args, **kwargs):
            event(*args, **kwargs)
            if args[2]=='consume_intent': clock[0]=50000
        with mock.patch.object(margin.time, 'time', lambda:clock[0]), \
                mock.patch.object(self.trial, 'event', delayed):
            permit = self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1,
                                      now=1100, pricing=self.pricing)
            with permit, self.assertRaises(margin.StudyError):
                permit.consume()
        self.assertFalse(permit.consumed)

    def test_sqlite_writer_does_not_stall_final_gate_or_another_quote(self):
        for drift in (False, True):
            with self.subTest(baseline_drift=drift):
                self.enable()
                _, a = self.quote(task='busy-gate-'+str(drift))
                with mock.patch.object(margin.time, 'time', return_value=1100):
                    permit = self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000),
                                              1, pricing=self.pricing)
                if drift:
                    permit.loader = lambda: dict(self.pricing, markdown=dict(start_hours=1))
                locked, release = threading.Event(), threading.Event()
                failures, times = [], {}
                def hold_sqlite():
                    try:
                        with contextlib.closing(sqlite3.connect(self.root/'journal.sqlite3')) as db:
                            db.execute('BEGIN IMMEDIATE')
                            times['locked'] = time.monotonic()
                            locked.set()
                            release.wait(.45)  # bounded escape if the event loop is blocked
                            db.rollback()
                    except BaseException as exc:
                        failures.append(type(exc).__name__)
                        locked.set()
                writer = threading.Thread(target=hold_sqlite)
                writer.start()
                self.assertTrue(locked.wait(1))
                off = margin.MarginStudy(Path(self.temp.name)/('off-'+str(drift)))
                async def heartbeat():
                    await asyncio.sleep(.015)
                    times['heartbeat'] = time.monotonic()-times['locked']
                    release.set()
                async def other_quote():
                    await asyncio.sleep(.005)  # progress after the final gate has begun
                    result, assignment = await asyncio.to_thread(off.quote, 'other', self.unit,
                        300000, .04, (), self.pricing)
                    self.assertIsNone(assignment)
                    self.assertEqual(result['open'],291000)
                    times['other_quote'] = time.monotonic()-times['locked']
                async def gate():
                    if drift:
                        with self.assertRaisesRegex(margin.StudyError, 'baseline changed'):
                            permit.consume()
                    else:
                        self.assertEqual(permit.consume(),1100)
                async def run():
                    await asyncio.gather(heartbeat(), other_quote(), gate())
                try:
                    with mock.patch.object(margin.time, 'time', return_value=1100), permit:
                        real_loop(run())
                finally:
                    release.set()
                    writer.join(2)
                self.assertFalse(writer.is_alive())
                self.assertEqual(failures,[])
                print('SQLITE_GATE_HEARTBEAT '+json.dumps(dict(drift=drift,**times)), flush=True)
                self.assertLess(times['heartbeat'],.15)
                self.assertLess(times['other_quote'],.15)


class SellerExposureTest(unittest.TestCase):
    def test_real_seller_first_quote_records_final_treatment_after_common_opening(self):
        self.exercise_case()

    def test_off_before_publication_cleans_open_and_owned_hold(self):
        self.exercise_case('off_before_ready')

    def test_peer_change_before_publication_aborts_prepared_quote(self):
        self.exercise_case('peer_before_ready')

    def test_baseline_change_before_publication_aborts_prepared_quote(self):
        self.exercise_case('baseline_before_ready')

    def test_all_four_adopted_policies_are_the_common_baseline(self):
        self.exercise_case('adopted_common')

    def test_unknown_grade_real_seller_keeps_baseline_quote(self):
        self.exercise_case('unknown_grade')

    def test_journal_io_before_publication_cleans_open_and_owned_hold(self):
        self.exercise_case('io_before_ready')

    def test_publication_transport_failure_retains_unknown_not_unpublished(self):
        self.exercise_case('transport_failure')

    def test_publication_cancel_retains_unknown_not_unpublished(self):
        self.exercise_case('transport_cancel')

    def test_lost_ack_buyer_can_accept_the_existing_unknown_quote(self):
        self.exercise_case('lost_ack_accept')

    def test_real_off_producer_after_ready_cleans_unpublished_trade(self):
        self.exercise_case('off_after_ready')

    def test_final_gate_baseline_drift_is_journaled_paused_unknown(self):
        self.exercise_case('baseline_after_ready')

    def test_published_off_and_sqlite_error_do_not_block_common_record_or_flush(self):
        self.exercise_case('published_journal_failure')

    def test_prior_memory_writer_failure_fixture_does_not_taint_next_public_trade(self):
        self.exercise_case('prior_writer_failure')

    def test_two_first_quotes_cannot_overwrite_the_same_unit_owner(self):
        self.exercise_case('concurrent_quotes')

    def test_eight_first_quotes_keep_one_owner(self):
        self.exercise_case('concurrent_quotes', quote_count=8)

    def test_study_off_first_quotes_still_keep_one_owner(self):
        self.exercise_case('concurrent_quotes', study_on=False)

    def test_real_loop_worker_quotes_preserve_one_owner_on_and_off(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.exercise_case('real_concurrent_quotes', study_on=enabled)

    def test_abort_journal_sqlite_busy_keeps_the_loop_responsive_and_cleans_hold(self):
        self.exercise_case('abort_busy_journal')

    def test_cancel_during_abort_journal_keeps_cleanup_and_the_observation(self):
        self.exercise_case('abort_busy_cancel')

    def test_unknown_publication_cancel_journal_keeps_terms_and_the_observation(self):
        self.exercise_case('unknown_busy_cancel')

    def test_same_task_prepared_off_never_exposes_price_offer_or_cancel(self):
        for message in ('price?', '291000', 'cancel'):
            with self.subTest(message=message):
                self.exercise_case('same_task_prepared_off_'+message)

    def test_same_task_prepared_on_followups_wait_and_initial_continues(self):
        for message in ('price?', '291000', 'cancel'):
            with self.subTest(message=message):
                self.exercise_case('same_task_prepared_on_'+message)

    def test_same_task_waiting_request_cancellation_keeps_first_owner(self):
        self.exercise_case('same_task_waiter_cancel')

    def test_same_task_preopen_duplicate_cannot_abort_first(self):
        self.exercise_case('same_task_preopen_duplicate')

    def test_same_task_sending_lostack_off_can_be_accepted(self):
        self.exercise_case('same_task_sending_lostack')

    def exercise_case(self, action=None, *, quote_count=2, study_on=True):
        # All filesystem/controller boundaries use this fresh fixture. No HTTP/model stubs
        # can conceal a model attempt: direct_runner's process/network sentinel rejects it.
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            root = Path(td)
            # zoneinfo must not discover host TZPATH through sysconfig/macOS metadata.
            stack.enter_context(mock.patch.dict(os.environ, MARKET_DIR=td, PYTHONTZPATH=''))
            import seller
            import shop_memory
            from shop_stock import StockBook
            trial = margin.MarginStudy(root/'new-study')
            # The approved historical seller source lacks this binding. Creating
            # the injected controller lets its real price path run and fail on
            # unchanged public-price assertions instead of a missing attribute.
            stack.enter_context(mock.patch.object(seller, 'MARGIN_STUDY', trial, create=True))
            controllers = ('READER_TRIAL', 'CONCESSION_TRIAL', 'LATE_CONCESSION', 'OPENING_DEFAULT',
                           'DRAIN', 'OPENING_CUE', 'LATE_SPEED', 'JEV_TRANSPORT', 'SHELF_REPLY',
                           'EXACT_ACCEPT', 'EXACT_COUNTER', 'GRADE_PREFERENCE', 'FOOD_OPENING',
                           'AT_FLOOR_EXACT', 'DEEP_LOWBALL', 'QUOTE_HOLD')
            for name in controllers:
                stack.enter_context(mock.patch.object(getattr(seller, name), 'root', root/name))
            if action == 'prior_writer_failure':
                # Exact polluter from the full-suite log, before the real public
                # trade/strict-flush victim below. Do not clear the victim's state.
                from test_seller_memory_writer import MemoryWriter
                MemoryWriter('test_a_failing_write_is_logged_not_raised').debug()
                # The polluter's fixture must also preserve a pre-existing sticky
                # failure, rather than clearing failures for the next test.
                ambient = seller.MEMORY_FAILURES
                with mock.patch.object(seller, 'MEMORY_FAILURES', [True]) as prior:
                    MemoryWriter('test_a_failing_write_is_logged_not_raised').debug()
                    self.assertIs(seller.MEMORY_FAILURES, prior)
                    self.assertEqual(prior, [True])
                    with self.assertRaisesRegex(RuntimeError, 'drain is not clean'):
                        seller.flush_memory(strict=True)
                self.assertIs(seller.MEMORY_FAILURES, ambient)
                MemoryWriter('test_remember_returns_at_once_and_the_writes_keep_their_order').debug()
                from test_seller_drain import PersistenceTest
                PersistenceTest('test_failed_memory_write_blocks_flush').debug()
                action = 'published_journal_failure'
            if action == 'adopted_common':
                for name in ('LATE_CONCESSION', 'AT_FLOOR_EXACT', 'EXACT_COUNTER', 'EXACT_ACCEPT'):
                    peer_root = getattr(seller, name).root
                    peer_root.mkdir(parents=True)
                    (peer_root/'adopted').touch()
            pricing = dict(price=dict(min_profit_share=0, goods_break=.04,
                           high_median_up=dict(min_list=300000, factor=.97),
                           high_price_trial=dict(share=0)), warranty=dict(max_break=.5))
            (root/'pricing.json').write_text(json.dumps(pricing))
            stack.enter_context(mock.patch.object(shop_memory, 'PRICING', str(root/'pricing.json')))
            stack.enter_context(mock.patch.object(shop_memory, 'MEMORY', str(root/'memory.json')))
            pricing = shop_memory.load_pricing()
            now = time.time()
            trial.root.mkdir()
            (trial.root/'controller.json').write_text(json.dumps(dict(
                enabled=True, version=margin.VERSION, study_id='sol-seller-integration-001',
                epoch=now-1, start=now-1, end=now+43199, salt='new-integration-salt',
                disk_snapshot_sha256=margin.DISK_SNAPSHOT_SHA256,
                **margin.sol_source_load.proof()['claim'],
                baseline=margin.baseline_snapshot(pricing, [getattr(seller, name) for name in controllers if name != 'DRAIN']),
                baseline_sha256=margin.digest(margin.baseline_snapshot(pricing, [getattr(seller, name) for name in controllers if name != 'DRAIN'])))))
            # Choose an actually assigned B cluster through the public price seam.
            u = dict(unit='u-000000000001', sku='G00000000', grade='A', kind='goods', cost=100000,
                     list_price=500000, name='anonymous goods', category='goods', supplied_at=now, expires_at=None)
            for n in range(100):
                u['sku'] = f'G{n:08x}'
                _, a = trial.quote('probe-'+str(n), u, 300000, None, (), pricing,
                                   others=[getattr(seller, name) for name in controllers if name != 'DRAIN'])
                if a['arm'] == 'B':
                    break
            self.assertEqual(a['arm'], 'B')
            if action == 'unknown_grade':
                u['grade'] = None
            (root/'briefing.json').write_text(json.dumps(dict(skus={u['sku']: dict(price_ratio=.6)})))
            stock = StockBook(lambda: dict(round=3, units=[u]))
            shop = seller.Seller(None, 'anonymous-shop', stock=stock)
            task = SimpleNamespace(id=str(uuid.uuid4()))
            class Updater:
                quote = None
                rejected = None
                async def requires_input(self, message):
                    self.quote = message
                    if action in ('transport_failure', 'lost_ack_accept'): raise OSError('fixture ambiguous delivery')
                    if action == 'transport_cancel': raise asyncio.CancelledError()
                async def reject(self, message): self.rejected = message
            up = Updater()
            if action and action.startswith('same_task_'):
                self.same_task_case(action, shop, trial, u, task.id, stock)
                return
            if action in ('abort_busy_journal', 'abort_busy_cancel', 'unknown_busy_cancel'):
                locked, release, observed = threading.Event(), threading.Event(), threading.Event()
                failures, times = [], {}
                def hold_sqlite():
                    try:
                        with contextlib.closing(sqlite3.connect(trial.root/'journal.sqlite3')) as db:
                            db.execute('BEGIN IMMEDIATE')
                            times['locked'] = time.monotonic()
                            locked.set()
                            release.wait(.45)  # bounded escape for the old synchronous abort
                            db.rollback()
                    except BaseException as exc:
                        failures.append(type(exc).__name__)
                        locked.set()
                writer = threading.Thread(target=hold_sqlite)
                def start_writer():
                    writer.start()
                    if not locked.wait(1): raise AssertionError('writer did not lock')
                ready, observe = trial.ready, trial.observe_event
                def fail_after_real_ready(*args, **kwargs):
                    ready(*args, **kwargs)  # actual prepared price and durable intent
                    start_writer()
                    if action == 'abort_busy_cancel': raise asyncio.CancelledError()
                    raise margin.StudyError('fixture failure after durable preparation')
                def watched(*args, **kwargs):
                    try: return observe(*args, **kwargs)
                    finally: observed.set()
                async def cancel_transport(message):
                    up.quote = message  # delivery is possible, never label it unpublished
                    await asyncio.to_thread(start_writer)
                    raise asyncio.CancelledError()
                async def run():
                    job = asyncio.create_task(shop.open_from_stock(task, u['sku'], up,
                                                   lambda text, price=None:(text, price)))
                    async def pulse():
                        while not locked.is_set(): await asyncio.sleep(.002)
                        await asyncio.sleep(.015)
                        times['heartbeat'] = time.monotonic()-times['locked']
                        if action != 'unknown_busy_cancel':
                            self.assertNotIn(task.id,shop.open)
                            self.assertNotEqual(stock.holder(u['unit']),task.id)
                        if action.endswith('_cancel'):
                            times['cancel_requested'] = job.cancel()
                            await asyncio.sleep(.005)
                            times['cancel_waits_for_observer'] = not job.done()
                        release.set()
                    results = await asyncio.gather(job, pulse(), return_exceptions=True)
                    if isinstance(results[1],BaseException): raise results[1]
                    self.assertIsInstance(results[0], margin.StudyError if action=='abort_busy_journal'
                                          else asyncio.CancelledError)
                    self.assertTrue(await asyncio.to_thread(observed.wait,1))
                with mock.patch.object(trial, 'observe_event', watched):
                    if action == 'unknown_busy_cancel':
                        stack.enter_context(mock.patch.object(up,'requires_input',cancel_transport))
                    else:
                        stack.enter_context(mock.patch.object(trial,'ready',fail_after_real_ready))
                    try:
                        real_loop(run())
                    finally:
                        release.set()
                        if writer.ident is not None: writer.join(2)
                self.assertFalse(writer.is_alive())
                self.assertEqual(failures,[])
                print('SQLITE_ABORT_HEARTBEAT '+json.dumps(dict(action=action,**times)), flush=True)
                self.assertLess(times['heartbeat'],.15)
                if action.endswith('_cancel'):
                    self.assertTrue(times['cancel_requested'])
                    self.assertTrue(times['cancel_waits_for_observer'])
                events=[e['event'] for e in trial.events() if e['task']==task.id]
                self.assertEqual(events.count('consume_intent'),1)
                self.assertEqual(events.count('quote_ready'),1)
                self.assertNotIn('quoted',events)
                self.assertIn(task.id,{r['task'] for r in trial.observations()})
                if action=='unknown_busy_cancel':
                    self.assertEqual(shop.open[task.id]['sol_margin_publication'],'unknown')
                    self.assertEqual(stock.holder(u['unit']),task.id)
                    self.assertNotIn(task.id,shop.margin_blocked)
                    self.assertEqual(events.count('publication_unknown'),1)
                else:
                    self.assertIn(task.id,shop.margin_blocked)
                    self.assertEqual(events.count('unpublished_abort'),1)
                return
            if action in ('off_after_ready', 'baseline_after_ready'):
                prepare = trial._prepare
                def stop_after_prepare(*args, **kwargs):
                    cfg = prepare(*args, **kwargs)
                    if action == 'off_after_ready':
                        trial.write_controller({'enabled':False})
                    else:
                        changed=json.loads((root/'pricing.json').read_text())
                        changed['price']['ask_caps']={u['sku']:{'A':280000}}
                        (root/'pricing.json').write_text(json.dumps(changed))
                    return cfg
                with mock.patch.object(trial, '_prepare', stop_after_prepare), self.assertRaises(margin.StudyError):
                    real_loop(shop.open_from_stock(task, u['sku'], up, lambda text, price=None:(text, price)))
                self.assertIsNone(up.quote)
                self.assertNotIn(task.id, shop.open)
                self.assertNotEqual(stock.holder(u['unit']), task.id)
                if action == 'baseline_after_ready':
                    events=[e for e in trial.events() if e['task']==task.id]
                    pauses=[e for e in events if e['event']=='paused']
                    self.assertEqual(len(pauses),1)
                    self.assertEqual(pauses[0]['economic_status'],'unknown')
                    self.assertTrue(any(e['event']=='unpublished_abort' for e in events))
                return
            if action == 'lost_ack_accept':
                with self.assertRaises(OSError):
                    real_loop(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                deal = shop.open[task.id]
                original_price = up.quote[1]  # the buyer received this before ACK was lost
                self.assertEqual(deal['sol_margin_publication'], 'unknown')
                class Continue:
                    failed_message = None
                    completed = False
                    receipt = None
                    def new_agent_message(self, **kw): return kw
                    async def failed(self, message): self.failed_message = message
                    async def reject(self, message): self.failed_message = message
                    async def add_artifact(self, parts, name): self.receipt = parts
                    async def complete(self, message): self.completed = True
                follow = Continue()
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                key = Ed25519PrivateKey.generate()  # ephemeral fixture, no real key file
                context = SimpleNamespace(current_task=SimpleNamespace(id=task.id, context_id='fixture'), message=None)
                with mock.patch.object(seller, 'get_message_text', return_value=str(original_price)), \
                     mock.patch.object(seller, 'TaskUpdater', return_value=follow), \
                     mock.patch.object(seller, 'load_shop_key', return_value=key), \
                     mock.patch.object(seller, 'decide_turn', side_effect=AssertionError('model forbidden')):
                    real_loop(shop._execute(context, None))
                seller.flush_memory(strict=True)
                self.assertTrue(follow.completed)
                self.assertIsNone(follow.failed_message)
                self.assertIsNotNone(follow.receipt)
                from google.protobuf.json_format import MessageToDict
                receipt = MessageToDict(follow.receipt[0].data)
                receipt['price'] = int(receipt['price'])  # official protobuf Value numeric wire type
                self.assertEqual((receipt['price'], receipt['task'], receipt['unit']),
                                 (original_price, task.id, u['unit']))
                key.public_key().verify(seller.base64.b64decode(receipt['sig_seller']),
                    seller.canon({k:receipt[k] for k in ('shop','item','price','task')}))
                self.assertEqual(stock.agreed[u['unit']]['price'], original_price)
                self.assertEqual(deal['sol_margin_publication'], 'unknown')
                self.assertFalse(any(e['event']=='quoted' and e['task']==task.id for e in trial.events()))
                self.assertTrue(any(e['event']=='local_agreed' and e['task']==task.id for e in trial.events()))
                return
            if action == 'real_concurrent_quotes':
                if not study_on:
                    (trial.root/'controller.json').write_text('{"enabled":false}')
                barrier = threading.Barrier(2, timeout=3)
                original = trial.quote
                def synchronized_quote(*args, **kwargs):
                    barrier.wait()  # both actual workers are after pick, before hold
                    return original(*args, **kwargs)
                tasks = [SimpleNamespace(id=str(uuid.uuid4())) for _ in range(2)]
                updates = [Updater() for _ in tasks]
                async def concurrent():
                    await asyncio.gather(*(shop.open_from_stock(t, u['sku'], out,
                        lambda text, price=None: (text, price)) for t,out in zip(tasks,updates)))
                with mock.patch.object(trial, 'quote', synchronized_quote):
                    real_loop(concurrent())
                winners = [i for i,out in enumerate(updates) if out.quote is not None]
                self.assertEqual(len(winners), 1)
                winner = tasks[winners[0]].id
                self.assertEqual(stock.holder(u['unit']), winner)
                self.assertEqual(set(shop.open), {winner})
                self.assertEqual(sum(out.rejected is not None for out in updates), 1)
                return
            if action == 'concurrent_quotes':
                if not study_on:
                    (trial.root/'controller.json').write_text('{"enabled":false}')
                # Real coroutines, deliberately suspended at the new quote await
                # after both picks. No loop/socket or timing sleeps are needed.
                checkpoint = object()
                class Pause:
                    def __await__(self):
                        yield checkpoint
                async def step(function, *args, **kwargs):
                    if getattr(function, '__self__', None) is trial and function.__name__ == 'quote':
                        await Pause()
                    return function(*args, **kwargs)
                tasks = [SimpleNamespace(id=str(uuid.uuid4())) for _ in range(quote_count)]
                updates = [Updater() for _ in tasks]
                pending = [shop.open_from_stock(t, u['sku'], out, lambda text, price=None: (text, price))
                           for t, out in zip(tasks, updates)]
                # These coroutines are stepped without a loop, so the warranty
                # worker's cancellation drain (create_task, covered on real loops
                # in test_seller_warranty_trial) runs as the same stepped to_thread.
                async def warranty_inline(fn, *args, **kwargs):
                    return await asyncio.to_thread(fn, *args, **kwargs)
                if hasattr(seller, 'seller_warranty_trial'):  # absent in the be49 historical fixture
                    stack.enter_context(mock.patch.object(seller.seller_warranty_trial, 'finish_before_cancel',
                                                          warranty_inline))
                with mock.patch.object(asyncio, 'to_thread', step):
                    try:
                        for c in pending:
                            self.assertIs(c.send(None), checkpoint)
                        self.assertIsNone(stock.holder(u['unit']))
                        for c in pending:
                            with self.assertRaises(StopIteration):
                                c.send(None)
                    finally:
                        for c in pending:
                            c.close()
                self.assertEqual(sum(out.quote is not None for out in updates), 1)
                self.assertEqual(stock.holder(u['unit']), tasks[0].id)
                self.assertEqual(set(shop.open), {tasks[0].id})
                self.assertEqual(updates[0].quote[1], 291000 if study_on else 282270)
                self.assertTrue(all(out.rejected is not None for out in updates[1:]))
                lost = [e for e in trial.events() if e['event'] == 'selection_lost']
                self.assertEqual([e['task'] for e in lost], [t.id for t in tasks[1:]] if study_on else [])
                return
            def before(function, args):
                if getattr(function, '__self__', None) is trial and function.__name__ == 'ready':
                    if action == 'off_before_ready':
                        (trial.root/'controller.json').write_text('{"enabled":false}')
                    if action == 'io_before_ready':
                        (trial.root/'journal.sqlite3').rename(trial.root/'saved-journal.sqlite3')
                        (trial.root/'journal.sqlite3').mkdir()  # real sqlite open error, no permissions changed
                    if action == 'peer_before_ready':
                        peer_root = seller.LATE_CONCESSION.root
                        peer_root.mkdir(parents=True, exist_ok=True)
                        (peer_root/'seller_concession_trial.json').write_text(json.dumps(dict(
                            enabled=True, id='new-live-peer', start=time.time()-1, end=time.time()+600, seed=1)))
                    if action == 'baseline_before_ready':
                        changed = json.loads((root/'pricing.json').read_text())
                        changed['price']['ask_caps'] = {u['sku']: {'A': 280000}}
                        (root/'pricing.json').write_text(json.dumps(changed))
            if action in ('off_before_ready', 'io_before_ready', 'peer_before_ready', 'baseline_before_ready'):
                with self.assertRaises(margin.StudyError):
                    offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)), before)
                self.assertIsNone(up.quote)
                self.assertFalse(task.id in shop.open, 'prepared task retained')
                self.assertNotEqual(stock.holder(u['unit']), task.id)
                if action == 'io_before_ready': self.assertGreater(trial.observation_unknown,0)
                # The same task must not consume its previous prepared B on retry.
                offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                self.assertIsNone(up.quote)
                self.assertIsNotNone(up.rejected)
                return
            if action in ('transport_failure', 'transport_cancel'):
                with self.assertRaises(OSError if action == 'transport_failure' else asyncio.CancelledError):
                    offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                self.assertEqual(shop.open[task.id]['sol_margin_publication'], 'unknown')
                self.assertEqual(stock.holder(u['unit']), task.id)
                offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                self.assertIsNotNone(up.rejected)
                return
            offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
            if action == 'unknown_grade':
                self.assertEqual(up.quote[1], 282270)
                self.assertEqual(shop.open[task.id]['sol_margin']['reason'], 'grade_unknown')
                self.assertFalse(shop.open[task.id]['sol_margin']['eligible'])
                return
            self.assertEqual(up.quote[1], 291000)  # B 300000 then common .97 opening
            deal = shop.open[task.id]
            self.assertEqual(deal['open'], 300000)
            self.assertEqual(deal['sol_margin']['baseline'], 291000)
            # hard_limits.SELLER_GOODS_WARRANTY_HOURS is 72. Both arms use
            # the unchanged warranty_terms; a pricing max_hours=6 cannot lower it.
            baseline_deal = dict(kind='goods', id=u['sku'], reserve=deal['reserve'],
                                 **{'break': deal['break']})
            shop.warranty_terms(baseline_deal, u['sku'])
            self.assertEqual(baseline_deal['warranty'], 72)
            self.assertEqual(deal['warranty'], baseline_deal['warranty'])
            self.assertIn(deal['sol_margin'], trial.observations())
            ready = next(e for e in trial.events() if e['task'] == task.id and e['event'] == 'quote_ready')
            self.assertEqual(ready['baseline_final'], 282270)
            self.assertEqual(ready['final_price'], 291000)
            self.assertTrue(ready['actual_treatment'])
            self.assertEqual([e['price'] for e in trial.events()
                              if e['task'] == task.id and e['event'] == 'quoted'], [291000])
            if action == 'published_journal_failure':
                self.assertEqual(deal['sol_margin_publication'], 'published')
                (trial.root/'controller.json').write_text('{"enabled":false}')
                self.assertIs(shop.open[task.id], deal)  # published terms survive OFF
                (trial.root/'journal.sqlite3').rename(trial.root/'saved-journal.sqlite3')
                (trial.root/'journal.sqlite3').mkdir()
                shop._remember_now(task.id, deal, 'canceled', None, time.time())
                shop.free(task.id, deal)
                seller.flush_memory(strict=True)
                records = json.loads((root/'memory.json').read_text())['negotiations']
                self.assertTrue(any(r['task'] == task.id and r['result'] == 'canceled' for r in records))
                self.assertNotEqual(stock.holder(u['unit']), task.id)
                self.assertGreater(trial.observation_unknown, 0)

    def same_task_case(self, action, shop, trial, unit, task_id, stock):
        # Real SDK protobuf Task/Message, RequestContext and TaskUpdater; only the
        # public queue transport and forbidden model/key seams are fixtures.
        import seller
        from a2a.server.agent_execution.context import RequestContext
        from a2a.server.context import ServerCallContext
        from a2a.server.events import EventQueue
        from a2a.types.a2a_pb2 import (Message, Role, SendMessageRequest, Task,
            TaskState, TaskStatus, TaskStatusUpdateEvent, TaskArtifactUpdateEvent)
        task = Task(id=task_id, context_id='same-task-fixture',
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
        def context(text, *, initial=False):
            message=Message(message_id=str(uuid.uuid4()), task_id=task.id,
                context_id=task.context_id, role=Role.ROLE_USER, parts=[seller.new_text_part(text)])
            return RequestContext(ServerCallContext(), request=SendMessageRequest(message=message),
                                  task_id=task.id, context_id=task.context_id,
                                  task=None if initial else task)
        reached, release = threading.Event(), threading.Event()
        prepared, release_prepared = threading.Event(), threading.Event()
        duplicate, release_duplicate = threading.Event(), threading.Event()
        records, calls, state = [], [], {}
        first_transport_release = None
        class Capture(EventQueue):
            async def enqueue_event(self, event):
                who=asyncio.current_task().get_name()
                records.append((who,event))
                if isinstance(event,Task):
                    task.CopyFrom(event)  # first returnImmediately-visible task, before any quote
                if (action=='same_task_sending_lostack' and who=='initial'
                        and isinstance(event,TaskStatusUpdateEvent)
                        and event.status.state==TaskState.TASK_STATE_INPUT_REQUIRED):
                    reached.set()  # real updater enqueued a possibly delivered quote
                    await first_transport_release.wait()
                    raise OSError('fixture delivery before lost ACK')
        queue=Capture()
        ready, quote=trial.ready, trial.quote
        def paused_ready(*args,**kwargs):
            prepared.set()
            if action=='same_task_preopen_duplicate':
                if not release_prepared.wait(2): raise AssertionError('prepared checkpoint timeout')
            else:
                reached.set()
                if not release.wait(2): raise AssertionError('ready checkpoint timeout')
            return ready(*args,**kwargs)
        call_lock=threading.Lock()
        def paused_quote(*args,**kwargs):
            if args[0]!=task.id: return quote(*args,**kwargs)
            with call_lock:
                calls.append(threading.get_ident())
                ordinal=len(calls)
            if ordinal==1:
                result=quote(*args,**kwargs)  # first assignment is already durable
                reached.set()
                if not release.wait(2): raise AssertionError('quote checkpoint timeout')
                return result
            try:
                return quote(*args,**kwargs)
            except margin.StudyError:
                duplicate.set()
                if not release_duplicate.wait(2): raise AssertionError('duplicate checkpoint timeout')
                raise
        def rule(text, deal, floor, reader):
            offer=int(text) if text.isdecimal() else 0
            return dict(action='accept' if offer>=deal['ask'] else 'counter',
                        buyer_offer=offer, price=deal['ask'], message='fixture reply', why='fixture rule')
        async def wait_for(event):
            for _ in range(400):
                if event.is_set(): return
                await asyncio.sleep(.002)
            raise AssertionError('producer checkpoint not reached')
        async def run():
            nonlocal first_transport_release
            first_transport_release=asyncio.Event()
            initial=asyncio.create_task(shop.execute(context(unit['sku'],initial=True),queue),name='initial')
            follower=None
            try:
                await wait_for(reached)
                if 'prepared_off_' in action or action=='same_task_sending_lostack':
                    await asyncio.to_thread(trial.write_controller,{'enabled':False})
                    state['off_completed']=True
                text=(action.rsplit('_',1)[-1] if '_prepared_' in action else
                      unit['sku'] if action=='same_task_preopen_duplicate' else
                      '291000' if action=='same_task_sending_lostack' else 'price?')
                follower=asyncio.create_task((shop.cancel if text=='cancel' else shop.execute)(
                    context(text),queue),name='followup')
                await asyncio.sleep(.03)  # both real producers have had runnable turns
                state['pending_events']=sum(who=='followup' for who,_ in records)
                state['initial_waiting']=not initial.done()
                if action=='same_task_waiter_cancel':
                    state['waiter_cancelled']=follower.cancel()
                    await asyncio.gather(follower,return_exceptions=True)
                    state['owner_after_cancel']=stock.holder(unit['unit'])
                if action=='same_task_preopen_duplicate':
                    state['quote_calls_pending']=len(calls)
                    release.set()
                    await wait_for(prepared)  # first deal/hold now exists
                    release_duplicate.set()   # let the duplicate error race its abort
                    await asyncio.sleep(.03)
                    state['first_still_open']=task.id in shop.open
                    state['first_still_owner']=stock.holder(unit['unit'])==task.id
                release.set(); release_prepared.set(); first_transport_release.set()
                state['results']=await asyncio.gather(initial,follower,return_exceptions=True)
            finally:
                release.set(); release_prepared.set(); release_duplicate.set()
                first_transport_release.set()
                await asyncio.gather(initial,*([] if follower is None else [follower]),return_exceptions=True)
        with mock.patch.object(trial,'ready',paused_ready) if action!='same_task_sending_lostack' else contextlib.nullcontext(), \
                mock.patch.object(trial,'quote',paused_quote) if action=='same_task_preopen_duplicate' else contextlib.nullcontext(), \
                mock.patch.object(seller.fast_classify,'seller_turn',rule), \
                mock.patch.object(seller,'decide_turn',side_effect=AssertionError('model forbidden')), \
                mock.patch.object(seller,'load_shop_key',return_value=None):
            real_loop(run())
        seller.flush_memory(strict=True)
        self.assertEqual(seller.DRAIN.inflight,0)
        self.assertEqual(shop._task_turns,{})  # no gate retention/cap after all producers exit
        print('SAME_TASK '+json.dumps(dict(action=action,**{k:v for k,v in state.items() if k!='results'},
            results=[type(r).__name__ if isinstance(r,BaseException) else 'ok' for r in state['results']])),flush=True)
        self.assertTrue(state['initial_waiting'])
        self.assertEqual(state['pending_events'],0,'followup published/terminated the task before initial finished')
        self.assertIsInstance(records[0][1],Task)
        self.assertEqual(records[0][1].status.state,TaskState.TASK_STATE_SUBMITTED)
        for _,event in records:
            self.assertEqual(event.id if isinstance(event,Task) else event.task_id,task.id)
            self.assertEqual(event.context_id,task.context_id)
        events=[e for e in trial.events() if e['task']==task.id]
        if 'prepared_off_' in action:
            self.assertTrue(state['off_completed'])
            self.assertIsInstance(state['results'][0],margin.StudyError)
            self.assertFalse(any(isinstance(e,TaskArtifactUpdateEvent) for _,e in records))
            self.assertFalse(any(e['event']=='quoted' for e in events))
            self.assertNotIn(task.id,shop.open)
            self.assertNotEqual(stock.holder(unit['unit']),task.id)
            self.assertTrue(any(e['event']=='unpublished_abort' for e in events))
        elif action=='same_task_sending_lostack':
            self.assertIsInstance(state['results'][0],OSError)
            self.assertIsNone(state['results'][1])
            self.assertTrue(any(e['event']=='publication_unknown' for e in events))
            self.assertFalse(any(e['event']=='quoted' for e in events))
            receipt=next(e.artifact.parts[0].data for _,e in records if isinstance(e,TaskArtifactUpdateEvent))
            from google.protobuf.json_format import MessageToDict
            self.assertEqual(int(MessageToDict(receipt)['price']),291000)
            self.assertEqual(stock.agreed[unit['unit']]['price'],291000)
        else:
            self.assertIsNone(state['results'][0])
            self.assertEqual(sum(e['event']=='quoted' for e in events),1)
            first_event=next(i for i,(w,e) in enumerate(records) if w=='initial' and isinstance(e,TaskStatusUpdateEvent))
            self.assertTrue(all(i>first_event for i,(w,e) in enumerate(records) if w=='followup'))
            if action=='same_task_preopen_duplicate':
                self.assertEqual(state['quote_calls_pending'],1)
                self.assertTrue(state['first_still_open'])
                self.assertTrue(state['first_still_owner'])
                self.assertEqual(len(calls),1)
            if action=='same_task_waiter_cancel':
                self.assertTrue(state['waiter_cancelled'])
                self.assertEqual(state['owner_after_cancel'],task.id)
                self.assertIsInstance(state['results'][1],asyncio.CancelledError)
            if action.endswith('_cancel') and action!='same_task_waiter_cancel':
                self.assertNotIn(task.id,shop.open)
                self.assertNotEqual(stock.holder(unit['unit']),task.id)
                self.assertEqual(records[-1][1].status.state,TaskState.TASK_STATE_CANCELED)
            elif action.endswith('_291000'):
                self.assertEqual(stock.agreed[unit['unit']]['price'],291000)
            else:
                self.assertEqual(stock.holder(unit['unit']),task.id)

    def test_baseline_and_treatment_keep_the_72_hour_product_warranty(self):
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, MARKET_DIR=td, PYTHONTZPATH=''))
            import seller
            import shop_memory
            stack.enter_context(mock.patch.object(shop_memory, 'PRICING', str(Path(td)/'pricing.json')))
            # Explicit low max_hours proves the hard-limit minimum, not an output snapshot.
            (Path(td)/'pricing.json').write_text(json.dumps(dict(warranty=dict(max_hours=6))))
            shop = seller.Seller(None, 'anonymous-shop')
            baseline = dict(kind='goods', id='G00000000', reserve=119118, open=291000)
            treatment = dict(baseline, open=300000)
            shop.warranty_terms(baseline, 'G00000000')
            shop.warranty_terms(treatment, 'G00000000')
            self.assertEqual(baseline['warranty'], 72)
            self.assertEqual(treatment['warranty'], 72)


class ExactOutcomeTest(unittest.TestCase):
    def setUp(self):
        # Actual full ledger envelope and URL-valued shop; anonymized receipt/task/order.
        self.shop = 'https://anonymous.invalid/shop'
        self.ledger = [
            dict(seq=2129981, ts=1001.0, kind='result', data=dict(
                order='o-anonymous', buyer='anonymous-buyer', shop=self.shop, verified=True,
                receipt=dict(task='task-anonymous', price=36857, shop=self.shop, item='anonymous-item'))),
            dict(seq=2129982, ts=1002.0, kind='settle', data=dict(
                order='o-anonymous', buyer='anonymous-buyer', shop=self.shop,
                amount=36857, unit='u-anonymous', sku='G-anonymous', warranty_hours=6.0))]
        self.payments = [dict(order='o-anonymous', paid=True, ledger='2129982', price=36857)]

    def outcome(self):
        return margin.exact_outcome('task-anonymous', self.payments, self.ledger, self.shop)

    def test_malformed_rows_are_unknown_and_later_valid_rows_remain_accounted(self):
        original = self.ledger
        for bad in (None, [None, *original], [dict(seq=1, ts=1, kind='result', data=dict(
                shop=self.shop, receipt=dict(task='task-anonymous'), order=[], buyer={})) , *original],
                [*original, dict(seq=9, ts=1003, kind='broken', data=dict(
                    order='o-anonymous', buyer='anonymous-buyer', shop=self.shop))]):
            with self.subTest(case=type(bad).__name__):
                self.ledger = bad
                # The last broken row is well formed here; remove its seq to test the actual bad case.
                if isinstance(bad, list) and len(bad) == 3 and isinstance(bad[-1], dict) and bad[-1].get('kind') == 'broken':
                    bad[-1].pop('seq')
                outcome = self.outcome()
                self.assertEqual(outcome['status'], 'unknown')
                if bad is not None:
                    self.assertGreaterEqual(outcome['valid_rows'], 2)
        self.ledger = original

    def test_exact_result_task_payment_seq_buyer_amount_join(self):
        self.assertEqual(self.outcome()['status'], 'confirmed')

    def test_verified_alias_binding_preserves_receipt_alias_and_ledger_url(self):
        self.ledger[0]['data']['receipt']['shop'] = 'anonymous-shop-alias'
        binding = dict(ledger_url=self.shop, receipt_alias='anonymous-shop-alias',
                       provenance='parent-verified-shop-binding', evidence_sha256='a'*64)
        result = margin.exact_outcome('task-anonymous', self.payments, self.ledger, binding)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['identity_evidence_sha256'], 'a'*64)
        binding['receipt_alias'] = 'wrong-alias'
        self.assertEqual(margin.exact_outcome('task-anonymous', self.payments, self.ledger, binding)['status'], 'unknown')
        self.assertEqual(self.outcome()['status'], 'unknown')
        self.payments.clear()
        binding['receipt_alias'] = 'anonymous-shop-alias'
        self.assertEqual(margin.exact_outcome('task-anonymous', self.payments, self.ledger, binding)['status'], 'unknown')

    def test_settle_without_payment_is_unknown(self):
        self.payments.clear()
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_official_settle_observation_is_separate_from_private_payment_confirmation(self):
        self.payments.clear()
        result = self.outcome()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['official_settle']['status'], 'observed')
        self.assertEqual(result['official_settle']['seq'], 2129982)
        self.ledger[1]['data']['amount'] += 1
        self.assertEqual(self.outcome()['official_settle']['status'], 'unknown')
        self.ledger[1]['data']['amount'] -= 1
        self.ledger[0]['data']['receipt']['shop'] = 'wrong-alias'
        self.assertEqual(self.outcome()['official_settle']['status'], 'unknown')

    def test_last_refusal_and_mismatched_amount_never_confirm(self):
        self.payments.append(dict(order='o-anonymous', paid=False))
        self.assertEqual(self.outcome()['status'], 'refused')
        self.payments.pop()
        self.ledger[1]['data']['amount'] += 1
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_buyer_task_or_shop_mismatch_and_duplicate_settle_are_unknown(self):
        self.ledger[1]['data']['buyer'] = 'other-anonymous-buyer'
        self.assertEqual(self.outcome()['status'], 'unknown')
        self.ledger[1]['data']['buyer'] = 'anonymous-buyer'
        self.ledger.append(self.ledger[1])
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_unverified_result_cannot_link_a_seller_task_to_payment(self):
        self.ledger[0]['data']['verified'] = False
        self.assertEqual(self.outcome()['status'], 'unknown')
