"""slot-recovery-v1 completion corrections (2026-11-12). Offline temp states / fake HTTP client only.
1. Terra: reasoning_effort 'none' (Terra does not support 'minimal'); nano unchanged; the RETURNED model is recorded
   separately from the requested one and a mismatch fails closed (no decision, never a retry on nano).
2. Financial reconciliation without physical unit mapping: an exact durable supply result (ledger 'supplied' + exact
   total, agreeing with the row's exact kit evidence) replaces the request's upper-bound reserve by its exact cost, counted
   once; that confirmed cost is discharged only by exact observed pilot unit costs of the SKU, the rest stays held as
   unmatched confirmed capital risk. Never oldest-first, never by sale/missing snapshot/TTL; cash is not debited twice."""
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import test_oneday_pilot as t

HERE = Path(__file__).resolve().parent
UB = 25549  # oneday_pilot.upper_bound(22710), SKU K


class Client:
    def __init__(self, returned='gpt-6.1-terra', status=200):
        self.calls, self.returned, self.status = [], returned, status

    def post(self, url, headers=None, json=None):
        import httpx
        self.calls.append(json)
        body = {'choices':[{'finish_reason':'stop', 'message':{'content':'{"action":"reject"}'}}],
                'usage':{'prompt_tokens':7, 'completion_tokens':3}}
        if self.returned is not None: body['model'] = self.returned
        return httpx.Response(self.status, request=httpx.Request('POST', url), json=body)


class TerraPayloadAndReturnedModel(unittest.TestCase):
    def call(self, model, returned):
        import openai_harness
        self.assertEqual(Path(openai_harness.__file__).resolve().parent, HERE)
        c, err = Client(returned), io.StringIO()
        with redirect_stderr(err):
            out = openai_harness.complete('p', client=c, api_key='k', model=model)
        return out, c.calls, err.getvalue()

    def test_terra_uses_reasoning_effort_none_with_json_chat_and_token_cap(self):
        out, calls, _ = self.call('gpt-6.1-terra', 'gpt-6.1-terra')
        self.assertEqual(out, '{"action":"reject"}')
        self.assertEqual(len(calls), 1)
        p = calls[0]
        self.assertEqual((p['model'], p['reasoning_effort']), ('gpt-6.1-terra', 'none'))
        self.assertEqual((p['response_format'], p['max_completion_tokens']), ({'type':'json_object'}, 256))
        self.assertNotIn('tools', p)

    def test_nano_payload_unchanged(self):
        import openai_harness
        out, calls, _ = self.call(openai_harness.MODEL, openai_harness.MODEL)
        self.assertEqual(out, '{"action":"reject"}')
        self.assertEqual(calls[0]['reasoning_effort'], 'minimal')

    def test_returned_model_logged_separately_from_requested(self):
        _, _, err = self.call('gpt-6.1-terra', 'gpt-6.1-terra-2026-10-01')
        line = [l for l in err.splitlines() if l.startswith('[openai] {')][0]
        rec = json.loads(line[len('[openai] '):])
        self.assertEqual((rec['requested'], rec['returned']), ('gpt-6.1-terra', 'gpt-6.1-terra-2026-10-01'))
        self.assertEqual((rec['input'], rec['output']), (7, 3))

    def test_dated_snapshot_of_requested_model_is_accepted(self):
        out, _, _ = self.call('gpt-6.1-terra', 'gpt-6.1-terra-2026-10-01')
        self.assertEqual(out, '{"action":"reject"}')

    def test_returned_model_mismatch_or_missing_fails_closed_without_retry(self):
        for returned in ('gpt-5-nano-2025-08-07', 'gpt-6.1-terra-mini', 'gpt-6.1', '', None):
            with self.subTest(returned=returned):
                out, calls, err = self.call('gpt-6.1-terra', returned)
                self.assertEqual(out, '')
                self.assertEqual([c['model'] for c in calls], ['gpt-6.1-terra'])
                self.assertIn('model_mismatch', err)


def rows_for(inv, index, qty, sku='K', at=t.NOW-50, ub=UB):
    import oneday_pilot
    req = {'invocation':inv, 'index':index, 'sku':sku, 'qty':qty, 'log':'nowhere.log', 'log_offset':0}
    return [{'sku':sku, 'at':at, 'reserve':ub, 'category':'c'+sku, 'policy_version':'bounded-v2',
             'one_day_pilot':oneday_pilot.PILOT, 'baseline_unit_ids':[], 'matched_unit_id':None, 'evidence_ids':[],
             'local_biased_memory_ids':[], 'request':dict(req, unit=k)} for k in range(qty)]


def supplied(rows, total, result='supplied'):
    for r in rows:
        r.update(supply_result=result, supply_evidence={'anchor':'a', 'entry':{}, 'http_status':200, 'total':total})
    return rows


def unit(uid, cost, at=t.NOW-40, sku='K'):
    return {'unit':uid, 'sku':sku, 'cost':cost, 'kind':'food', 'grade':'A', 'supplied_at':at,
            'expires_at':t.NOW+700*3600}


class FinancialReconciliation(t.Base):
    """Every fixture is constructed (not a live replay)."""
    def stage(self, *requests, units=(), observe=True, balance=t.BALANCE):
        """requests: (rows, qty, ub). Ledger request entries are durable BEFORE rows (as _oneday_plan does)."""
        import oneday_pilot
        if not (self.p/oneday_pilot.CONFIG).exists(): t.setup(self.p, balance=balance)
        led = t.read(self.p, oneday_pilot.LEDGER)
        led.update(requests={}, units={}, events={})  # constructed fixture: each stage starts from a clean pilot ledger
        rows = []
        for rs, qty, ub in requests:
            req = rs[0]['request']
            led['requests'][oneday_pilot.request_key(req)] = {
                'sku':rs[0]['sku'], 'qty':qty, 'unit_ub':ub, 'ub':qty*ub, 'at':rs[0]['at'], 'status':None,
                'total':None, 'evidence_ratio':1.0, 'evidence_ids':[]}
            rows += rs
        t.write(self.p, oneday_pilot.LEDGER, led)
        t.write(self.p, 'restock-pending.json', t.LEGACY+rows)
        t.inventory(self.p, balance, units)
        if observe:
            oneday_pilot.observe(self.p, rows, t.read(self.p, 'inventory.json'),
                                 t.read(self.p, 'public-settlements.json'), t.NOW)
        return rows

    def test_successful_receipt_replaces_ub_by_exact_cost_snapshot_lag_and_cash_not_debited_twice(self):
        # Two units supplied for an exact 30,000 (balance already debited); neither visible yet (snapshot lag).
        self.stage((supplied(rows_for('invA', 0, 2), 30000), 2, UB))
        c = t.ctx(self.p)
        self.assertEqual(c['concurrent'], 30000)  # exact confirmed cost, not 2 x 25,549 UB
        self.assertEqual(c['new_reserve'], 0)
        self.assertEqual(c['cash_room'], t.BALANCE-245080-5000000)  # the confirmed debit is not subtracted again
        self.assertEqual(c['lifetime_committed'], 30000)  # counted once
        self.assertEqual(c['financial']['unmatched_confirmed']['K']['cost'], 30000)
        self.assertEqual(c['financial']['recovery'], 'partial_conservative')

    def test_arrival_discharges_only_by_exact_observed_cost_without_unit_mapping(self):
        rows = supplied(rows_for('invA', 0, 2), 30000)
        self.stage((rows, 2, UB), units=[unit('u1', 14000), unit('u2', 16000)])
        c = t.ctx(self.p)
        self.assertEqual(c['concurrent'], 30000)  # on-hand 30,000 + unmatched 0: no double count
        self.assertEqual(c['by_sku']['K'], 30000)
        self.assertTrue(all(r['matched_unit_id'] is None for r in t.read(self.p, 'restock-pending.json')))
        # partial arrival: one unit seen, the rest of the exact cost stays held
        self.stage((supplied(rows_for('invB', 0, 2), 30000), 2, UB), units=[unit('u1', 14000)])
        self.assertEqual(t.ctx(self.p)['concurrent'], 14000+16000)

    def test_sale_between_snapshots_keeps_confirmed_cost_held(self):
        self.stage((supplied(rows_for('invA', 0, 1), 15000), 1, UB))
        c = t.ctx(self.p, t.NOW+3600)  # unit never observed (sold unseen or still lagging): no release by time
        self.assertEqual(c['concurrent'], 15000)
        self.assertEqual(c['financial']['unmatched_confirmed']['K']['cost'], 15000)

    def test_observed_then_sold_unit_coverage_is_exact_and_once(self):
        import oneday_pilot
        self.stage((supplied(rows_for('invA', 0, 1), 15000), 1, UB), units=[unit('u1', 15000)])
        t.inventory(self.p, t.BALANCE, [])  # sold between snapshots after being observed: ledger unit stays recorded
        oneday_pilot.observe(self.p, [], t.read(self.p, 'inventory.json'), t.read(self.p, 'public-settlements.json'),
                             t.NOW+60)
        self.assertEqual(t.ctx(self.p, t.NOW+60)['concurrent'], 0)

    def test_unknown_result_keeps_full_upper_bound(self):
        self.stage((rows_for('invA', 0, 2), 2, UB), units=[unit('u1', 14000)])
        c = t.ctx(self.p)
        self.assertEqual(c['concurrent'], 14000+2*UB)
        self.assertEqual(c['new_reserve'], 2*UB)

    def test_unknown_and_confirmed_mix_is_an_aggregate_upper_bound(self):
        a = supplied(rows_for('invA', 0, 1), 15000)
        b = rows_for('invB', 0, 1, at=t.NOW-45)
        self.stage((a, 1, UB), (b, 1, UB), units=[unit('u1', 15000)])
        self.assertEqual(t.ctx(self.p)['concurrent'], 15000+UB+0)

    def test_pending_written_before_ledger_observe_crash_keeps_ub(self):
        self.stage((supplied(rows_for('invA', 0, 2), 30000), 2, UB), observe=False)
        c = t.ctx(self.p)
        self.assertEqual(c['concurrent'], 2*UB)
        self.assertEqual(c['lifetime_committed'], 2*UB)

    def test_supplied_without_exact_total_keeps_ub(self):
        self.stage((supplied(rows_for('invA', 0, 2), None), 2, UB))
        self.assertEqual(t.ctx(self.p)['concurrent'], 2*UB)

    def test_duplicate_rows_count_confirmed_cost_once(self):
        import copy
        rows = supplied(rows_for('invA', 0, 2), 30000)
        self.stage((rows+copy.deepcopy(rows), 2, UB))
        c = t.ctx(self.p)
        self.assertEqual(c['concurrent'], 30000)
        self.assertEqual(c['lifetime_committed'], 30000)

    def test_conflicting_partial_evidence_fails_closed_and_holds_ub(self):
        import oneday_pilot
        rows = supplied(rows_for('invA', 0, 2), 30000)
        self.stage((rows, 2, UB))
        pend = t.read(self.p, 'restock-pending.json')
        pend[-1]['supply_evidence']['total'] = 29000  # one row disagrees with the durable ledger total
        t.write(self.p, 'restock-pending.json', pend)
        c = t.ctx(self.p)
        self.assertFalse(c['active'])
        self.assertEqual(c['reason'], 'financial_evidence_conflict')
        self.assertGreaterEqual(c['concurrent'], 2*UB)
        led = t.read(self.p, oneday_pilot.LEDGER)
        led['requests']['invA:0']['status'] = 'refused'
        led['requests']['invA:0']['total'] = None
        t.write(self.p, oneday_pilot.LEDGER, led)
        t.write(self.p, 'restock-pending.json', t.LEGACY+rows)
        self.assertEqual(t.ctx(self.p)['reason'], 'financial_evidence_conflict')

    def test_units_supplied_before_the_request_never_cover_it(self):
        self.stage((supplied(rows_for('invA', 0, 1), 15000), 1, UB), units=[unit('u0', 15000, at=t.ACT-280)])
        c = t.ctx(self.p)
        self.assertEqual(c['financial']['unmatched_confirmed']['K']['cost'], 15000)

    def test_bounds_room_and_legacy_unchanged(self):
        self.stage((supplied(rows_for('invA', 0, 2), 30000), 2, UB))
        c = t.ctx(self.p)  # existing fail-closed gate kept: a supplied unit not yet observed pauses new acquisition
        self.assertEqual((c['active'], c['reason']), (False, 'unattributed_acquisition'))
        self.stage((supplied(rows_for('invA', 0, 2), 30000), 2, UB), units=[unit('u1', 14000), unit('u2', 16000)])
        c = t.ctx(self.p)
        self.assertTrue(c['active'], c)
        self.assertEqual(c['room'], 100000-30000)  # stage cap binds on exact confirmed cost
        self.assertEqual((c['legacy_count'], c['legacy_reserve']), (4, 245080))
        self.stage((supplied(rows_for('invA', 0, 4), 100000), 4, UB), units=[unit(f'u{k}', 25000) for k in range(4)])
        c = t.ctx(self.p)
        self.assertEqual((c['active'], c['reason']), (False, 'capacity_full'))


if __name__ == '__main__':
    unittest.main()
