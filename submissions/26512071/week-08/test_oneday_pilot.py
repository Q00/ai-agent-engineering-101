"""One-day inventory pilot (one-day-v1): user-authorized 24 h mandate, hash-bound, fail-closed.
Offline fixtures only. Legacy UNKNOWN rows stay frozen at full reserve; new acquisitions are reserved at the supply
contract's upper bound (A-grade 75% list x max surge 1.5), never at list; gross losses never netted by profits."""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

OWN = 'https://own.example'
START = 5000000
BALANCE = 5634613
OLD_ACT = 10  # expired risk-exception-v1 pilot, like live
ACT = 200000  # one-day activation in the fixture
NOW = ACT+100


def settle(seq, sku, amount, unit=None, ts=None, shop=OWN):
    return {'seq':seq, 'ts':ts or NOW-500, 'kind':'settle', 'round':3,
            'data':{'shop':shop, 'item':sku, 'amount':amount, 'unit':unit or f'u-old-{seq}'}}


def item(cat, price, kind='food', **kw):
    if kind == 'food': kw.setdefault('shelf_hours', 720)
    return dict(name=f'Fixture {cat}', category=cat, kind=kind, list_price=price, **kw)


CATALOG = {'K':item('cK', 22710, shelf_hours=168), 'O':item('cO', 36000), 'C':item('cC', 14790),
           'S':item('cS', 10000, shelf_hours=48), 'G':item('cG', 12900, 'goods'), 'X':item('cX', 9000),
           'H':item('cH', 50000), 'L':item('cL', 61270, 'goods')}
# Realized/list: K .66, O .63, C .60, S .90 (shelf too short), G .68 goods, H .70 (too dear for stage-1 SKU cap),
# X one settle only (unproven).
ROWS = [settle(10, 'K', 14989), settle(11, 'K', 14989), settle(12, 'O', 22680), settle(13, 'O', 22680),
        settle(14, 'C', 8874), settle(15, 'C', 8874), settle(16, 'S', 9000), settle(17, 'S', 9000),
        settle(18, 'G', 8772), settle(19, 'G', 8772), settle(20, 'X', 8000), settle(21, 'H', 35000),
        settle(22, 'H', 35000)]
LEGACY = [{'sku':'L', 'at':5+k, 'reserve':61270, 'category':'cL', 'policy_version':'bounded-v2', 'baseline_unit_ids':[],
           'matched_unit_id':None, 'evidence_ids':[1, 2], 'local_biased_memory_ids':[]} for k in range(4)]  # 245080


def read(p, name):
    return json.loads((p/name).read_text(encoding='utf-8'))


def write(p, name, obj):
    (p/name).write_text(json.dumps(obj), encoding='utf-8')


def inventory(p, balance=BALANCE, units=(), **kw):
    inv = {'round':3, 'balance':balance, 'start':START, 'bankrupt_line':500000, 'storage_per_day':0.01,
           'units':list(units)}
    inv.update(kw)
    write(p, 'inventory.json', inv)


def cache(p, rows=ROWS, fetched=None):
    write(p, 'public-settlements.json', {'source_url':'https://market.ourolabs.ai/api/ledger?after=-500',
                                         'fetched_at':NOW-10 if fetched is None else fetched, 'own_urls':[OWN+'/'],
                                         'rows':list(rows), 'price_rows':[]})


def setup(p, balance=BALANCE, units=(), act=ACT, gate=True, caps=None, legacy_rows=LEGACY):
    import risk_pilot, oneday_pilot
    inventory(p, balance, units)
    write(p, 'catalog.json', CATALOG)
    cache(p)
    data = json.dumps(list(legacy_rows), indent=2).encode()
    (p/'restock-pending.json').write_bytes(data)
    (p/'backup-pending.json').write_bytes(data)
    old = risk_pilot.prepare(p, OLD_ACT, p/'backup-pending.json', authorization='fixture')
    got = oneday_pilot.activate(p, act, authorization='fixture', sources={'fixture':'0'}, caps=caps)
    g = {'approved':True, 'policy_version':'bounded-v2', 'source_sha256':{}, 'risk_pilot':old}
    if gate: g['one_day_pilot'] = got
    write(p, 'seller-v2-approved.json', g)
    return got


def ctx(p, now=NOW):
    import oneday_pilot, risk_pilot
    pending = read(p, 'restock-pending.json')
    led = risk_pilot.load_ledger(p)
    ids, complete = risk_pilot.frozen(pending, led)
    return oneday_pilot.context(p, now, read(p, 'inventory.json'), pending, ids, complete, led)


INV = {'invocation':'inv1', 'at':NOW, 'log':'nowhere.log', 'log_offset':None}


def plan(p, now=NOW, inv=None):
    import restock_r3
    return restock_r3.plan(p, now, inv or dict(INV, at=now))['buy']


class Base(unittest.TestCase):
    def setUp(self):
        import oneday_pilot, restock_r3
        here = Path(__file__).resolve().parent
        # Staged discovery may shadow the candidate: the modules under test must be the ones next to this file.
        self.assertEqual(Path(oneday_pilot.__file__).resolve().parent, here)
        self.assertEqual(Path(restock_r3.__file__).resolve().parent, here)
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)/'.market'
        self.p.mkdir()

    def tearDown(self):
        self.tmp.cleanup()


class Activation(Base):
    def test_one_shot_24h_and_exact_caps(self):
        import oneday_pilot
        setup(self.p)
        cfg = read(self.p, oneday_pilot.CONFIG)
        self.assertEqual(cfg['activated_at'], ACT)
        self.assertEqual(cfg['expires_at'], ACT+86400)
        c = cfg['caps']
        self.assertEqual((c['lifetime_spend'], c['stage1'], c['stage2'], c['gross_loss'], c.get('sku_share'), c['cash_floor']),
                         (1000000, 100000, 350000, 120000, None, 5000000))  # sku_share retired
        self.assertEqual(cfg['baseline']['balance'], BALANCE)
        self.assertEqual(cfg['legacy']['reserve'], 245080)
        self.assertEqual(cfg['legacy']['count'], 4)
        with self.assertRaises(ValueError):  # never re-armed, never reset
            oneday_pilot.activate(self.p, NOW, authorization='again', sources={})

    def test_refuses_activation_when_legacy_rows_changed(self):
        import oneday_pilot, risk_pilot
        inventory(self.p)
        write(self.p, 'catalog.json', CATALOG)
        data = json.dumps(LEGACY).encode()
        (self.p/'backup-pending.json').write_bytes(data)
        (self.p/'restock-pending.json').write_bytes(data)
        risk_pilot.prepare(self.p, OLD_ACT, self.p/'backup-pending.json')
        write(self.p, 'restock-pending.json', LEGACY[:3])
        with self.assertRaises(ValueError):
            oneday_pilot.activate(self.p, ACT, sources={})
        self.assertFalse((self.p/oneday_pilot.CONFIG).exists())

    def test_upper_bound_is_contract_max_not_list(self):
        import oneday_pilot
        self.assertEqual(oneday_pilot.upper_bound(14790), 16639)  # ceil(14790 x .75 x 1.5)
        self.assertEqual(oneday_pilot.upper_bound(22710), 25549)


class Context(Base):
    def test_active_rooms_stage1(self):
        setup(self.p)
        c = ctx(self.p)
        self.assertTrue(c['active'], c)
        self.assertEqual(c['stage'], 1)
        self.assertEqual(c['stage_cap'], 100000)
        self.assertIsNone(c['sku_cap'])  # slot-recovery-v1: concentration cap retired
        self.assertEqual(c['legacy_reserve'], 245080)
        self.assertEqual(c['room'], 100000)
        self.assertEqual(c['lifetime_committed'], 0)

    def test_cash_guard_allows_documented_expanded_case(self):
        # 5634613 - 350000 - 245080 = 5039533 >= 5,000,000: the risk-adjusted guard must not deadlock stage 2.
        setup(self.p)
        c = ctx(self.p)
        self.assertGreaterEqual(c['cash_room'], 350000)
        inventory(self.p, balance=5300000)
        self.assertEqual(ctx(self.p)['room'], 5300000-245080-5000000)
        inventory(self.p, balance=5245080)
        c = ctx(self.p)
        self.assertFalse(c['active'])
        self.assertEqual(c['reason'], 'cash_insufficient')

    def test_gate_without_binding_or_with_changed_config_fails_closed(self):
        import oneday_pilot
        setup(self.p, gate=False)
        self.assertEqual(ctx(self.p)['reason'], 'gate_closed')
        g = read(self.p, 'seller-v2-approved.json')
        g['one_day_pilot'] = {'config_sha256':'0'*64}
        write(self.p, 'seller-v2-approved.json', g)
        self.assertEqual(ctx(self.p)['reason'], 'config_hash_mismatch')

    def test_missing_or_corrupt_ledger_fails_closed_and_is_never_reset(self):
        import oneday_pilot
        setup(self.p)
        (self.p/oneday_pilot.LEDGER).write_text('{corrupt', encoding='utf-8')
        self.assertEqual(ctx(self.p)['reason'], 'ledger_invalid')
        self.assertEqual(plan(self.p), [])
        self.assertEqual((self.p/oneday_pilot.LEDGER).read_text(encoding='utf-8'), '{corrupt')
        (self.p/oneday_pilot.LEDGER).unlink()
        self.assertEqual(ctx(self.p)['reason'], 'ledger_invalid')
        self.assertEqual(plan(self.p), [])
        self.assertFalse((self.p/oneday_pilot.LEDGER).exists())

    def test_expiry_24h_from_activation(self):
        setup(self.p)
        self.assertTrue(ctx(self.p, ACT+86399)['active'])
        c = ctx(self.p, ACT+86400)
        self.assertFalse(c['active'])
        self.assertEqual(c['reason'], 'expired')
        self.assertFalse(ctx(self.p, ACT-1)['active'])

    def test_legacy_ledger_changed_fails_closed(self):
        setup(self.p)
        rows = read(self.p, 'restock-pending.json')
        rows[0]['operator_failure'] = 'invented'
        write(self.p, 'restock-pending.json', rows)
        self.assertEqual(ctx(self.p)['reason'], 'legacy_rows_changed')
        self.assertEqual(plan(self.p), [])

    def test_invalid_inventory_fails_closed(self):
        import oneday_pilot
        with self.assertRaises(ValueError):  # activation itself refuses an invalid inventory
            setup(self.p, units=[{'unit':'u-1', 'sku':'C', 'cost':float('nan'), 'kind':'food'}])
        self.assertFalse((self.p/oneday_pilot.CONFIG).exists())
        tmp = Path(self.tmp.name)/'b'/'.market'
        tmp.mkdir(parents=True)
        setup(tmp)
        inventory(tmp, units=[{'unit':'u-1', 'sku':'C', 'cost':float('nan'), 'kind':'food'}])
        self.assertEqual(ctx(tmp)['reason'], 'inventory_invalid')
        self.assertEqual(plan(tmp), [])


class Plan(Base):
    def test_zero_stock_bootstrap_buys_within_stage1_at_upper_bound(self):
        import oneday_pilot
        setup(self.p)
        buy = plan(self.p)
        self.assertEqual(buy, BUY)  # slot-recovery-v1 re-pin: no held_max/sku_share; H (ub 56250 > old 50000 share) now eligible
        rows = [r for r in read(self.p, 'restock-pending.json') if r.get('one_day_pilot') == oneday_pilot.PILOT]
        self.assertEqual(sorted(r['reserve'] for r in rows), [16639, 25549, 56250])
        self.assertLessEqual(sum(r['reserve'] for r in rows), 100000)
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertEqual(sorted(q['ub'] for q in led['requests'].values()), [16639, 25549, 56250])
        self.assertEqual(read(self.p, 'restock-pending.json')[:4], LEGACY)  # legacy rows byte-identical in content

    def test_repeated_ticks_never_exceed_stage_or_lifetime(self):
        setup(self.p)
        plan(self.p)
        for k in range(5):
            self.assertEqual(plan(self.p, NOW+60*(k+1), dict(INV, invocation=f'tick{k}')), [])
        c = ctx(self.p, NOW+400)
        self.assertLessEqual(c['concurrent'], 100000)
        self.assertEqual(c['lifetime_committed'], 98438)

    def test_single_sku_share_retired_stage_cap_still_binds(self):
        # slot-recovery-v1: the old .5 x stage share (50000) no longer limits one SKU; the stage cap still does
        setup(self.p)
        plan(self.p)
        rows = read(self.p, 'restock-pending.json')
        by = {}
        for r in rows[4:]: by[r['sku']] = by.get(r['sku'], 0)+r['reserve']
        self.assertEqual(by.get('H'), 56250)
        self.assertLessEqual(sum(by.values()), 100000)

    def test_short_shelf_and_unproven_skus_excluded(self):
        setup(self.p)
        skus = {b['sku'] for b in plan(self.p)}
        self.assertFalse(skus & {'S', 'X'})

    def test_expired_pilot_buys_nothing_and_never_falls_through(self):
        setup(self.p)
        self.assertEqual(plan(self.p, ACT+86400), [])
        self.assertEqual(read(self.p, 'restock-pending.json'), LEGACY)
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['reason'], 'one_day_pilot_inactive')

    def test_frozen_legacy_rows_hold_money_not_unit_slots(self):
        # Live: legacy UNKNOWN rows of the best SKUs (2 x kimchi) are covered by the separate legacy reserve; counting
        # them again as held units would block the SKU twice. Real on-hand units and new pending rows still count.
        legacy = [dict(LEGACY[0], sku='K', reserve=22710, category='cK', at=7+k) for k in range(2)]+LEGACY[:2]
        setup(self.p, legacy_rows=legacy)
        self.assertEqual(plan(self.p)[1], {'sku':'K', 'qty':1})  # legacy K rows never block K as slots

    def test_no_invocation_no_buy(self):
        import restock_r3
        setup(self.p)
        self.assertEqual(restock_r3.plan(self.p, NOW, None)['buy'], [])

    def test_cash_guard_bounds_plan(self):
        setup(self.p, balance=5300000)
        rows_before = len(read(self.p, 'restock-pending.json'))
        plan(self.p)
        new = read(self.p, 'restock-pending.json')[rows_before:]
        self.assertLessEqual(sum(r['reserve'] for r in new), 5300000-245080-5000000)

    def test_concurrent_plans_share_the_caps(self):
        setup(self.p)
        out = []
        ts = [threading.Thread(target=lambda k=k: out.append(plan(self.p, NOW, dict(INV, invocation=f'c{k}'))))
              for k in range(4)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertLessEqual(ctx(self.p)['concurrent'], 100000)
        self.assertEqual(sum(1 for b in out if b), 1)

    def test_new_unknown_is_never_released_and_lifetime_never_recycled(self):
        import oneday_pilot
        setup(self.p)
        plan(self.p)
        for k in range(3):
            plan(self.p, NOW+86000, dict(INV, invocation=f'late{k}'))
        rows = [r for r in read(self.p, 'restock-pending.json') if r.get('one_day_pilot') == oneday_pilot.PILOT]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['matched_unit_id'] is None and r['reserve'] > 0 for r in rows))
        self.assertEqual(ctx(self.p, NOW+86000)['lifetime_committed'], 98438)


def kit_log(p, inv_id, buy, supply):
    """The kit's log for one hook invocation: marker, buy line, one [supply] line per entry, next hook output."""
    log = p.parent/'logs'/'market-kit.log'
    log.parent.mkdir(exist_ok=True)
    lines = [f'14:00:00 [buyer] [restock] invocation {inv_id}',
             '14:00:00 [buyer] '+json.dumps({'buy':buy, 'invocation':inv_id})]+[f'14:00:1{k} {x}' for k, x in enumerate(supply)]
    log.write_text('\n'.join(lines+['14:01:00 [buyer] [restock] invocation next'])+'\n', encoding='utf-8')


def unit(uid, sku, cost, kind='food', supplied=NOW+20, expires=NOW+700*3600):
    u = {'unit':uid, 'sku':sku, 'cost':cost, 'kind':kind, 'grade':'A', 'supplied_at':supplied}
    if kind == 'food': u['expires_at'] = expires
    return u


# slot-recovery-v1 re-fixture: the default plan is H1, K1, C1 (98438 at upper bound) instead of K1, O1, C2
BUY = [{'sku':'H', 'qty':1}, {'sku':'K', 'qty':1}, {'sku':'C', 'qty':1}]
ARRIVED = [unit('u-h1', 'H', 45000), unit('u-k1', 'K', 15000), unit('u-c1', 'C', 8500)]
REFUSED_402 = "[supply] {'sku': 'H', 'qty': 1} refused: 402 {\"error\": \"balance\"}"


class Observe(Base):
    def bought(self):
        log = self.p.parent/'logs'/'market-kit.log'
        log.parent.mkdir(exist_ok=True)
        log.write_text('', encoding='utf-8')
        setup(self.p)
        self.assertEqual(plan(self.p, NOW, dict(INV, log=str(log), log_offset=0)), BUY)

    def supplied(self):
        self.bought()
        kit_log(self.p, 'inv1', BUY, ['[supply] 1 x Fixture cH for 45000, balance 5589613',
                                      '[supply] 1 x Fixture cK for 15000, balance 5574613',
                                      '[supply] 1 x Fixture cC for 8500, balance 5566113'])
        inventory(self.p, balance=5566113, units=ARRIVED)

    def tick(self, now=NOW+60):
        import restock_r3
        restock_r3.plan(self.p, now, None)
        return ctx(self.p, now)

    def test_refused_request_releases_lifetime_and_reserve(self):
        import oneday_pilot
        self.bought()
        kit_log(self.p, 'inv1', BUY, [REFUSED_402])
        c = self.tick()
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertEqual(sorted(q['status'] for q in led['requests'].values()), ['not_attempted', 'not_attempted', 'refused'])
        self.assertEqual(c['lifetime_committed'], 0)
        self.assertEqual(c['new_reserve'], 0)
        self.assertEqual(read(self.p, 'restock-pending.json'), LEGACY)

    def test_unknown_result_keeps_upper_bound(self):
        self.bought()
        kit_log(self.p, 'inv1', BUY, [])  # segment ended with no [supply] line: unknown, never released
        c = self.tick()
        self.assertEqual(c['lifetime_committed'], 98438)
        self.assertEqual(c['new_reserve'], 98438)

    def test_supplied_totals_replace_upper_bound_and_units_recorded(self):
        import oneday_pilot
        self.supplied()
        c = self.tick()
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertEqual(sorted(led['units']), ['u-c1', 'u-h1', 'u-k1'])
        self.assertEqual(c['lifetime_committed'], 68500)
        # completion correction: exact supplied totals replace the rows' upper bound financially (no row->unit mapping);
        # the confirmed 68,500 is fully covered by the exact observed pilot unit costs, so it is counted once
        self.assertEqual(c['concurrent'], 68500)
        self.assertEqual(c['new_reserve'], 0)
        self.assertEqual(c['financial']['unmatched_total'], 0)
        self.assertTrue(all(r['matched_unit_id'] is None for r in read(self.p, 'restock-pending.json') if r.get('one_day_pilot')))
        self.assertEqual(c['stage'], 1)
        for k in range(3): c = self.tick(NOW+120+k)
        self.assertEqual(c['lifetime_committed'], 68500)  # replays never add
        self.assertEqual((c['new_reserve'], c['concurrent']), (0, 68500))  # replays never re-add a reserve

    def test_explicitly_prebound_matched_rows_count_once(self):
        # FIXTURE-ONLY historical binding (rows matched before slot-recovery-v1 keep their mapping); not kit proof
        self.supplied()
        rows = read(self.p, 'restock-pending.json')
        ids = iter(['u-h1', 'u-k1', 'u-c1'])
        for r in rows[4:]: r.update(matched_unit_id=next(ids), reserve=0)
        write(self.p, 'restock-pending.json', rows)
        c = self.tick()
        self.assertEqual((c['lifetime_committed'], c['concurrent'], c['new_reserve']), (68500, 68500, 0))

    def test_settled_sale_expands_stage_and_profit_never_offsets(self):
        self.supplied()
        self.tick()
        cache(self.p, ROWS+[settle(100, 'K', 20000, 'u-k1', NOW+100), settle(101, 'C', 8000, 'u-c1', NOW+101)])
        inventory(self.p, balance=5594113, units=ARRIVED[:1])
        c = self.tick(NOW+200)
        self.assertEqual(c['stage'], 2)
        self.assertEqual(c['stage_cap'], 350000)
        self.assertIsNone(c['sku_cap'])
        self.assertEqual(c['gross_loss'], 500)  # 8500-8000; the 5000 profit on u-k1 offsets nothing

    def test_foreign_shop_or_non_pilot_settle_is_ignored(self):
        self.supplied()
        self.tick()
        cache(self.p, ROWS+[settle(100, 'K', 20000, 'u-k1', NOW+100, shop='https://other.example'),
                            settle(101, 'C', 8000, 'u-zz', NOW+101)])
        c = self.tick(NOW+200)
        self.assertEqual(c['stage'], 1)
        self.assertEqual(c['settled_sale_events'], [])

    def test_expired_food_counts_cost_once(self):
        self.supplied()
        self.tick()
        units = ARRIVED[:2]+[unit('u-c1', 'C', 8500, expires=NOW+150)]
        inventory(self.p, balance=5566113, units=units)
        for k in range(3): c = self.tick(NOW+200+k)
        self.assertEqual(c['gross_loss'], 8500)
        inventory(self.p, balance=5566113, units=ARRIVED[:2])  # thrown away at cost
        self.assertEqual(self.tick(NOW+300)['gross_loss'], 8500)

    def test_vanished_without_evidence_is_provisional_then_replaced_by_sale(self):
        self.supplied()
        self.tick()
        inventory(self.p, balance=5566113, units=[ARRIVED[0]]+ARRIVED[2:])
        c = self.tick(NOW+200)
        self.assertEqual(c['gross_loss'], 15000)
        self.assertEqual(c['loss']['provisional'], 15000)
        cache(self.p, ROWS+[settle(102, 'K', 30000, 'u-k1', NOW+150)])
        self.assertEqual(self.tick(NOW+260)['gross_loss'], 0)

    def test_broken_refund_counted_once_with_storage(self):
        setup(self.p)
        g = unit('u-g1', 'G', 7000, 'goods', supplied=NOW)
        inventory(self.p, units=[g])
        self.tick(NOW+10)
        inventory(self.p, units=[])
        rows = ROWS+[settle(103, 'G', 9000, 'u-g1', NOW+86400),
                     {'seq':104, 'ts':NOW+86400+60, 'kind':'broken', 'round':3,
                      'data':{'shop':OWN, 'unit':'u-g1', 'refunded':9000, 'item':'G'}}]
        cache(self.p, rows)
        c = self.tick(NOW+86400+120)
        # corrected: margin max(0, 7000-9000)=0, refund outflow 9000 (no evidenced recovery), 1 day storage 70
        self.assertAlmostEqual(c['gross_loss'], 9000+7000*.01*1.0)
        self.assertAlmostEqual(c['loss']['storage'], 70.0)
        self.assertAlmostEqual(self.tick(NOW+86400+180)['gross_loss'], 9070.0)

    def test_loss_cap_blocks_new_buys_but_tracking_continues_after_expiry(self):
        setup(self.p)
        dead = [unit(f'u-d{k}', 'C', 40000, expires=NOW+5) for k in range(3)]  # 120000 expired at cost
        inventory(self.p, units=dead)
        self.assertEqual(plan(self.p, NOW+10), [])
        self.assertEqual(ctx(self.p, NOW+10)['reason'], 'loss_cap')
        inventory(self.p, units=[])
        cache(self.p, ROWS+[settle(105, 'C', 41000, 'u-d0', NOW+3)])  # sold before expiry: no loss on u-d0
        c = self.tick(ACT+86400+100)
        self.assertEqual(c['reason'], 'expired')
        self.assertEqual(c['gross_loss'], 80000)
        self.assertEqual(c['settled_sale_events'], ['settle:105'])

    def test_status_artifact_is_reporter_readable(self):
        import oneday_pilot
        setup(self.p)
        plan(self.p)
        st = read(self.p, oneday_pilot.STATUS)
        for k in ('activated_kst', 'expires_kst', 'caps', 'stage', 'room', 'lifetime_committed', 'gross_loss', 'legacy',
                  'baseline', 'blockers', 'requests', 'units', 'loss', 'reason'):
            self.assertIn(k, st)
        self.assertEqual(st['activated_kst'], oneday_pilot.kst(ACT))
        self.assertEqual(st['lifetime_committed'], 98438)
        self.assertEqual(st['legacy']['reserve'], 245080)


class Cli(Base):
    def test_cli_hook_buys_through_candidate_modules(self):
        import subprocess, sys, time, oneday_pilot, restock_r3
        here = Path(__file__).resolve().parent
        now = time.time()
        setup(self.p, act=now-10)
        cache(self.p, fetched=now)  # fresh: the CLI refresh makes no network call
        g = read(self.p, 'seller-v2-approved.json')
        g['source_sha256'] = {n:hashlib.sha256((here/n).read_bytes()).hexdigest() for n in restock_r3.SOURCES}
        write(self.p, 'seller-v2-approved.json', g)
        r = subprocess.run([sys.executable, str(here/'restock_r3.py'), '--state', str(self.p)], cwd=str(here),
                           capture_output=True, text=True, timeout=60)
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines and lines[0].startswith('[restock] invocation '), r.stdout+r.stderr)
        out = json.loads(lines[-1])
        self.assertEqual(out['buy'], BUY)
        st = read(self.p, oneday_pilot.STATUS)
        self.assertEqual(st['lifetime_committed'], 98438)
        self.assertEqual(sorted(k.split(':')[0] for k in st['requests']), [out['invocation']]*3)
        g['source_sha256']['oneday_pilot.py'] = '0'*64  # any source drift closes the hook
        write(self.p, 'seller-v2-approved.json', g)
        r = subprocess.run([sys.executable, str(here/'restock_r3.py'), '--state', str(self.p)], cwd=str(here),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])['buy'], [])


def led_with(units, events):
    return {'pilot':'one-day-v1', 'requests':{}, 'units':units, 'events':events}


def food(cost):
    return {'sku':'K', 'cost':cost, 'kind':'food', 'supplied_at':NOW, 'expires_at':NOW+10**6, 'gone_at':NOW+50}


def sold(uid, price, seq):
    return {f'settle:{seq}':{'kind':'settle', 'seq':seq, 'ts':NOW+40, 'unit':uid, 'price':price}}


def broke(uid, refunded, seq):
    return {f'broken:{seq}':{'kind':'broken', 'seq':seq, 'ts':NOW+60, 'unit':uid, 'refunded':refunded}}


class GrossLossCorrection(Base):
    """Correction 1 (parent review 2026-11-10): a profitable original sale must not offset a later refund outflow."""

    def test_profitable_sale_does_not_offset_refund(self):
        import oneday_pilot
        loss = oneday_pilot.losses(led_with({'u-1':food(100)}, {**sold('u-1', 150, 1), **broke('u-1', 50, 2)}),
                                   {'units':[]}, NOW+100)
        self.assertEqual((loss['margin'], loss['refund'], loss['total']), (0, 50, 50))

    def test_below_cost_sale_and_refund_count_cost_once(self):
        import oneday_pilot
        loss = oneday_pilot.losses(led_with({'u-1':food(100)}, {**sold('u-1', 80, 1), **broke('u-1', 80, 2)}),
                                   {'units':[]}, NOW+100)
        self.assertEqual((loss['margin'], loss['refund'], loss['total']), (20, 80, 100))

    def test_profitable_other_units_never_offset(self):
        import oneday_pilot
        loss = oneday_pilot.losses(led_with({'u-1':food(100), 'u-2':food(100)},
                                            {**sold('u-1', 300, 1), **sold('u-2', 70, 2)}), {'units':[]}, NOW+100)
        self.assertEqual(loss['total'], 30)

    def test_unknown_refund_amount_fails_acquisition_closed(self):
        import oneday_pilot
        loss = oneday_pilot.losses(led_with({'u-1':food(100)}, {**sold('u-1', 150, 1), **broke('u-1', None, 2)}),
                                   {'units':[]}, NOW+100)
        self.assertTrue(loss['unknown'])
        setup(self.p)
        g = unit('u-g1', 'G', 7000, 'goods', supplied=NOW)
        inventory(self.p, units=[g])
        plan(self.p, NOW+10, dict(INV, invocation='a'))
        inventory(self.p, units=[])
        cache(self.p, ROWS+[settle(103, 'G', 9000, 'u-g1', NOW+20),
                            {'seq':104, 'ts':NOW+30, 'kind':'broken', 'round':3,
                             'data':{'shop':OWN, 'unit':'u-g1', 'item':'G'}}])  # refunded missing
        self.assertEqual(plan(self.p, NOW+60, dict(INV, invocation='b')), [])
        self.assertEqual(ctx(self.p, NOW+60)['reason'], 'loss_unknown')
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertIsNone(led['events']['broken:104']['refunded'])  # recorded as unknown, never normalized to 0


class PreflightCorrection(Base):
    """Correction 2: one-day integrity is checked BEFORE supply resolution/reconciliation/pruning mutate pending."""

    def matchable(self):
        import oneday_pilot
        setup(self.p)
        row = dict(LEGACY[0], sku='C', reserve=16639, category='cC', at=NOW, one_day_pilot=oneday_pilot.PILOT)
        write(self.p, 'restock-pending.json', LEGACY+[row])
        inventory(self.p, units=[unit('u-new', 'C', 8000)])  # would match the row and zero its reserve

    def assert_untouched(self, reason):
        import oneday_pilot
        before = (self.p/'restock-pending.json').read_bytes()
        counters = (self.p/'risk-pilot-counters.json').read_bytes()
        self.assertEqual(plan(self.p, NOW+60), [])
        self.assertEqual((self.p/'restock-pending.json').read_bytes(), before)
        self.assertEqual((self.p/'risk-pilot-counters.json').read_bytes(), counters)
        self.assertEqual(read(self.p, oneday_pilot.STATUS)['reason'], reason)

    def test_corrupt_ledger_leaves_pending_untouched(self):
        import oneday_pilot
        self.matchable()
        (self.p/oneday_pilot.LEDGER).write_text('{corrupt', encoding='utf-8')
        self.assert_untouched('ledger_invalid')

    def test_missing_ledger_leaves_pending_untouched(self):
        import oneday_pilot
        self.matchable()
        (self.p/oneday_pilot.LEDGER).unlink()
        self.assert_untouched('ledger_invalid')

    def test_config_binding_mismatch_leaves_pending_untouched(self):
        self.matchable()
        g = read(self.p, 'seller-v2-approved.json')
        g['one_day_pilot'] = {'config_sha256':'0'*64}
        write(self.p, 'seller-v2-approved.json', g)
        self.assert_untouched('config_hash_mismatch')

    def test_expiry_alone_still_reconciles_and_observes(self):
        import oneday_pilot
        self.matchable()
        self.assertEqual(plan(self.p, ACT+86400+10), [])
        rows = read(self.p, 'restock-pending.json')
        # exact-evidence rule: u-new is observed in the pilot ledger, but the row is NOT inferred to be u-new: it stays
        # outstanding at full reserve after expiry (no TTL), legacy rows byte-equal in content
        open_rows = [r for r in rows if r.get('one_day_pilot') and r['matched_unit_id'] is None]
        self.assertEqual([(r['sku'], r['reserve']) for r in open_rows], [('C', 16639)])
        self.assertEqual(rows[:4], LEGACY)
        self.assertIn('u-new', read(self.p, oneday_pilot.LEDGER)['units'])
        self.assertEqual(read(self.p, oneday_pilot.STATUS)['reason'], 'expired')


LIVE = Path(__file__).resolve().parent/'fixture_live_unattributed_20261110.json'


class UnattributedAcquisition(Base):
    """Final gap (parent review 2026-11-10): a supplied pilot unit that arrived and sold between inventory snapshots.
    Its supplied cost must stay a provisional gross loss and block new acquisition until an exact join attributes it."""

    def live(self):
        import copy
        snap = json.loads(LIVE.read_text(encoding='utf-8'))
        write(self.p, 'one-day-pilot.json', snap['config'])
        write(self.p, 'one-day-pilot-ledger.json', snap['ledger'])
        write(self.p, 'inventory.json', snap['inventory'])
        write(self.p, 'catalog.json', snap['catalog'])
        return copy.deepcopy(snap)

    def test_live_snapshot_missing_unit_is_provisional_not_zero(self):
        import oneday_pilot
        snap = self.live()
        now = snap['captured_at']
        loss = oneday_pilot.losses(snap['ledger'], snap['inventory'], now)
        self.assertEqual(loss['unattributed'], {'Ga2e3075f':{'missing':1, 'cost':8827}})
        self.assertEqual(loss['unattributed_acquisition_cost'], 8827)
        self.assertGreaterEqual(loss['provisional'], 8827)
        self.assertGreaterEqual(loss['total'], 8827)
        self.assertEqual(oneday_pilot.losses(snap['ledger'], snap['inventory'], now+60)['unattributed_acquisition_cost'],
                         8827)  # repeated ticks: same liability, never added twice

    def test_live_public_settle_exact_join_attributes_unit_once(self):
        import oneday_pilot
        snap = self.live()
        now = snap['captured_at']
        cache = {'own_urls':snap['own_urls'], 'rows':snap['raw_rows'], 'price_rows':[]}
        self.assertTrue(oneday_pilot.observe(self.p, [], snap['inventory'], cache, now))
        led = read(self.p, oneday_pilot.LEDGER)
        u = led['units']['u-9b29c415094b']
        self.assertEqual((u['sku'], u['cost'], u['kind'], u['attributed']), ('Ga2e3075f', 8827, 'goods', 'exact_residual_join'))
        self.assertIn('settle:4759057', led['events'])
        loss = oneday_pilot.losses(led, snap['inventory'], now)
        self.assertEqual(loss['unattributed_acquisition_cost'], 0)
        self.assertEqual(loss['units']['u-9b29c415094b']['state'], 'sold')
        self.assertFalse(oneday_pilot.observe(self.p, [], snap['inventory'], cache, now+60))  # idempotent
        broken = {'seq':4769999, 'ts':now+100, 'kind':'broken', 'round':3,
                  'data':{'shop':snap['own_urls'][-1], 'unit':'u-9b29c415094b', 'refunded':8827}}
        oneday_pilot.observe(self.p, [], snap['inventory'], dict(cache, rows=snap['raw_rows']+[broken]), now+120)
        loss = oneday_pilot.losses(read(self.p, oneday_pilot.LEDGER), snap['inventory'], now+120)
        self.assertEqual(loss['refund'], 8827)  # the joined unit's refund no longer escapes

    def test_ambiguous_missing_units_are_never_allocated(self):
        import oneday_pilot
        setup(self.p)
        led = read(self.p, oneday_pilot.LEDGER)
        led['requests']['x:0'] = {'sku':'G', 'qty':2, 'unit_ub':14513, 'ub':29026, 'at':NOW, 'status':'supplied',
                                  'total':14000}
        write(self.p, oneday_pilot.LEDGER, led)
        cache = {'own_urls':[OWN], 'rows':[settle(200, 'G', 9000, 'u-x', NOW+50)], 'price_rows':[]}
        oneday_pilot.observe(self.p, [], read(self.p, 'inventory.json'), cache, NOW+60)
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertNotIn('u-x', led['units'])  # two missing units: no per-unit cost is provable
        loss = oneday_pilot.losses(led, read(self.p, 'inventory.json'), NOW+60)
        self.assertEqual(loss['unattributed'], {'G':{'missing':2, 'cost':14000}})

    def test_unattributed_cost_denies_new_acquisition(self):
        import oneday_pilot
        setup(self.p)
        led = read(self.p, oneday_pilot.LEDGER)
        led['requests']['x:0'] = {'sku':'C', 'qty':1, 'unit_ub':16639, 'ub':16639, 'at':NOW, 'status':'supplied',
                                  'total':8000}
        write(self.p, oneday_pilot.LEDGER, led)
        for k in range(3):
            self.assertEqual(plan(self.p, NOW+60+k, dict(INV, invocation=f'u{k}')), [])
        c = ctx(self.p, NOW+70)
        self.assertEqual(c['reason'], 'unattributed_acquisition')
        self.assertEqual(c['gross_loss'], 8000)
        st = read(self.p, oneday_pilot.STATUS)
        self.assertEqual(st['unattributed_acquisition_cost'], 8000)

    def test_observed_unit_gone_before_sale_ingest_stays_provisional_once(self):
        import oneday_pilot
        setup(self.p)
        led = read(self.p, oneday_pilot.LEDGER)
        led['requests']['x:0'] = {'sku':'C', 'qty':1, 'unit_ub':16639, 'ub':16639, 'at':NOW, 'status':'supplied',
                                  'total':8000}
        write(self.p, oneday_pilot.LEDGER, led)
        inventory(self.p, units=[unit('u-c9', 'C', 8000)])
        plan(self.p, NOW+60, dict(INV, invocation='a'))
        inventory(self.p, units=[])
        plan(self.p, NOW+120, dict(INV, invocation='b'))
        loss = oneday_pilot.losses(read(self.p, oneday_pilot.LEDGER), {'units':[]}, NOW+120)
        self.assertEqual((loss['unattributed_acquisition_cost'], loss['provisional'], loss['total']), (0, 8000, 8000))


SELLER = Path(__file__).resolve().parent/'fixture_live_seller_decisions_20261110.json'


class ExactSellerBackfill(Base):
    """Exact backfill (parent brief 2026-11-10): our own seller's decision journal names the unit, SKU and cost it
    negotiated; with the exact own-shop public settle of that unit it restores the never-observed supplied unit.
    Arrival time is NOT invented: supplied_at_unknown, storage counted from activation (labelled assumption)."""

    def live(self, lines=None, partial=b''):
        snap = json.loads(LIVE.read_text(encoding='utf-8'))
        write(self.p, 'one-day-pilot.json', snap['config'])
        write(self.p, 'one-day-pilot-ledger.json', snap['ledger'])
        write(self.p, 'inventory.json', snap['inventory'])
        write(self.p, 'catalog.json', snap['catalog'])
        raw = [x['raw'] for x in json.loads(SELLER.read_text(encoding='utf-8'))['lines']] if lines is None else lines
        (self.p/'decisions.jsonl').write_bytes(('\n'.join(['{"event": "other"}']*3+raw)+'\n').encode('utf-8')+partial)
        return snap

    def observe(self, snap, now=None):
        import oneday_pilot
        cache = {'own_urls':snap['own_urls'], 'rows':snap['raw_rows'], 'price_rows':[]}
        return oneday_pilot.observe(self.p, [], snap['inventory'], cache, now or snap['captured_at'])

    def test_live_seller_rows_backfill_unit_with_provenance(self):
        import oneday_pilot
        snap = self.live(partial=b'{"unit": "u-9b29c415094b", "cost": 1')  # torn tail line is never read
        self.assertTrue(self.observe(snap))
        led = read(self.p, oneday_pilot.LEDGER)
        u = led['units']['u-9b29c415094b']
        self.assertEqual((u['sku'], u['cost'], u['attributed']), ('Ga2e3075f', 8827, 'exact_seller_decision'))
        self.assertIsNone(u['supplied_at'])
        self.assertTrue(u['supplied_at_unknown'])
        self.assertEqual(u['storage_from'], snap['config']['activated_at'])
        prov = u['evidence']
        self.assertEqual([x['line'] for x in prov['seller_rows']], [4, 5, 6, 7])  # fixture line numbers
        self.assertEqual(prov['settle_seq'], 4759057)
        self.assertEqual(len(prov['seller_rows'][0]['sha256']), 64)
        loss = oneday_pilot.losses(led, snap['inventory'], snap['captured_at'])
        self.assertEqual(loss['unattributed_acquisition_cost'], 0)
        self.assertEqual(loss['units']['u-9b29c415094b']['state'], 'sold')
        self.assertFalse(self.observe(snap, snap['captured_at']+60))  # idempotent

    def test_conflicting_seller_costs_fail_closed(self):
        import oneday_pilot
        rows = [x['raw'] for x in json.loads(SELLER.read_text(encoding='utf-8'))['lines']]
        bad = json.loads(rows[1])
        bad['cost'] = 9000
        snap = self.live(rows[:1]+[json.dumps(bad)]+rows[2:])
        self.observe(snap)
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertNotIn('u-9b29c415094b', led['units'])  # neither seller backfill nor residual join
        self.assertIn('u-9b29c415094b', led['conflicts'])
        self.assertEqual(oneday_pilot.losses(led, snap['inventory'], snap['captured_at'])['unattributed_acquisition_cost'],
                         8827)

    def test_seller_cost_disagreeing_with_exact_residual_fails_closed(self):
        import oneday_pilot
        rows = []
        for x in json.loads(SELLER.read_text(encoding='utf-8'))['lines']:
            d = json.loads(x['raw'])
            if 'cost' in d and d['cost'] is not None: d['cost'] = 8000
            rows.append(json.dumps(d))
        snap = self.live(rows)
        self.observe(snap)
        led = read(self.p, oneday_pilot.LEDGER)
        self.assertNotIn('u-9b29c415094b', led['units'])
        self.assertIn('u-9b29c415094b', led['conflicts'])

    def test_rows_before_activation_or_not_seller_rows_are_ignored(self):
        import oneday_pilot
        rows = []
        for x in json.loads(SELLER.read_text(encoding='utf-8'))['lines']:
            d = json.loads(x['raw'])
            d.pop('decided_by', None)  # not a seller decision record
            rows.append(json.dumps(d))
        snap = self.live(rows)
        led = read(self.p, oneday_pilot.LEDGER)
        for q in led['requests'].values():
            if q['sku'] == 'Ga2e3075f': q['total'] = None  # residual no longer exact
        write(self.p, oneday_pilot.LEDGER, led)
        self.observe(snap)
        self.assertNotIn('u-9b29c415094b', read(self.p, oneday_pilot.LEDGER)['units'])
        rows = []
        for x in json.loads(SELLER.read_text(encoding='utf-8'))['lines']:
            d = json.loads(x['raw'])
            d['ts'] = snap['config']['activated_at']-10
            rows.append(json.dumps(d))
        (self.p/'decisions.jsonl').write_text('\n'.join(rows)+'\n', encoding='utf-8')
        self.observe(snap)
        self.assertNotIn('u-9b29c415094b', read(self.p, oneday_pilot.LEDGER)['units'])

    def test_residual_joined_unit_loses_invented_arrival_time(self):
        import oneday_pilot
        snap = self.live()
        led = read(self.p, oneday_pilot.LEDGER)
        led['units']['u-9b29c415094b'] = {'sku':'Ga2e3075f', 'cost':8827, 'kind':'goods', 'grade':'A',
                                          'supplied_at':1794288253.416813, 'expires_at':None, 'first_seen':1,
                                          'gone_at':1, 'attributed':'exact_residual_join', 'evidence_seq':4759057}
        write(self.p, oneday_pilot.LEDGER, led)
        self.observe(snap)
        u = read(self.p, oneday_pilot.LEDGER)['units']['u-9b29c415094b']
        self.assertIsNone(u['supplied_at'])
        self.assertTrue(u['supplied_at_unknown'])
        self.assertEqual(u['storage_from'], snap['config']['activated_at'])
        self.assertEqual(u['previous_supplied_at_assumption'], 1794288253.416813)
        self.assertEqual([x['line'] for x in u['evidence']['seller_rows']], [4, 5, 6, 7])

    def test_storage_counts_from_activation_when_arrival_unknown(self):
        import oneday_pilot
        led = led_with({'u-g':{'sku':'G', 'cost':10000, 'kind':'goods', 'supplied_at':None, 'supplied_at_unknown':True,
                               'storage_from':NOW, 'gone_at':NOW+86400}}, sold('u-g', 10000, 1))
        led['events']['settle:1']['ts'] = NOW+86400
        self.assertAlmostEqual(oneday_pilot.losses(led, {'units':[]}, NOW+90000)['storage'], 100.0)


if __name__ == '__main__':
    unittest.main()
