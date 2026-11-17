"""Risk-exception replenishment pilot: frozen legacy UNKNOWN ledger + bounded, expiring, hash-bound extra allowance.
Offline fixtures only. Legacy rows are never released, matched or absorbed; new unknowns are never exempt."""
import json
import tempfile
import unittest
from pathlib import Path

OWN = 'https://own.example'
START = 5000000
NOW = 100
ACT = 90  # pilot activation time used by the fixture


def settle(seq, sku, amount=8000):
    return {'seq':seq, 'kind':'settle', 'round':3, 'data':{'shop':OWN, 'item':sku, 'amount':amount}}


def item(cat, price, kind='goods', **kw):
    return dict(name=f'Fixture {cat}', category=cat, kind=kind, list_price=price, **kw)


CATALOG = {'L':item('cL', 59480), 'A':item('cA', 12000), 'B':item('cB', 36000), 'C':item('cC', 20000),
           'D':item('cD', 25000), 'U':item('cU', 9500), 'K':item('cK', 70000), 'S':item('cS', 5000, 'food', shelf_hours=12),
           'Z':item('cZ', 3860)}
# Proven = two distinct exact own settle ids; U and Z have one. U (9500) exceeds the normal bounded-v2 exposure
# left by the legacy reserve (250000-2191-238440 = 9369), so like live the normal path alone buys nothing.
ROWS = [settle(i, s) for i, s in enumerate(['L', 'L', 'A', 'A', 'B', 'B', 'C', 'C', 'D', 'D', 'K', 'K', 'S', 'S',
                                             'U', 'Z'], 10)]


def legacy(sku, at, n=1):
    return [{'sku':sku, 'at':at, 'reserve':CATALOG[sku]['list_price'], 'category':CATALOG[sku]['category'],
             'policy_version':'bounded-v2', 'baseline_unit_ids':[], 'matched_unit_id':None, 'evidence_ids':[1, 2],
             'local_biased_memory_ids':[]} for _ in range(n)]


LEGACY = legacy('L', 10, 3)+legacy('A', 20, 2)+legacy('B', 30)  # 6 rows, reserve 238440
MATCHED = {'sku':'B', 'at':5, 'reserve':0, 'category':'cB', 'policy_version':'bounded-v2', 'baseline_unit_ids':[],
           'matched_unit_id':'u-old'}
Z = {'unit':'u-z1', 'sku':'Z', 'cost':2191, 'category':'cZ'}


def read(p, name):
    return json.loads((p/name).read_text(encoding='utf-8'))


def setup(p, units=(Z,), caps=None, gate_extra=None, legacy_rows=LEGACY, balance=5629511, act=ACT):
    import risk_pilot
    (p/'inventory.json').write_text(json.dumps({'round':3, 'balance':balance, 'start':START, 'units':list(units)}))
    (p/'catalog.json').write_text(json.dumps(CATALOG))
    (p/'public-settlements.json').write_text(json.dumps({'source_url':'https://market.ourolabs.ai/api/ledger?after=-500',
                                                         'fetched_at':50, 'own_urls':[OWN+'/'], 'rows':ROWS}))
    data = json.dumps(list(legacy_rows)+[MATCHED], indent=2).encode()
    (p/'restock-pending.json').write_bytes(data)
    backup = p/'backup-pending.json'
    backup.write_bytes(data)
    hashes = risk_pilot.prepare(p, act, backup, caps=caps, authorization='fixture')
    gate = {'approved':True, 'policy_version':'bounded-v2', 'source_sha256':{}, 'risk_pilot':hashes}
    gate.update(gate_extra or {})
    (p/'seller-v2-approved.json').write_text(json.dumps(gate))
    return hashes


def plan(p, now=NOW):
    import restock_r3, supply_journal
    return restock_r3.plan(p, now, supply_journal.begin(p, now))['buy']


def legacy_rows_now(p):
    import risk_pilot
    rows = read(p, 'restock-pending.json')
    return [r for r in rows if 'request' not in r and r.get('matched_unit_id') is None]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)/'.market'
        self.p.mkdir()

    def tearDown(self):
        self.tmp.cleanup()


class LedgerTests(Base):
    def test_ledger_freezes_original_unmatched_rows_with_stable_ids(self):
        import hashlib, risk_pilot
        setup(self.p)
        led = read(self.p, risk_pilot.LEDGER)
        self.assertEqual(led['count'], 6)
        self.assertEqual(led['reserve'], 238440)
        self.assertEqual([r['row'] for r in led['rows']], LEGACY)  # original rows, matched row excluded
        self.assertEqual(len({r['id'] for r in led['rows']}), 6)  # identical rows still get distinct ids
        self.assertEqual(led['source_pending_sha256'],
                         hashlib.sha256((self.p/'backup-pending.json').read_bytes()).hexdigest())
        again = risk_pilot.freeze((self.p/'backup-pending.json').read_bytes())
        self.assertEqual([r['id'] for r in again['rows']], [r['id'] for r in led['rows']])
        self.assertIsNotNone(risk_pilot.load_ledger(self.p))

    def test_corrupt_or_tampered_ledger_is_rejected(self):
        import risk_pilot
        setup(self.p)
        path = self.p/risk_pilot.LEDGER
        led = read(self.p, risk_pilot.LEDGER)
        led['rows'][0]['row']['reserve'] = 1
        path.write_text(json.dumps(led))
        self.assertIsNone(risk_pilot.load_ledger(self.p))
        path.write_text('{not json')
        self.assertIsNone(risk_pilot.load_ledger(self.p))

    def test_prepare_validates_against_backup_and_is_one_shot(self):
        import risk_pilot
        setup(self.p)
        with self.assertRaises(ValueError):  # a second arming would recycle the lifetime budget
            risk_pilot.prepare(self.p, ACT, self.p/'backup-pending.json', authorization='again')
        q = self.p.parent/'other'
        q.mkdir()
        (q/'restock-pending.json').write_text(json.dumps(LEGACY[1:]))  # live pending lost a legacy row
        (q/'backup.json').write_text(json.dumps(LEGACY))
        with self.assertRaises(ValueError):
            risk_pilot.prepare(q, ACT, q/'backup.json', authorization='x')

    def test_source_gate_binds_pilot_helper(self):
        import restock_r3
        self.assertIn('risk_pilot.py', restock_r3.SOURCES)


class PilotPlanTests(Base):
    def test_guarded_allowance_buys_cheapest_proven_low_price_with_exact_request_ids(self):
        import risk_pilot
        setup(self.p)
        buy = plan(self.p)
        # C (20000, need 2) is the cheapest proven low-price SKU with room; A is at TARGET via legacy rows;
        # U unproven, K high price, S short-shelf food are never chosen.
        self.assertEqual(buy, [{'sku':'C', 'qty':2}])
        rows = [r for r in read(self.p, 'restock-pending.json') if 'request' in r]
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['request']['unit'] for r in rows], [0, 1])
        self.assertTrue(all(r['risk_pilot'] == risk_pilot.PILOT for r in rows))
        ids = set(read(self.p, risk_pilot.COUNTERS)['planned'])
        self.assertEqual(ids, {risk_pilot.request_id(r['request']) for r in rows})

    def test_old_legacy_rows_stay_untouched_even_when_same_sku_unit_arrives(self):
        setup(self.p)
        before = legacy_rows_now(self.p)
        inv = read(self.p, 'inventory.json')
        inv['units'].append({'unit':'u-late-a', 'sku':'A', 'cost':9000, 'category':'cA'})
        (self.p/'inventory.json').write_text(json.dumps(inv))
        plan(self.p)
        self.assertEqual(legacy_rows_now(self.p), before)  # not matched, not released, reserve intact

    def test_combined_risk_cap_counts_full_legacy_reserve(self):
        setup(self.p, caps={'max_combined_risk':270000})
        self.assertEqual(plan(self.p), [{'sku':'C', 'qty':1}])  # 270000-238440-2191 = 29369

    def test_combined_units_and_active_caps(self):
        setup(self.p, caps={'max_combined_units':8})
        self.assertEqual(plan(self.p), [{'sku':'C', 'qty':1}])  # 8-6 legacy-1 on hand
        q = self.p.parent/'b'
        q.mkdir()
        setup(q, units=[dict(Z, unit=f'u-z{i}') for i in range(3)])
        self.assertEqual(plan(q), [])  # active on hand 3 already

    def test_cash_floor_still_applies(self):
        setup(self.p, balance=.8*START+238440+10000)
        self.assertEqual(plan(self.p), [])

    def test_corrupt_ledger_or_gate_mismatch_blocks_pilot(self):
        import risk_pilot
        setup(self.p)
        led = read(self.p, risk_pilot.LEDGER)
        led['note'] = 'edited'
        (self.p/risk_pilot.LEDGER).write_text(json.dumps(led))
        self.assertEqual(plan(self.p), [])
        for i, extra in enumerate(({'approved':False}, {'risk_pilot':{'config_sha256':'0'*64, 'ledger_sha256':'0'*64}})):
            q = self.p.parent/f'g{i}'
            q.mkdir()
            setup(q, gate_extra=extra)
            self.assertEqual(plan(q), [], extra)

    def test_changed_legacy_row_blocks_pilot(self):
        setup(self.p)
        rows = read(self.p, 'restock-pending.json')
        rows[0]['evidence_ids'] = [9]  # still counted by the normal path, but no longer the frozen original
        (self.p/'restock-pending.json').write_text(json.dumps(rows))
        self.assertEqual(plan(self.p), [])

    def test_duplicate_unit_ids_block_pilot(self):
        setup(self.p, units=[Z, dict(Z)])
        self.assertEqual(plan(self.p), [])

    def test_new_unknown_is_never_exempt_and_blocks_further_buys(self):
        import risk_pilot
        setup(self.p)
        self.assertEqual(len(plan(self.p)), 1)
        self.assertEqual(plan(self.p, NOW+60), [])  # pilot rows still unresolved: fail closed
        self.assertEqual(read(self.p, risk_pilot.LEDGER)['count'], 6)  # never absorbed into the frozen ledger
        out = [r for r in read(self.p, 'restock-pending.json') if r.get('matched_unit_id') is None]
        self.assertEqual(sum(r['reserve'] for r in out), 238440+40000)  # still fully counted

    def test_restart_idempotent_counters_and_missing_counters_fail_closed(self):
        import risk_pilot
        setup(self.p)
        entry = {'r1':{'sku':'C', 'list':20000, 'at':NOW}}
        risk_pilot.record(self.p, entry)
        risk_pilot.record(self.p, entry)  # replay after restart
        self.assertEqual(list(read(self.p, risk_pilot.COUNTERS)['planned']), ['r1'])
        (self.p/risk_pilot.COUNTERS).unlink()
        self.assertEqual(plan(self.p), [])

    def test_counter_write_failure_plans_nothing(self):
        import risk_pilot
        from unittest.mock import patch
        setup(self.p)
        with patch.object(risk_pilot, 'record', side_effect=OSError('disk')):
            self.assertEqual(plan(self.p), [])
        self.assertFalse(any('request' in r for r in read(self.p, 'restock-pending.json')))

    def test_expiry_closes_pilot(self):
        import risk_pilot
        setup(self.p)
        cfg = read(self.p, risk_pilot.CONFIG)
        self.assertEqual(cfg['expires_at'], ACT+24*3600)
        self.assertEqual(plan(self.p, cfg['expires_at']), [])

    def test_lifetime_budget_not_recycled_and_unit_transition_not_double_counted(self):
        import risk_pilot
        setup(self.p)
        self.assertEqual(plan(self.p), [{'sku':'C', 'qty':2}])  # 40000 of the 50000 lifetime budget
        inv = read(self.p, 'inventory.json')
        inv['units'] += [{'unit':'u-c1', 'sku':'C', 'cost':15000}, {'unit':'u-c2', 'sku':'C', 'cost':15000}]
        (self.p/'inventory.json').write_text(json.dumps(inv))
        plan(self.p, NOW+10)  # exact-evidence rule: no inferred pending->unit match; both rows stay at full reserve
        ctx = risk_pilot.last(self.p)
        self.assertEqual(ctx['active_units'], 5)
        self.assertEqual(ctx['combined_risk'], 238440+2191+30000+2*CATALOG['C']['list_price'])
        inv['units'] = [Z]  # both sold; matched rows pruned later
        (self.p/'inventory.json').write_text(json.dumps(inv))
        self.assertEqual(plan(self.p, NOW+400), [])  # 10000 budget left buys nothing: no recycling by sales
        self.assertEqual(risk_pilot.last(self.p)['used_budget'], 40000)
        self.assertEqual(risk_pilot.last(self.p)['used_units'], 2)


class ArmedNoFallthroughTests(Base):
    """Once pilot artifacts exist: integrity failure leaves pending bytes untouched; any inactive pilot denies buys
    (never falls through to the baseline bounded-v2 planner)."""
    def test_corrupt_ledger_keeps_legacy_rows_byte_identical_even_with_matching_unit(self):
        import risk_pilot
        setup(self.p)
        (self.p/risk_pilot.LEDGER).write_text('{corrupt')
        inv = read(self.p, 'inventory.json')
        inv['units'].append({'unit':'u-late-a', 'sku':'A', 'cost':9000, 'category':'cA'})
        (self.p/'inventory.json').write_text(json.dumps(inv))
        before = (self.p/'restock-pending.json').read_bytes()
        self.assertEqual(plan(self.p), [])
        self.assertEqual((self.p/'restock-pending.json').read_bytes(), before)

    def test_missing_ledger_with_config_keeps_pending_bytes(self):
        import risk_pilot
        setup(self.p)
        (self.p/risk_pilot.LEDGER).unlink()
        inv = read(self.p, 'inventory.json')
        inv['units'].append({'unit':'u-late-a', 'sku':'A', 'cost':9000, 'category':'cA'})
        (self.p/'inventory.json').write_text(json.dumps(inv))
        before = (self.p/'restock-pending.json').read_bytes()
        self.assertEqual(plan(self.p), [])
        self.assertEqual((self.p/'restock-pending.json').read_bytes(), before)

    def test_expired_or_inactive_pilot_never_falls_through_to_baseline(self):
        import restock_r3, risk_pilot
        small = legacy('A', 20, 1)  # baseline bounded-v2 would have room and buy cheap candidates
        q = self.p.parent/'control'
        q.mkdir()
        setup(q, legacy_rows=small)
        for n in (risk_pilot.CONFIG, risk_pilot.LEDGER, risk_pilot.COUNTERS): (q/n).unlink()
        self.assertTrue(plan(q))  # control: no pilot artifacts ever -> original baseline behaviour buys
        setup(self.p, legacy_rows=small)
        cfg = read(self.p, risk_pilot.CONFIG)
        self.assertEqual(plan(self.p, cfg['expires_at']), [])  # expired
        self.assertEqual(restock_r3.plan(self.p, NOW)['buy'], [])  # no invocation id
        self.assertTrue(plan(self.p))  # active pilot buys
        self.assertEqual(plan(self.p, NOW+60), [])  # new unknown outstanding: no baseline fallthrough
        (self.p/risk_pilot.COUNTERS).write_text('{bad')
        self.assertEqual(plan(self.p, NOW+120), [])


if __name__ == '__main__':
    unittest.main()
