"""microecon-v2 turnover priority (Claude Opus 5.5, 2026-11-11): own net contribution per unit of exact capital-time
(sum over units of cost x hours held), censored on-hand capital-time included, explicit sparse fallback, public only
as a qualified tie-break; food eligibility from empirical own rotation + buffer (original 72 h rule as fallback);
gross-loss components split into confirmed (monotone) and provisional. Offline fixtures only."""
import json
import unittest

from test_oneday_pilot import ACT, NOW, Base, CATALOG, setup, plan, read, write, ctx
from test_microeconomics import led_with, u, sale, broke, INV

H = 3600
DAY = 86400
T = NOW+DAY  # evaluation time for most fixtures


def build(rows, held=(), kind='food', now=T, extra_units=None, extra_events=None, inv=None):
    """rows: sku -> list of (cost, price, hours held); every unit supplied at NOW, sold at NOW+hours.
    held: (sku, cost, age_hours, kind) on-hand units at `now`."""
    import microeconomics
    units, events, seq = {}, {}, 100
    for sku, sales in rows.items():
        for cost, price, hours in sales:
            seq += 1
            units[f'u-{seq}'] = u(sku, cost, kind if not isinstance(kind, dict) else kind[sku])
            events.update(sale(f'u-{seq}', sku, price, seq, NOW+hours*H))
    units.update(extra_units or {})
    events.update(extra_events or {})
    inv = dict(inv or INV)
    inv['units'] = [{'unit':f'u-h{k}', 'sku':s, 'cost':c, 'kind':kd, 'grade':'A', 'supplied_at':now-age*H,
                     'expires_at':now+30*DAY if kd == 'food' else None}
                    for k, (s, c, age, kd) in enumerate(held)]
    led = led_with(units, events, conflicts={})
    return microeconomics.contributions(led, inv, now, ACT), led, inv


def order(cands, facts, public=None):
    import microeconomics
    return [c[0] for c in microeconomics.rank(cands, facts, public)[0]]


def table(cands, facts, public=None):
    import microeconomics
    return {r['sku']:r for r in microeconomics.rank(cands, facts, public)[1]}


def cands(*skus):
    return [(s, 1000, 4, 1.5) for s in skus]


class CapitalTimeRank(Base):
    def test_faster_low_margin_beats_slow_high_margin(self):
        f = build({'S':[(1000, 1600, 20), (1000, 1600, 20)], 'F':[(1000, 1100, .5), (1000, 1100, .5)]})[0]
        self.assertEqual(order(cands('S', 'F'), f), ['F', 'S'])
        t = table(cands('S', 'F'), f)
        self.assertEqual(t['F']['tier'], 'supported_positive_turnover')
        self.assertAlmostEqual(t['F']['velocity']['return_per_capital_hour'], 200/1000)
        self.assertAlmostEqual(t['S']['velocity']['return_per_capital_hour'], 1200/40000)
        self.assertGreater(t['S']['return_on_capital'], t['F']['return_on_capital'])  # old ROI-first rule would flip

    def test_exact_unit_capital_time_not_aggregate_profit_over_median_hold(self):
        # X: 1000 for 1 h (+100) and 100 for 10 h (+10): 110 / (1000 + 1000) = .055 per capital-hour.
        # aggregate ROI / median hold would give .1 / 5.5 = .018 < Y's .04 and put Y first.
        f = build({'Y':[(100, 104, 1), (100, 104, 1)], 'X':[(1000, 1100, 1), (100, 110, 10)]})[0]
        self.assertEqual(order(cands('Y', 'X'), f), ['X', 'Y'])
        v = table(cands('Y', 'X'), f)['X']['velocity']
        self.assertAlmostEqual(v['sold_capital_hours'], 2000)
        self.assertAlmostEqual(v['numerator'], 110)
        self.assertAlmostEqual(v['return_per_capital_hour'], 110/2000)

    def test_censored_on_hand_capital_time_lowers_slow_movers(self):
        rows = {'M':[(1000, 1300, 1), (1000, 1300, 1)], 'P':[(1000, 1100, 1), (1000, 1100, 1)]}
        kinds = {'M':'goods', 'P':'food'}
        f0 = build(rows, kind=kinds)[0]
        self.assertEqual(order(cands('P', 'M'), f0), ['M', 'P'])  # without unsold stock M turns more per hour
        f = build(rows, kind=kinds, held=[('M', 1000, 20, 'goods')]*3)[0]
        t = table(cands('P', 'M'), f)
        self.assertEqual(order(cands('P', 'M'), f), ['P', 'M'])
        v = t['M']['velocity']
        self.assertEqual(v['censored_units'], 3)
        self.assertAlmostEqual(v['censored_capital_hours'], 3*1000*20)
        self.assertAlmostEqual(v['censored_storage'], 3*1000*.01*20/24)  # storage already incurred on held goods
        sold_storage = 2*1000*.01*1/24
        self.assertAlmostEqual(v['numerator'], 600-sold_storage-v['censored_storage'])
        self.assertAlmostEqual(v['denominator'], 2000+60000)

    def test_sparse_clock_falls_back_to_return_on_capital_explicitly(self):
        unk = lambda sku, cost: u(sku, cost, supplied=None, supplied_at_unknown=True, storage_from=ACT)
        units = {'u-q1':unk('Q', 100), 'u-q2':unk('Q', 100), 'u-r1':unk('R', 100), 'u-r2':unk('R', 100)}
        events = {**sale('u-q1', 'Q', 200, 1, NOW+H), **sale('u-q2', 'Q', 200, 2, NOW+H),
                  **sale('u-r1', 'R', 150, 3, NOW+H), **sale('u-r2', 'R', 150, 4, NOW+H)}
        f = build({'F':[(1000, 1100, .5), (1000, 1100, .5)]}, extra_units=units, extra_events=events)[0]
        self.assertEqual(order(cands('R', 'Q', 'F'), f), ['F', 'Q', 'R'])
        t = table(cands('R', 'Q', 'F'), f)
        self.assertEqual(t['Q']['tier'], 'supported_positive_contribution_sparse_turnover')
        self.assertEqual(t['Q']['basis'], 'sparse_turnover_fallback_return_on_capital')
        self.assertEqual(t['Q']['velocity']['exact_samples'], 0)
        self.assertFalse(t['Q']['velocity']['qualified'])

    def test_missing_clock_units_excluded_from_numerator_and_denominator(self):
        units = {'u-x9':u('X', 100, supplied=None, supplied_at_unknown=True, storage_from=ACT)}
        f = build({'X':[(100, 150, 1), (100, 150, 1)]}, extra_units=units,
                  extra_events=sale('u-x9', 'X', 1000, 9, NOW+H))[0]
        v = f['velocity']['X']
        self.assertEqual((v['exact_samples'], v['excluded_clock_unknown']), (2, 1))
        self.assertAlmostEqual(v['return_per_capital_hour'], 100/200)
        self.assertEqual(f['skus']['X']['net_contribution'], 1000)  # the realized total still reports every sale

    def test_known_refund_reduces_numerator_unknown_refund_never_ranked(self):
        f = build({'X':[(100, 300, 1), (100, 300, 1)]}, extra_events=broke('u-101', 150, 50, NOW+2*H))[0]
        self.assertAlmostEqual(f['velocity']['X']['numerator'], 400-150)
        f = build({'X':[(100, 300, 1), (100, 300, 1)], 'F':[(100, 110, 1), (100, 110, 1)]},
                  extra_events=broke('u-101', None, 50, NOW+2*H))[0]
        t = table(cands('X', 'F'), f)
        self.assertEqual(order(cands('X', 'F'), f), ['F', 'X'])
        self.assertEqual((t['X']['tier'], t['X']['basis']), ('baseline_order', 'refund_unknown'))

    def test_conflicting_evidence_never_enters_turnover_tier(self):
        import microeconomics
        f, led, inv = build({'X':[(100, 300, 1), (100, 300, 1)], 'F':[(100, 110, 1), (100, 110, 1)]})
        led['conflicts'] = {'u-101':'seller rows disagree'}
        f = microeconomics.contributions(led, inv, T, ACT)
        t = table(cands('X', 'F'), f)
        self.assertNotEqual(t['X']['tier'], 'supported_positive_turnover')
        self.assertEqual(order(cands('X', 'F'), f), ['F', 'X'])

    def test_noncausal_only_evidence_at_or_before_now(self):
        now = NOW+2*H
        f = build({'X':[(100, 150, 1), (100, 150, 3)]}, now=now, held=[('X', 100, -1, 'food')])[0]
        v = f['velocity']['X']
        self.assertEqual((v['exact_samples'], v['excluded_future']), (1, 2))  # sale after now, arrival after now
        self.assertFalse(v['qualified'])
        self.assertIn('not a forecast', v['formula'])

    def test_expired_own_units_write_off_in_numerator(self):
        exp = {'u-e1':u('X', 100, expires_at=NOW+5*H, gone=NOW+5*H)}
        ev = {'expire:60':{'kind':'expire', 'seq':60, 'ts':NOW+5*H, 'units':['u-e1'], 'shop':'own'}}
        f = build({'X':[(100, 150, 1), (100, 150, 1)], 'F':[(100, 110, 1), (100, 110, 1)]},
                  extra_units=exp, extra_events=ev)[0]
        v = f['velocity']['X']
        self.assertEqual(v['expired_units'], 1)
        self.assertAlmostEqual(v['numerator'], 100-100)
        self.assertAlmostEqual(v['denominator'], 200+100*5)
        t = table(cands('X', 'F'), f)
        self.assertEqual((t['X']['tier'], t['X']['basis']), ('baseline_order', 'turnover_contribution_not_positive'))
        self.assertEqual(order(cands('X', 'F'), f), ['F', 'X'])  # demoted, never removed

    def test_gone_without_settle_is_censored_exposure_with_no_presumed_profit(self):
        gone = {'u-g1':u('X', 100, gone=NOW+4*H)}
        f = build({'X':[(100, 150, 1), (100, 150, 1)]}, extra_units=gone)[0]
        v = f['velocity']['X']
        self.assertEqual(v['provisional_units'], 1)
        self.assertAlmostEqual(v['numerator'], 100)
        self.assertAlmostEqual(v['denominator'], 200+100*4)

    def test_public_is_only_a_qualified_tie_break_ineligible_last(self):
        f = build({'A':[(100, 150, 1)]*2, 'B':[(100, 150, 1)]*2, 'C':[(100, 160, 1)]*2, 'D':[(100, 140, 1)]*2})[0]
        pub = {'B':{'use':True, 'market_proxy_return':.3}, 'A':{'use':False, 'reason':'2 fresh exact comparable'},
               'D':{'use':True, 'market_proxy_return':.9}}
        self.assertEqual(order(cands('A', 'B', 'D', 'C'), f, pub), ['C', 'B', 'A', 'D'])
        t = table(cands('A', 'B', 'D', 'C'), f, pub)
        self.assertEqual(t['B']['public']['role'], 'secondary_after_own_return')

    def test_hold_floor_bounds_division_and_is_disclosed(self):
        import microeconomics
        f = build({'A':[(100, 150, .05)]*2, 'B':[(100, 150, .2)]*2})[0]
        self.assertEqual(f['velocity']['A']['hold_floor_hours'], microeconomics.HOLD_FLOOR_H)
        self.assertAlmostEqual(f['velocity']['A']['return_per_capital_hour'],
                               f['velocity']['B']['return_per_capital_hour'])
        self.assertEqual(order(cands('B', 'A'), f), ['B', 'A'])  # equal after the floor: baseline order

    def test_bootstrap_and_low_margin_are_not_banned(self):
        f = build({'L':[(1000, 1020, .5), (1000, 1020, .5)]})[0]
        ranked = order(cands('N', 'L'), f)
        self.assertEqual(ranked, ['L', 'N'])  # low but positive margin, fast: kept and ranked
        t = table(cands('N', 'L'), f)
        self.assertEqual((t['N']['tier'], t['N']['basis']), ('baseline_order', 'no_own_realized_sale'))

    def test_same_tuples_deterministic_and_formula_disclosed(self):
        import microeconomics
        base = cands('S', 'F', 'N')
        f = build({'S':[(1000, 1600, 20)]*2, 'F':[(1000, 1100, .5)]*2})[0]
        r1, t1 = microeconomics.rank(base, f)
        self.assertEqual(sorted(r1), sorted(base))
        self.assertEqual(microeconomics.rank(base, f), (r1, t1))
        row = {r['sku']:r for r in t1}['F']
        for k in ('formula', 'exact_samples', 'censored_units', 'sold_capital_hours', 'censored_capital_hours',
                  'numerator', 'denominator', 'min_samples', 'hold_floor_hours'):
            self.assertIn(k, row['velocity'])
        self.assertEqual(microeconomics.MICRO_POLICY, 'microecon-v2')


def food_catalog(**shelf):
    return {k:{'name':k, 'category':f'c{k}', 'kind':'food', 'list_price':10000, 'shelf_hours':v}
            for k, v in shelf.items()}


def evidence_for(*skus):
    ids, ledger, seq = {}, {}, 0
    for s in skus:
        for _ in range(2):
            seq += 1
            ids.setdefault(s, []).append(seq)
            ledger[seq] = {'sku':s, 'price':9000}
    return {'own_sku_ids':ids, 'own_ledger':ledger}


class FoodRotation(Base):
    def rot(self, rows, catalog, held=(), life_h=None, now=T):
        import microeconomics
        f, led, inv = build(rows, held=held, now=now)
        if life_h is not None:
            for x in led['units'].values(): x['expires_at'] = x['supplied_at']+life_h*H
            f = microeconomics.contributions(led, inv, now, ACT)
        return microeconomics.food_rotation(f, led, inv, now, catalog)

    def elig(self, catalog, rot):
        import oneday_pilot
        return [c[0] for c in oneday_pilot.candidates(catalog, evidence_for(*catalog), 2, rot)]

    def test_proven_quick_food_beats_blind_72h(self):
        cat = food_catalog(Q=48)
        self.assertEqual(self.elig(cat, None), [])  # original rule: 48 <= 72
        r = self.rot({'Q':[(5000, 9000, 1)]*3}, cat, life_h=48)
        self.assertTrue(r['Q']['qualified'])
        self.assertAlmostEqual(r['Q']['rotation_hours'], 1)
        # buffer = max(own slowest hold, slowest own food hold of any SKU) = 1 h here: no blind 24 h constant
        self.assertAlmostEqual(r['Q']['threshold_hours'], 1+1)
        self.assertEqual(r['Q']['buffer_basis'], 'own_slowest_hold')  # the only own food SKU: pooled tail = own
        self.assertEqual(self.elig(cat, r), ['Q'])

    def test_pooled_slowest_own_food_hold_sets_the_buffer(self):
        cat = food_catalog(Q=48, P=168)
        rows = {'Q':[(5000, 9000, 1)]*3, 'P':[(5000, 9000, 1), (5000, 9000, 1), (5000, 9000, 40)]}
        r = self.rot(rows, cat, life_h=48, now=NOW+2*DAY)
        self.assertAlmostEqual(r['Q']['buffer_hours'], 40)
        self.assertAlmostEqual(r['Q']['threshold_hours'], 1+40)
        self.assertEqual(r['Q']['buffer_basis'], 'pooled_own_food_slowest_hold')
        self.assertEqual(self.elig(cat, r), ['P', 'Q'] if r['P']['eligible_by_shelf'] else ['Q'])
        r = self.rot(rows, cat, life_h=40, now=NOW+2*DAY)  # every own unit arrives with 40 h: 41 h is not covered
        self.assertNotIn('Q', self.elig(cat, r))

    def test_sparse_rotation_falls_back_to_original_72h(self):
        cat = food_catalog(R=48, K=168)
        r = self.rot({'R':[(5000, 9000, 1)]*2}, cat, life_h=48)
        self.assertFalse(r['R']['qualified'])
        self.assertEqual(r['R']['basis'], 'fallback_original_72h')
        self.assertEqual(self.elig(cat, r), ['K'])

    def test_slow_rotation_needs_more_shelf_life(self):
        cat = food_catalog(W=48)
        # evaluated after the 30 h sale (at NOW+24 h that sale is still in the future and is excluded)
        r = self.rot({'W':[(5000, 9000, 1), (5000, 9000, 2), (5000, 9000, 30)]}, cat, life_h=100, now=NOW+2*DAY)
        self.assertAlmostEqual(r['W']['threshold_hours'], 30+30)
        self.assertEqual(self.elig(cat, r), [])

    def test_observed_actual_shelf_life_shorter_than_catalog_is_used(self):
        cat = food_catalog(K=168)
        r = self.rot({'K':[(5000, 9000, 1)]*3}, cat, life_h=1.5)
        self.assertAlmostEqual(r['K']['actual_shelf_hours'], 1.5)  # catalog 168 h alone would admit it
        self.assertEqual(self.elig(cat, r), [])

    def test_censored_held_age_counts_toward_rotation(self):
        cat = food_catalog(Q=48)
        r = self.rot({'Q':[(5000, 9000, 1)]*3}, cat, held=[('Q', 5000, 30, 'food')], life_h=48)
        self.assertAlmostEqual(r['Q']['rotation_hours'], 30)
        self.assertEqual(self.elig(cat, r), [])


class LossComponents(Base):
    """CORRECTED 2026-11-11 after parent review (PARENT_REVIEW_BLOCKERS.txt): the earlier version of this class labelled
    storage accrual as confirmed loss; storage is a mutable estimate (an exact sale ts can shorten it), so it is now
    tracked as estimated_storage. confirmed = exact settled negative margins + exact observed refunds + explicit
    market expire events only. total = confirmed + provisional_acquisition + estimated_storage."""
    G = {'sku':'G', 'cost':10000, 'kind':'goods', 'grade':'A', 'supplied_at':NOW, 'expires_at':None,
         'first_seen':NOW, 'gone_at':None}

    def parts(self, x):
        self.assertAlmostEqual(x['total'], x['confirmed_total']+x['provisional_acquisition_total']+x['estimated_storage'])

    def test_provisional_replaced_by_exact_settle_storage_is_estimate(self):
        import oneday_pilot
        led = led_with({'u-1':dict(self.G, gone_at=NOW+2*H+16)}, {}, conflicts={})
        inv = dict(INV, units=[])
        before = oneday_pilot.losses(led, inv, NOW+3*H)
        self.assertEqual(before['provisional_acquisition_total'], 10000)
        self.assertAlmostEqual(before['estimated_storage'], 10000*.01*(2*H+16)/DAY)
        self.assertEqual([x['unit'] for x in before['provisional_units']], ['u-1'])
        self.assertEqual(before['confirmed_total'], 0)
        led['events'].update(sale('u-1', 'G', 11000, 7, NOW+2*H))
        after = oneday_pilot.losses(led, inv, NOW+3*H)
        self.assertEqual((after['provisional_acquisition_total'], after['provisional_units']), (0, []))
        self.assertEqual(after['confirmed_total'], 0)  # profitable exact sale: no irreversible loss
        self.assertAlmostEqual(after['estimated_storage'], 10000*.01*2*H/DAY)  # corrected to the exact sale ts
        self.parts(before)
        self.parts(after)

    def test_held_to_gone_confirmed_never_shrinks(self):  # parent_repro.py HELD_GONE
        import oneday_pilot
        led = led_with({'u-1':dict(self.G)}, {}, conflicts={})
        a = oneday_pilot.losses(led, dict(INV, units=[dict(self.G, unit='u-1')]), NOW+H)
        led['units']['u-1']['gone_at'] = NOW+H+60
        b = oneday_pilot.losses(led, dict(INV, units=[]), NOW+H+60)
        self.assertGreaterEqual(b['confirmed_total'], a['confirmed_total'])
        self.assertEqual(a['confirmed_total'], 0)
        self.assertAlmostEqual(a['estimated_storage'], 10000*.01*H/DAY)
        self.parts(a)
        self.parts(b)

    def test_late_exact_settle_older_than_last_held_snapshot(self):  # parent_repro.py HELD_LATE_SETTLE
        import oneday_pilot
        led = led_with({'u-1':dict(self.G)}, {}, conflicts={})
        a = oneday_pilot.losses(led, dict(INV, units=[dict(self.G, unit='u-1')]), NOW+H)
        led['units']['u-1']['gone_at'] = NOW+H+60
        led['events'].update(sale('u-1', 'G', 12000, 1, NOW+H-10))
        c = oneday_pilot.losses(led, dict(INV, units=[]), NOW+H+120)
        self.assertGreaterEqual(c['confirmed_total'], a['confirmed_total'])
        self.assertLess(c['estimated_storage'], a['estimated_storage'])  # an estimate corrected, not a confirmed reset
        self.parts(c)

    def test_known_refund_counts_while_unaccounted_or_expired(self):
        import oneday_pilot
        led = led_with({'u-1':dict(self.G, gone_at=NOW+H),
                        'u-2':{'sku':'F', 'cost':5000, 'kind':'food', 'grade':'A', 'supplied_at':NOW,
                               'expires_at':NOW+H, 'first_seen':NOW, 'gone_at':NOW+H}},
                       {**broke('u-1', 300, 5, NOW+H+10), **broke('u-2', 200, 6, NOW+H+20),
                        'expire:9':{'kind':'expire', 'seq':9, 'ts':NOW+H, 'units':['u-2'], 'shop':'own'}},
                       conflicts={})
        x = oneday_pilot.losses(led, dict(INV, units=[]), NOW+2*H)
        self.assertEqual(x['confirmed']['refund'], 500)
        self.assertEqual(x['confirmed']['expired_event'], 5000)
        self.assertEqual(x['confirmed_total'], 5500)
        self.assertEqual(x['provisional_acquisition_total'], 10000)
        self.parts(x)

    def test_expire_and_settle_conflict_keeps_cost_and_blocks(self):
        import oneday_pilot
        unit = {'sku':'F', 'cost':5000, 'kind':'food', 'grade':'A', 'supplied_at':NOW, 'expires_at':NOW+H,
                'first_seen':NOW, 'gone_at':NOW+H}
        led = led_with({'u-1':unit}, {**sale('u-1', 'F', 9000, 3, NOW+H-60),
                                      'expire:9':{'kind':'expire', 'seq':9, 'ts':NOW+H, 'units':['u-1'], 'shop':'own'}},
                       conflicts={})
        x = oneday_pilot.losses(led, dict(INV, units=[]), NOW+2*H)
        self.assertTrue(x['unknown'])
        self.assertEqual(x['conflicting_events'], ['u-1'])
        self.assertEqual(x['confirmed']['expired_event'], 5000)  # never erased by the sale row
        self.assertEqual(x['units']['u-1']['state'], 'conflict')
        self.parts(x)

    def test_inferred_expiry_is_provisional_event_expiry_confirmed(self):
        import oneday_pilot
        unit = {'sku':'F', 'cost':5000, 'kind':'food', 'grade':'A', 'supplied_at':NOW, 'expires_at':NOW+H,
                'first_seen':NOW, 'gone_at':NOW+H}
        led = led_with({'u-1':dict(unit)}, {}, conflicts={})
        a = oneday_pilot.losses(led, dict(INV, units=[]), NOW+2*H)
        self.assertEqual((a['expired'], a['confirmed_total'], a['provisional_acquisition_total']), (5000, 0, 5000))
        self.assertEqual(a['provisional_acquisition']['inferred_expiry'], 5000)
        led['events']['expire:9'] = {'kind':'expire', 'seq':9, 'ts':NOW+H, 'units':['u-1'], 'shop':'own'}
        b = oneday_pilot.losses(led, dict(INV, units=[]), NOW+2*H)
        self.assertEqual((b['expired'], b['confirmed_total'], b['provisional_acquisition_total']), (5000, 5000, 0))

    def test_cap_still_counts_provisional(self):
        setup(self.p)
        led = read(self.p, 'one-day-pilot-ledger.json')
        led['units']['u-1'] = {'sku':'K', 'cost':130000, 'kind':'food', 'grade':'A', 'supplied_at':ACT+10,
                               'expires_at':ACT+700*H, 'first_seen':ACT+20, 'gone_at':ACT+50}
        write(self.p, 'one-day-pilot-ledger.json', led)
        c = ctx(self.p)
        self.assertEqual(c['reason'], 'loss_cap')
        self.assertEqual(c['loss']['provisional_acquisition_total'], 130000)

    def test_expire_settle_conflict_blocks_acquisition(self):
        setup(self.p)
        led = read(self.p, 'one-day-pilot-ledger.json')
        led['units']['u-1'] = {'sku':'K', 'cost':5000, 'kind':'food', 'grade':'A', 'supplied_at':ACT+10,
                               'expires_at':ACT+60, 'first_seen':ACT+20, 'gone_at':ACT+70}
        led['events'].update(sale('u-1', 'K', 9000, 3, ACT+50))
        led['events']['expire:9'] = {'kind':'expire', 'seq':9, 'ts':ACT+60, 'units':['u-1'], 'shop':'own'}
        write(self.p, 'one-day-pilot-ledger.json', led)
        self.assertEqual(ctx(self.p)['reason'], 'loss_unknown')


class OnHandExpiredCapitalTime(Base):
    def test_on_hand_expired_food_is_written_off_in_the_numerator(self):
        import microeconomics
        f, led, inv = build({'X':[(100, 150, 1), (100, 150, 1)]}, held=[('X', 100, 30, 'food')])
        self.assertEqual((f['velocity']['X']['censored_units'], f['velocity']['X']['expired_units']), (1, 0))
        inv['units'][0]['expires_at'] = T-10*H  # still listed on hand, already past expiry
        v = microeconomics.contributions(led, inv, T, ACT)['velocity']['X']
        self.assertEqual((v['expired_units'], v['censored_units']), (1, 0))
        self.assertAlmostEqual(v['numerator'], 100-100)
        self.assertAlmostEqual(v['denominator'], 200+100*20)  # exposure to expiry, not to now


def own_ledger(p, rows):
    """rows: (uid, sku, kind, cost, price, hold_hours, life_hours); supplied NOW-30h, sold after hold."""
    units, events = {}, {}
    for k, (uid, sku, kind, cost, price, hold, life) in enumerate(rows):
        t0 = NOW-30*H
        units[uid] = {'sku':sku, 'cost':cost, 'kind':kind, 'grade':'A', 'supplied_at':t0,
                      'expires_at':t0+life*H if kind == 'food' else None, 'first_seen':t0, 'gone_at':t0+hold*H+60}
        events.update(sale(uid, sku, price, 900+k, t0+hold*H))
    write(p, 'one-day-pilot-ledger.json', {'pilot':'one-day-v1', 'requests':{}, 'units':units, 'events':events,
                                           'conflicts':{}})


class Integration(Base):
    def test_hook_buys_fast_low_margin_before_slow_high_margin(self):
        setup(self.p)
        own_ledger(self.p, [('u-c1', 'C', 'food', 5000, 8874, 20, 720), ('u-c2', 'C', 'food', 5000, 8874, 20, 720),
                            ('u-g1', 'G', 'goods', 5000, 6000, .5, 0), ('u-g2', 'G', 'goods', 5000, 6000, .5, 0)])
        buy = [b['sku'] for b in plan(self.p)]
        self.assertEqual(buy[:2], ['G', 'C'])
        st = read(self.p, 'microeconomics-status.json')
        self.assertEqual(st['policy_version'], 'microecon-v2')
        g = {r['sku']:r for r in st['ranking']}['G']
        self.assertEqual(g['tier'], 'supported_positive_turnover')
        self.assertIn('velocity', g)
        self.assertIn('food_rotation', st)

    def test_hook_admits_proven_quick_short_shelf_food(self):
        setup(self.p)
        own_ledger(self.p, [(f'u-s{k}', 'S', 'food', 5000, 9000, 1, 48) for k in range(3)])
        buy = [b['sku'] for b in plan(self.p)]
        self.assertIn('S', buy)  # catalog shelf 48 h <= 72 h, but proven 1 h rotation + 24 h buffer < 48 h
        st = read(self.p, 'microeconomics-status.json')
        self.assertTrue(st['food_rotation']['S']['qualified'])


if __name__ == '__main__':
    unittest.main()
