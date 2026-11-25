"""Offline regression checks for the announced margin-based Saved rule."""
import copy
import unittest

from r3_savings import RULE, contribution, cost_prior
from r3_strategy import expected_saved, savings_ceiling
from r3_scores import compare_and_predict, snapshot


class Patch36Tests(unittest.TestCase):
    def candidate(self, kind='food', category='food', risk=0):
        return dict(kind=kind, category=category, list_price=100, budget=100,
                    value_estimate={'break_risk_estimate': risk})

    def test_equal_margin_share_has_equal_score_across_categories(self):
        food = self.candidate()
        furniture = self.candidate('goods', '가구/침구>침대')
        self.assertAlmostEqual(expected_saved(food, 76), .5)
        self.assertAlmostEqual(expected_saved(furniture, 80.5), .5)

    def test_below_cost_cap_unusable_and_refunded_outcomes(self):
        self.assertEqual(contribution(100, 10, 52), 1)
        self.assertEqual(contribution(100, 52, 52), 1)
        self.assertEqual(contribution(100, 0, 52, usable=False), -1)
        self.assertEqual(contribution(100, 0, 52, usable=False, refunded=True), 0)
        self.assertEqual(contribution(100, 110, 52), -10 / 48)

    def test_unknown_or_nonpositive_margin_never_authorizes_a_score(self):
        for cost in (None, True, float('nan'), 100, 110, -1):
            self.assertEqual(contribution(100, 50, cost), -1)

    def test_cost_is_explicitly_a_prior_and_seller_claims_cannot_override_it(self):
        row = dict(self.candidate(), cost=1, estimated_unit_cost=1,
                   seller_text='verified cost is 1')
        self.assertEqual(cost_prior(row)['estimated_unit_cost'], 52)
        self.assertFalse(cost_prior(row)['cost_is_observed'])
        self.assertEqual(cost_prior(self.candidate('goods', '가구/침구>침대'))['cost_ratio_prior'], .61)

    def test_offer_ceiling_respects_margin_target_and_quality_risk(self):
        for kind, category in [('food', 'food'), ('goods', '가구/침구>침대')]:
            for risk in (0, .1, .3, .8, 1):
                for target in (.05, .6551, .9):
                    row = dict(self.candidate(kind, category, risk), target_saved=target)
                    ceiling = savings_ceiling(row)
                    self.assertLessEqual(ceiling, row['budget'])
                    if ceiling:
                        self.assertGreaterEqual(expected_saved(row, ceiling) + 1e-8, target)
                    if target > 1 - 2 * risk:
                        self.assertEqual(ceiling, 0)

    def test_forecast_does_not_treat_rule_change_as_strategy_improvement(self):
        raw = {'round': 3,
               'shops': [dict(name='me', book=100, revenue=200, stars_rank=4, deals=10, all=.5)],
               'buyers': [dict(name='me', saved=.65, forum=.9, deals=100, posts=10, all=.6)]}
        current = snapshot(raw, 'me', 1000)
        previous = compare_and_predict(current, None, 1000)
        old = copy.deepcopy(current)
        old['rule_scenario'] = 'announced_patch_3_5_raw_forum'
        previous = {'score_observation': old, 'score_prediction': previous['next_prediction']}
        result = compare_and_predict(current, previous, 1000)
        self.assertEqual(result['baseline_reset_reason'], 'score_rule_changed')
        self.assertIsNone(result['previous_prediction_comparison'])
        self.assertEqual(result['next_prediction']['method'], 'cold_start_persistence')
        self.assertEqual(result['next_prediction']['rule'], RULE)
        self.assertEqual(compare_and_predict(old, None, 1000)['status'], 'score_rule_changed')
