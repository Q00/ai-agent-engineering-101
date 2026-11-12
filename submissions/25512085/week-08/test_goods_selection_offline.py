"""Goods quote selection regression; isolated temporary keys/state, no network."""
import itertools
import unittest
from unittest.mock import patch

import seller
import test_seller_food_offline as food_fixture


class GoodsSelectionTests(unittest.TestCase):
    setUp = food_fixture.FoodSafetyTests.setUp
    turn = food_fixture.FoodSafetyTests.turn
    receipt = food_fixture.FoodSafetyTests.receipt

    def goods(self, uid, cost, hours=1, grade='A', sku='G00000001', price=2500000):
        self.catalog[sku] = {'category': 'goods>sofa', 'kind': 'goods', 'list_price': price}
        return dict(unit=uid, sku=sku, kind='goods', name='sofa', category='goods>sofa',
                    grade=grade, cost=cost, list_price=price,
                    supplied_at=self.now-hours*3600, cert={'proof': 'offline'})

    def prepare_gap(self):
        self.inventory['units'] = [self.goods('old-expensive', 2030000, 2),
                                   self.goods('new-cheap', 1850000)]

    def test_expensive_fifo_regression_execute(self):
        self.prepare_gap()
        self.turn('G00000001')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'new-cheap')

    def test_within_band_oldest_wins_execute(self):
        self.inventory['units'] = [self.goods('old', 1851000, 2, price=2000000),
                                   self.goods('cheap', 1850000, price=2000000)]
        old, cheap = [seller.compute_unit_pricing_and_terms(u, **self.context()) for u in self.inventory['units']]
        self.assertGreater(old['open_ask'], cheap['open_ask'])
        self.assertLessEqual(old['open_ask']-cheap['open_ask'], 18500)
        self.turn('G00000001')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'old')

    def context(self, hub=None):
        return dict(hub={} if hub is None else hub, now=self.now, catalog=self.catalog, clearance=1.0)

    def select(self, units, text='G00000001', **kwargs):
        return seller.select_best_unit(units, text, pricing_context=self.context(**kwargs))

    def test_disabled_retains_fifo_execute(self):
        self.prepare_gap()
        policy = dict(seller.cash_policy.config(), cohort_selection_enabled=False)
        with patch.object(seller.cash_policy, 'config', return_value=policy):
            self.turn('G00000001')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'old-expensive')

    def test_raw_negative_timestamp_bc_floor_preserved(self):
        for grade in ('B', 'C'):
            unit = dict(self.goods('raw', 100000, grade=grade), supplied_at=-1)
            expected = seller.cash_policy.profit_floor(unit, {}, self.catalog, self.now)
            self.assertGreater(expected, 20000000)
            for enabled in (False, True):
                with self.subTest(grade=grade, enabled=enabled):
                    policy = dict(seller.cash_policy.config(), cohort_selection_enabled=enabled)
                    with patch.object(seller.cash_policy, 'config', return_value=policy):
                        quote = seller.compute_unit_pricing_and_terms(unit, **self.context())
                    self.assertEqual(quote['hard_floor'], expected)

    def test_missing_timestamp_legacy_fifo_and_category_sku_preserved(self):
        missing = self.goods('missing-sku1', 2030000)
        missing.pop('supplied_at')
        other = self.goods('sku2', 100, 2, sku='G00000002')
        same = self.goods('same-sku1', 1850000)
        units = [other, missing, same]
        self.assertEqual(seller.select_best_unit(units, '')['unit'], 'missing-sku1')
        policy = dict(seller.cash_policy.config(), cohort_selection_enabled=False)
        with patch.object(seller.cash_policy, 'config', return_value=policy):
            self.assertEqual(self.select(units)['unit'], 'missing-sku1')
        self.inventory['units'] = units
        self.turn('sofa')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'same-sku1')

    def test_large_finite_integer_config_band_falls_back(self):
        self.prepare_gap()
        base = seller.cash_policy.config()
        for name in ('holding_cost_rate_per_day', 'holding_tie_horizon_hours', 'holding_tie_max_fraction'):
            with self.subTest(name=name):
                with patch.object(seller.cash_policy, 'config', return_value=dict(base, **{name: 10**308})):
                    self.assertEqual(self.select(self.inventory['units'])['unit'], 'old-expensive')

    def test_direct_legacy_callables_keep_fifo_without_context(self):
        self.prepare_gap()
        self.assertEqual(seller.select_best_unit(self.inventory['units'], '')['unit'], 'old-expensive')
        unit, _ = seller.find_stock_unit('G00000001', set(), self.inventory, self.catalog)
        self.assertEqual(unit['unit'], 'old-expensive')

    def test_real_eight_hour_pricing_branch(self):
        # 8h markdown is cheaper despite a higher cost; cost-only selection is wrong.
        old = self.goods('eight-hours', 1900000, 8)
        fresh = self.goods('fresh', 1850000, 1)
        qold = seller.compute_unit_pricing_and_terms(old, **self.context())
        qnew = seller.compute_unit_pricing_and_terms(fresh, **self.context())
        self.assertLess(qold['open_ask'], qnew['open_ask'])
        self.inventory['units'] = [fresh, old]
        self.turn('G00000001')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'eight-hours')
        self.assertEqual(self.agent.open['task-1']['ask'], qold['open_ask'])

    def test_grade_preferences_and_warranty_cohort_execute(self):
        units = [self.goods('a', 2030000, grade='A'), self.goods('b', 1000000, grade='B'),
                 self.goods('c', 500000, grade='C')]
        self.inventory['units'] = units
        for text, expected, warranty in [('G00000001', 'a', 72),
                                          ('G00000001 cheap', 'c', 0)]:
            with self.subTest(text=text):
                self.agent = food_fixture.fresh_seller(self.tmp)
                self.turn(text)
                deal = self.agent.open['task-1']
                self.assertEqual(deal['unit']['unit'], expected)
                result = self.receipt(self.turn(str(deal['ask'])))
                self.assertEqual(result['warranty_hours'], warranty)
        self.inventory['units'] = units[1:]
        self.agent = food_fixture.fresh_seller(self.tmp)
        self.turn('G00000001')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'b')

    def test_deterministic_permutations_inside_cohort(self):
        units = [self.goods('z', 1850000, 2), self.goods('a', 1850000, 2),
                 self.goods('new', 1850000, 1)]
        for ordering in itertools.permutations(units):
            self.assertEqual(self.select(list(ordering))['unit'], 'a')

    def test_cheapest_tie_break_floor_cost_is_deterministic(self):
        # Equal ask but different floors/costs: the lower floor is the baseline.
        units = [self.goods('expensive', 1851000, 2), self.goods('cheap', 1850000, 2),
                 self.goods('new', 1850000, 1)]
        for ordering in itertools.permutations(units):
            self.assertEqual(self.select(list(ordering))['unit'], 'cheap')

    def test_category_preserves_original_sku_choice(self):
        units = [self.goods('sku1-old', 2030000, 3),
                 self.goods('sku2', 100, 2, sku='G00000002'),
                 self.goods('sku1-cheap', 1850000, 1)]
        self.inventory['units'] = units
        self.turn('sofa')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'sku1-cheap')

    def test_exact_sku_never_substitutes(self):
        self.inventory['units'] = [self.goods('other', 100, sku='G00000002')]
        events = self.turn('G00000001')
        self.assertIn('reject', [kind for kind, _ in events])
        self.assertFalse(self.agent.open)

    def test_reserved_committed_and_taken_excluded(self):
        units = [self.goods('reserved', 100), self.goods('committed', 200), self.goods('free', 1850000)]
        self.inventory['units'] = units
        self.agent.open['other'] = dict(unit=units[0], created_at=self.now)
        self.agent.committed_units['committed'] = self.now
        self.turn('G00000001')
        self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'free')
        chosen, _ = seller.find_stock_unit('G00000001', {'reserved', 'committed'},
                                          self.inventory, self.catalog, pricing_context=self.context())
        self.assertEqual(chosen['unit'], 'free')

    def test_food_selection_unchanged(self):
        food = food_fixture.FoodSafetyTests.food
        units = [food(self, 400, 'first'), food(self, 500, 'later')]
        self.assertEqual(self.select(units, 'use within 360h')['unit'], 'first')
        self.assertEqual(seller.select_best_unit(units, 'use within 360h'), self.select(units, 'use within 360h'))

    def test_bad_timestamps_never_create_unbounded_age_credit(self):
        cheap = self.goods('cheap', 1850000)
        for ts in (None, 0, float('nan'), float('inf'), -1, self.now+360000):
            with self.subTest(ts=ts):
                old = dict(self.goods('bad', 2030000), supplied_at=ts)
                self.assertEqual(self.select([old, cheap])['unit'], 'cheap')
                price = seller.compute_unit_pricing_and_terms(old, **self.context())
                self.assertGreaterEqual(price['open_ask'], price['hard_floor'])
        ancient = dict(self.goods('ancient', 10000000), supplied_at=1)
        self.assertEqual(self.select([ancient, cheap])['unit'], 'cheap')

    def test_timestamp_sanitizing_only_in_cohort_tie_key(self):
        valid = self.goods('valid-old', 1850000, 2)
        for ts in (None, '', 'invalid', float('nan'), float('inf'), -1, self.now+360000):
            with self.subTest(ts=ts):
                bad = dict(self.goods('bad', 1850000), supplied_at=ts)
                self.assertEqual(seller._goods_timestamp(bad, self.now), self.now)
                # Legacy FIFO cannot compare a string timestamp with a number;
                # that pre-existing malformed-input limitation is not repaired.
                if not isinstance(ts, str):
                    self.assertEqual(self.select([valid, bad])['unit'], 'valid-old')

    def test_extra_malformed_price_candidate_falls_back_execute(self):
        valid = self.goods('valid', 1850000, 2)
        for bad, error in ((self.goods('bad-cost', 'invalid', 1), ValueError),
                           (dict(self.goods('bad-ts', 1850000), supplied_at=10**1000), OverflowError)):
            with self.subTest(unit=bad['unit']):
                self.inventory['units'] = [valid, bad]
                self.agent = food_fixture.fresh_seller(self.tmp)
                self.turn('G00000001')
                self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'valid')
                # No global pricing normalization: directly malformed prices still fail.
                with self.assertRaises(error):
                    seller.compute_unit_pricing_and_terms(bad, **self.context())

    def test_band_boundary_inclusive_real_pricing(self):
        cheap = self.goods('cheap', 100000, price=1)
        for cost, expected in ((100807, 'old'), (100808, 'cheap')):
            old = self.goods('old', cost, 2, price=1)
            with self.subTest(cost=cost):
                q = seller.compute_unit_pricing_and_terms(old, **self.context())
                self.assertEqual(q['open_ask'], 125000 if cost == 100807 else 125001)
                self.assertEqual(self.select([old, cheap])['unit'], expected)

    def test_band_two_percent_cap(self):
        units = [self.goods('old', 104100, 2, price=1), self.goods('cheap', 100000, price=1)]
        policy = dict(seller.cash_policy.config(), holding_cost_rate_per_day=1)
        with patch.object(seller.cash_policy, 'config', return_value=policy):
            self.assertEqual(self.select(units)['unit'], 'cheap')

    def test_invalid_config_fails_closed_to_fifo(self):
        self.prepare_gap()
        base = seller.cash_policy.config()
        for name in ('holding_cost_rate_per_day', 'holding_tie_horizon_hours', 'holding_tie_max_fraction'):
            for value in (None, '0.01', True, -1, float('nan'), float('inf'), 1e308, 10**1000):
                with self.subTest(name=name, value=value):
                    policy = dict(base, **{name: value})
                    with patch.object(seller.cash_policy, 'config', return_value=policy):
                        self.assertEqual(self.select(self.inventory['units'])['unit'], 'old-expensive')
        for value in ('true', 1, None):
            with patch.object(seller.cash_policy, 'config', return_value=dict(base, cohort_selection_enabled=value)):
                self.assertEqual(self.select(self.inventory['units'])['unit'], 'old-expensive')

    def test_missing_config_defaults_and_zero_band(self):
        self.prepare_gap()
        base = seller.cash_policy.config()
        for name in ('cohort_selection_enabled', 'holding_cost_rate_per_day',
                     'holding_tie_horizon_hours', 'holding_tie_max_fraction'):
            base.pop(name, None)
        with patch.object(seller.cash_policy, 'config', return_value=base):
            self.assertEqual(self.select(self.inventory['units'])['unit'], 'new-cheap')
        units = [self.goods('old', 100001, 2, price=1), self.goods('cheap', 100000, price=1)]
        with patch.object(seller.cash_policy, 'config', return_value=dict(base, holding_tie_horizon_hours=0)):
            self.assertEqual(self.select(units)['unit'], 'cheap')

    def test_selection_preserves_inventory_and_fixed_legacy_a_prices(self):
        units = [self.goods('a', 2030000, 2), self.goods('b', 1850000)]
        original = [dict(u) for u in units]
        self.select(units)
        self.assertEqual(units, original)
        for u in units:
            q = seller.compute_unit_pricing_and_terms(u, **self.context())
            self.assertEqual(q['open_ask'], max(int(u['cost']*1.24), 2375000))
            self.assertEqual(q['hard_floor'], max(u['cost']+50, int(u['cost']*1.08), int(u['cost']/.97*1.05)))

    def comparable_hub(self, grade):
        warranty = {'A': 72, 'B': 6, 'C': 0}[grade]
        return {'demand_radar': {'sku_comparables': {'G00000001': [
            dict(grade=grade, amount=50000, ts=self.now-i, warranty_hours=warranty)
            for i in range(5)]}}}

    def test_actual_comparable_cash_quotes_multi_candidate_execute(self):
        for grade in ('A', 'B', 'C'):
            hub = self.comparable_hub(grade)
            for old_cost, expected, within in ((140000, 'cheap', False), (100500, 'old', True)):
                with self.subTest(grade=grade, within=within):
                    old = self.goods('old', old_cost, 2, grade=grade)
                    cheap = self.goods('cheap', 100000, 1, grade=grade)
                    self.inventory['units'] = [old, cheap]
                    quotes = [seller.compute_unit_pricing_and_terms(u, **self.context(hub)) for u in (old, cheap)]
                    for unit, q in zip((old, cheap), quotes):
                        self.assertTrue(q['cash_first_quote'])
                        expected_floor = seller.cash_policy.profit_floor(unit, hub, self.catalog, self.now)
                        self.assertEqual((q['hard_floor'], q['open_ask']), (expected_floor, expected_floor))
                    gap = quotes[0]['open_ask']-quotes[1]['open_ask']
                    self.assertGreater(gap, 0)
                    # baseline cost=100000 => one-day band=1000 (2% cap is larger).
                    self.assertLessEqual(gap, 1000) if within else self.assertGreater(gap, 1000)
                    self.agent = food_fixture.fresh_seller(self.tmp)
                    with patch.object(seller, 'load_hub', return_value=hub):
                        events = self.turn('G00000001')
                    deal = self.agent.open['task-1']
                    self.assertEqual(deal['unit']['unit'], expected)
                    chosen_q = quotes[0 if expected == 'old' else 1]
                    self.assertEqual(deal['ask'], chosen_q['open_ask'])
                    self.assertIn(str(deal['ask']), str(events))
                    result = self.receipt(self.turn(str(deal['ask'])))
                    self.assertEqual((result['unit'], result['price'], result['warranty_hours']),
                                     (expected, chosen_q['open_ask'], {'A': 72, 'B': 6, 'C': 0}[grade]))

    def test_abc_eight_hour_branch_protected_floors_execute(self):
        for grade, expected_ask in (('A', 2200000), ('B', 2150000), ('C', 1600000)):
            with self.subTest(grade=grade):
                hub = self.comparable_hub(grade)
                old = self.goods('eight-hours', 100000, 8, grade=grade)
                fresh = self.goods('fresh', 2500000, 1, grade=grade)
                q = seller.compute_unit_pricing_and_terms(old, **self.context(hub))
                self.assertFalse(q['cash_first_quote'])
                self.assertEqual(q['open_ask'], expected_ask)
                # Intentional Nov 6 policy correction: age no longer resets A's
                # normal floor to 1.02*cost. Selection/holding band is unchanged.
                floor = seller.cash_policy.profit_floor(old, hub, self.catalog, self.now)
                if grade != 'C':
                    floor = max(floor, 100050, int(100000*1.08),
                                int(100000/(1-seller.BREAK_RATE[grade])*1.05))
                self.assertEqual(q['hard_floor'], floor)
                self.inventory['units'] = [fresh, old]
                self.agent = food_fixture.fresh_seller(self.tmp)
                with patch.object(seller, 'load_hub', return_value=hub):
                    self.turn('G00000001')
                self.assertEqual(self.agent.open['task-1']['unit']['unit'], 'eight-hours')
                self.assertEqual(self.agent.open['task-1']['ask'], expected_ask)

    def test_snapshot_matches_selection_display_receipt(self):
        self.prepare_gap()
        calls = []
        compute = seller.compute_unit_pricing_and_terms
        def traced(unit, *args, **kwargs):
            result = compute(unit, *args, **kwargs)
            calls.append((unit['unit'], kwargs, result))
            return result
        with patch.object(seller, 'compute_unit_pricing_and_terms', side_effect=traced), \
             patch.object(seller, 'load_hub', return_value={}) as hub, \
             patch.object(seller, 'load_catalog', return_value=self.catalog) as catalog, \
             patch.object(seller, 'clearance_factor', return_value=1.0) as clearance:
            events = self.turn('G00000001')
        self.assertEqual((hub.call_count, catalog.call_count, clearance.call_count), (1, 1, 1))
        self.assertTrue(all(k['now'] == self.now and k['catalog'] is self.catalog and k['clearance'] == 1 for _, k, _ in calls))
        deal = self.agent.open['task-1']
        quote = next(q for uid, _, q in calls if uid == deal['unit']['unit'])
        self.assertEqual(deal['ask'], quote['open_ask'])
        self.assertIn(str(quote['open_ask']), str(events))
        result = self.receipt(self.turn(str(deal['ask'])))
        self.assertEqual((result['unit'], result['price']), (deal['unit']['unit'], quote['open_ask']))

    def test_snapshot_at_eight_hour_boundary(self):
        unit = self.goods('boundary', 1850000, 8)
        early = seller.compute_unit_pricing_and_terms(unit, **dict(self.context(), now=self.now-1))
        boundary = seller.compute_unit_pricing_and_terms(unit, **self.context())
        self.assertNotEqual(early['open_ask'], boundary['open_ask'])

    def test_old_age_never_increases_band_or_subtracts_storage_twice(self):
        cheap = self.goods('cheap', 1850000)
        for hours in (24, 240, 2400, 24000):
            old = self.goods('expensive-old', 3000000, hours)
            with self.subTest(hours=hours):
                self.assertEqual(self.select([old, cheap])['unit'], 'cheap')

    def test_final_day_clearance_snapshot_reused(self):
        self.prepare_gap()
        calls = []
        original = seller.compute_unit_pricing_and_terms
        def trace(unit, *args, **kwargs):
            q = original(unit, *args, **kwargs)
            calls.append((unit['unit'], q))
            return q
        with patch.object(seller, 'clearance_factor', side_effect=[.5, .1]) as clearance, \
             patch.object(seller, 'compute_unit_pricing_and_terms', side_effect=trace):
            self.turn('G00000001')
        self.assertEqual(clearance.call_count, 1)
        selected = self.agent.open['task-1']
        expected = next(q for uid, q in calls if uid == selected['unit']['unit'])
        self.assertEqual(selected['ask'], expected['open_ask'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
