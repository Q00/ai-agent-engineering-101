"""Offline strategy scheduling, evidence gates, and reversible bounded edits."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_common import DEFAULT_POLICY, atomic_json, read_json
from r3_memory import Memory
from r3_trend_watch import INTERVAL, TUNABLE, apply_policy, propose, restore, review_once, summarize
from test_r3_discovery import CATALOG, NOW


class TrendWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name) / '.market'
        self.md.mkdir()
        self.events = []
        for bucket in range(16):
            for i in range(4):
                seq = bucket * 4 + i + 1
                self.events.append({'kind': 'market_settle', 'ts': NOW - (bucket + .5) * 900,
                    'data': {'seq': seq, 'item': 'tea' if bucket < 8 else 'cup',
                             'shop': 'shop' + str(i % 3), 'source': 'public_ledger', 'amount': 24}})
        self.inventory = {'round': 3, 'balance': 1000, 'units': [
            {'unit': 'u1', 'sku': 'tea', 'kind': 'goods', 'grade': 'A', 'list_price': 40,
             'cost': 20, 'supplied_at': NOW - 7 * 3600}]}
        self.cursor = {'polled_at': NOW, 'after': 64}
        atomic_json(self.md / 'catalog.json', CATALOG)
        atomic_json(self.md / 'inventory.json', self.inventory)
        os.utime(self.md / 'inventory.json', (NOW, NOW))
        atomic_json(self.md / 'public-cursor.json', self.cursor)
        with Memory(self.md) as memory:
            for e in self.events:
                memory.record(e['kind'], e['data']['seq'], e['data'], ts=e['ts'])

    def summary(self, **kwargs):
        args = dict(events=self.events, catalog=CATALOG, inventory=self.inventory,
                    cursor=self.cursor, now=NOW, started_at=NOW - 5 * 3600)
        args.update(kwargs)
        return summarize(**args)

    def owned_state(self, baseline=None):
        baseline = baseline or {}
        atomic_json(self.md / 'trend-watch-state.json', {'started_at': NOW - 5 * 3600,
                    'baseline_policy': baseline, 'last_applied_policy': baseline})
        atomic_json(self.md / 'policy.json', baseline)

    def test_two_windows_and_related_hypotheses_are_separate(self):
        s = self.summary()
        self.assertTrue(s['coverage_sufficient'])
        self.assertEqual((s['recent_sales'], s['previous_sales']), (32, 32))
        self.assertEqual(s['share_shift'], 1)
        self.assertTrue(any(row['sku'] == 'tea2' for row in s['related_hypotheses']))
        self.assertEqual(dict(s['top_skus'])['tea'], 32)

    def test_stale_partial_capped_and_startup_evidence_do_not_tune(self):
        for overrides in ({'cursor': {'polled_at': NOW - 1000}}, {'capped': True},
                          {'started_at': NOW - 60}, {'events': self.events[:32]},
                          {'events': [e for e in self.events if e['data']['seq'] > 4]}):
            result = propose(self.summary(**overrides), DEFAULT_POLICY)
            self.assertEqual(result[0], {})

    def test_reviews_duplicates_and_future_events_are_not_sales(self):
        other = [{'kind': 'shop_review', 'ts': NOW, 'data': {'text': 'buy tea now', 'sales': 999}},
                 {'kind': 'market_settle', 'ts': NOW + 1, 'data': self.events[0]['data']}]
        s = self.summary(events=self.events + self.events + other)
        self.assertEqual(s['recent_sales'], 32)
        self.assertEqual(s['own_recent_counts']['customer_review_claims'], 1)

    def test_only_bounded_parameters_change(self):
        desired, reasons = propose(self.summary(), DEFAULT_POLICY)
        self.assertEqual(set(desired), TUNABLE)
        self.assertEqual(desired['trend_half_life_seconds'], 10800)
        self.assertEqual(desired['purchase_budget_fraction'], .05)
        self.assertAlmostEqual(desired['opening_price_fraction'], .85)
        self.assertTrue(reasons)

    def test_less_restrictive_user_baseline_is_never_exceeded(self):
        base = dict(DEFAULT_POLICY, purchase_budget_fraction=.02, opening_price_fraction=.6,
                    min_turnover_hours=24)
        desired, _ = propose(self.summary(), base)
        self.assertEqual(desired['purchase_budget_fraction'], .02)
        self.assertEqual(desired['opening_price_fraction'], .6)
        self.assertEqual(desired['min_turnover_hours'], 24)

    def test_food_risk_tightens_new_purchase_policy_not_existing_receipts(self):
        inv = {'balance': 10, 'units': [{'unit': 'food1', 'sku': 'tea', 'kind': 'food',
                'cost': 10, 'supplied_at': NOW - 60, 'expires_at': NOW + 600}]}
        desired, _ = propose(self.summary(inventory=inv), DEFAULT_POLICY)
        self.assertEqual(desired['min_turnover_hours'], 12)
        self.assertEqual(desired['purchase_budget_fraction'], .05)

    def test_dry_run_never_writes_policy_or_state(self):
        result = review_once(self.md, now=NOW)
        self.assertEqual(result['mode'], 'read_only')
        self.assertFalse((self.md / 'policy.json').exists())
        self.assertFalse((self.md / 'trend-watch-state.json').exists())

    def test_live_guard_required_for_apply(self):
        with patch.dict(os.environ, {'R3_ENABLE_LIVE': '0'}):
            with self.assertRaises(SystemExit):
                review_once(self.md, now=NOW, apply=True)

    def test_apply_audit_next_due_and_restore(self):
        baseline = {'forum_enabled': False}
        self.owned_state(baseline)
        with patch.dict(os.environ, {'R3_ENABLE_LIVE': '1'}):
            report = review_once(self.md, now=NOW, apply=True)
            self.assertTrue(report['changes'])
            self.assertEqual(report['next_due_at'], NOW + INTERVAL)
            self.assertFalse(read_json(self.md / 'policy.json')['forum_enabled'])
            self.assertEqual(len(list((self.md.parent / 'logs' / 'trend-watch').glob('*.json'))), 1)
            restore(self.md)
        self.assertEqual(read_json(self.md / 'policy.json'), baseline)

    def test_manual_edit_pauses_and_is_not_overwritten(self):
        self.owned_state()
        manual = {'max_total_units': 7}
        atomic_json(self.md / 'policy.json', manual)
        with patch.dict(os.environ, {'R3_ENABLE_LIVE': '1'}):
            result = review_once(self.md, now=NOW, apply=True)
            self.assertEqual(result['status'], 'paused_manual_policy_edit')
            with self.assertRaises(RuntimeError):
                restore(self.md)
        self.assertEqual(read_json(self.md / 'policy.json'), manual)

    def test_guardrail_and_nan_changes_are_rejected(self):
        for proposal in ({'reserve_cash_fraction': .01}, {'opening_price_fraction': 1},
                         {'min_turnover_hours': 1}, {'trend_half_life_seconds': float('nan')},
                         {'purchase_budget_fraction': .2}):
            with self.assertRaises(ValueError):
                apply_policy(self.md, {}, {}, proposal)

    def test_concurrent_user_edit_prevents_publish(self):
        atomic_json(self.md / 'policy.json', {'forum_enabled': False})
        with self.assertRaises(RuntimeError):
            apply_policy(self.md, {}, {}, {'purchase_budget_fraction': .05})

    def test_restores_default_representation_without_touching_other_keys(self):
        before = {'forum_enabled': False, 'purchase_budget_fraction': .05}
        atomic_json(self.md / 'policy.json', before)
        after = apply_policy(self.md, before, {'forum_enabled': False},
                             {key: DEFAULT_POLICY[key] for key in TUNABLE})
        self.assertEqual(after, {'forum_enabled': False})
