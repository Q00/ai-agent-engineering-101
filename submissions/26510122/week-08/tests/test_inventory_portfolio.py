from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import inventory_portfolio as portfolio
import replenishment_plan as plans
import restock


def catalog():
    return {f's{i}': {'kind': 'food' if i % 2 else 'goods', 'name': f'Product {i}',
                     'category': f'category{i}', 'list_price': 10000, 'shelf_hours': 96 if i % 2 else None}
            for i in range(30)}


def inventory(variety=5):
    return {'round': 3, 'balance': 5_000_000,
            'restock': {'open': True, 'opens_at': 900, 'closes_at': 1200},
            'units': [{'unit': f'u{i}', 'sku': f's{i}', 'category': f'category{i}',
                       'grade': 'A', 'cost': 6500} for i in range(variety)]}


def reviewed_model(model):
    """Accounting fixtures supply explicit synthetic allocation reasoning.

    Dedicated strategy tests exercise absent/incomplete reviews separately.
    """
    def call(role,prompt,schema):
        answer=model(role,prompt,schema)
        if isinstance(answer,dict) and answer.get('action')=='buy':
            answer={**answer,'strategy_review':{
                'compared_ids':[a['id'] for a in prompt.get('basket_comparison',{}).get('alternatives',[])][:2],
                'chosen_approach':'custom','tradeoff':'Synthetic fixed basket isolates the accounting behavior under test.',
                'exceptions':[{'sku':w['sku'],'reason':'Synthetic fixture retains its specified target to isolate the existing accounting check.'}
                              for w in prompt.get('replenishment_candidates',[])]}}
        return answer
    return call


def approve(role, prompt, schema):
    anchor = prompt['exploration_anchor_sku']
    skus = [anchor] + [r['sku'] for r in prompt['candidates'] if r['sku'] != anchor][:prompt['maximum_new_skus']-1]
    return {'action': 'buy', 'skus': skus, 'reason': 'Small diverse experiment'}


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temp.name)
        self.path = Path('state/test-portfolio.json')

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def run_portfolio(self, inv=None, rng=None, model=approve, now=1000, refresh=None, products=None):
        inv = inv or inventory()
        return portfolio.run(inv, products or catalog(), [], now, model=model,
                             refresh=refresh or (lambda: (inv, now, True)),
                             rng=rng or random.Random(1), state_path=self.path)

    def test_stockout_trials_rank_eligible_unserved_exact_requests(self):
        now = 50_000
        history = [
            {'event': 'no_stock', 'at': now-10, 'requested_skus': [sku], 'task_id': f'{sku}-{i}'}
            for sku, count in [('s0', 8), ('s5', 7), ('s6', 6), ('s7', 5), ('s8', 4), ('s9', 3), ('s10', 2)]
            for i in range(count)
        ]
        history += [next(row for row in history if row['task_id'] == 's8-0'),
                    {'event': 'no_stock', 'at': now-5, 'requested_skus': ['s8', 's11'], 'task_id': 'ambiguous'},
                    {'event': 'opening', 'at': now-1, 'sku': 's6', 'task_id': 's6-0'},
                    {'event': 'settled', 'at': now-1, 'sku': 's6', 'task_id': 's6-1', 'ledger': 1}]
        rows = portfolio.candidates(catalog(), inventory(), history, now, {'s5'}, random.Random(1))
        trials = [(r['sku'], r['stockout_trial']) for r in rows if r.get('stockout_trial')]
        self.assertEqual(trials, [
            ('s7', {'unserved_exact_tasks_12h': 5, 'quantity': 1}),
            ('s6', {'unserved_exact_tasks_12h': 4, 'quantity': 1}),
            ('s8', {'unserved_exact_tasks_12h': 4, 'quantity': 1}),
        ])

    def test_midprice_deficit_keeps_costly_units_out_of_that_window(self):
        now = 1000
        products = catalog()
        products['s0']['list_price'] = 90_000
        products['s2']['list_price'] = 900_000
        products['s4']['list_price'] = 800_000
        inv = inventory(0)
        history = []
        for sku, paid, cost in [('s0', 70_000, 50_000), ('s2', 700_000, 500_000)]:
            for index in range(2):
                unit = f'{sku}-sold-{index}'
                history += [
                    {'event': 'receipt_signed', 'at': now-100, 'unit': unit, 'cost': cost},
                    {'event': 'settled', 'at': now-80, 'unit': unit, 'sku': sku, 'ledger': unit, 'price': paid},
                ]
        model = Mock(return_value={'action': 'hold', 'reason': 'fixture'})
        portfolio.run(inv, products, history, now, model=model, refresh=lambda: (inv, now, True),
                      rng=random.Random(1), state_path=self.path)
        prompt = model.call_args.args[1]
        self.assertEqual(prompt['spend_guidance']['midprice_window_unit_cost_ceiling_krw'], 300_000)
        self.assertEqual([row['sku'] for row in prompt['replenishment_candidates']], ['s0'])
        self.assertTrue(all(row['cost_ceiling'] <= 300_000 for row in prompt['candidates']))

    def test_inflow_trials_alternate_on_public_volume_and_competition_with_margin(self):
        now = 50_000
        watch = Path('state/seller-meta-market.json')
        watch.parent.mkdir()
        watch.write_text(json.dumps({'at': now-20, 'competition_at': now-10, 'rows': [
            {'sku': 's0', 'public_settles': 12, 'sample_mean_paid': 9_500, 'competing_shops': 12},
            {'sku': 's2', 'public_settles': 6, 'sample_mean_paid': 9_500, 'competing_shops': 2},
            {'sku': 's4', 'public_settles': 40, 'sample_mean_paid': 1_000, 'competing_shops': 1},
        ]}))
        rows = portfolio.candidates(catalog(), inventory(0), [], now, set(), random.Random(1))
        volume = portfolio.inflow_anchor(rows, [])
        self.assertEqual((volume['sku'], volume['inflow_trial']['lane']), ('s0', 'market_volume'))
        rows = portfolio.candidates(catalog(), inventory(0), [], now, {'s0'}, random.Random(1))
        scarcity = portfolio.inflow_anchor(rows, [volume])
        self.assertEqual((scarcity['sku'], scarcity['inflow_trial']['lane']), ('s2', 'limited_competition'))
        self.assertEqual(scarcity['inflow_trial']['quantity'], 1)
        self.assertFalse(any(row['sku']=='s4' and row.get('inflow_evidence') for row in rows))
        self.assertIsNone(portfolio.inflow_anchor(rows, [volume, volume, scarcity, scarcity]))

    def test_market_watch_keeps_volume_beyond_the_displayed_opportunities(self):
        from seller_meta import market_watch_rows
        shown = {'sku': 'displayed', 'public_settles': 1, 'sample_mean_paid': 5_000}
        ledger = [dict(kind='settle', data={'item': 'outside', 'amount': 8_000+i*1_000})
                  for i in range(5)]
        self.assertEqual(market_watch_rows([shown], ledger), [
            shown, {'sku': 'outside', 'public_settles': 5, 'sample_mean_paid': 10_000}])

    def test_epsilon_draw_is_persistent_not_redrawn_per_poll_or_restart(self):
        model = Mock(side_effect=approve)
        first = self.run_portfolio(model=model)  # seed1 => .134 < .30
        self.assertEqual(len(first['buy']), 8)
        self.assertTrue(all(b['qty'] == 1 for b in first['buy']))
        self.assertEqual(self.run_portfolio(model=model, rng=random.Random(2)), {'buy': []})
        self.assertEqual(model.call_count, 1)
        state = json.loads(self.path.read_text())
        self.assertEqual(len(state['windows']), 1)
        self.assertEqual(sum(i['cost_ceiling'] for i in state['intents']), 90000)

    def test_exploitation_draw_delegates_without_exploration_model(self):
        model = Mock(side_effect=AssertionError('must delegate'))
        inv = inventory(portfolio.MIN_VARIETY); inv['balance'] = 2_000_000
        for unit in inv['units']: unit['cost'] = 100000  # stock covers the dynamic working-capital target
        self.assertIsNone(self.run_portfolio(inv, model=model, rng=random.Random(2)))
        self.assertIsNone(self.run_portfolio(inv, model=model, rng=random.Random(1)))
        model.assert_not_called()

    def test_low_variety_forces_coverage_even_with_high_draw(self):
        inv = inventory(3)
        inv['units'] += [dict(inv['units'][0], unit=f'extra{i}') for i in range(9)]
        result = self.run_portfolio(inv, rng=random.Random(2))
        self.assertEqual(len(result['buy']), 8)
        self.assertFalse(any(b['sku'] in {'s0', 's1', 's2'} for b in result['buy']))
        self.assertTrue(next(iter(json.loads(self.path.read_text())['windows'].values()))['coverage_recovery'])

    def test_no_repeat_after_unknown_supply_or_model_failure(self):
        for model in (Mock(side_effect=RuntimeError('unavailable')), Mock(side_effect=approve)):
            self.path.unlink(missing_ok=True)
            self.run_portfolio(model=model)
            self.assertEqual(self.run_portfolio(model=model), {'buy': []})
            self.assertEqual(model.call_count, 1)

    def test_new_window_draws_again_but_excludes_yesterdays_intents(self):
        first = self.run_portfolio()
        inv = inventory()
        inv['restock'].update(opens_at=2000, closes_at=2300)
        second = self.run_portfolio(inv, now=2100)
        self.assertEqual(len(second['buy']), 8)
        self.assertTrue({b['sku'] for b in first['buy']}.isdisjoint(b['sku'] for b in second['buy']))

    def test_freshness_window_cash_and_capacity_rechecked_after_model(self):
        changed = inventory()
        changed['balance'] = plans.MIN_BALANCE+15_000
        self.assertEqual(len(self.run_portfolio(refresh=lambda: (changed, 1000, True))['buy']), 1)
        for refresh in (lambda: (inventory(), 1000, False), lambda: (inventory(), 1201, True)):
            self.path.unlink(missing_ok=True)
            self.assertEqual(self.run_portfolio(refresh=refresh), {'buy': []})
            self.assertEqual(json.loads(self.path.read_text())['intents'], [])

    def test_invalid_baskets_do_not_issue_purchase(self):
        for skus in (['unknown'], ['s8', 's8'], [None], [], ['s8', 's9', 's10', 's11']):
            self.path.unlink(missing_ok=True)
            self.assertEqual(self.run_portfolio(model=lambda *args: {'action': 'buy', 'skus': skus, 'reason': ''}), {'buy': []})
            self.assertEqual(json.loads(self.path.read_text())['intents'], [])

    def test_model_cannot_silently_drop_the_random_exploration_anchor(self):
        def familiar_only(role, prompt, schema):
            other = next(r['sku'] for r in prompt['candidates'] if r['sku'] != prompt['exploration_anchor_sku'])
            return {'action': 'buy', 'skus': [other], 'reason': 'prefer a familiar product'}
        self.assertEqual(self.run_portfolio(model=familiar_only), {'buy': []})
        self.assertEqual(json.loads(self.path.read_text())['intents'], [])

    def test_rolling_reservation_budget_survives_restart_and_does_not_block_greedy(self):
        # Unknown intents retain their spend reservations even after a restart.
        self.path.parent.mkdir()
        intents = [{'sku': f'x{i}', 'cost_ceiling': 500_000, 'at': 999,
                    'prior_units': [], 'status': 'issued_unknown'}
                   for i in range(portfolio.ROLLING_BUDGET//500_000)]
        self.path.write_text(json.dumps({'version': 1, 'windows': {}, 'intents': intents}))
        model = Mock(side_effect=AssertionError('budget exhausted'))
        self.assertIsNone(self.run_portfolio(model=model))
        model.assert_not_called()
        self.assertEqual(len(json.loads(self.path.read_text())['intents']), len(intents))

    def test_expansion_resumes_above_old_rolling_cap_without_resetting_spend(self):
        self.path.parent.mkdir()
        prior = [{'sku':f'old{i}', 'cost_ceiling':500_000, 'qty':1, 'at':999,
                  'prior_units':[], 'status':'settled'} for i in range(12)]
        self.path.write_text(json.dumps({'version':1, 'windows':{}, 'intents':prior}))
        products = {f's{i}':dict(kind='goods',name=f'Furniture {i}',category=f'category{i}',
                               list_price=400_000,shelf_hours=None) for i in range(16)}
        prompts = []
        def model(role, prompt, schema):
            prompts.append(prompt)
            return approve(role,prompt,schema)
        result = self.run_portfolio(inventory(0), model=model, products=products)
        self.assertEqual(prompts[0]['reserved_24h'],6_000_000)
        self.assertEqual(len(result['buy']),8)
        saved = json.loads(self.path.read_text())['intents']
        self.assertEqual(saved[:12],prior)  # no allowance reset or erased reservations
        self.assertEqual(sum(i['cost_ceiling']*i['qty'] for i in saved[12:]),3_600_000)

    def test_larger_approval_still_obeys_fresh_cash_after_model(self):
        products = {f's{i}':dict(kind='goods',name=f'Furniture {i}',category=f'category{i}',
                               list_price=400_000,shelf_hours=None) for i in range(16)}
        current = inventory(0);current['balance'] = plans.MIN_BALANCE+450_000
        result = self.run_portfolio(inventory(0), products=products,
                                    refresh=lambda:(current,1000,True))
        self.assertEqual(len(result['buy']),1)
        saved = json.loads(self.path.read_text())['intents']
        self.assertEqual(sum(i['cost_ceiling']*i['qty'] for i in saved),450_000)

    def test_request_evidence_is_deduplicated_and_goods_survive_candidate_sampling(self):
        at = datetime.fromtimestamp(999, timezone.utc).isoformat()
        history = [{'event': e, 'at': at, 'task_id': 't', 'requested_skus': ['s20']}
                   for e in ('request_seen', 'no_stock')]
        rows = portfolio.candidates(catalog(), inventory(), history, 1000, set(), random.Random(1))
        self.assertEqual(next(r for r in rows if r['sku'] == 's20')['observed_requests_12h'], 1)
        self.assertEqual({r['kind'] for r in rows}, {'food', 'goods'})

    def test_arrivals_are_observed_without_reauthorizing_the_same_window(self):
        first = self.run_portfolio()
        inv = inventory()
        inv['units'] += [{'sku': b['sku'], 'unit': f'arrived{i}', 'grade': 'A'} for i, b in enumerate(first['buy'])]
        self.assertEqual(self.run_portfolio(inv), {'buy': []})
        self.assertTrue(all(i['status'] == 'observed_inventory' for i in json.loads(self.path.read_text())['intents']))

    def test_corrupt_state_and_closed_window_fail_closed(self):
        self.path.parent.mkdir()
        self.path.write_text('{broken')
        model = Mock(side_effect=AssertionError('must not call model'))
        self.assertEqual(self.run_portfolio(model=model), {'buy': []})
        self.path.unlink()
        inv = inventory(); inv['restock']['open'] = False
        self.assertEqual(self.run_portfolio(inv, model=model), {'buy': []})
        self.assertFalse(self.path.exists())

    def test_exploitation_excludes_concentrated_stock_and_can_replenish_goods(self):
        products = catalog()
        inv = inventory(0)
        inv['units'] = [{'sku': 's1', 'grade': 'A', 'category': 'category1'} for _ in range(10)]
        rows = restock.choose_candidates(products, inv, [], Counter({'s1': 100, 's2': 20}))
        self.assertNotIn('s1', [r['sku'] for r in rows])
        self.assertEqual(rows[0]['sku'], 's2')
        self.assertEqual(rows[0]['kind'], 'goods')

    def test_old_large_targets_cannot_replenish_and_category_limit_is_rechecked(self):
        obs = {'observed_visits': 10, 'observed_no_stock_visits': 0, 'observed_visit_rate_while_stocked': None}
        p = plans.create(sku='s1', product=catalog()['s1'], candidate={'qty': 2, 'target_stock': 2},
                         approval={'valid_for_seconds': 600, 'max_total_qty': 4}, observation=obs, now=1000)
        p['target_stock'] = 10
        result, _ = plans.execute(p, inventory=inventory(0), product=catalog()['s1'], observation=obs, now=1000)
        self.assertIsNone(result)
        p['target_stock'] = 2
        inv = inventory(0)
        inv['units'] = [{'sku': 'other', 'grade': 'A', 'category': 'category1'} for _ in range(plans.MAX_CATEGORY_STOCK)]
        result, _ = plans.execute(p, inventory=inv, product=catalog()['s1'], observation=obs, now=1000)
        self.assertEqual(result, {'buy': []})

    def test_real_restock_path_routes_completed_startup_to_recurring_exploration(self):
        state = Path('.market'); state.mkdir()
        now = time.time()
        inv = inventory(3)
        inv['restock'].update(opens_at=now - 20, closes_at=now + 180)
        (state/'inventory.json').write_text(json.dumps(inv))
        (state/'catalog.json').write_text(json.dumps(catalog()))
        Path('state').mkdir()
        Path('state/inventory-exploration.json').write_text(json.dumps({
            'version': 1, 'round': 3, 'completed': True, 'intents': [], 'initial_skus': [],
            'initial_cost': 0, 'reserved': 0, 'basket': []}))
        with patch.object(restock, 'decide_json', side_effect=approve) as model:
            result = restock.run(state)
        self.assertEqual(len(result['buy']), 8)
        self.assertEqual(model.call_args.args[0], 'recurring inventory explorer')

    def test_goods_use_goods_cost_prior_and_storage_in_actual_quantity_approval(self):
        state = Path('.market'); state.mkdir()
        (state/'inventory.json').write_text(json.dumps(inventory(0) | {'restock': None}))
        (state/'catalog.json').write_text(json.dumps({'s0': catalog()['s0']}))
        choice = {'action': 'buy', 'sku': 's0', 'expected_sale_price': 9200,
                  'demand_mean': 1.2, 'continuation_value': 0, 'reason': 'observed goods demand'}
        def approve(role,prompt,schema):
            if role=='restocking agent':return choice
            row=next(r for r in prompt['quantity_candidates'] if r['qty'])
            return {'action':'approve','candidate_id':row['id'],'reason':'bounded Book plan'}
        # This fixture tests24h carrying cost, not the live round's closing clock.
        operating = restock.operating_policy(time.time())
        operating.update(hours_to_end=48, ends_at=time.time()+48*3600)
        with patch.object(restock, 'decide_json', side_effect=approve) as model, \
             patch.object(restock, 'fetch_market_sales', return_value=Counter({'s0': 3})), \
             patch.object(restock, 'operating_policy', return_value=operating):
            result = restock.run(state)
        self.assertEqual(result, {'buy': [{'sku': 's0', 'qty': 2}]})
        calc = next(c['calculation'] for c in model.call_args.args[1]['quantity_candidates'] if c['qty'])
        self.assertEqual(calc['cost'], 5900)
        self.assertEqual(calc['holding_per_leftover'], 59)

        self.assertGreater(calc['salvage'],0)
        self.assertGreater(model.call_args.args[1]['book_continuation']['book_retention_floor'],0)

    def test_larger_basket_can_pass_twenty_units_without_deepening_existing_sku(self):
        inv = inventory(6)
        inv['units'] += [dict(inv['units'][0], unit=f'concentrated{i}') for i in range(11)]
        result = self.run_portfolio(inv)
        self.assertEqual(len(result['buy']), 8)
        self.assertGreater(len(inv['units']) + len(result['buy']), 20)
        self.assertTrue(all(b['qty'] == 1 for b in result['buy']))
        self.assertFalse({u['sku'] for u in inv['units']} & {b['sku'] for b in result['buy']})

    def test_basket_size_and_category_floors_have_an_affordable_witness(self):
        rows = [{'sku': f'x{i}', 'cost_ceiling': cost, 'category': f'c{i}'}
                for i, cost in enumerate((200000, 5000, 5000, 5000, 5000, 250000))]
        self.assertEqual(portfolio.basket_limits(rows, rows[0], 8, 225000), (5, 5, 4))
        self.assertEqual(portfolio.basket_limits(rows, rows[0], 8, 200000), (1, 1, 1))
        # Cardinality is affordable even when multiple expensive categories are not.
        rows = [{'sku': f'x{i}', 'cost_ceiling': cost, 'category': cat}
                for i, (cost, cat) in enumerate(((10, 'a'), (10, 'a'), (10, 'a'), (100, 'b')))]
        self.assertEqual(portfolio.basket_limits(rows, rows[0], 8, 30), (3, 3, 1))

    def test_new_cost_tier_includes_furniture_excluded_by_old_fifty_thousand_limit(self):
        products = catalog()
        products['furniture'] = {'kind': 'goods', 'name': 'Desk', 'category': 'furniture',
                                 'list_price': 240000, 'shelf_hours': None}
        rows = portfolio.candidates(products, inventory(0), [], 1000, set(), random.Random(1))
        desk = next(r for r in rows if r['sku'] == 'furniture')
        self.assertEqual(desk['cost_ceiling'], 270000)
        self.assertEqual(desk['price_band'], 'upper')

    def test_cost_exposure_rechecked_after_approval_in_both_planners(self):
        inv = inventory(0)
        inv['units'] = [{'unit': 'expensive', 'sku': 'old', 'grade': 'A', 'cost': 19_975_000}]
        risk = plans.inventory_risk(inv, 1000)
        self.assertEqual(risk['available_to_invest'], 5000)
        self.assertEqual(self.run_portfolio(refresh=lambda: (inv, 1000, True)), {'buy': []})
        obs = {'observed_visits': 10, 'observed_no_stock_visits': 0, 'observed_visit_rate_while_stocked': None}
        p = plans.create(sku='s1', product=catalog()['s1'], candidate={'qty': 2, 'target_stock': 2},
                         approval={'valid_for_seconds': 600, 'max_total_qty': 4}, observation=obs, now=1000)
        result, _ = plans.execute(p, inventory=inv, product=catalog()['s1'], observation=obs, now=1000)
        self.assertEqual(result, {'buy': []})
        self.assertEqual(plans.inventory_risk(inventory(), 1000, pending_cost=plans.MAX_INVENTORY_COST)['available_to_invest'], 0)

    def test_cached_planner_counts_unresolved_exploration_without_double_counting_arrivals(self):
        path = Path('state/inventory-portfolio.json'); path.parent.mkdir()
        path.write_text(json.dumps({'intents': [{'sku': 'new', 'at': 999, 'cost_ceiling': 250000,
                                               'prior_units': [], 'status': 'issued_unknown'}]}))
        inv = inventory(0)
        self.assertEqual(plans.inventory_risk(inv, 1000)['pending_cost_ceiling'], 375000)
        inv['units'].append({'sku': 'new', 'unit': 'arrived', 'grade': 'A', 'cost': 180000})
        risk = plans.inventory_risk(inv, 1000)
        self.assertEqual(risk['pending_cost_ceiling'], 0)
        self.assertEqual(risk['stock_acquisition_cost'], 180000)
        path.write_text('{invalid')
        self.assertEqual(plans.inventory_risk(inv, 1000)['available_to_invest'], 0)


if __name__ == '__main__':
    unittest.main()
