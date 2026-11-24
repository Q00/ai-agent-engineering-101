import copy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from restock_evidence import attach_pilot_state
from restock_policy import plan
from restock_runtime import SupplyGuard
import test_exploration


class TurnoverTests(unittest.TestCase):
    setUp = test_exploration.ExplorationTests.setUp
    planned = test_exploration.ExplorationTests.planned
    market = test_exploration.ExplorationTests.market

    def test_turnover_allows_reconciled_unsold_pilot_and_negative_estimate(self):
        cfg = replace(self.cfg, turnover_mode=True, purchase_budget=500000)
        inv = {**self.inv, '_exploration_occupied': True,
               'surge': [{'sku': 'x', 'factor': 1.5}]}
        result = plan(inv, self.catalog, self.demand, now=self.now, cfg=cfg)
        self.assertTrue(result['buy'])
        self.assertLess(result['candidate_economics']['x']['estimated_margin'], 0)
        inv['_exploration_pending'] = True
        self.assertFalse(plan(inv, self.catalog, self.demand, now=self.now, cfg=cfg)['buy'])

    def test_total_cap_survives_restart_and_new_window_even_with_stale_proposal(self):
        cfg = replace(self.cfg, turnover_mode=True, purchase_budget=50000)
        proposal = self.planned()
        market = self.market()
        guard = SupplyGuard(self.state, 'r', cfg, clock=lambda: self.now)
        stale = lambda *args: proposal
        self.assertEqual(guard.step(market, stale, self.catalog, lambda inv: 50000)['reason'], 'bought')
        used = guard.read()['purchase_commitment_used']
        self.assertEqual(used, proposal['cash_commitment_upper'])
        self.now += 200
        self.inv['restock'] = {'open': True, 'opens_at': self.now-1, 'closes_at': self.now+180}
        restarted = SupplyGuard(self.state, 'r', cfg, clock=lambda: self.now)
        self.assertEqual(restarted.step(market, stale, self.catalog, lambda inv: 50000)['reason'],
                         'purchase_budget_exhausted')
        self.assertEqual(market.posts, 1)
        fresh = copy.deepcopy(self.inv)
        attach_pilot_state(fresh, restarted.read(), None, cfg)
        self.assertEqual(fresh['_purchase_remaining'], 50000-used)

    def test_refusal_releases_total_reservation_but_timeout_keeps_it(self):
        cfg = replace(self.cfg, turnover_mode=True, purchase_budget=50000)
        proposal = self.planned()
        def call(method, *args, **kwargs):
            if method == 'POST':
                return SimpleNamespace(status_code=400, json=lambda: {})
            return SimpleNamespace(status_code=200, json=lambda: copy.deepcopy(self.inv))
        guard = SupplyGuard(self.state, 'r', cfg, clock=lambda: self.now)
        guard.step(SimpleNamespace(call=call), lambda *a: proposal, self.catalog, lambda inv: 50000)
        self.assertEqual(guard.read()['purchase_commitment_used'], 0)
        self.now += 61
        def uncertain(method, *args, **kwargs):
            if method == 'POST':
                raise TimeoutError()
            return call(method, *args, **kwargs)
        guard.step(SimpleNamespace(call=uncertain), lambda *a: proposal, self.catalog, lambda inv: 50000)
        self.assertEqual(guard.read()['purchase_commitment_used'], proposal['cash_commitment_upper'])
        self.assertIsNotNone(guard.read()['pending'])
