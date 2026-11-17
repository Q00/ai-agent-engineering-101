"""unrestricted-rank-v1 (amendment #8): config['financial_limits'] = 'disabled' turns off every simulated-market financial
limit (lifetime spend, stage/concurrent exposure, cash floor, bankrupt-line hold, combined loss+warranty ceiling,
pilot per-run line cap). Finite states only; unknown/corrupt values (incl. numeric sentinels) fail closed; configs
without the key stay enforced. Money room = observed balance - genuine in-flight (held, unconfirmed) reserves.
Also: market-tier public-only demand sizing only when the public median paid covers the contract worst-bound cost.
Run from the stage dir (sources under test + root test_warranty.py fixtures)."""
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import microeconomics
import oneday_pilot
import restock_r3
import warranty
from test_warranty import fresh_ctx, key_file, pilot_state

HERE = Path(__file__).resolve().parent


def set_config(d, **kw):
    """Test-only: rewrite the fixture config and rebind its hash in the gate (sources binding untouched)."""
    d = Path(d)
    c = json.loads((d/oneday_pilot.CONFIG).read_text())
    caps = kw.pop('caps', None)
    if caps: c['caps'] = dict(c['caps'], **caps)
    c.update(kw)
    (d/oneday_pilot.CONFIG).write_text(json.dumps(c))
    g = json.loads((d/'seller-v2-approved.json').read_text())
    g['one_day_pilot']['config_sha256'] = hashlib.sha256((d/oneday_pilot.CONFIG).read_bytes()).hexdigest()
    (d/'seller-v2-approved.json').write_text(json.dumps(g))


class Pins(unittest.TestCase):
    def test_modules_under_test_are_the_staged_files(self):
        for m in (oneday_pilot, warranty, microeconomics, restock_r3):
            self.assertEqual(Path(m.__file__).resolve().parent, HERE, m.__name__)


class FinancialLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.key = key_file(self.d)
        self.kp = patch.object(warranty, 'KEY', self.key)
        self.kp.start()
        self.now = 1_000_000.0
        pilot_state(self.d, self.now)
        self.inv = json.loads((self.d/'inventory.json').read_text())

    def tearDown(self):
        self.kp.stop()
        self.tmp.cleanup()

    def test_mode_parser_is_finite_state(self):
        self.assertEqual(oneday_pilot.financial_limits_mode({}), 'enforced')
        self.assertEqual(oneday_pilot.financial_limits_mode({'financial_limits':'disabled'}), 'disabled')
        self.assertEqual(oneday_pilot.financial_limits_mode({'financial_limits':'enforced'}), 'enforced')
        for bad in ('Infinity', 'DISABLED', '', math.inf, float('nan'), 10**18, True, None, ['disabled'], {'x':1}):
            self.assertIsNone(oneday_pilot.financial_limits_mode({'financial_limits':bad}), repr(bad))
        self.assertIsNone(oneday_pilot.financial_limits_mode(None))

    def test_absent_key_keeps_every_cap_enforced(self):
        set_config(self.d, caps={'lifetime_spend':0})
        ctx = fresh_ctx(self.d, self.now)
        self.assertFalse(ctx['active'])
        self.assertEqual(ctx['reason'], 'lifetime_cap')
        self.assertEqual(ctx['financial_limits_mode'], 'enforced')

    def test_disabled_ignores_lifetime_stage_cash_floor_and_loss_ceiling(self):
        set_config(self.d, loss_ceiling='disabled', financial_limits='disabled',
                   caps={'lifetime_spend':1, 'stage1':1, 'stage2':1, 'cash_floor':10**9, 'gross_loss':1})
        ctx = fresh_ctx(self.d, self.now)
        self.assertTrue(ctx['active'], ctx.get('reason'))
        self.assertEqual(ctx['reason'], 'active')
        bal = self.inv['balance']
        self.assertEqual(ctx['room'], bal-ctx['new_reserve'])  # full server cash minus genuine in-flight reserves
        self.assertEqual(ctx['per_run_lines'], oneday_pilot.KIT_MAX_LINES)
        self.assertEqual(oneday_pilot.KIT_MAX_LINES, 10)
        fl = ctx['financial_limits']
        self.assertEqual(fl['mode'], 'disabled')
        self.assertFalse(fl['enforced'])
        self.assertEqual(fl['available_cash'], bal-ctx['new_reserve'])
        for k in ('lifetime_spend', 'stage', 'cash_floor', 'bankrupt_line_hold', 'loss_ceiling', 'per_run_lines'):
            self.assertIn(k, fl['not_enforced'])
        # old accounting still computed and reported, not hidden
        for k in ('legacy_reserve', 'warranty_liability', 'lifetime_committed', 'concurrent', 'gross_loss',
                  'cash_room', 'bank_room'):
            self.assertIn(k, ctx)
        self.assertEqual(ctx['caps']['lifetime_spend'], 1)  # historical value retained, not rewritten
        for v in (ctx['room'], fl['available_cash']):
            self.assertTrue(math.isfinite(v))

    def test_disabled_still_deducts_in_flight_and_fails_when_cash_gone(self):
        set_config(self.d, loss_ceiling='disabled', financial_limits='disabled')
        pend = json.loads((self.d/'restock-pending.json').read_text())
        bal = self.inv['balance']
        pend.append({'sku':'Gx', 'at':self.now, 'reserve':bal+1, 'matched_unit_id':None, 'one_day_pilot':'one-day-v1'})
        (self.d/'restock-pending.json').write_text(json.dumps(pend))
        ctx = fresh_ctx(self.d, self.now)
        self.assertFalse(ctx['active'])
        self.assertEqual(ctx['reason'], 'cash_insufficient')

    def test_disabled_keeps_expiry_and_integrity_guards(self):
        set_config(self.d, loss_ceiling='disabled', financial_limits='disabled')
        c = json.loads((self.d/oneday_pilot.CONFIG).read_text())
        self.assertEqual(fresh_ctx(self.d, c['expires_at'])['reason'], 'expired')
        g = json.loads((self.d/'seller-v2-approved.json').read_text())
        g['one_day_pilot']['config_sha256'] = '0'*64
        (self.d/'seller-v2-approved.json').write_text(json.dumps(g))
        self.assertEqual(fresh_ctx(self.d, self.now)['reason'], 'config_hash_mismatch')

    def test_invalid_mode_fails_closed(self):
        for bad in ('Infinity', 1e300, 'off'):
            set_config(self.d, loss_ceiling='disabled', financial_limits=bad)
            ctx = fresh_ctx(self.d, self.now)
            self.assertFalse(ctx['active'])
            self.assertEqual(ctx['reason'], 'financial_limits_mode_invalid')
            self.assertIsNone(warranty._money(self.d, self.now))

    def test_warranty_money_and_accounting_follow_available_cash(self):
        set_config(self.d, loss_ceiling='disabled', financial_limits='disabled', caps={'cash_floor':10**9})
        ctx = fresh_ctx(self.d, self.now)
        self.assertEqual(warranty._money(self.d, self.now), ctx['financial_limits']['available_cash'])
        acc = microeconomics.accounting(ctx, self.inv)
        self.assertEqual(acc['actionable_acquisition_room'], ctx['room'])
        self.assertEqual(acc['financial_limits_mode'], 'disabled')

    def test_status_reports_disabled_mode(self):
        set_config(self.d, loss_ceiling='disabled', financial_limits='disabled')
        ctx = fresh_ctx(self.d, self.now)
        st = oneday_pilot.write_status(self.d, ctx, self.now)
        self.assertEqual(st['financial_limits_mode'], 'disabled')
        self.assertFalse(st['financial_limits']['enforced'])
        self.assertEqual(st['room'], ctx['room'])


class WorstBoundMarketSizing(unittest.TestCase):
    def table(self, median, ub=1000):
        return [{'sku':'G1', 'tier':microeconomics.NEW_GOODS_TIER,
                 'market':{'settlements':52, 'shops':16, 'window_s':7200, 'median_paid':median,
                           'unit_upper_bound':ub, 'compatibility_median_over_upper_bound':median/ub}}]

    def test_public_median_below_worst_bound_is_a_single_probe(self):
        d = restock_r3._demand('G1', {'units':{}}, self.table(605), [], [], 1000.0)
        self.assertEqual(d['basis'], 'public_below_worst_bound_probe')
        self.assertEqual(d['target'], 1)
        self.assertEqual(d['need'], 1)
        held = [{'sku':'G1', 'kind':'goods', 'unit':'u1'}]
        self.assertEqual(restock_r3._demand('G1', {'units':{}}, self.table(605), held, [], 1000.0)['need'], 0)

    def test_public_median_at_or_above_worst_bound_uses_public_rate(self):
        d = restock_r3._demand('G1', {'units':{}}, self.table(1000), [], [], 1000.0)
        self.assertEqual(d['basis'], 'public_per_shop_share')
        self.assertEqual(d['target'], math.ceil(52/16/2*restock_r3.COVER_H))

    def test_own_exact_settles_still_win(self):
        facts = {'units':{'a':{'sku':'G1', 'sold_at':900.0}}}
        d = restock_r3._demand('G1', facts, self.table(605), [], [], 1000.0)
        self.assertEqual(d['basis'], 'own_exact_settles')


if __name__ == '__main__':
    unittest.main()
