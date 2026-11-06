"""Offline budget, inventory freshness, commitment, and purchase-boundary tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_common import DEFAULT_POLICY, atomic_json, read_json
from r3_discovery import build_candidates
from r3_memory import Memory
from r3_restock import _pending, _snapshot_id, bounded_proposals, main, plan_restock, validate_intent, wholesale_bound
from test_r3_discovery import CATALOG, NOW, choose_all, event


def inventory(balance=1000, units=None):
    return {'round': 3, 'balance': balance, 'units': units or [], 'storage_per_day': 1, 'surge': [],
            'restock': {'open': True, 'opens_at': NOW - 60, 'closes_at': NOW + 3600}}


def unit(sku='tea', name='held', cost=30):
    return dict(CATALOG[sku], unit=name, sku=sku, cost=cost, grade='A', supplied_at=NOW - 60,
                expires_at=None, cert='test-only')


def analysis(events=None, catalog=None):
    inputs = build_candidates(catalog or CATALOG, events or [], now=NOW)
    return {'status': 'ok', 'inputs': inputs, 'current_inputs': inputs,
            'hypotheses': [{'sku': row['sku'], 'evidence_ids': row['evidence_ids'], 'reason': 'Test evidence.'}
                           for row in inputs['candidates']], 'input_version': 'offline'}


def choose(system, state, validator, purpose, deadline, **kwargs):
    if purpose == 'r3-discovery':
        return choose_all(system, state, validator, purpose, deadline, **kwargs)
    return validator({'buy': state['proposals']})


class RestockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.md = Path(self.directory.name)
        atomic_json(self.md / 'policy.json', {'fallback_enabled': False})
        atomic_json(self.md / 'catalog.json', CATALOG)
        self.write_inventory(inventory())

    def write_inventory(self, value, now=NOW):
        atomic_json(self.md / 'inventory.json', value)
        os.utime(self.md / 'inventory.json', (now, now))

    def test_initial_exploration_is_three_diverse_units_under_budget(self):
        result = bounded_proposals(CATALOG, inventory(balance=1500), analysis(), [], DEFAULT_POLICY, NOW)
        self.assertEqual(sum(row['qty'] for row in result), 3)
        self.assertEqual(len({CATALOG[row['sku']]['category'] for row in result}), 3)
        self.assertLessEqual(sum(wholesale_bound(CATALOG[row['sku']]) * row['qty'] for row in result), 150)
        self.assertEqual(bounded_proposals(CATALOG, inventory(units=[unit(), unit('cup', 'u2'),
                                          unit('pen', 'u3')]), analysis(), [], DEFAULT_POLICY, NOW), [])

    def test_pending_budget_preserves_cash_reserve_and_stock_caps(self):
        pending = [{'sku': 'tea', 'qty': 3}, {'sku': 'cup', 'qty': 3}]
        result = bounded_proposals(CATALOG, inventory(balance=240), analysis(), pending, DEFAULT_POLICY, NOW)
        self.assertEqual(result, [])
        events = [event('market_settle', str(i), {'seq': i, 'item': 'tea'}) for i in range(4)]
        result = bounded_proposals(CATALOG, inventory(units=[unit(), unit(name='u2')]),
                                   analysis(events), [], DEFAULT_POLICY, NOW)
        self.assertEqual(next(row for row in result if row['sku'] == 'tea')['qty'], 1)
        result = bounded_proposals(CATALOG, inventory(units=[unit(name=str(i)) for i in range(40)]),
                                   analysis(events), [], DEFAULT_POLICY, NOW)
        self.assertEqual(result, [])

    def test_category_exposure_limit(self):
        units = [unit(name=str(i), cost=200) for i in range(3)]
        events = [event('market_settle', str(i), {'seq': i, 'item': 'tea'}) for i in range(4)]
        result = bounded_proposals(CATALOG, inventory(units=units), analysis(events), [], DEFAULT_POLICY, NOW)
        self.assertFalse(any(CATALOG[row['sku']]['category'] == 'drinks' for row in result))

    def test_one_order_does_not_purchase_all_substitutes_or_repeat_later(self):
        events = [event('order_seen', 'o1', {'order': 'o1', 'category': 'drinks', 'acceptable': ['tea', 'tea2']})]
        result = bounded_proposals(CATALOG, inventory(), analysis(events), [], DEFAULT_POLICY, NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['qty'], 1)
        held = inventory(units=[unit(result[0]['sku'])])
        self.assertEqual(bounded_proposals(CATALOG, held, analysis(events), [], DEFAULT_POLICY, NOW), [])

    def test_food_and_trading_horizon_guard(self):
        policy = dict(DEFAULT_POLICY, trading_deadline=NOW + 2 * 3600)
        self.assertEqual(bounded_proposals(CATALOG, inventory(), analysis(), [], policy, NOW), [])
        policy['trading_deadline'] = NOW + 12 * 3600
        self.assertEqual(bounded_proposals(CATALOG, inventory(), analysis(), [], policy, NOW), [])
        food = {'food': {'name': 'Fresh', 'category': 'fresh', 'kind': 'food', 'list_price': 40, 'shelf_hours': 2}}
        self.assertEqual(bounded_proposals(food, inventory(), analysis(catalog=food), [], DEFAULT_POLICY, NOW), [])

    def test_goods_charge_one_percent_of_cost_daily_but_food_has_no_storage_charge(self):
        policy = dict(DEFAULT_POLICY, min_turnover_hours=34 * 24, trading_deadline=None)
        state = inventory()
        state.pop('storage_per_day')
        self.assertEqual(bounded_proposals(CATALOG, state, analysis(), [], policy, NOW), [])
        food = {'food': {'name': 'Food', 'category': 'food', 'kind': 'food',
                         'list_price': 40, 'shelf_hours': 1000}}
        self.assertEqual(bounded_proposals(food, dict(state, storage_per_day=100),
                                          analysis(catalog=food), [], policy, NOW),
                         [{'sku': 'food', 'qty': 1}])

    def test_reconciliation_preserves_fulfilled_units_before_matching_new_arrivals(self):
        ledger = {'intents': [
            {'expires_at': NOW + 1800, 'baseline_units': [],
             'buy': [{'sku': 'tea', 'qty': 1}], 'fulfilled_units': ['z-old'],
             'fulfilled_by_sku': {'tea': ['z-old']}},
            {'expires_at': NOW + 1800, 'baseline_units': ['z-old'],
             'buy': [{'sku': 'tea', 'qty': 1}], 'fulfilled_units': [], 'fulfilled_by_sku': {}},
        ]}
        state = inventory(units=[unit(name='z-old'), unit(name='a-new')])
        self.assertEqual(_pending(ledger, state, NOW), [])
        self.assertEqual(ledger['intents'][0]['fulfilled_by_sku']['tea'], ['z-old'])
        self.assertEqual(ledger['intents'][1]['fulfilled_by_sku']['tea'], ['a-new'])
        self.assertEqual(_pending(ledger, state, NOW), [])

    def test_validation_cannot_raise_caps_add_skus_or_duplicate(self):
        for rows in ([{'sku': 'tea', 'qty': 3}], [{'sku': 'cup', 'qty': 1}],
                     [{'sku': 'tea', 'qty': True}], [{'sku': 'tea', 'qty': 1}, {'sku': 'tea', 'qty': 1}]):
            with self.assertRaises(ValueError):
                validate_intent({'buy': rows}, [{'sku': 'tea', 'qty': 2}])

    def test_simultaneous_hooks_emit_one_intent(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(plan_restock, self.md, now=NOW, decide_fn=choose) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(sum(bool(row['buy']) for row in results), 1)
        ledger = read_json(self.md / 'restock_commitments.json')
        self.assertEqual(len(ledger['intents']), 1)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=choose), {'buy': []})

    def test_stale_snapshot_blocks_model_and_intent(self):
        def fail(*args, **kwargs):
            self.fail('Stale inventory must not invoke a model')
        self.assertEqual(plan_restock(self.md, now=NOW + 46, decide_fn=fail), {'buy': []})
        self.assertEqual(len(Memory(self.md).events(kind='restock_error')), 1)

    def test_fresh_snapshot_cooldown_and_commitment_ttl(self):
        self.write_inventory(inventory(balance=1500))
        first = plan_restock(self.md, now=NOW, decide_fn=choose)
        self.assertTrue(first['buy'])
        self.write_inventory(inventory(), now=NOW + 100)
        self.assertEqual(plan_restock(self.md, now=NOW + 100, decide_fn=choose), {'buy': []})
        self.write_inventory(inventory(), now=NOW + 901)
        self.assertEqual(plan_restock(self.md, now=NOW + 901, decide_fn=choose), {'buy': []})
        self.write_inventory(inventory(), now=NOW + 1801)
        self.assertTrue(plan_restock(self.md, now=NOW + 1801, decide_fn=choose)['buy'])

    def test_model_failure_or_invalid_result_does_not_fabricate_purchase(self):
        def fail(*args, **kwargs):
            raise RuntimeError('model unavailable')
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=fail), {'buy': []})
        self.assertEqual(len(Memory(self.md).events(kind='restock_error')), 1)

    def test_lower_balance_during_model_can_reduce_intent_to_empty(self):
        def changing(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                self.write_inventory(inventory(balance=10))
            return choose(system, state, validator, purpose, deadline, **kwargs)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=changing), {'buy': []})
        self.assertEqual(read_json(self.md / 'restock_commitments.json')['intents'], [])

    def test_inventory_timestamp_refresh_during_model_preserves_intent(self):
        def refreshing(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                os.utime(self.md / 'inventory.json', (NOW + 10, NOW + 10))
            return choose(system, state, validator, purpose, deadline, **kwargs)
        result = plan_restock(self.md, now=NOW, decide_fn=refreshing)
        self.assertTrue(result['buy'])
        self.assertEqual(plan_restock(self.md, now=NOW + 10, decide_fn=choose), {'buy': []})

    def test_arriving_stock_reduces_only_affected_sku_and_uses_fresh_baseline(self):
        self.write_inventory(inventory(balance=1500))
        current = inventory(units=[unit(name='new-unit')])
        def changing(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                self.write_inventory(current)
            return choose(system, state, validator, purpose, deadline, **kwargs)
        result = plan_restock(self.md, now=NOW, decide_fn=changing)
        self.assertEqual({row['sku']: row['qty'] for row in result['buy']}, {'cup': 1, 'pen': 1})
        ledger = read_json(self.md / 'restock_commitments.json')
        self.assertEqual(ledger['intents'][0]['baseline_units'], ['new-unit'])
        self.assertEqual(ledger['last_snapshot_id'], _snapshot_id(self.md, current))
        self.assertEqual(ledger['intents'][0]['snapshot_id'], ledger['last_snapshot_id'])

    def test_normal_sale_during_model_preserves_approved_restock(self):
        self.write_inventory(inventory(units=[unit(name='sold-unit')]))
        approved = []
        current = inventory(balance=1040)
        def selling(system, state, validator, purpose, deadline, **kwargs):
            result = choose(system, state, validator, purpose, deadline, **kwargs)
            if purpose == 'r3-restock':
                approved.extend(result['buy'])
                self.write_inventory(current)
            return result
        result = plan_restock(self.md, now=NOW, decide_fn=selling)
        self.assertTrue(result['buy'])
        self.assertEqual(result['buy'], approved)
        self.assertNotIn('tea', {row['sku'] for row in result['buy']})
        ledger = read_json(self.md / 'restock_commitments.json')
        self.assertEqual(ledger['intents'][0]['baseline_units'], [])
        self.assertEqual(ledger['last_snapshot_id'], _snapshot_id(self.md, current))

    def test_smaller_cash_budget_partially_reduces_approved_purchases(self):
        self.write_inventory(inventory(balance=1500))
        def spending(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                self.assertEqual(len(state['proposals']), 3)
                self.write_inventory(inventory(balance=900))
            return choose(system, state, validator, purpose, deadline, **kwargs)
        result = plan_restock(self.md, now=NOW, decide_fn=spending)
        self.assertEqual(sum(row['qty'] for row in result['buy']), 2)
        self.assertLessEqual(sum(wholesale_bound(CATALOG[row['sku']]) * row['qty']
                                 for row in result['buy']), 90)

    def test_more_cash_never_increases_model_approved_qty_or_adds_skus(self):
        # Three proven units still fit after reserving 20% for pilots.
        self.write_inventory(inventory(balance=2000))
        with Memory(self.md) as memory:
            for i in range(4):
                memory.record('market_settle', str(i), {'seq': i, 'item': 'tea'}, ts=NOW - 60)
            for i in range(2):
                memory.record('seller_settlement', str(i), {'sku': 'tea', 'price': 36, 'list_price': 40,
                    'holding_hours': 1, 'margin_after_storage': 6}, ts=NOW - 60)
        def limited(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                self.assertEqual(next(row['qty'] for row in state['proposals']
                                      if row['sku'] == 'tea'), 3)
                self.write_inventory(inventory(balance=10000))
                return validator({'buy': [{'sku': 'tea', 'qty': 1}]})
            return choose(system, state, validator, purpose, deadline, **kwargs)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=limited),
                         {'buy': [{'sku': 'tea', 'qty': 1}]})

    def test_quantity_limits_do_not_reserve_budget_for_unapproved_quantities(self):
        events = [event('market_settle', str(i), {'seq': i, 'item': 'tea' if i < 4 else 'cup'})
                  for i in range(8)]
        result = bounded_proposals(CATALOG, inventory(balance=900), analysis(events), [],
                                   DEFAULT_POLICY, NOW, quantity_limits={'tea': 1, 'cup': 1})
        self.assertEqual({row['sku']: row['qty'] for row in result}, {'tea': 1, 'cup': 1})

    def test_new_pending_commitments_are_reloaded_before_approval(self):
        def reserving(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                atomic_json(self.md / 'restock_commitments.json', {'intents': [{
                    'expires_at': NOW + 1800, 'baseline_units': [],
                    'buy': [{'sku': 'tea', 'qty': 3}], 'fulfilled_units': [],
                    'fulfilled_by_sku': {}}]})
            return choose(system, state, validator, purpose, deadline, **kwargs)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=reserving), {'buy': []})
        ledger = read_json(self.md / 'restock_commitments.json')
        self.assertEqual(len(ledger['intents']), 1)
        self.assertEqual(ledger['intents'][0]['remaining'], [{'sku': 'tea', 'qty': 3}])

    def test_fulfilled_commitments_are_reconciled_against_fresh_inventory(self):
        atomic_json(self.md / 'restock_commitments.json', {'intents': [{
            'expires_at': NOW + 1800, 'baseline_units': [],
            'buy': [{'sku': 'tea', 'qty': 1}], 'fulfilled_units': [], 'fulfilled_by_sku': {}}]})
        def fulfilling(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                self.write_inventory(inventory(balance=970, units=[unit(name='delivered')]))
            return choose(system, state, validator, purpose, deadline, **kwargs)
        result = plan_restock(self.md, now=NOW, decide_fn=fulfilling)
        self.assertEqual({row['sku'] for row in result['buy']}, {'cup', 'pen'})
        ledger = read_json(self.md / 'restock_commitments.json')
        self.assertEqual(ledger['intents'][0]['remaining'], [])
        self.assertEqual(ledger['intents'][0]['fulfilled_units'], ['delivered'])
        self.assertEqual(ledger['intents'][1]['baseline_units'], ['delivered'])

    def test_observed_fulfillment_survives_sale_during_deliberation(self):
        atomic_json(self.md / 'restock_commitments.json', {'intents': [{
            'expires_at': NOW + 1800, 'baseline_units': [],
            'buy': [{'sku': 'tea', 'qty': 1}], 'fulfilled_units': [], 'fulfilled_by_sku': {}}]})
        self.write_inventory(inventory(units=[unit(name='delivered-then-sold')]))
        def selling(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                self.write_inventory(inventory(balance=1040))
            return choose(system, state, validator, purpose, deadline, **kwargs)
        self.assertTrue(plan_restock(self.md, now=NOW, decide_fn=selling)['buy'])
        ledger = read_json(self.md / 'restock_commitments.json')
        self.assertEqual(ledger['intents'][0]['remaining'], [])
        self.assertEqual(ledger['intents'][0]['fulfilled_units'], ['delivered-then-sold'])

    def test_fresh_cooldown_prevents_second_intent(self):
        def reserving(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                atomic_json(self.md / 'restock_commitments.json',
                            {'intents': [], 'last_issued_at': NOW})
            return choose(system, state, validator, purpose, deadline, **kwargs)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=reserving), {'buy': []})
        self.assertEqual(read_json(self.md / 'restock_commitments.json')['intents'], [])

    def test_updated_policy_is_applied_without_another_model_call(self):
        for policy, count in (({'reserve_cash_fraction': .99}, 0),
                              ({'purchase_budget_fraction': .02}, 0),
                              ({'max_category_fraction': .01}, 0),
                              ({'max_total_units': 1}, 1),
                              ({'min_turnover_hours': 34 * 24}, 0)):
            with self.subTest(policy=policy):
                atomic_json(self.md / 'policy.json', {})
                (self.md / 'restock_commitments.json').unlink(missing_ok=True)
                calls = []
                def reconfiguring(system, state, validator, purpose, deadline, **kwargs):
                    calls.append(purpose)
                    if purpose == 'r3-restock':
                        atomic_json(self.md / 'policy.json', policy)
                    return choose(system, state, validator, purpose, deadline, **kwargs)
                result = plan_restock(self.md, now=NOW, decide_fn=reconfiguring)
                self.assertEqual(sum(row['qty'] for row in result['buy']), count)
                self.assertEqual(calls.count('r3-restock'), 1)

    def test_food_lifetime_is_rechecked_from_current_catalog(self):
        food = {'food': {'name': 'Fresh', 'category': 'fresh', 'kind': 'food',
                         'list_price': 40, 'shelf_hours': 72}}
        atomic_json(self.md / 'catalog.json', food)
        def changing(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                atomic_json(self.md / 'catalog.json',
                            {'food': dict(food['food'], shelf_hours=2)})
            return choose(system, state, validator, purpose, deadline, **kwargs)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=changing), {'buy': []})

    def test_deliberation_crossing_trading_horizon_cancels_purchase(self):
        atomic_json(self.md / 'policy.json', {'trading_deadline': NOW + 27 * 3600 + 10})
        clock = [100.]
        def delayed(system, state, validator, purpose, deadline, **kwargs):
            if purpose == 'r3-restock':
                clock[0] += 20
                self.write_inventory(inventory(), now=NOW + 20)
            return choose(system, state, validator, purpose, deadline, **kwargs)
        with patch('r3_restock.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=delayed), {'buy': []})

    def test_inventory_must_still_be_fresh_and_round_three_after_model(self):
        for invalid in ('stale', 'round', 'malformed'):
            with self.subTest(invalid=invalid):
                self.write_inventory(inventory())
                clock = [100.]
                def invalidating(system, state, validator, purpose, deadline, **kwargs):
                    if purpose == 'r3-restock':
                        if invalid == 'stale':
                            clock[0] += 46
                        elif invalid == 'round':
                            self.write_inventory(dict(inventory(), round=2))
                        else:
                            self.write_inventory([])
                    return choose(system, state, validator, purpose, deadline, **kwargs)
                with patch('r3_restock.time.monotonic', side_effect=lambda: clock[0]):
                    self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=invalidating), {'buy': []})
                self.assertFalse((self.md / 'restock_commitments.json').exists())

    def test_cli_is_inert_without_explicit_live_enable(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'R3_ENABLE_LIVE': '0'}), redirect_stdout(output), \
                patch('r3_restock.plan_restock') as planner:
            self.assertEqual(main(['--state', str(self.md)]), 0)
        planner.assert_not_called()
        self.assertEqual(output.getvalue().strip(), '{"buy": []}')


if __name__ == '__main__':
    unittest.main()
