"""Rank cohort, assignment and confirmed-payment regressions (offline only)."""
import json
import os
import tempfile
import unittest
import concurrent.futures
import contextlib
import fcntl
import io
from pathlib import Path
from unittest import mock

import buyer_rank_pg as rp
import test_sw_route as sw_route

NOW = 1794140000.0


def measured(**extra):
    return dict(ts=NOW-60, order="o-fixture", shop="b", kind="goods", state="TASK_STATE_FAILED",
                passed_to_kit=False, price=None, seconds={"quotes": 1, "end": 3},
                quotes=[{"shop": "short", "ask": 4500, "limit": 5000, "fit": True},
                        {"shop": "b", "ask": 7400, "limit": 7000, "fit": False}], **extra)


class TierPopulation(unittest.TestCase):
    def test_short_fit_does_not_remove_normal_nofit_from_learning(self):
        rk = {"stats": {}}
        picked = sw_route.ShortWarrantyRouteTest().run_bq({"a": 4500, "b": 7400, "c": 8000}, {"a": 0}, rk=rk)
        self.assertEqual(picked, "b")
        self.assertEqual(rk["selection"]["tier"], "normal")
        self.assertTrue(rk["selection"]["eligible"])
        st = rp.stats_from_rows([measured(rank_selection=rk["selection"])], {}, NOW)
        self.assertEqual(st["nofit"]["shops"]["b|goods"]["n"], 1)

    def test_legacy_tier_is_explicitly_unknown(self):
        st = rp.stats_from_rows([measured()], {}, NOW)
        self.assertEqual(st["counts"]["nofit_population_unknown"], 1)
        self.assertEqual(st["nofit"]["shops"], {})

    def test_b_selection_and_learning_use_normal_tier_even_with_short_fit(self):
        rk = dict(nofit=True, stats={"nofit": {"pool": {}, "shops": {"b|goods": {"p": 0.01}, "c|goods": {"p": 0.9}}}})
        picked = sw_route.ShortWarrantyRouteTest().run_bq({"a": 4500, "b": 7400, "c": 8000}, {"a": 0}, rk=rk)
        self.assertEqual(picked, "c")
        r = measured(rank_selection=rk['selection'])
        r.update(shop='c')
        r['quotes'].append(dict(shop='c', ask=8000, limit=7000, fit=False))
        st = rp.stats_from_rows([r], {}, NOW)
        self.assertEqual(st['nofit']['shops']['c|goods']['n'], 1)


class AssignmentJournal(unittest.TestCase):
    def switch(self, md):
        sw = dict(enabled=True, nofit="ab", nofit_version=2, nofit_experiment="fixture", nofit_epoch=NOW-100)
        with open(os.path.join(md, rp.SWITCH), "w") as f:
            json.dump(sw, f)
        return sw

    def test_assignment_survives_cold_cache_and_exception(self):
        for outcome in (None, RuntimeError("broken"), SystemExit(9)):
            with self.subTest(outcome=type(outcome).__name__), tempfile.TemporaryDirectory() as md:
                self.switch(md)
                with mock.patch.object(rp, "stats", side_effect=outcome if isinstance(outcome, BaseException) else None,
                                       return_value=None):
                    try:
                        rk = rp.state(md, {"order": "o-early"}, now=NOW)
                    except SystemExit:
                        pass
                    else:
                        self.assertIn(rk["nofit_arm"], ("A", "B"))
                        self.assertFalse(rk["nofit"])
                        self.assertEqual(rk["treatment"], "untreated")
                itt = rp.reconcile_itt(md, "fixture", NOW-100, end=NOW+1)
                self.assertEqual(itt["total"], 1)
                self.assertEqual(itt["assignment"]["unknown"], 0)
                self.assertEqual(itt['treatment']['unknown' if isinstance(outcome, SystemExit) else 'untreated'], 1)

    def test_concurrent_assignments_and_invalid_types_stay_in_itt(self):
        with tempfile.TemporaryDirectory() as md:
            self.switch(md)
            with mock.patch.object(rp, "stats", return_value=None):
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                    list(ex.map(lambda i: rp.state(md, {"order": "o-"+str(i)}, now=NOW), range(24)))
                for bad in ([], 12, None, {"order": []}):
                    rk = rp.state(md, bad, now=NOW)
                    self.assertEqual(rk["assignment"], "unknown")
            report = rp.reconcile_itt(md, "fixture", NOW-100, end=NOW+1)
            self.assertEqual(report["total"], 28)
            self.assertEqual(report["assignment"]["unknown"], 4)

    def test_old_switch_cannot_activate_corrected_experiment(self):
        with tempfile.TemporaryDirectory() as md:
            for old in (True, "ab"):
                with open(os.path.join(md, rp.SWITCH), "w") as f:
                    json.dump(dict(enabled=True, nofit=old), f)
                with mock.patch.object(rp, "stats", return_value={}):
                    self.assertFalse(rp.state(md, {"order": "o-1"}, now=NOW)["nofit"])

    def test_full_epoch_orders_are_not_limited_to_rank_success(self):
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            rows = [dict(ts=NOW, order="o-nostock", kind="goods", state="NO_SHOP_IN_STOCK"),
                    dict(ts=NOW, order=[], kind="goods"), dict(ts=NOW, order="o-food", kind="food")]
            with open(os.path.join(md, "measure/orders.jsonl"), "w") as f:
                f.write("".join(json.dumps(r)+"\n" for r in rows))
            out = rp.reconcile_itt(md, "fixture", NOW-100, end=NOW+1)
            self.assertEqual(out["total"], 3)
            self.assertEqual(out["assignment"]["unknown"], 3)
            self.assertEqual({r['order'] for r in out['orders']}, {'o-nostock', 'o-food', None})

    def test_cold_locked_cache_keeps_assignment(self):
        with tempfile.TemporaryDirectory() as md:
            self.switch(md)
            with open(os.path.join(md, rp.CACHE)+".lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                rk = rp.state(md, {"order": "o-lock"}, now=NOW)
            self.assertIn(rk["nofit_arm"], ("A", "B"))
            self.assertEqual(rk["untreated_reason"], "cache_unavailable")
            self.assertEqual(rp.reconcile_itt(md, "fixture", NOW-100, NOW+1)["total"], 1)

    def test_partial_concurrent_journal_writes_do_not_interleave(self):
        write = os.write
        with tempfile.TemporaryDirectory() as md:
            self.switch(md)
            with mock.patch.object(rp, "stats", return_value=None), mock.patch.object(os, "write", side_effect=lambda fd, data: write(fd, data[:17])):
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                    list(ex.map(lambda i: rp.state(md, {"order": "partial-"+str(i)}, now=NOW), range(24)))
            out = rp.reconcile_itt(md, "fixture", NOW-100, NOW+1)
            self.assertEqual(out["total"], 24)
            self.assertEqual(out["inputs"]["journal"]["malformed_lines"], 0)
            self.assertEqual(out["treatment"]["untreated"], 24)

    def test_failed_partial_write_does_not_erase_next_pre_cache_assignment(self):
        write = os.write
        for failure in (OSError('disk write failed'), SystemExit(9), 0):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as md:
                self.switch(md)
                journal = Path(md, rp.JOURNAL)
                calls = 0

                def interrupted_write(fd, data):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return write(fd, data[:17])
                    if isinstance(failure, BaseException):
                        raise failure
                    return failure

                with mock.patch.object(rp, 'stats', return_value=None), \
                     mock.patch.object(os, 'write', side_effect=interrupted_write):
                    if isinstance(failure, SystemExit):
                        with self.assertRaises(SystemExit):
                            rp.state(md, {'order': 'o-interrupted'}, now=NOW)
                    else:
                        first = rp.state(md, {'order': 'o-interrupted'}, now=NOW)
                        self.assertTrue(first['journal_error'])
                partial = journal.read_bytes()
                self.assertEqual(len(partial), 17)
                self.assertFalse(partial.endswith(b'\n'))

                appended = []
                journal_append = rp._journal

                def capture_append(*args):
                    result = journal_append(*args)
                    appended.append(result)
                    return result

                with mock.patch.object(rp, 'stats', side_effect=SystemExit(10)), \
                     mock.patch.object(rp, '_journal', side_effect=capture_append) as append:
                    with self.assertRaises(SystemExit):
                        rp.state(md, {'order': 'o-next'}, now=NOW)
                    self.assertEqual(append.call_count, 1)
                self.assertEqual(appended, [True])
                out = rp.reconcile_itt(md, 'fixture', NOW-100, NOW+1)
                self.assertEqual(out['total'], 1)
                self.assertEqual(out['orders'][0]['order'], 'o-next')
                self.assertEqual(out['orders'][0]['event'], 'assignment')
                self.assertFalse(out['orders'][0]['has_order'])
                self.assertEqual(out['assignment']['unknown'], 0)
                self.assertEqual(out['treatment']['unknown'], 1)
                self.assertEqual(out['inputs']['journal']['malformed_lines'], 1)
                self.assertFalse(out['coverage_certified'])
                self.assertFalse(out['all_observed_rows_linked'])
                self.assertTrue(journal.read_bytes().startswith(partial + b'\n'))
                self.assertFalse(Path(md, 'measure/orders.jsonl').exists())

    def test_tail_separator_write_failure_is_retryable_without_truncation(self):
        with tempfile.TemporaryDirectory() as md:
            self.switch(md)
            journal = Path(md, rp.JOURNAL)
            partial = b'{"assignment":'
            journal.write_bytes(partial)
            with mock.patch.object(os, 'write', side_effect=OSError('separator failed')), \
                 mock.patch.object(rp, 'stats', return_value=None):
                failed = rp.state(md, {'order': 'o-separator-failed'}, now=NOW)
            self.assertTrue(failed['journal_error'])
            self.assertFalse(failed['nofit'])
            self.assertEqual(journal.read_bytes(), partial)
            with mock.patch.object(rp, 'stats', side_effect=SystemExit(10)):
                with self.assertRaises(SystemExit):
                    rp.state(md, {'order': 'o-retry'}, now=NOW)
            out = rp.reconcile_itt(md, 'fixture', NOW-100, NOW+1)
            self.assertEqual([r['order'] for r in out['orders']], ['o-retry'])
            self.assertEqual(out['inputs']['journal']['malformed_lines'], 1)
            self.assertTrue(journal.read_bytes().startswith(partial + b'\n'))

    def test_main_no_stock_and_exception_before_quotes_are_journaled(self):
        import market_buyer as mb
        for crash in (False, True):
            with self.subTest(crash=crash), tempfile.TemporaryDirectory() as md:
                self.switch(md)
                shops = Path(md)/"shops.json"
                shops.write_text("[]")
                order = Path(md)/"order.json"
                order.write_text(json.dumps(dict(order="o-main", kind="goods", acceptable=["G1"])))
                argv = ['market_buyer', '--shops', str(shops), '--order', str(order), '--item', 'fixture', '--budget', '10000']
                with mock.patch('sys.argv', argv), mock.patch.object(mb, 'MEMORY', str(Path(md)/'memory.json')), \
                     mock.patch.object(mb, 'AGENT_DIR', mb.AGENT_DIR), \
                     mock.patch.object(rp, 'stats', return_value=None), mock.patch.object(mb, 'log'), \
                     mock.patch.object(mb, 'warm_ledger', side_effect=RuntimeError('early') if crash else None), \
                     contextlib.redirect_stdout(io.StringIO()):
                    if crash:
                        with self.assertRaises(RuntimeError):
                            mb.main()
                    else:
                        self.assertEqual(mb.main(), 0)
                out = rp.reconcile_itt(md, "fixture", NOW-100, end=__import__('time').time()+1)
                self.assertEqual(out['total'], 1)
                self.assertEqual(out['assignment']['unknown'], 0)

    def test_noquote_and_real_treatment_have_distinct_journal_status(self):
        from test_rank_nofit import NoFitPickTest
        with tempfile.TemporaryDirectory() as md:
            self.switch(md)
            b_order = next('o-'+str(i) for i in range(100) if rp.nofit_arm(self.switch(md), {'order': 'o-'+str(i)}) == 'B')
            with mock.patch.object(rp, 'stats', return_value=NoFitPickTest().rk()['stats']):
                rk = rp.state(md, {'order': b_order}, now=NOW)
            NoFitPickTest().run_bq({'b':7400, 'c':8000}, rk)
            out = rp.reconcile_itt(md, 'fixture', NOW-100, NOW+1)
            self.assertEqual(out['treatment']['applied'], 1)
            with mock.patch.object(rp, 'stats', return_value=NoFitPickTest().rk()['stats']):
                rk2 = rp.state(md, {'order': 'o-noquotes'}, now=NOW)
            NoFitPickTest().run_bq({'b':None, 'c':None}, rk2)
            out = rp.reconcile_itt(md, 'fixture', NOW-100, NOW+1)
            self.assertEqual(out['treatment']['untreated'], 1)
            self.assertEqual(out['treatment']['applied'], 1)
            self.assertEqual(next(r['untreated_reason'] for r in out['orders'] if r['order']=='o-noquotes'), 'no_quotes')


class ConfirmedPayments(unittest.TestCase):
    def compute(self, payment=None, settle=None, omit=False):
        fixture = json.loads(Path(__file__).with_name("rank_paid_fixture.json").read_text())
        orders = [dict(r, shop="b", kind="goods", seconds={"end": 3, "quotes": 1}) for r in fixture["orders"]]
        pays = fixture["payments"]
        if payment is not None:
            pays[-1].update(payment)
        if omit:
            pays = pays[:-1]
        if settle is not None:
            fixture["ledger"][-1].update(settle)
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            for path, rows in (("measure/orders.jsonl", orders), ("payments.jsonl", pays), ("ledger_rows.jsonl", fixture["ledger"])):
                with open(os.path.join(md, path), "w") as f:
                    f.write("".join(json.dumps(r)+"\n" for r in rows))
            return rp.compute(md, NOW+4000, buyer=fixture["buyer"])

    def test_actual_schema_confirmed_and_refused(self):
        c = self.compute()["shops"]["b|goods"]
        self.assertEqual((c["confirmed"], c["refused"], c["unknown"]), (1, 1, 0))
        self.assertEqual(c["p"], 0.5)

    def test_missing_paid_and_wrong_exact_keys_are_unknown(self):
        for opts in (dict(omit=True), dict(payment={"ledger": 4105937}), dict(settle={"amount": 13515}),
                     dict(settle={"buyer": "other"}), dict(settle={"order": "o-other"}),
                     dict(payment={"paid": "true"}), dict(payment={"ledger": []}), dict(settle={"amount": True})):
            with self.subTest(opts=opts):
                c = self.compute(**opts)["shops"]["b|goods"]
                self.assertEqual((c["confirmed"], c["refused"], c["unknown"]), (0, 1, 1))
                self.assertEqual(c["resolved_n"], 1)

    def test_numeric_string_seq_matches_real_non_one_seq(self):
        self.assertEqual(self.compute(payment={"ledger": "4105936"})["shops"]["b|goods"]["confirmed"], 1)

    def test_unconfirmed_completed_is_not_success_or_zero_observation(self):
        r = measured()
        r.update(state=rp.DONE, passed_to_kit=True, price=7000)
        c = rp.stats_from_rows([r], {}, NOW)["shops"]["b|goods"]
        self.assertEqual(c["k"], 0)
        self.assertEqual((c["k"], c["unknown"], c["resolved_n"]), (0, 1, 0))
        self.assertIsNone(c["p"])

    def test_last_payment_and_url_shaped_order_are_exact(self):
        oid = 'https://orders.invalid/example'
        orders = [dict(order=oid)]
        p = dict(order=oid, paid=True, ledger='4105936', price=13514)
        s = dict(k='settle', seq=4105936, order=oid, buyer='fixture-buyer', amount=13514)
        self.assertEqual(rp.payment_outcomes(orders, [p], [s], 'fixture-buyer')[oid], 'confirmed')
        self.assertEqual(rp.payment_outcomes(orders, [p, dict(p, paid=False)], [s], 'fixture-buyer')[oid], 'refused')
        self.assertEqual(rp.payment_outcomes(orders, [p, dict(p, paid=None)], [s], 'fixture-buyer')[oid], 'unknown')

    def test_real_prefix_provenance_does_not_assume_seq_one(self):
        obs = self.compute()['observation']['inputs']['ledger']
        self.assertEqual(obs['first_seq'], 4085925)
        self.assertEqual(obs['first_ts'], 1794139300.029)
        self.assertEqual(obs['max_seq'], 4105936)
        self.assertEqual(len(obs['first_row_sha256']), 64)

    def test_settlement_outside_bounded_tail_remains_unknown(self):
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md,'measure'))
            r = measured()
            r.update(state=rp.DONE, passed_to_kit=True, price=13514)
            Path(md,'measure/orders.jsonl').write_text(json.dumps(r)+'\n')
            Path(md,'payments.jsonl').write_text(json.dumps(dict(order='o-fixture',paid=True,ledger=4105936,price=13514))+'\n')
            ledger = [dict(k='settle',seq=4105936,order='o-fixture',buyer='nocha12',amount=13514)]
            ledger += [dict(k='other',seq=4105937+i,padding='x'*100) for i in range(100)]
            Path(md,'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in ledger))
            with mock.patch.object(rp,'TAIL_BYTES',4096):
                st = rp.compute(md,NOW)
            self.assertEqual(st['shops']['b|goods']['unknown'],1)
            self.assertTrue(st['observation']['inputs']['ledger']['truncated'])
            self.assertIsNone(st['shops']['b|goods']['p'])


class MalformedIsolation(unittest.TestCase):
    def test_completed_with_malformed_delivery_flag_is_unknown(self):
        r=measured()
        r.update(state=rp.DONE,price=7000,passed_to_kit=[])
        st=rp.stats_from_rows([r,measured()],{},NOW)
        c=st['shops']['b|goods']
        self.assertEqual((c['unknown'],c['failed']),(1,1))
        self.assertEqual(st['counts']['malformed_rows'],1)

    def test_malformed_row_then_normal_row_preserves_valid_learning(self):
        for patch in (dict(order=[]), dict(seconds=[1]), dict(quotes=17), dict(kind=[]), dict(shop=[]),
                      dict(state=[]), dict(price=[]), dict(ts=float('inf')), dict(quotes=[17, {}])):
            with self.subTest(patch=patch):
                bad = measured()
                bad.update(state=rp.DONE, passed_to_kit=True, price=7000)
                bad.update(patch)
                good = measured()
                st = rp.stats_from_rows([bad, good], {}, NOW)
                self.assertGreaterEqual(st["shops"]["b|goods"]["failed"], 1)
                self.assertGreaterEqual(st["counts"]["malformed_rows"], 1)

    def test_non_dict_and_unhashable_order_are_explicitly_unknown_or_excluded(self):
        bad = measured()
        bad.update(order=[], state=rp.DONE, passed_to_kit=True, price=7000)
        st = rp.stats_from_rows([[], None, "invalid", bad, measured()], {}, NOW)
        c = st["shops"]["b|goods"]
        self.assertEqual((c["n"], c["unknown"], c["failed"]), (2, 1, 1))
        self.assertEqual(st["counts"]["excluded_rows"], 3)
        self.assertEqual(st["counts"]["malformed_rows"], 4)

    def test_compute_reports_parse_errors_and_preserves_following_row(self):
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md,'measure'))
            Path(md,'measure/orders.jsonl').write_text('bad-json\n[]\n'+json.dumps(measured())+'\n')
            st=rp.compute(md,NOW)
            self.assertEqual(st['counts']['malformed_input_lines'],2)
            self.assertEqual(st['shops']['b|goods']['failed'],1)
            self.assertTrue(st['observation']['inputs']['payments']['missing'])


if __name__ == "__main__":
    unittest.main()
