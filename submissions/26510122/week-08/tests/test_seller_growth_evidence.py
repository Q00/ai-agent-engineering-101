import unittest

from inventory_observation import expiry_shortfalls
from procurement_audit import select_lines
from seller_forum import prioritized_requests
from seller_requirements import expiry_shortfall, initial
from stock_policy import replenishment_candidates, sales_evidence


class SellerGrowthEvidenceTests(unittest.TestCase):
    now = 100_000
    food = {'name': 'Tea', 'kind': 'food', 'category': 'drinks',
            'list_price': 100_000, 'shelf_hours': 100}

    def unit(self, hours, grade='A', key='u'):
        return {'sku': 'tea', 'unit': key, 'kind': 'food', 'category': 'drinks',
                'grade': grade, 'expires_at': self.now + hours * 3600}

    def shortfall(self, task, hours=70):
        return {'event': 'requirements_unmet', 'task_id': task, 'at': self.now - 100,
                'requested_skus': ['tea'],
                'expiry_shortfall': {'sku': 'tea', 'required_hours': hours, 'best_available_hours': 30}}

    def test_shortfall_excludes_alternatives_other_conditions_and_usable_stock(self):
        req = initial('Tea grade A, must last at least 70 hours')
        choices = [self.unit(35, 'B'), self.unit(40)]
        self.assertEqual(expiry_shortfall(choices, ['tea'], req, self.now),
                         {'sku': 'tea', 'required_hours': 70, 'best_available_hours': 40})
        self.assertIsNone(expiry_shortfall(choices, ['tea', 'coffee'], req, self.now))
        self.assertIsNone(expiry_shortfall(choices, ['tea'], req | {'grades': ['C']}, self.now))
        self.assertIsNone(expiry_shortfall(choices + [self.unit(75)], ['tea'], req, self.now))

    def test_deduplicates_and_checks_legacy_and_already_served_requests(self):
        history = [self.shortfall('a', 70), self.shortfall('a', 70),
                   self.shortfall('served', 88), self.shortfall('unattainable', 99),
                   {'event': 'opening', 'at': self.now - 50, 'task_id': 'served', 'sku': 'tea'},
                   {'event': 'request_seen', 'at': self.now - 110, 'task_id': 'legacy', 'requested_skus': ['tea']},
                   {'event': 'requirements_unmet', 'at': self.now - 100, 'task_id': 'legacy',
                    'requirements': {'min_hours_left': 75}}]
        gaps = expiry_shortfalls(history, {'tea': self.food}, [self.unit(40)], self.now)
        self.assertEqual(gaps['tea']['unserved_tasks_12h'], 2)
        self.assertEqual(gaps['tea']['confirmed_expiry_shortfalls'], 1)
        self.assertEqual(gaps['tea']['target_remaining_hours'], 70)
        self.assertEqual(expiry_shortfalls(history, {'tea': self.food}, [self.unit(75)], self.now), {})

    def test_proven_stock_can_get_one_fresh_unit_trial_with_cooldown(self):
        inv = {'round': 3, 'balance': 8_000_000,
               'units': [self.unit(35, key=f'old-{i}') | {'cost': 30_000} for i in range(10)]}
        history = [self.shortfall('a'), self.shortfall('b', 72)]
        for i in range(2):
            history += [{'event': 'receipt_signed', 'sku': 'tea', 'unit': f'sold-{i}', 'cost': 40_000,
                         'at': self.now - 7200},
                        {'event': 'settled', 'sku': 'tea', 'unit': f'sold-{i}', 'price': 60_000,
                         'ledger': i, 'at': self.now - 7200}]
        rows = replenishment_candidates({'tea': self.food}, inv, history, self.now)
        option, = rows[0]['target_options']
        self.assertEqual((option['scenario'], option['qty'], option['target_stock']), ('expiry_match', 1, 11))
        self.assertEqual(option['expiry_trial']['target_remaining_hours'], 70)
        self.assertEqual(replenishment_candidates({'tea': self.food}, inv, history, self.now,
                                                  expiry_trialed={'tea'}), [])
        self.assertEqual(replenishment_candidates({'tea': self.food}, inv, history, self.now,
                                                  pending={'tea': 1}), [])

    def test_satisfied_lifetime_blocks_an_approved_trial_at_execution(self):
        line = {'sku': 'tea', 'kind': 'replenishment', 'category': 'drinks', 'qty': 1,
                'target_stock': 3, 'cost_ceiling': 50_000, 'expiry_trial': {'target_remaining_hours': 70}}
        result, audit = select_lines(
            [line], stocks={'tea': 2}, categories={'drinks': 2}, pending_stock={},
            catalog={'tea': self.food}, unit_room=10, supply_lines=10, budget=100_000,
            allow_replenishment=True, allow_new_skus=True, time_limited=False,
            covered_expiry_trials={'tea'})
        self.assertEqual(result, [])
        self.assertEqual(audit[0]['reasons'], ['expiry_demand_already_covered'])

    def test_midprice_contribution_includes_losses_in_the_same_band(self):
        history = [{'event': 'receipt_signed', 'sku': 'tea', 'unit': 'paid', 'cost': 35_000},
                   {'event': 'settled', 'sku': 'tea', 'unit': 'paid', 'ledger': 1,
                    'at': self.now - 100, 'price': 40_000},
                   {'event': 'receipt_signed', 'sku': 'tea', 'unit': 'loss', 'cost': 65_000},
                   {'event': 'settled', 'sku': 'tea', 'unit': 'loss', 'ledger': 2,
                    'at': self.now - 99, 'price': 40_000}]
        self.assertEqual(sales_evidence('tea', history, self.now, kind='food')['midprice_contribution_12h'],
                         -20_000)

    def test_actual_quote_options_prioritize_unknown_buyers_and_exact_skus(self):
        def req(thread, author, basis):
            return {'id': thread, 'author': author, 'posted_at': self.now,
                    'catalog_matches': [{'sku': 'tea', 'basis': basis}]}
        evidence = {'known_paid_buyers': {'repeat'}, 'requests': [
            req(1, 'repeat', 'exact_sku_or_unique_full_name'),
            req(2, 'new-broad', 'category_alternatives'),
            req(3, 'new-exact', 'exact_sku_or_unique_full_name'),
            req(4, 'without-stock', 'exact_sku_or_unique_full_name')]}
        quotes = {i: [{'sku': 'tea', 'price': 80_000}] for i in (1, 2, 3)}
        self.assertEqual([r['id'] for r in prioritized_requests(evidence, quotes)], [3, 2])
