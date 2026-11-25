from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import inventory_exploration as explore
import restock


def catalog():
    return {f's{i}': {'name': f'Product {i}', 'kind': 'food' if i % 2 else 'goods',
                      'category': f'category{i}', 'list_price': 10000, 'shelf_hours': 96}
            for i in range(12)}


def inventory(open=True, balance=5_000_000):
    return {'round': 3, 'balance': balance, 'units': [],
            'restock': {'open': open, 'opens_at': 900, 'closes_at': 1200}}


def select(*args):
    return {'skus': [f's{i}' for i in range(8)], 'reason': 'Eight distinct small experiments'}


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        # This class tests active-market behavior, independently of today's date.
        # Production keeps its real deadline; only this fixture uses an active period.
        synthetic_end = time.time() + 48 * 3600
        for target in ('capital_policy.ROUND_END', 'newsvendor_tool.ROUND_END'):
            deadline = patch(target, synthetic_end)
            deadline.start()
            self.addCleanup(deadline.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temp.name)
        self.path = Path('state/test-exploration.json')

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def run_basket(self, inv=None, now=1000, model=select, **kwargs):
        return explore.run(inv or inventory(), catalog(), [], Counter(), now,
                           state_path=self.path, model=model, **kwargs)

    def test_bootstrap_buys_many_skus_even_without_any_sales(self):
        result = self.run_basket()
        self.assertEqual(len(result['buy']), 8)
        self.assertTrue(all(r['qty'] == 1 for r in result['buy']))
        state = json.loads(self.path.read_text())
        self.assertEqual(state['reserved'], 90000)
        self.assertTrue(state['completed'])
        self.assertIsNone(self.run_basket(now=1060, model=lambda *_: self.fail('must not replan')))

    def test_closed_window_prepares_basket_but_does_not_reserve_or_emit(self):
        self.assertEqual(self.run_basket(inventory(False)), {'buy': []})
        state = json.loads(self.path.read_text())
        self.assertEqual(state['reserved'], 0)
        self.assertEqual(state['intents'], [])
        self.assertEqual(len(state['basket']), 8)
        self.assertEqual(len(self.run_basket(model=lambda *_: self.fail('reuse basket'))['buy']), 8)

    def test_unknown_requests_are_never_retried_even_after_fresh_snapshot(self):
        # Only two sequential supply calls fit; all remaining calls wait for the next window.
        first = self.run_basket(now=1160)
        self.assertEqual(len(first['buy']), 2)
        second = self.run_basket(now=1001)  # next simulated open window
        self.assertTrue(set(r['sku'] for r in first['buy']).isdisjoint(r['sku'] for r in second['buy']))
        self.assertEqual(len(second['buy']), 6)

    def test_existing_stock_counts_toward_variety_and_budget(self):
        inv = inventory(); inv['units'] = [{'unit': 'old', 'sku': 's0', 'cost': 200000}]
        def model(role, prompt, schema):
            self.assertEqual(prompt['budget_remaining'], 50000)
            self.assertFalse(any(r['sku'] == 's0' for r in prompt['candidates']))
            return {'skus': ['s1', 's2', 's3', 's4'], 'reason': 'Fit remaining budget'}
        result = self.run_basket(inv, model=model)
        self.assertEqual(len(result['buy']), 4)
        state = json.loads(self.path.read_text())
        self.assertLessEqual(state['initial_cost'] + state['reserved'], explore.BUDGET)

    def test_invalid_or_overbudget_model_baskets_cannot_purchase(self):
        cases = [['s0'] * 5, ['unknown'] * 5, ['s0'], [None] * 5]
        for selection in cases:
            with self.subTest(selection=selection):
                self.path.unlink(missing_ok=True)
                result = self.run_basket(model=lambda *_: {'skus': selection, 'reason': ''})
                self.assertEqual(result, {'buy': []})
                self.assertEqual(json.loads(self.path.read_text())['reserved'], 0)
        self.path.unlink()
        expensive = {s: dict(p, list_price=60000) for s,p in catalog().items()}
        with patch.object(explore, 'candidates', return_value=[dict(p, sku=s, cost_ceiling=45000) for s,p in expensive.items()]):
            self.assertEqual(self.run_basket(), {'buy': []})

    def test_cash_is_rechecked_at_execution_not_only_model_selection(self):
        self.run_basket(inventory(False))
        result = self.run_basket(inventory(balance=explore.MIN_BALANCE+15_000), model=lambda *_: self.fail('reuse'))
        self.assertEqual(len(result['buy']), 1)
        self.assertEqual(json.loads(self.path.read_text())['reserved'], 11250)

    def test_catalog_change_replans_without_erasing_prior_reservations(self):
        self.run_basket(inventory(False))
        changed = catalog(); changed['s0']['list_price'] = 20000
        result = explore.run(inventory(), changed, [], Counter(), 1000,
                             state_path=self.path, model=select)
        self.assertEqual(result, {'buy': []})
        self.assertIsNone(json.loads(self.path.read_text())['basket'])

    def test_window_closing_or_stale_inventory_during_model_call_prevents_purchase(self):
        for fresh in (True, False):
            self.path.unlink(missing_ok=True)
            self.assertEqual(self.run_basket(refresh=lambda: (inventory(False), 1201, fresh)), {'buy': []})
            self.assertEqual(json.loads(self.path.read_text())['reserved'], 0)

    def test_candidate_set_excludes_fragile_expiry_and_contains_goods(self):
        products = catalog(); products['short'] = dict(products['s1'], shelf_hours=24)
        products['huge'] = dict(products['s0'], list_price=1_000_000)
        rows = explore.candidates(products, inventory(), [], Counter())
        self.assertTrue(any(r['kind'] == 'goods' for r in rows))
        self.assertNotIn('short', [r['sku'] for r in rows]); self.assertNotIn('huge', [r['sku'] for r in rows])

    def test_unavailable_model_has_backoff_and_no_automatic_purchase(self):
        def offline(*_): raise RuntimeError('offline')
        self.assertEqual(self.run_basket(model=offline), {'buy': []})
        self.assertEqual(self.run_basket(now=1060, model=lambda *_: self.fail('backoff')), {'buy': []})

    def test_restocker_routes_window_inventory_through_exploration_first(self):
        state = Path('.market'); state.mkdir()
        inv = inventory(); inv['restock'].update(opens_at=time.time()-10, closes_at=time.time()+300)
        (state/'inventory.json').write_text(json.dumps(inv))
        (state/'catalog.json').write_text(json.dumps(catalog()))
        with patch.object(restock, 'decide_json', side_effect=select) as model, \
             patch.object(restock, 'fetch_market_sales', return_value=Counter()), \
             patch.object(explore, 'window_open', return_value=True):
            result = restock.run(state)
        self.assertEqual(len(result['buy']), 8)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.args[0], 'startup inventory selector')

    def test_continuation_value_changes_the_actual_quantity_calculation(self):
        no_future = restock.quantity_candidates(1., 22740, 18950, 0, 2)
        future = restock.quantity_candidates(1., 22740, 18950, 0, 2, 12000)
        self.assertTrue(any(r['qty'] > 0 for r in future))
        self.assertGreater(max(r['qty'] for r in future), max(r['qty'] for r in no_future))
        calc = next(r for r in future if r['qty'])['calculation']
        self.assertEqual(calc['salvage'], 12000)


if __name__ == '__main__':
    unittest.main()
