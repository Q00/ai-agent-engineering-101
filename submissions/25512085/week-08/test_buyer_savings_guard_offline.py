"""Synthetic savings guards; reuse isolated fake RPC fixtures, never live state."""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import market_buyer as buyer
import test_buyer_price_offline as price_tests
import test_buyer_retry_offline as retry_tests
import test_buyer_savings_ranking_offline as ranking_tests


class GoodsSavingsGuardTests(unittest.TestCase):
    setUp = price_tests.BuyerPriceTests.setUp
    receipt = price_tests.BuyerPriceTests.receipt
    run_buyer = price_tests.BuyerPriceTests.run_buyer
    run_market = retry_tests.BuyerRetryTests.run_market

    def goods(self):
        self.order['kind'] = 'goods'
        self.catalog[self.sku]['list_price'] = 100000
        self.catalog['f00000002']['list_price'] = 50000

    def test_completed_at_every_turn_cannot_bypass_cap(self):
        self.goods()
        for turn in range(4):
            with self.subTest(turn=turn):
                result, offers, _, memory = self.run_buyer(
                    ask=85000, receipt=self.receipt(85000, warranty_hours=6),
                    complete_after=turn, budget=200000)
                self.assertIsNone(result['receipt'])
                self.assertTrue(all(type(p) is int and p <= 80000 for p in offers))
                self.assertFalse(memory['shop']['dishonest'])
                self.assertFalse(self.hub.get('pending_deals'))

    def test_third_ask_counter_is_capped_and_bounded(self):
        self.goods()
        result, offers, methods, _ = self.run_buyer(ask=85000, budget=200000)
        self.assertIsNone(result['receipt'])
        self.assertEqual(len(offers), 3)
        self.assertEqual(offers[-1], 80000)
        self.assertEqual(methods.count('CancelTask'), 1)

    def test_cheap_completed_and_budget_boundary(self):
        self.goods()
        for price, budget, accepted in [(80000, 200000, True), (80001, 200000, False),
                                         (70001, 70000, False)]:
            with self.subTest(price=price):
                result, _, _, _ = self.run_buyer(receipt=self.receipt(price, warranty_hours=6), budget=budget)
                self.assertEqual(result['receipt'] is not None, accepted)

    def test_alternate_sku_uses_own_catalog_price(self):
        self.goods()
        for price, accepted in [(40000, True), (40001, False)]:
            with self.subTest(price=price):
                rec = self.receipt(price, warranty_hours=6, list_price=999999)
                rec['item'] = 'f00000002'
                result, _, _, _ = self.run_buyer(receipt=rec, budget=200000)
                self.assertEqual(result['receipt'] is not None, accepted)

    def test_unknown_alternate_price_is_not_target_price(self):
        self.goods()
        for lp in (None, 0, True, float('nan'), 'bad'):
            self.catalog['f00000002']['list_price'] = lp
            rec = self.receipt(30000, warranty_hours=6)
            rec['item'] = 'f00000002'
            result, _, _, _ = self.run_buyer(receipt=rec, budget=200000)
            self.assertIsNone(result['receipt'])

    def test_alternate_completed_at_each_counter_rechecks_actual_cap(self):
        self.goods()
        for turn in range(4):
            with self.subTest(turn=turn):
                rec = self.receipt(45000, warranty_hours=6)
                rec['item'] = 'f00000002'  # original cap=80000; actual cap=40000
                result, offers, _, memory = self.run_buyer(
                    ask=85000, receipt=rec, complete_after=turn, budget=200000)
                self.assertIsNone(result['receipt'])
                self.assertFalse(memory['shop']['dishonest'])
                self.assertTrue(all(p <= 80000 for p in offers))

    def test_unacceptable_sku_signature_and_no_warranty_checks_stay(self):
        self.goods()
        for extra in ({'item': 'not-acceptable'}, {'sig_seller': ''},
                      {'claim': 'C', 'warranty_hours': 0}):
            rec = self.receipt(79000, warranty_hours=6)
            rec.update(extra)
            result, _, _, _ = self.run_buyer(receipt=rec, budget=200000)
            self.assertIsNone(result['receipt'])

    def test_food_above_twenty_percent_cap_is_unchanged(self):
        result, _, _, _ = self.run_buyer(receipt=self.receipt(14500))
        self.assertEqual(result['receipt']['price'], 14500)
        self.assertEqual(result['accept']['min_hours_left'], 12.25)

    def test_goods_policy_decline_retries_other_shop(self):
        self.goods()
        # retry fixture budget=25000, list=30000 -> savings cap=24000
        self.catalog[self.sku]['list_price'] = 30000
        terms = {0: {'claim': 'A', 'warranty_hours': 6},
                 1: {'claim': 'A', 'warranty_hours': 6}}
        with patch.object(buyer, 'GOODS_MIN_DISCOUNT_PERCENT', 0):
            control = self.run_market([24500], receipt_terms=terms)
        self.assertEqual(control.result['receipt']['price'], 24500)
        self.hub['pending_deals'].clear()
        run = self.run_market([24500, 18000], receipt_terms=terms)
        self.assertEqual(run.result['shop'], 'https://shop1.invalid')
        self.assertEqual(run.result['receipt']['price'], 18000)
        self.assertEqual(run.cards, [0, 1])
        self.assertFalse(run.memory['shop0']['dishonest'])
        self.assertEqual(run.memory['shop0']['recent'][-1]['outcome'], 'declined-price-policy')
        self.assertIn('cap 24000', run.diagnostic)
        self.assertNotIn('break rate', run.diagnostic)

    def test_final_accept_integer_and_receipt_revalidation(self):
        self.goods()

        @contextmanager
        def local_hub():
            yield self.hub

        for shipped, price, accepted in [(self.sku, 79999, True), (self.sku, 80001, False),
                                          ('f00000002', 79999, False)]:
            with self.subTest(shipped=shipped, price=price):
                texts = []

                def rpc(url, method, params):
                    text = params['message']['parts'][0]['text']
                    texts.append(text)
                    turn = len(texts) - 1
                    if turn:
                        self.assertRegex(text, r'^[0-9]+$')
                        self.assertLessEqual(int(text), 80000)
                        self.assertEqual(params['message']['taskId'], 'task-1')
                    done = turn == 4
                    task = {'id': 'task-1', 'status': {
                        'state': 'TASK_STATE_COMPLETED' if done else 'TASK_STATE_INPUT_REQUIRED',
                        'message': {'parts': [{'text': 'certified ask ' + str([100000, 90000, 85000, 79999, price][turn])}]}}}
                    if done:
                        rec = self.receipt(price, warranty_hours=6)
                        rec['item'] = shipped
                        task['artifacts'] = [{'parts': [{'data': rec}]}]
                    return task

                with patch.object(buyer, 'rpc_call', side_effect=rpc), \
                        patch.object(buyer.httpx, 'get', return_value=SimpleNamespace(json=lambda: {})), \
                        patch.object(buyer, 'mutate_hub', local_hub), \
                        patch.object(buyer, 'update_memory'):
                    result = buyer._run_attempt(
                        {'name': 'shop', 'url': 'https://offline.invalid'},
                        {'sku': self.sku, 'name': 'synthetic', 'list_price': 100000},
                        'synthetic', {}, self.hub, self.catalog, self.order, 'goods', 200000, {})
                self.assertEqual(len(texts), 5)  # inquiry + 3 counters + final acceptance
                self.assertEqual(texts[-2:], ['80000', '79999'])
                self.assertNotIn('200000', ' '.join(texts))
                self.assertEqual(result['receipt'] is not None, accepted)

    def test_unknown_selected_price_defers_and_fractional_cap_floors(self):
        self.goods()
        self.catalog[self.sku]['list_price'] = 100003
        self.assertEqual(buyer.goods_price_cap(self.catalog, self.hub, self.sku, 200000), 80002)
        for lp in (None, 0, False, float('nan'), float('inf'), 'bad', 100.5):
            self.catalog[self.sku]['list_price'] = lp
            self.assertIsNone(buyer.goods_price_cap(self.catalog, self.hub, self.sku, 200000))

    def test_alternate_receipt_quality_uses_alternate_list(self):
        self.goods()
        rec = self.receipt(39000, claim='C', warranty_hours=0)
        rec['item'] = 'f00000002'
        result, _, _, _ = self.run_buyer(receipt=rec, budget=200000)
        self.assertIsNone(result['receipt'])  # actual C surplus <=0, original SKU would pass

    def test_mature_settles_tighten_goods_and_food_caps(self):
        self.goods()
        self.hub['demand_radar']['sku_settles'] = {self.sku: [60000] * 5}
        self.assertEqual(buyer.goods_price_cap(self.catalog, self.hub, self.sku, 200000), 60000)
        self.hub['demand_radar']['sku_settles'] = {self.sku: [75000] * 5}
        self.assertEqual(buyer.goods_price_cap(self.catalog, self.hub, self.sku, 200000), 67000)
        result, offers, _, _ = self.run_buyer(ask=80000, budget=200000)
        self.assertIsNone(result['receipt'])
        self.assertEqual(offers[-1], 67000)
        self.order['kind'] = 'food'
        self.catalog[self.sku]['list_price'] = 100000
        self.hub['demand_radar']['sku_settles'] = {self.sku: [60000] * 5}
        cap, _ = buyer.food_price_policy(self.catalog, self.hub, self.sku, 200000, self.order)
        self.assertEqual(cap, 60000)
        self.hub['demand_radar']['sku_settles'] = {self.sku: [80000] * 5}
        cap, _ = buyer.food_price_policy(self.catalog, self.hub, self.sku, 200000, self.order)
        self.assertEqual(cap, 66000)

    def test_mature_uncovered_goods_require_minimum_expected_saved(self):
        self.goods()
        self.hub['demand_radar']['sku_settles'] = {self.sku: [75000] * 5}
        # C with w=None at 46% of LP has expected Saved ~ +0.001 <= 0.25 -> rejected
        result, _, _, _ = self.run_buyer(receipt=self.receipt(46000, claim='C'), budget=200000)
        self.assertIsNone(result['receipt'])
        # Covered C (w=6) at 65% of LP has expected Saved = 0.65 * 0.35 = +0.2275 <= 0.25 -> rejected
        result, _, _, _ = self.run_buyer(receipt=self.receipt(65000, claim='C', warranty_hours=6), budget=200000)
        self.assertIsNone(result['receipt'])
        # Covered C (w=6) at 60% of LP has expected Saved = 0.65 * 0.40 = +0.26 > 0.25 -> accepted
        result, _, _, _ = self.run_buyer(receipt=self.receipt(60000, claim='C', warranty_hours=6), budget=200000)
        self.assertIsNotNone(result['receipt'])
        # Covered A (w=6) at 67% of LP has expected Saved = 0.97 * 0.33 = +0.3201 > 0.25 -> accepted
        self.hub['pending_deals'] = {}
        result, _, _, _ = self.run_buyer(receipt=self.receipt(67000, claim='A', warranty_hours=6), budget=200000)
        self.assertIsNotNone(result['receipt'])
        # B with w=None at 60% of LP has expected Saved = 0.85 * 0.40 - 0.15 = +0.19 <= 0.25 -> rejected
        self.hub['pending_deals'] = {}
        result, _, _, _ = self.run_buyer(receipt=self.receipt(60000, claim='B'), budget=200000)
        self.assertIsNone(result['receipt'])
        # B with w=None at 50% of LP has expected Saved = 0.85 * 0.50 - 0.15 = +0.275 > 0.25 -> accepted
        result, _, _, _ = self.run_buyer(receipt=self.receipt(50000, claim='B'), budget=200000)
        self.assertIsNotNone(result['receipt'])


class PriceEvidenceTests(unittest.TestCase):
    select = ranking_tests.BuyerSavingsRankingTests.select

    def test_market_median_not_optimistically_clipped(self):
        for median in (5000, 8500, 11000):
            with self.subTest(median=median):
                result, scores, _ = self.select({'sku': 10000}, settles={'sku': [median]*3})
                self.assertIsNotNone(result[0])  # retain candidates, not fictitious feasible prices
                # Only utility is floored; the median remains 11000, not a fictitious cap price.
                self.assertAlmostEqual(scores['sku'], max(0, .65*(10000-median)/10000-.35) * (3.8/4.2))

    def test_evidence_is_not_replaced_by_budget(self):
        _, scores, _ = self.select({'sku': 10000}, budget=7000, settles={'sku': [8500]*3})
        self.assertEqual(scores['sku'], 0)  # unknown condition: no warranty benefit

    def test_bad_prices_and_insufficient_samples_use_explicit_fallback(self):
        for values in ([8500]*2, [None, 0, False, True, float('nan'), float('inf'), -1, '8500'], []):
            _, scores, _ = self.select({'sku': 10000}, settles={'sku': values})
            self.assertEqual(scores['sku'], 0)

    def test_structured_qty_cannot_be_invented_from_scalar_schema(self):
        for qty in (None, 0, False, True, float('nan'), 2):
            _, scores, _ = self.select({'sku': 10000},
                settles={'sku': [{'amount': 8500, 'qty': qty}] * 3})
            self.assertEqual(scores['sku'], 0)
        # Scalar observations count samples, not units or arbitrary dict quantities.
        for rows in (None, 0, False, True, float('nan'), {'qty': 3}):
            self.assertEqual(buyer.settled_price_samples({'demand_radar': {'sku_settles': {'sku': rows}}}, 'sku'), [])

    def test_valid_sample_count_last_fifteen_and_price_range(self):
        rows = [5000] * 15 + [8500] * 15 + [True, float('nan'), 10**12 + 1]
        _, scores, _ = self.select({'sku': 10000}, settles={'sku': rows})
        self.assertEqual(scores['sku'], 0)
        _, scores, _ = self.select({'sku': 10000}, settles={'sku': [8500]*2 + [True]})
        self.assertEqual(scores['sku'], 0)

    def test_negative_savings_does_not_reward_low_trust_or_diversity_penalties(self):
        shops = [{'name': name, 'url': 'https://' + name + '.invalid',
                  'offers': [{'sku': 'sku'}]} for name in ('good', 'penalized')]
        order = {'order': 'synthetic', 'kind': 'goods', 'acceptable': ['sku']}
        for median in (4000, 11000):
            for memory, board in [({}, {'good': {'stars': 5}, 'penalized': {'stars': 3}}),
                                   ({'penalized': {'last_visited_ts': ranking_tests.NOW - 10}}, {}),
                                   ({'penalized': {'recent': [{'outcome': 'no-deal'}]}}, {}),
                                   ({'penalized': {'recent': [{'outcome': 'no-deal'}] * 4,
                                                  'last_visited_ts': ranking_tests.NOW - 10}}, {})]:
                with self.subTest(median=median, memory=memory, board=board):
                    candidates = []
                    with patch.object(buyer, 'fetch_board_stats', return_value=board), \
                            patch.object(buyer, 'check_acp_support', return_value=True), \
                            patch.object(buyer.time, 'time', return_value=ranking_tests.NOW), \
                            patch.object(buyer, 'filter_candidates', side_effect=lambda rows, *a: candidates.extend(rows) or rows):
                        result = buyer.pick_round3_shop_and_sku(shops, memory,
                            {'demand_radar': {'sku_settles': {'sku': [median]*3}}},
                            {'sku': {'list_price': 10000}}, order, 100000)
                    scores = {row[1]['name']: row[0] for row in candidates}
                    self.assertGreaterEqual(scores['good'], scores['penalized'])
                    self.assertEqual(result[0]['name'], 'good')
                    if median == 4000:
                        self.assertGreater(scores['good'], scores['penalized'])

    def test_negative_savings_feasibility_penalty_is_not_a_reward(self):
        # Same raw savings rate (-10%), different pre-existing feasibility multipliers.
        _, scores, _ = self.select({'feasible': 10000, 'infeasible': 20000},
            budget=10000, settles={'feasible': [11000]*3, 'infeasible': [22000]*3})
        self.assertGreaterEqual(scores['feasible'], scores['infeasible'])

    def test_goods_uncappable_sku_does_not_hide_valid_same_shop_sku(self):
        for price in (None, 0, False, 1, 100.5):
            with self.subTest(price=price):
                shops = [{'name': 'shop', 'url': 'https://shop.invalid',
                          'offers': [{'sku': 'unknown'}, {'sku': 'valid'}]}]
                order = {'order': 'synthetic', 'kind': 'goods', 'acceptable': ['unknown', 'valid']}
                catalog = {'unknown': {'list_price': price}, 'valid': {'list_price': 10000}}
                hub = {'demand_radar': {'sku_settles': {'valid': [8500]*3}}}
                with patch.object(buyer, 'fetch_board_stats', return_value={}), \
                        patch.object(buyer, 'check_acp_support', return_value=True) as probe:
                    result = buyer.pick_round3_shop_and_sku(shops, {}, hub, catalog, order, 10000)
                self.assertEqual(result[1]['sku'], 'valid')
                probe.assert_called_once_with('https://shop.invalid')

    def test_uncappable_goods_never_probes_a_shop(self):
        for price in (None, 0, 1, 100.5):
            with self.subTest(price=price):
                result, scores, probes = self.select({'sku': price}, budget=10000)
                self.assertIsNone(result[0])
                self.assertEqual(scores, {})
                self.assertEqual(probes, 0)

    def test_same_shop_valid_sku_and_other_url_retry_both_remain(self):
        shops = [{'name': 'first', 'url': 'https://first.invalid',
                  'offers': [{'sku': 'unknown'}, {'sku': 'valid'}]},
                 {'name': 'second', 'url': 'https://second.invalid',
                  'offers': [{'sku': 'valid'}]}]
        order = {'order': 'synthetic', 'kind': 'goods', 'acceptable': ['unknown', 'valid']}
        hub = {'demand_radar': {'sku_settles': {'valid': [8500]*3}}}
        with patch.object(buyer, 'fetch_board_stats', return_value={}), \
                patch.object(buyer, 'check_acp_support', return_value=True), \
                patch.object(buyer, 'run_attempt', side_effect=[
                    {'receipt': None, 'review': 'synthetic decline'},
                    {'receipt': {'item': 'valid'}, 'review': 'synthetic receipt selected'}]) as attempt:
            result = buyer.run_round3(shops, {}, hub,
                {'unknown': {}, 'valid': {'list_price': 10000}}, order, 'goods', 10000,
                buyer.time.monotonic() + 100)
        self.assertEqual(result['receipt']['item'], 'valid')
        self.assertEqual([call.args[0]['name'] for call in attempt.call_args_list], ['first', 'second'])
        self.assertEqual([call.args[1]['sku'] for call in attempt.call_args_list], ['valid', 'valid'])


if __name__ == '__main__':
    unittest.main()
