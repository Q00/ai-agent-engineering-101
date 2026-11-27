"""sol-sku-policy-v1: facts, validation/clamps, cadence/cap, fallback, TTL. Offline: fake model calls, temp state."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sol_policy

HERE = Path(__file__).resolve().parent


def unit(uid, sku='S1', cost=50, lst=100, kind='goods', grade='A', supplied_at=1000, expires_at=None):
    return dict(unit=uid, sku=sku, name=f'item {sku}', category='x>y', grade=grade, kind=kind, cost=cost,
                list_price=lst, expires_at=expires_at, supplied_at=supplied_at)


def reply(skus, returned='gpt-6.1-sol'):
    return lambda prompt: {'text':json.dumps({'skus':skus}), 'requested':'gpt-6.1-sol', 'returned':returned,
                           'http_status':200, 'usage':{'input':900, 'output':120, 'reasoning':0}, 'latency_s':1.5,
                           'finish_reason':'stop', 'cost_usd':0.003, 'error':None, 'reasoning_effort':'low'}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)
        self.now = 1_800_000_000.0
        self.inv = {'round':3, 'storage_per_day':0.01,
                    'units':[unit('u1', supplied_at=self.now-7200), unit('u2', cost=60, supplied_at=self.now-3600),
                             unit('f1', sku='F1', cost=10, lst=30, kind='food', expires_at=self.now+5*3600)]}
        (self.state/'inventory.json').write_text(json.dumps(self.inv), encoding='utf-8')
        (self.state/'catalog.json').write_text(json.dumps({'S1':{'name':'item S1', 'list_price':100}}), encoding='utf-8')
        self.enable()

    def tearDown(self):
        self._tmp.cleanup()

    def enable(self, **kw):
        (self.state/sol_policy.CONFIG).write_text(json.dumps(dict({'enabled':True}, **kw)), encoding='utf-8')

    def calls(self):
        p = self.state/sol_policy.CALLS
        return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


class ModuleTests(Base):
    def test_module_is_the_candidate_not_a_root_shadow(self):
        self.assertEqual(Path(sol_policy.__file__).resolve().parent, HERE)

    def test_config_defaults_and_fail_closed(self):
        self.assertEqual(sol_policy.config(self.state), {'cadence_minutes':30, 'max_calls_24h':24, 'ttl_minutes':120})
        for bad in ({'enabled':True, 'max_calls_24h':0}, {'enabled':True, 'cadence_minutes':'30'},
                    {'enabled':True, 'ttl_minutes':True}, {'enabled':'yes'}):
            (self.state/sol_policy.CONFIG).write_text(json.dumps(bad), encoding='utf-8')
            self.assertIsNone(sol_policy.config(self.state), bad)

    def test_active_needs_the_hash_gate(self):
        sol_policy._GATE.update(at=0.0, state=None)
        with patch('restock_r3.approved', return_value=False):
            self.assertFalse(sol_policy.active(self.state, now=1.0))
        sol_policy._GATE.update(at=0.0, state=None)
        with patch('restock_r3.approved', return_value=True):
            self.assertTrue(sol_policy.active(self.state, now=100.0))
        sol_policy._GATE.update(at=0.0, state=None)


class FactsTests(Base):
    def test_facts_are_code_computed_and_missing_comparables_never_invented(self):
        f, snap = sol_policy.facts(self.state, self.inv, self.now)
        s = f['S1']
        self.assertEqual((s['units'], s['cost_min'], s['cost_max'], s['cost_total'], s['list_price']), (2, 50, 60, 110, 100))
        self.assertEqual(s['inventory_age_hours'], {'min':1.0, 'max':2.0})
        self.assertEqual(s['storage_cost_per_day'], 1.1)
        self.assertEqual(s['own_settles']['n'], 0)
        self.assertIsNone(s['own_settles']['median'])
        self.assertEqual(s['comparables']['n'], 0)
        self.assertTrue(s['comparables']['missing'])
        self.assertIsNone(s['comparables']['anchor'])
        self.assertEqual(s['baseline_start'], 90)  # 90% of list: the tested baseline
        self.assertEqual(f['F1']['food_life_hours_min'], 5.0)
        self.assertEqual(len(snap['facts_sha256']), 64)

    def test_own_exact_settlements_from_the_cache(self):
        cache = {'own_ledger':{'11':{'sku':'S1', 'price':80}, '12':{'sku':'S1', 'price':90}, '13':{'sku':'X', 'price':5}},
                 'own_urls':['https://me.example'], 'price_rows':[
                     {'kind':'settle', 'seq':12, 'ts':self.now-600, 'data':{'shop':'https://me.example/', 'item':'S1'}}]}
        f, _ = sol_policy.facts(self.state, self.inv, self.now, cache=cache)
        own = f['S1']['own_settles']
        self.assertEqual((own['n'], own['ids'], own['prices'], own['median'], own['median_over_list']), (2, [11, 12], [80, 90], 85.0, 0.85))
        self.assertEqual(f['S1']['sellthrough_own_2h'], 1)


class ValidateTests(Base):
    def test_wrong_sku_nan_noninteger_rejected_and_clamps_recorded(self):
        f, _ = sol_policy.facts(self.state, self.inv, self.now)
        parsed = {'skus':{'NOPE':{'start_ask':1, 'target_settle':1, 'min_accept':1},
                          'F1':{'start_ask':float('nan'), 'target_settle':20, 'min_accept':15},
                          'S1':{'start_ask':150, 'target_settle':40, 'min_accept':10.0, 'rationale_refs':['own_cost', 'evil', 99]}}}
        entries, clamps, rejected = sol_policy.validate(parsed, f)
        self.assertEqual(entries['S1']['start_ask'], 100)     # list cap
        self.assertEqual(entries['S1']['min_accept'], 50)     # cost_min floor (each unit re-floored at its own cost)
        self.assertEqual(entries['S1']['target_settle'], 50)  # within [min, start]
        self.assertEqual(entries['S1']['rationale_refs'], ['own_cost'])
        self.assertEqual({r['sku']:r['reason'] for r in rejected}, {'NOPE':'wrong_sku', 'F1':'non_integer_or_nan'})
        self.assertEqual({(c['sku'], c['field']) for c in clamps},
                         {('S1', 'start_ask'), ('S1', 'min_accept'), ('S1', 'target_settle'), ('S1', 'rationale_refs')})

    def test_strings_and_bools_are_not_prices(self):
        f, _ = sol_policy.facts(self.state, self.inv, self.now)
        for bad in ('90', True, None, math.inf):
            e, _, r = sol_policy.validate({'skus':{'S1':{'start_ask':bad, 'target_settle':80, 'min_accept':70}}}, f)
            self.assertEqual(e, {})


class RefreshTests(Base):
    def test_valid_reply_persists_policy_with_provenance(self):
        got = sol_policy.refresh(self.state, now=self.now, call=reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}}))
        self.assertEqual(got['status'], 'updated')
        p = json.loads((self.state/sol_policy.POLICY).read_text(encoding='utf-8'))
        self.assertEqual((p['returned_model'], p['version'], p['usage']['input'], p['latency_s']), ('gpt-6.1-sol', sol_policy.VERSION, 900, 1.5))
        self.assertEqual(p['expires_at'], self.now+7200)
        self.assertEqual(p['skus']['S1']['start_ask'], 97)
        self.assertEqual(len(p['root_sha256']), 64)
        c = self.calls()
        self.assertEqual([r['event'] for r in c], ['call', 'result'])  # durable call row BEFORE the result
        self.assertEqual(c[1]['returned'], 'gpt-6.1-sol')

    def test_invalid_json_api_error_wrong_model_keep_last_valid(self):
        sol_policy.refresh(self.state, now=self.now, call=reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}}))
        before = (self.state/sol_policy.POLICY).read_bytes()
        bad = [lambda p: {'text':'not json', 'returned':'gpt-6.1-sol'},
               lambda p: {'text':'', 'error':'http_status', 'http_status':500},
               lambda p: {'text':json.dumps({'skus':{'NOPE':{'start_ask':1, 'target_settle':1, 'min_accept':1}}}), 'returned':'gpt-6.1-sol'}]
        for i, call in enumerate(bad):
            got = sol_policy.refresh(self.state, now=self.now+i+1, call=call, force=True)
            self.assertEqual(got['status'], 'kept_last_valid', got)
            self.assertEqual((self.state/sol_policy.POLICY).read_bytes(), before)
        entry, prov = sol_policy.policy_for(self.state, unit('u1'), self.now+10)
        self.assertEqual(entry['start_ask'], 97)

    def test_no_policy_and_error_means_baseline(self):
        got = sol_policy.refresh(self.state, now=self.now, call=lambda p: {'text':'', 'error':'ConnectError'})
        self.assertEqual(got['status'], 'baseline')
        self.assertEqual(sol_policy.policy_for(self.state, unit('u1'), self.now), (None, 'no valid sol policy'))

    def test_stale_policy_falls_back_to_baseline(self):
        sol_policy.refresh(self.state, now=self.now, call=reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}}))
        self.assertEqual(sol_policy.policy_for(self.state, unit('u1'), self.now+7200), (None, 'sol policy outside TTL'))

    def test_mixed_cost_sku_min_is_not_lifted_to_the_most_expensive_unit(self):
        # live 2026-11-12 G2b7e0fe3 scaled /100: costs 37..111, list 148, own paid median 87; one 111 unit forced
        # min_accept 111 on every unit, so offers 87..92 on a 37-cost unit were countered and 0 paid in 4.7 h
        inv = {'round':3, 'storage_per_day':0.01,
               'units':[unit('p1', sku='P1', cost=37, lst=148, supplied_at=self.now-3600),
                        unit('p2', sku='P1', cost=111, lst=148, supplied_at=self.now-3600)]}
        (self.state/'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
        f, _ = sol_policy.facts(self.state, inv, self.now)
        self.assertNotIn('cost_max <= min_accept', sol_policy.prompt(f, {}))
        entries, clamps, _ = sol_policy.validate({'skus':{'P1':{'start_ask':118, 'target_settle':110, 'min_accept':80}}}, f)
        self.assertEqual(entries['P1']['min_accept'], 80)
        self.assertEqual([c for c in clamps if c['field'] == 'min_accept'], [])
        sol_policy.refresh(self.state, now=self.now, inv=inv,
                           call=reply({'P1':{'start_ask':118, 'target_settle':110, 'min_accept':80}}))
        cheap, _ = sol_policy.policy_for(self.state, inv['units'][0], self.now+1)
        dear, prov = sol_policy.policy_for(self.state, inv['units'][1], self.now+1)
        self.assertEqual(cheap['min_accept'], 80)
        self.assertEqual(dear['min_accept'], 111)            # still never below this unit's own cost
        self.assertIn('min_accept', prov['unit_clamps'])
        lo, _, _ = sol_policy.validate({'skus':{'P1':{'start_ask':118, 'target_settle':110, 'min_accept':5}}}, f)
        self.assertEqual(lo['P1']['min_accept'], 37)         # never below the cheapest unit's cost either

    def test_unit_reclamp_never_below_this_units_cost(self):
        sol_policy.refresh(self.state, now=self.now, call=reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}}))
        entry, prov = sol_policy.policy_for(self.state, unit('u9', cost=80), self.now+1)
        self.assertEqual(entry, {'start_ask':97, 'target_settle':88, 'min_accept':80})
        self.assertEqual(prov['unit_clamps'], ['min_accept'])


class CadenceTests(Base):
    def test_cadence_material_change_and_rolling_cap(self):
        ok = reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}})
        self.assertEqual(sol_policy.refresh(self.state, now=self.now, call=ok)['status'], 'updated')
        self.assertEqual(sol_policy.refresh(self.state, now=self.now+60, call=ok)['reason'], 'within cadence')
        self.assertEqual(sol_policy.refresh(self.state, now=self.now+1900, call=ok)['reason'], 'no material change')
        self.inv['units'].append(unit('u3', cost=55))
        (self.state/'inventory.json').write_text(json.dumps(self.inv), encoding='utf-8')
        self.assertEqual(sol_policy.refresh(self.state, now=self.now+1901, call=ok)['status'], 'updated')
        self.assertEqual(sol_policy.refresh(self.state, now=self.now+1902+5400, call=ok)['status'], 'updated')  # near expiry

    def test_max_calls_in_rolling_day_even_when_forced(self):
        self.enable(max_calls_24h=2)
        bad = lambda p: {'text':'', 'error':'http_status'}
        sol_policy.refresh(self.state, now=self.now, call=bad)
        sol_policy.refresh(self.state, now=self.now+400, call=bad)
        got = sol_policy.refresh(self.state, now=self.now+800, call=bad, force=True)
        self.assertEqual(got['status'], 'not_due')
        self.assertTrue(got['reason'].startswith('call cap 2/2'))
        self.assertEqual(sum(r['event'] == 'call' for r in self.calls()), 2)
        self.assertEqual(sol_policy.refresh(self.state, now=self.now+86401, call=bad)['status'], 'baseline')  # day rolled

    def test_disabled_makes_no_call(self):
        (self.state/sol_policy.CONFIG).unlink()
        called = []
        self.assertEqual(sol_policy.refresh(self.state, now=self.now, call=lambda p: called.append(p))['status'], 'disabled')
        self.assertEqual(called, [])



class MalformedPolicyTests(Base):
    def good(self):
        sol_policy.refresh(self.state, now=self.now, call=reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}}))
        return json.loads((self.state/sol_policy.POLICY).read_text(encoding='utf-8'))

    def mutations(self):
        nan, inf = float('nan'), float('inf')
        yield 'created nan', lambda p: p.update(created_at=nan)
        yield 'expires inf', lambda p: p.update(expires_at=inf)
        yield 'expires before created', lambda p: p.update(expires_at=p['created_at']-1)
        yield 'ttl over max', lambda p: p.update(expires_at=p['created_at']+13*3600)
        yield 'created bool', lambda p: p.update(created_at=True)
        yield 'no evidence', lambda p: p.pop('evidence')
        yield 'evidence list', lambda p: p.update(evidence=[1])
        yield 'facts sha bad', lambda p: p['evidence'].update(facts_sha256='zz')
        yield 'policy id bad', lambda p: p.update(policy_id=7)
        yield 'root sha missing', lambda p: p.pop('root_sha256')
        yield 'dated model', lambda p: p.update(returned_model='gpt-6.1-sol-2026-01-01')
        yield 'skus list', lambda p: p.update(skus=[])
        yield 'entry missing key', lambda p: p['skus']['S1'].pop('min_accept')
        yield 'entry float', lambda p: p['skus']['S1'].update(start_ask=97.0)
        yield 'entry nan', lambda p: p['skus']['S1'].update(target_settle=nan)
        yield 'entry unordered', lambda p: p['skus']['S1'].update(min_accept=99)
        yield 'entry not dict', lambda p: p['skus'].update(S1='97')
        yield 'not object', lambda p: p.clear()

    def test_malformed_persisted_policy_fails_closed_without_raising(self):
        base = self.good()
        for name, mutate in self.mutations():
            with self.subTest(name=name):
                p = json.loads(json.dumps(base))
                mutate(p)
                (self.state/sol_policy.POLICY).write_text(json.dumps(p), encoding='utf-8')
                self.assertIsNone(sol_policy.current(self.state))
                entry, why = sol_policy.policy_for(self.state, unit('u1'), self.now+1)
                self.assertIsNone(entry)
        (self.state/sol_policy.POLICY).write_text('{not json', encoding='utf-8')
        self.assertEqual(sol_policy.policy_for(self.state, unit('u1'), self.now+1)[0], None)

    def test_dated_returned_model_is_not_accepted(self):
        got = sol_policy.refresh(self.state, now=self.now, call=reply({'S1':{'start_ask':97, 'target_settle':88, 'min_accept':75}},
                                                                     returned='gpt-6.1-sol-2026-01-01'))
        self.assertEqual(got['status'], 'baseline')
        self.assertFalse((self.state/sol_policy.POLICY).exists())


if __name__ == '__main__':
    unittest.main()
