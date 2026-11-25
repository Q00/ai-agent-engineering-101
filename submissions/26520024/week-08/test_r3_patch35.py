"""Offline fixtures for score-aware strategy; not market performance evidence."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from r3_common import DEFAULT_POLICY, atomic_json, load_policy
from r3_memory import Memory
from r3_buyer import acceptance
from r3_strategy import expected_saved, savings_ceiling, selection_fallback, buyer_objective
from r3_portfolio import diversified, enrich, observe_offers
from r3_restock import bounded_proposals, wholesale_bound
from r3_reputation import dislike_quote, summarize as reputation, vote_result, maintain_reputation
from r3_scores import snapshot, score, compare_and_predict
from r3_seller import pricing
from test_r3_discovery import NOW, CATALOG, build_candidates, event
from test_r3_restock import inventory, analysis


class Patch35Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.memory = Memory(self.md)
        self.addCleanup(self.memory.close)

    def candidate(self, price=100, risk=.1, estimated=60, name='shop'):
        return {'shop_name': name, 'sku': name, 'list_price': price,
                'value_estimate': {'break_risk_estimate': risk, 'estimated_price': estimated}}

    def board(self, at=NOW, **buyer_changes):
        buyer = dict(name='me', saved=.2, forum=.9, deals=100, posts=10, all=.6)
        buyer.update(buyer_changes)
        return snapshot({'round': 3, 'shops': [dict(name='me', book=100, revenue=200,
             stars_rank=4, deals=10, all=.5), dict(name='other', book=200, revenue=400,
             stars_rank=3, deals=20, all=.6)],
             'buyers': [buyer, dict(name='other', saved=.3, forum=.91, deals=200, posts=30, all=.7)]}, 'me', at)

    def test_normalized_savings_beats_larger_absolute_discount(self):
        cheap = self.candidate(100, 0, 60, 'cheap')
        costly = self.candidate(10000, 0, 8000, 'expensive')
        self.assertEqual(selection_fallback([costly, cheap], 10000)['shop_name'], 'cheap')

    def test_failed_purchase_penalty_is_minus_one_not_minus_price_ratio(self):
        self.assertAlmostEqual(expected_saved(self.candidate(), 60), .9 * (40 / 41) - .1)
        self.assertEqual(expected_saved(self.candidate(risk=1), 0), -1)

    def test_ceiling_meets_target_over_risk_price_budget_grid(self):
        for risk in (0, .1, .3, .8, 1):
            for listed in (40, 39900):
                for target in (.05, .2, .4):
                    row = dict(self.candidate(listed, risk), budget=100000, target_saved=target)
                    ceiling = savings_ceiling(row)
                    self.assertLessEqual(ceiling, row['budget'])
                    if ceiling > 0:
                        self.assertGreaterEqual(expected_saved(row, ceiling) + 1e-8, target)

    def test_missing_list_price_cannot_authorize_offer(self):
        self.assertEqual(savings_ceiling(dict(budget=100)), 0)

    def test_warranty_requirements_do_not_change_seller_promises(self):
        self.assertEqual(acceptance({'kind': 'goods'}, {'warranty_hours': 6})['warranty_hours'], 0)
        self.assertEqual(acceptance({'kind': 'goods'}, {'buyer_required_warranty_hours': 12})['warranty_hours'], 12)

    def test_fresh_saved_threshold_and_stale_fallback(self):
        self.memory.record('score_snapshot', '1', self.board(), ts=NOW)
        self.assertEqual(buyer_objective(self.memory, DEFAULT_POLICY, NOW)['target_saved'], .2)
        self.assertEqual(buyer_objective(self.memory, DEFAULT_POLICY, NOW+901)['target_saved'], .05)

    def test_invalid_policy_is_rejected(self):
        for data in ({'buyer_min_saved': 1}, {'buyer_min_saved': -1},
                     {'buyer_required_warranty_hours': 73}, {'buyer_min_saved': True}):
            atomic_json(self.md / 'policy.json', data)
            with self.assertRaises(ValueError):
                load_policy(self.md)

    def test_end_date_does_not_fire_sale_goods_even_if_cost_exceeds_list(self):
        unit = dict(kind='goods', cost=120, list_price=100, supplied_at=NOW-86400)
        result = pricing(unit, dict(DEFAULT_POLICY, trading_deadline=NOW+60), NOW)
        self.assertFalse(result['liquidation'])
        self.assertGreaterEqual(result['minimum'], 120)
        self.assertLessEqual(pricing(unit, DEFAULT_POLICY, NOW, previous_ask=90)['ceiling'], 90)

    def test_food_can_liquidate_at_expiry_or_end(self):
        unit = dict(kind='food', cost=100, list_price=200, supplied_at=NOW, expires_at=NOW+86400)
        self.assertTrue(pricing(unit, dict(DEFAULT_POLICY, trading_deadline=NOW+60), NOW)['liquidation'])
        self.assertTrue(pricing(dict(unit, expires_at=NOW+60), DEFAULT_POLICY, NOW)['liquidation'])

    def test_candidate_quota_keeps_pilot_in_short_prefix(self):
        rows = [dict(sku=str(i), score=100-i, list_price=40, demand_status='observed_sale') for i in range(30)]
        rows.append(dict(sku='pilot', score=.01, list_price=40, demand_status='related_unseen'))
        chosen = diversified(rows, 24)
        self.assertEqual(len(chosen), 24)
        self.assertIn('pilot', [r['sku'] for r in chosen[:4]])

    def test_partial_supply_changes_rank_and_stale_supply_is_unknown(self):
        rows = build_candidates(CATALOG, [event('market_settle', '1', {'seq': 1, 'item': 'tea'})], NOW)
        observe_offers(self.memory, {'order': 'o1', 'category': 'drinks'},
                       [{'name': 'competitor', 'offers': [{'sku': 'tea', 'qty': 20}]}], NOW)
        fresh = enrich(copy.deepcopy(rows), self.memory, {'surge': []}, NOW)
        tea = next(r for r in fresh['candidates'] if r['sku']=='tea')
        self.assertEqual(tea['portfolio']['competitor_shops'], 1)
        stale = enrich(copy.deepcopy(rows), self.memory, {'surge': []}, NOW+1801)
        self.assertIsNone(next(r for r in stale['candidates'] if r['sku']=='tea')['portfolio']['competitor_shops'])

    def test_global_sales_do_not_prove_own_profitable_turnover(self):
        rows = build_candidates(CATALOG, [event('market_settle', '1', {'seq': 1, 'item': 'tea'})], NOW)
        fresh = enrich(rows, self.memory, {'surge': []}, NOW)
        self.assertFalse(fresh['candidates'][0]['portfolio']['scale_ready'])
        for i in range(2):
            self.memory.record('seller_settlement', str(i), dict(sku='tea', price=35, list_price=40,
                               holding_hours=2, margin_after_storage=10), ts=NOW)
        self.assertTrue(next(r for r in enrich(rows, self.memory, {'surge': []}, NOW)['candidates']
                             if r['sku']=='tea')['portfolio']['scale_ready'])

    def test_pilots_have_separate_budget_and_one_unit_cap(self):
        a = analysis([event('market_settle', str(i), {'seq': i, 'item': 'tea'}) for i in range(10)])
        for row in a['inputs']['candidates']:
            row['portfolio'] = dict(scale_ready=False, estimated_margin=10)
        rows = bounded_proposals(CATALOG, inventory(balance=10000), a, [], DEFAULT_POLICY, NOW)
        self.assertTrue(rows)
        self.assertTrue(all(r['qty']==1 for r in rows))
        self.assertLessEqual(sum(wholesale_bound(CATALOG[r['sku']])*r['qty'] for r in rows), 200)

    def test_dislike_cost_is_quadratic_increment_plus_new_stance_fee(self):
        q = dislike_quote({'dislike_power_unit': 20000, 'next_dislike_fee': 40000},
                          4, previous_power=2, deals=50)
        self.assertEqual(q['total_cost'], 20000*(9-1)+40000)
        self.assertTrue(q['eligible'])
        self.assertFalse(q['auto_send'])
        self.assertEqual(q['paid_budget'], 0)
        self.assertEqual(dislike_quote({'dislike_power_unit': 20000}, 2, previous_power=4,
                         new_buyer_stance=False)['total_cost'], 0)

    def test_missing_fee_and_small_account_never_authorize_spending(self):
        self.assertIsNone(dislike_quote({}, 4, deals=49)['total_cost'])
        self.assertFalse(dislike_quote({}, 4, deals=49)['eligible'])
        with self.assertRaises(ValueError):
            dislike_quote({}, True)

    def test_author_stance_is_not_number_of_post_ratings(self):
        for i in (1, 2, 3):
            self.memory.record('forum_claim', str(i), {'id': i, 'author': 'same', 'text': ''})
            vote_result(self.memory, {'sent': {'vote': i, 'value': 1, 'as': 'buyer'},
                         'status': 200, 'answer': '{"counted":true}'}, str(i))
        report = reputation(self.memory, {}, NOW)
        self.assertEqual(len(report['authors']), 1)
        self.assertAlmostEqual(report['authors']['same']['weight_estimate'], 1+math.log2(3))
        vote_result(self.memory, {'sent': {'vote': 1, 'value': -4, 'as': 'shop'},
                         'status': 402, 'answer': '{"fee":100000}'}, 'refused')
        self.assertEqual(reputation(self.memory, {}, NOW)['authors']['same']['stance'], 'like')

    def test_sent_log_is_idempotent_and_never_emits_outbox(self):
        row = {'sent': {'vote': 1, 'value': -2, 'as': 'buyer'}, 'status': 200, 'answer': '{truncated'}
        (self.md/'outbox.sent.jsonl').write_text(json.dumps(row)+'\n')
        for _ in range(2):
            maintain_reputation(self.memory, self.md, NOW)
        self.assertEqual(len(self.memory.events('forum_vote_result')), 1)
        self.assertFalse((self.md/'outbox.jsonl').exists())
        self.assertFalse(self.memory.events('forum_vote_result')[0]['data']['answer_complete'])

    def test_score_uses_raw_forum_and_excludes_private_fields(self):
        board = self.board()
        result = score(board['own'], board['cohorts'])
        self.assertAlmostEqual(result['components']['buyer.forum'], .27)
        self.assertNotIn('card', str(board))

    def test_prediction_compares_outcomes_not_claimed_policy_lift(self):
        first = self.board()
        report = compare_and_predict(first, {}, NOW)
        self.assertEqual(report['next_prediction']['method'], 'cold_start_persistence')
        previous = {'score_observation': first, 'score_prediction': report['next_prediction']}
        current = self.board(NOW+7200, forum=.95, all=.62, deals=120)
        result = compare_and_predict(current, previous, NOW+7200)
        comparison = result['previous_prediction_comparison']
        self.assertTrue(comparison['on_schedule'])
        self.assertAlmostEqual(comparison['realized_fixed_cohort_delta'], .015)
        self.assertAlmostEqual(comparison['official_all_delta']['buyer'], .02)
        self.assertEqual(result['next_prediction']['projected_own']['buyer']['deals'], 140)
        self.assertEqual(result['next_prediction']['policy_lift_assumed'], 0)

    def test_stale_missing_and_late_results_not_valid_prediction_tests(self):
        self.assertIsNone(compare_and_predict(self.board(), {}, NOW+1000)['next_prediction'])
        first = compare_and_predict(self.board(), {}, NOW)
        prior = {'score_observation': self.board(), 'score_prediction': first['next_prediction']}
        result = compare_and_predict(self.board(NOW+15000), prior, NOW+15000)
        self.assertIsNone(result['previous_prediction_comparison']['prediction_error'])

    def test_unknown_counted_vote_is_not_confirmed_reputation(self):
        self.memory.record('forum_claim', '1', {'id': 1, 'author': 'writer', 'text': ''})
        vote_result(self.memory, {'sent': {'vote': 1, 'value': -4}, 'status': 200,
                                 'answer': '{truncated'}, 'one')
        row = reputation(self.memory, {}, NOW)['authors']['writer']
        self.assertIsNone(row['confirmed_weight'])
        self.assertEqual(row['weight_estimate'], 4)

    def test_issued_jwt_stays_blocked_even_if_issuance_marker_corrupted(self):
        from test_r3_cash_recovery import CashRecoveryTests
        fixture = CashRecoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.reservations.transaction() as db:
            row = db.execute('SELECT data FROM deals WHERE task=?', ('task1',)).fetchone()
            data = json.loads(row['data'])
            data['checkout_started'] = False
            db.execute('UPDATE deals SET data=? WHERE task=?', (json.dumps(data), 'task1'))
        report = fixture.reconcile([])
        self.assertEqual(report['revoked'], 0)
        self.assertEqual(report['units'][0]['recovery_action'], 'hold_unresolved_checkout')

    def test_recovery_audit_is_read_only_and_omits_tokens(self):
        from test_r3_cash_recovery import CashRecoveryTests
        from r3_recovery import inspect
        fixture = CashRecoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        atomic_json(fixture.md / 'inventory.json', fixture.inventory)
        before = fixture.reservations.path.read_bytes()
        report = inspect(fixture.md, fixture.now + 1000)
        self.assertEqual(report['blocked_cost'], 80)
        self.assertEqual(report['items'][0]['action'], 'hold_unresolved_checkout')
        self.assertNotIn('checkout_jwt', json.dumps(report))
        self.assertEqual(fixture.reservations.path.read_bytes(), before)

    def test_safe_unissued_promise_is_revoked_but_cannot_reopen(self):
        from test_r3_checkout import CheckoutTests
        fixture = CheckoutTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from r3_seller import Reservations
        reservations = Reservations(fixture.md)
        report = reservations.reconcile({'units': []}, [], 'shop1', fixture.now + 10000)
        self.assertEqual(report['revoked'], 1)
        with self.assertRaises(ValueError):
            fixture.gate.begin(fixture.receipt, fixture.now + 10001)

    def test_model_offer_within_budget_still_obeys_savings_guard(self):
        from test_r3_buyer import BuyerTests
        fixture = BuyerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        atomic_json(fixture.md/'policy.json', {'buyer_min_saved': .8, 'fallback_enabled': False})
        fixture.execute()
        # Food cost prior=78, margin=72: 150 - .8*72 => 92 whole credits.
        # The model's 100 is budget-valid but exceeds the Patch 3.6 target ceiling.
        # The validated fallback opens at 60% of list (90), below that ceiling.
        self.assertEqual(fixture.states[1]['savings_ceiling'], 92)
        self.assertEqual(fixture.inputs[1], ('90', 'task-1'))

    def test_coverage_redundancy_not_semantic_complements(self):
        rows = build_candidates(CATALOG, [event('order_seen','o1',
            {'order':'o1','category':'drinks','acceptable':['tea','tea2']})], NOW)
        result = enrich(rows, self.memory, {'surge': [], 'units': [{'sku':'tea'}]}, NOW)
        self.assertNotIn('cup', [r['sku'] for r in result['candidates']])
        self.assertTrue(all(r['portfolio']['new_order_coverage']==0 for r in result['candidates']))


if __name__ == '__main__':
    unittest.main()
