"""microecon-v1: read-only capital-pool views and own realized contribution per SKU (one-day pilot ledger only).
Offline fixtures only. Views are arithmetic over already approved capital, never new money and never additive."""
import json
import unittest
from pathlib import Path

from test_oneday_pilot import ACT, NOW, BALANCE, Base, setup, ctx, read, inventory

DAY = 86400


def led_with(units, events, conflicts=None):
    out = {'pilot':'one-day-v1', 'requests':{}, 'units':units, 'events':events}
    if conflicts is not None: out['conflicts'] = conflicts
    return out


def u(sku, cost, kind='food', supplied=NOW, gone=NOW+DAY, **kw):
    out = {'sku':sku, 'cost':cost, 'kind':kind, 'grade':'A', 'supplied_at':supplied, 'gone_at':gone,
           'expires_at':NOW+30*DAY if kind == 'food' else None}
    out.update(kw)
    return out


def sale(uid, sku, price, seq, ts):
    return {f'settle:{seq}':{'kind':'settle', 'seq':seq, 'ts':ts, 'unit':uid, 'sku':sku, 'price':price}}


def broke(uid, refunded, seq, ts):
    return {f'broken:{seq}':{'kind':'broken', 'seq':seq, 'ts':ts, 'unit':uid, 'refunded':refunded}}


INV = {'balance':BALANCE, 'start':5000000, 'storage_per_day':.01, 'units':[]}


class Accounting(Base):
    def test_capital_identity_no_double_count(self):
        import microeconomics
        setup(self.p)
        units = [{'unit':'u-h1', 'sku':'C', 'cost':8000, 'kind':'food', 'supplied_at':NOW, 'expires_at':NOW+9*DAY}]
        inventory(self.p, units=units)
        c = ctx(self.p)
        self.assertTrue(c['active'])
        a = microeconomics.accounting(c, read(self.p, 'inventory.json'))
        self.assertEqual(a['inventory_cost'], 8000)
        self.assertEqual(a['new_pending_reserve'], 0)
        self.assertEqual(a['legacy_UNKNOWN_reserve'], 245080)  # separate full liability, never in the stage pool
        self.assertEqual(a['free_pool'], 100000-8000)
        self.assertEqual(a['lifetime_spend_remaining'], 1000000-c['lifetime_committed'])
        self.assertEqual(a['gross_loss_headroom'], 120000-c['gross_loss'])
        self.assertEqual(a['observed_balance'], BALANCE)
        # cash room already subtracts legacy + new reserves once from the observed balance: reported, not re-subtracted
        self.assertEqual(a['cash_room'], BALANCE-245080-0-5000000)
        self.assertEqual(a['actionable_acquisition_room'], c['room'])
        self.assertEqual(a['actionable_acquisition_room'],
                         min(a['free_pool'], a['lifetime_spend_remaining'], a['cash_room'], a['bank_room']))
        self.assertFalse(a['additive'])

    def test_inactive_context_has_zero_actionable_room_but_keeps_views(self):
        import microeconomics
        setup(self.p)
        c = ctx(self.p, ACT+DAY)
        self.assertEqual(c['reason'], 'expired')
        a = microeconomics.accounting(c, read(self.p, 'inventory.json'))
        self.assertEqual(a['actionable_acquisition_room'], 0)
        self.assertEqual(a['legacy_UNKNOWN_reserve'], 245080)
        self.assertEqual(a['free_pool'], 100000)
        a = microeconomics.accounting({'active':False, 'reason':'config_invalid'}, read(self.p, 'inventory.json'))
        self.assertEqual(a['actionable_acquisition_room'], 0)
        self.assertIsNone(a['free_pool'])

    def test_decimal_precision_2_899_is_not_2899(self):
        import microeconomics
        a = microeconomics.accounting({'active':True, 'stage_cap':100000, 'new_reserve':0, 'legacy_reserve':245080,
                                       'lifetime_committed':0, 'gross_loss':2.899, 'cash_room':10, 'bank_room':10,
                                       'room':10, 'caps':{'lifetime_spend':1000000, 'gross_loss':120000}}, INV)
        self.assertEqual(a['gross_loss'], 2.899)
        self.assertEqual(a['gross_loss_headroom'], 120000-2.899)
        self.assertNotEqual(a['gross_loss_headroom'], 120000-2899)
        # goods storage: 28990 x 1% x 0.01 day = 2.899 exactly as computed, unrounded
        f = microeconomics.contributions(led_with({'u-1':u('G', 28990, 'goods')},
                                                  sale('u-1', 'G', 30000, 1, NOW+864)), INV, NOW+DAY, ACT)
        self.assertAlmostEqual(f['units']['u-1']['storage'], 2.899, places=12)
        self.assertAlmostEqual(f['skus']['G']['net_contribution'], 30000-28990-2.899, places=9)


class Contribution(Base):
    def test_positive_and_negative_margins_are_both_kept(self):
        import microeconomics
        led = led_with({'u-1':u('A', 100), 'u-2':u('B', 100)},
                       {**sale('u-1', 'A', 150, 1, NOW+3600), **sale('u-2', 'B', 70, 2, NOW+7200)})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertEqual(f['skus']['A']['net_contribution'], 50)
        self.assertEqual(f['skus']['A']['positive_contribution'], 50)
        self.assertEqual(f['skus']['B']['net_contribution'], -30)
        self.assertEqual(f['skus']['B']['negative_contribution'], -30)
        self.assertEqual(f['skus']['A']['median_turnover_hours'], 1.0)
        self.assertEqual(f['skus']['B']['median_turnover_hours'], 2.0)
        self.assertEqual(f['skus']['A']['ledger_ids'], [1])
        self.assertEqual(f['skus']['A']['unit_ids'], ['u-1'])

    def test_same_sku_distinct_units(self):
        import microeconomics
        led = led_with({'u-1':u('A', 100), 'u-2':u('A', 120), 'u-3':u('A', 90)},
                       {**sale('u-1', 'A', 150, 5, NOW+3600), **sale('u-2', 'A', 150, 6, NOW+3*3600),
                        **sale('u-3', 'A', 150, 7, NOW+2*3600)})
        s = microeconomics.contributions(led, INV, NOW+DAY, ACT)['skus']['A']
        self.assertEqual((s['n'], s['revenue'], s['cost'], s['net_contribution']), (3, 450, 310, 140))
        self.assertEqual(s['unit_ids'], ['u-1', 'u-2', 'u-3'])
        self.assertEqual(s['ledger_ids'], [5, 6, 7])
        self.assertEqual(s['median_turnover_hours'], 2.0)
        self.assertEqual(s['return_on_capital'], 140/310)

    def test_exact_join_rejects_mismatch_unknown_unit_and_conflicting_duplicates(self):
        import microeconomics
        led = led_with({'u-1':u('A', 100), 'u-2':u('A', 100), 'u-3':u('A', -5), 'u-4':u('A', 100)},
                       {**sale('u-1', 'B', 150, 1, NOW+60),                         # SKU mismatch
                        **sale('u-2', 'A', 150, 2, NOW+60), **sale('u-2', 'A', 160, 3, NOW+90),  # sold twice
                        **sale('u-3', 'A', 150, 4, NOW+60),                         # invalid cost
                        **sale('u-9', 'A', 150, 5, NOW+60),                         # unit (cost) unknown
                        **sale('u-4', 'A', 150, 6, NOW-60)},                        # sale before arrival
                       conflicts={})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertNotIn('A', {k for k, v in f['skus'].items() if v['n']})
        self.assertEqual(set(f['rejected']), {'u-1', 'u-2', 'u-3', 'u-4', 'u-9'})
        self.assertTrue(f['skus']['A']['evidence_conflict'])

    def test_ledger_conflict_units_are_rejected(self):
        import microeconomics
        led = led_with({'u-1':u('A', 100)}, sale('u-1', 'A', 150, 1, NOW+60), conflicts={'u-1':'seller rows disagree'})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertIn('u-1', f['rejected'])
        self.assertEqual(f['skus']['A']['n'], 0)

    def test_refund_reduces_contribution_but_gross_loss_is_unchanged(self):
        import microeconomics, oneday_pilot
        led = led_with({'u-1':u('A', 100, 'goods', gone=NOW+DAY)},
                       {**sale('u-1', 'A', 300, 1, NOW+DAY), **broke('u-1', 300, 2, NOW+DAY+60)})
        before = json.dumps(led, sort_keys=True)
        f = microeconomics.contributions(led, INV, NOW+2*DAY, ACT)
        self.assertAlmostEqual(f['skus']['A']['net_contribution'], 300-100-1.0-300)
        self.assertEqual(f['skus']['A']['refunds'], 300)
        loss = oneday_pilot.losses(led, INV, NOW+2*DAY)  # gross: refund outflow counted, the 200 margin offsets nothing
        self.assertAlmostEqual(loss['total'], 300+1.0)
        self.assertEqual(json.dumps(led, sort_keys=True), before)

    def test_unknown_refund_holds_sku_unranked(self):
        import microeconomics
        led = led_with({'u-1':u('A', 100, 'goods'), 'u-2':u('A', 100, 'goods')},
                       {**sale('u-1', 'A', 300, 1, NOW+60), **sale('u-2', 'A', 300, 2, NOW+60),
                        **broke('u-1', None, 3, NOW+120)})
        s = microeconomics.contributions(led, INV, NOW+DAY, ACT)['skus']['A']
        self.assertTrue(s['refund_unknown'])
        self.assertIsNone(s['net_contribution'])

    def test_unknown_arrival_storage_from_activation_and_turnover_unknown(self):
        import microeconomics
        rec = u('G', 10000, 'goods', supplied=None, supplied_at_unknown=True, storage_from=ACT)
        f = microeconomics.contributions(led_with({'u-1':rec}, sale('u-1', 'G', 12000, 1, ACT+DAY)), INV, NOW+DAY, ACT)
        x = f['units']['u-1']
        self.assertEqual(x['storage_basis'], 'activation_bound_conservative')
        self.assertAlmostEqual(x['storage'], 100.0)
        self.assertIsNone(x['turnover_hours'])
        self.assertIsNone(f['skus']['G']['median_turnover_hours'])
        rec = u('G', 10000, 'goods', supplied=None)  # no arrival, no activation bound: storage unknown, not zero
        f = microeconomics.contributions(led_with({'u-1':rec}, sale('u-1', 'G', 12000, 1, ACT+DAY)), INV, NOW+DAY, None)
        self.assertIn('u-1', f['rejected'])

    def test_held_units_age_storage_life_and_break_even(self):
        import microeconomics
        inv = dict(INV, units=[{'unit':'u-g', 'sku':'G', 'cost':10000, 'kind':'goods', 'supplied_at':NOW-2*DAY},
                               {'unit':'u-f', 'sku':'F', 'cost':5000, 'kind':'food', 'supplied_at':NOW-3600,
                                'expires_at':NOW+10*3600}])
        h = microeconomics.contributions(led_with({}, {}), inv, NOW, ACT)['held']
        self.assertEqual(h['u-g']['age_hours'], 48.0)
        self.assertAlmostEqual(h['u-g']['storage_accrued'], 200.0)
        self.assertAlmostEqual(h['u-g']['break_even'], 10200.0)
        self.assertIsNone(h['u-g']['remaining_life_hours'])
        self.assertEqual(h['u-f']['remaining_life_hours'], 10.0)
        self.assertEqual(h['u-f']['storage_accrued'], 0)
        self.assertEqual(h['u-f']['break_even'], 5000)

    def test_coverage_note_never_claims_complete_history(self):
        import microeconomics
        f = microeconomics.contributions(led_with({}, {}), INV, NOW, ACT)
        self.assertFalse(f['complete'])
        self.assertIn('not complete', f['coverage_note'])


class ParentReviewV2(Base):
    """Parent review: refunds keyed like losses() (units list or unit), same-seq conflicting payloads, bad numbers,
    future arrival on held units, activation-bound storage never reported as actual turnover."""

    def test_broken_event_with_single_units_list_refund_is_counted(self):
        import microeconomics
        led = led_with({'u-1':u('A', 100), 'u-2':u('A', 100)},
                       {**sale('u-1', 'A', 300, 1, NOW+60), **sale('u-2', 'A', 300, 2, NOW+60),
                        'broken:3':{'kind':'broken', 'seq':3, 'ts':NOW+120, 'units':['u-1'], 'refunded':300}})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertEqual(f['units']['u-1']['refunds'], 300)
        self.assertEqual(f['skus']['A']['net_contribution'], 200-300+200)

    def test_multiunit_ambiguous_refund_stays_unknown_and_unranked(self):
        import microeconomics
        led = led_with({'u-1':u('C', 100), 'u-2':u('C', 100)},
                       {**sale('u-1', 'C', 300, 1, NOW+60), **sale('u-2', 'C', 300, 2, NOW+60),
                        'broken:3':{'kind':'broken', 'seq':3, 'ts':NOW+120, 'units':['u-1', 'u-2'], 'refunded':300}})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertTrue(f['units']['u-1']['refund_unknown'] and f['units']['u-2']['refund_unknown'])
        self.assertIsNone(f['skus']['C']['net_contribution'])
        self.assertEqual(microeconomics.rank(BASE, f)[0], BASE)

    def test_same_seq_conflicting_payload_is_rejected(self):
        import microeconomics
        led = led_with({'u-1':u('C', 100), 'u-2':u('C', 100), 'u-3':u('C', 100)},
                       {**sale('u-1', 'C', 300, 1, NOW+60), 'settle:x':dict(sale('u-1', 'C', 900, 1, NOW+60)['settle:1']),
                        **sale('u-2', 'C', 300, 2, NOW+60), 'settle:y':dict(sale('u-3', 'C', 300, 2, NOW+60)['settle:2'])})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertIn('u-1', f['rejected'])  # one seq, two different prices
        self.assertIn('u-2', f['rejected'])  # one seq names two units
        self.assertIn('u-3', f['rejected'])
        self.assertEqual(f['skus']['C']['n'], 0)

    def test_identical_duplicate_settle_is_deduplicated(self):
        import microeconomics
        e = sale('u-1', 'C', 300, 1, NOW+60)['settle:1']
        led = led_with({'u-1':u('C', 100)}, {'settle:1':e, 'settle:dup':dict(e)})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        self.assertEqual((f['skus']['C']['n'], f['skus']['C']['net_contribution']), (1, 200))

    def test_bool_negative_nonfinite_values_are_rejected(self):
        import microeconomics
        units = {f'u-{k}':u('C', 100) for k in range(5)}
        units['u-4'] = u('C', True)
        events = {**sale('u-0', 'C', True, 1, NOW+60), **sale('u-1', 'C', -5, 2, NOW+60),
                  **sale('u-2', 'C', 300, 3, float('inf')), **sale('u-3', 'C', 300, True, NOW+60),
                  **sale('u-4', 'C', 300, 5, NOW+60)}
        f = microeconomics.contributions(led_with(units, events), INV, NOW+DAY, ACT)
        self.assertEqual(set(f['rejected']), set(units))
        led = led_with({'u-1':u('C', 100)}, {**sale('u-1', 'C', 300, 1, NOW+60), **broke('u-1', -50, 2, NOW+90)})
        self.assertTrue(microeconomics.contributions(led, INV, NOW+DAY, ACT)['units']['u-1']['refund_unknown'])
        led = led_with({'u-1':u('C', 100)}, {**sale('u-1', 'C', 300, 1, NOW+60), **broke('u-1', True, 2, NOW+90)})
        self.assertTrue(microeconomics.contributions(led, INV, NOW+DAY, ACT)['units']['u-1']['refund_unknown'])

    def test_held_future_arrival_is_unknown_not_negative(self):
        import microeconomics
        inv = dict(INV, units=[{'unit':'u-g', 'sku':'G', 'cost':10000, 'kind':'goods', 'supplied_at':NOW+3600},
                               {'unit':'u-b', 'sku':'G', 'cost':-1, 'kind':'goods', 'supplied_at':NOW-3600}])
        h = microeconomics.contributions(led_with({}, {}), inv, NOW, ACT)['held']
        self.assertIsNone(h['u-g']['age_hours'])
        self.assertIsNone(h['u-g']['storage_accrued'])
        self.assertIsNone(h['u-g']['break_even'])
        self.assertNotIn('u-b', h)

    def test_activation_bound_storage_never_reported_as_actual_turnover(self):
        import microeconomics
        units = {f'u-{k}':u('C', 100, 'goods', supplied=None, supplied_at_unknown=True, storage_from=ACT) for k in range(2)}
        events = {**sale('u-0', 'C', 300, 1, ACT+3600), **sale('u-1', 'C', 300, 2, ACT+7200)}
        f = microeconomics.contributions(led_with(units, events), INV, NOW+DAY, ACT)
        self.assertEqual({x['storage_basis'] for x in f['units'].values()}, {'activation_bound_conservative'})
        self.assertIsNone(f['skus']['C']['median_turnover_hours'])
        self.assertEqual(f['skus']['C']['turnover_samples'], 0)


def facts_for(rows):
    """rows: sku -> list of (cost, price, hours held); food units, distinct ids."""
    import microeconomics
    units, events, seq = {}, {}, 100
    for sku, sales in rows.items():
        for cost, price, hours in sales:
            seq += 1
            units[f'u-{seq}'] = u(sku, cost)
            events.update(sale(f'u-{seq}', sku, price, seq, NOW+hours*3600))
    return microeconomics.contributions(led_with(units, events), INV, NOW+DAY, ACT)


BASE = [('H', 56250, 4, 1.4), ('K', 25549, 4, 1.32), ('O', 40500, 4, 1.26), ('C', 16639, 4, 1.2), ('G', 14513, 4, 1.15)]


class Rank(Base):
    def test_supported_positive_contribution_promoted_deterministically(self):
        import microeconomics
        f = facts_for({'C':[(5000, 8874, 2), (5000, 8874, 4)], 'G':[(5000, 8772, 1), (5000, 8772, 1)],
                       'K':[(15000, 14989, 1), (15000, 14989, 1)]})
        ranked, table = microeconomics.rank(BASE, f)
        # SUPERSEDED (microecon-v2, 2026-11-11): was ['C', 'G', ...] by return on capital first. G returns 7544 on
        # 10,000 capital-hours (.75/h) vs C 7748 on 30,000 (.26/h): the faster turnover now leads.
        self.assertEqual([x[0] for x in ranked], ['G', 'C', 'H', 'K', 'O'])
        self.assertNotEqual([x[0] for x in ranked], [x[0] for x in BASE])  # differs from the old ratio heuristic
        self.assertEqual(sorted(ranked), sorted(BASE))  # same tuples: no new SKU, no changed bound or hold limit
        self.assertEqual(microeconomics.rank(BASE, f), (ranked, table))
        row = {r['sku']:r for r in table}
        self.assertEqual(row['C']['tier'], 'supported_positive_turnover')  # SUPERSEDED tier name (v2)
        self.assertEqual(row['C']['baseline_index'], 3)
        self.assertEqual(row['C']['ledger_ids'], [101, 102])
        self.assertEqual(row['K']['tier'], 'baseline_order')
        self.assertEqual(row['K']['basis'], 'net_contribution_not_positive')  # demoted, never removed

    def test_equal_return_ties_break_on_faster_turnover_then_baseline(self):
        import microeconomics
        f = facts_for({'O':[(100, 150, 10), (100, 150, 10)], 'C':[(100, 150, 2), (100, 150, 2)],
                       'G':[(100, 150, 2), (100, 150, 2)]})
        self.assertEqual([x[0] for x in microeconomics.rank(BASE, f)[0]], ['C', 'G', 'O', 'H', 'K'])

    def test_sparse_or_unknown_evidence_keeps_baseline_order(self):
        import microeconomics
        self.assertEqual(microeconomics.rank(BASE, facts_for({}))[0], BASE)
        self.assertEqual(microeconomics.rank(BASE, facts_for({'C':[(5000, 9000, 1)]}))[0], BASE)  # one sale is sparse
        self.assertEqual(microeconomics.rank(BASE, None)[0], BASE)
        led = led_with({'u-1':u('C', 100, 'goods'), 'u-2':u('C', 100, 'goods')},
                       {**sale('u-1', 'C', 300, 1, NOW+60), **sale('u-2', 'C', 300, 2, NOW+60),
                        **broke('u-1', None, 3, NOW+120)})
        f = microeconomics.contributions(led, INV, NOW+DAY, ACT)
        ranked, table = microeconomics.rank(BASE, f)
        self.assertEqual(ranked, BASE)
        self.assertEqual({r['sku']:r for r in table}['C']['basis'], 'refund_unknown')


def own_sales_ledger(p):
    """C and G: two exact own sales each, strongly positive; K: two sales just below cost."""
    units, events = {}, {}
    for k, (sku, kind, cost, price) in enumerate([('C', 'food', 5000, 8874), ('C', 'food', 5000, 8874),
                                                  ('G', 'goods', 5000, 8772), ('G', 'goods', 5000, 8772),
                                                  ('K', 'food', 15000, 14989), ('K', 'food', 15000, 14989)]):
        uid = f'u-s{k}'
        units[uid] = {'sku':sku, 'cost':cost, 'kind':kind, 'grade':'A', 'supplied_at':NOW-5*3600,
                      'expires_at':NOW+30*DAY if kind == 'food' else None, 'first_seen':NOW-5*3600, 'gone_at':NOW-3600}
        events.update(sale(uid, sku, price, 900+k, NOW-3*3600))
    led = {'pilot':'one-day-v1', 'requests':{}, 'units':units, 'events':events, 'conflicts':{}}
    (p/'one-day-pilot-ledger.json').write_text(json.dumps(led), encoding='utf-8')


class Integration(Base):
    def plan(self, now=NOW):
        from test_oneday_pilot import plan
        return plan(self.p, now)

    def test_hook_reranks_within_original_caps_and_persists_evidence(self):
        import microeconomics, oneday_pilot, restock_r3
        from test_oneday_pilot import CATALOG, LEGACY
        setup(self.p)
        own_sales_ledger(self.p)
        cfg = (self.p/oneday_pilot.CONFIG).read_bytes()
        c0 = ctx(self.p)
        old = [x[0] for x in oneday_pilot.candidates(CATALOG, self.evidence(), c0['stage'])]
        self.assertEqual(old[:3], ['H', 'K', 'O'])
        buy = self.plan()
        # demand-cover-v1 re-pin (was C20+G1, money-bound): C and G each 2 sales in 12 h -> 1/6 h x 6 h = 1 unit;
        # H has no rate evidence and nothing held -> 1-unit probe; per_run_lines 3 ends the plan
        self.assertEqual(buy, [{'sku':'C', 'qty':1}, {'sku':'G', 'qty':1}, {'sku':'H', 'qty':1}])
        # caps, expiry, lifetime, legacy: unchanged rules and bytes
        self.assertEqual((self.p/oneday_pilot.CONFIG).read_bytes(), cfg)
        self.assertEqual(read(self.p, 'restock-pending.json')[:4], LEGACY)
        c = ctx(self.p)
        self.assertEqual(c['caps'], c0['caps'])
        self.assertEqual(c['expires_at'], ACT+DAY)
        self.assertLessEqual(c['concurrent'], 350000)
        self.assertIsNone(c['sku_cap'])  # slot-recovery-v1: no per-SKU share cap
        self.assertEqual(c['lifetime_committed'], 16639+14513+56250)  # upper bounds of the new requests
        self.assertEqual(restock_r3.POLICY, 'bounded-v2')
        d = read(self.p, 'restock-observation.json')['decision']
        self.assertEqual(d['microeconomic_policy'], microeconomics.MICRO_POLICY)
        self.assertEqual(d['microeconomics']['ranked'][:3], ['C', 'G', 'H'])
        ev = d['microeconomics']['chosen_evidence']
        self.assertEqual(ev['C']['ledger_ids'], [900, 901])
        self.assertEqual(ev['C']['unit_ids'], ['u-s0', 'u-s1'])
        self.assertEqual(ev['C']['n'], 2)
        self.assertEqual(set(ev), {'C', 'G', 'H'})  # demand-cover-v1: covered C/G leave room for H
        self.assertEqual({r['sku']:r for r in read(self.p, microeconomics.STATUS)['ranking']}['H']['tier'], 'baseline_order')
        row = json.loads((self.p/'restock-decisions.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(row['microeconomics']['ranked'], d['microeconomics']['ranked'])
        st = read(self.p, microeconomics.STATUS)
        self.assertEqual(st['policy_version'], microeconomics.MICRO_POLICY)
        self.assertEqual(st['outcome'], 'requested')
        self.assertFalse(st['acquisition_claimed'])
        self.assertEqual(st['chosen'], ['C', 'G', 'H'])
        self.assertEqual(st['accounting']['legacy_UNKNOWN_reserve'], 245080)
        self.assertEqual(st['skus']['K']['net_contribution'], -22)

    def evidence(self):
        from public_evidence import from_cache, load_cache
        from test_oneday_pilot import CATALOG
        return from_cache(load_cache(self.p), CATALOG, NOW)

    def test_sparse_evidence_keeps_original_plan(self):
        setup(self.p)
        self.assertEqual(self.plan(), [{'sku':'H', 'qty':1}, {'sku':'K', 'qty':1}, {'sku':'C', 'qty':1}])

    def test_closed_window_still_writes_status_without_purchase(self):
        import microeconomics
        setup(self.p)
        own_sales_ledger(self.p)
        inventory(self.p, restock={'open':False, 'opens_at':NOW+1000, 'closes_at':NOW+1180})
        self.assertEqual(self.plan(), [])
        st = read(self.p, microeconomics.STATUS)
        self.assertEqual(st['outcome'], 'none')
        self.assertEqual(st['decision_reason'], 'window')
        self.assertEqual(st['ranked'][:2], ['C', 'G'])  # actual ranking preview, no order placed
        self.assertEqual(st['chosen'], [])
        self.assertGreater(st['accounting']['actionable_acquisition_room'], 0)

    def test_blocked_capacity_reports_blocked_not_acquisition(self):
        import microeconomics
        setup(self.p, balance=5245080)  # cash room exactly 0 after the legacy reserve and the floor
        self.assertEqual(self.plan(), [])
        st = read(self.p, microeconomics.STATUS)
        self.assertEqual(st['outcome'], 'blocked')
        self.assertEqual(st['reason'], 'cash_insufficient')
        self.assertEqual(st['accounting']['actionable_acquisition_room'], 0)
        self.assertFalse(st['acquisition_claimed'])

    def test_expired_window_status_and_no_purchase(self):
        import microeconomics
        setup(self.p)
        own_sales_ledger(self.p)
        self.assertEqual(self.plan(ACT+DAY), [])
        st = read(self.p, microeconomics.STATUS)
        self.assertEqual((st['outcome'], st['reason']), ('blocked', 'expired'))
        self.assertEqual(st['skus']['C']['n'], 2)  # observation of owned evidence continues after expiry

    def test_corrupt_evidence_no_purchase_no_frozen_mutation(self):
        import microeconomics, oneday_pilot
        setup(self.p)
        (self.p/oneday_pilot.LEDGER).write_text('{corrupt', encoding='utf-8')
        cfg, pend = (self.p/oneday_pilot.CONFIG).read_bytes(), (self.p/'restock-pending.json').read_bytes()
        self.assertEqual(self.plan(), [])
        self.assertEqual((self.p/oneday_pilot.CONFIG).read_bytes(), cfg)
        self.assertEqual((self.p/'restock-pending.json').read_bytes(), pend)
        self.assertEqual((self.p/oneday_pilot.LEDGER).read_text(encoding='utf-8'), '{corrupt')
        st = read(self.p, microeconomics.STATUS)
        self.assertEqual((st['outcome'], st['reason']), ('blocked', 'ledger_invalid'))

    def test_conflicting_sale_evidence_falls_back_to_baseline(self):
        setup(self.p)
        own_sales_ledger(self.p)
        led = read(self.p, 'one-day-pilot-ledger.json')
        led['events']['settle:999'] = dict(led['events']['settle:900'], seq=999, price=1)  # u-s0 sold twice
        led['events']['settle:998'] = dict(led['events']['settle:902'], seq=998, price=1)  # u-s2 sold twice
        (self.p/'one-day-pilot-ledger.json').write_text(json.dumps(led), encoding='utf-8')
        self.assertEqual([b['sku'] for b in self.plan()][:1], ['H'])
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:3], ['H', 'K', 'O'])

    def test_microeconomics_failure_falls_back_to_baseline_order(self):
        import microeconomics
        setup(self.p)
        own_sales_ledger(self.p)
        orig = microeconomics.contributions
        microeconomics.contributions = lambda *a, **k: 1/0
        try:
            self.assertEqual([b['sku'] for b in self.plan()][:1], ['H'])
            self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:3],
                             ['H', 'K', 'O'])
        finally:
            microeconomics.contributions = orig

    def test_module_is_in_the_source_hash_gate(self):
        import restock_r3
        self.assertIn('microeconomics.py', restock_r3.SOURCES)
        import microeconomics
        self.assertEqual(Path(microeconomics.__file__).resolve().parent, Path(__file__).resolve().parent)


class Cli(Base):
    def test_cli_hook_writes_microeconomics_status(self):
        import hashlib, subprocess, sys, time, microeconomics, restock_r3
        from test_oneday_pilot import cache
        here = Path(__file__).resolve().parent
        now = time.time()
        setup(self.p, act=now-10)
        cache(self.p, fetched=now)
        g = read(self.p, 'seller-v2-approved.json')
        g['source_sha256'] = {n:hashlib.sha256((here/n).read_bytes()).hexdigest() for n in restock_r3.SOURCES}
        (self.p/'seller-v2-approved.json').write_text(json.dumps(g), encoding='utf-8')
        r = subprocess.run([sys.executable, str(here/'restock_r3.py'), '--state', str(self.p)], cwd=str(here),
                           capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        st = read(self.p, microeconomics.STATUS)
        self.assertEqual(st['chosen'], [b['sku'] for b in out['buy']], r.stdout+r.stderr)
        self.assertEqual(st['policy_version'], microeconomics.MICRO_POLICY)


if __name__ == '__main__':
    unittest.main()
