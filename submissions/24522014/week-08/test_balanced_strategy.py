"""Boundary checks for expanded buyer and evidence-limited seller experiments."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest

import buyer_fast
import buyer_support
import forum_policy
import seller_quality
import warranty_trial
import strategy_controller as controller
from balanced_metrics import entry, history
from market_state import read, write
from test_strategy_controller import observation, trade, SETTINGS, T


class FastQuotes(unittest.TestCase):
    def test_one_clear_price(self):
        self.assertEqual(buyer_fast.decision('asking 1,000', 1000)['price'], 1000)

    def test_no_guessing_bad_or_ambiguous_prices(self):
        for text in ('asking 1,00', 'asking 100.50', 'asking 0', 'asking 100 and asking 110', 'budget 100'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))

    def test_cap_and_repeated_quote(self):
        self.assertIsNone(buyer_fast.decision('asking 101', 100))
        self.assertEqual(buyer_fast.decision('asking 100', 100, 100)['action'], 'walk_away')

    def test_sentence_final_period_is_not_a_decimal(self):
        for text, price in (('asking 110.', 110), ('Counteroffer: 234659.', 234659),
                            ('Our asking price is 42630.', 42630), ('The asking price is 42630', 42630),
                            ('asking 1,000.', 1000), ('Asking: 28652. ', 28652),
                            ('Asking price: 21500.\n', 21500), ('asking 110, no warranty.', 110),
                            ('가격: 110원.', 110), ('asking 1881820 KRW.', 1881820)):
            with self.subTest(text=text):
                self.assertEqual(buyer_fast.decision(text, 2000000),
                                 {'action': 'offer', 'price': price, 'reason': buyer_fast.OFFER_REASON})

    def test_shapes_seen_in_real_quotes(self):
        for text, price in (
                ('Foo 700gx2 (1.4kg): asking 14354. Send your offer as a number.\nReply with a number, or ok to take the ask: 14354', 14354),
                ('Bar 500mL X 60 (Gdf79cd07), grade C, 243.2 h shelf life left. Asking 18308. Send your offer as a number.', 18308),
                ('Baz Q (G1515d951), 6 h warranty. This negotiation closes after 6 offers. Asking 326334. Send your offer as a number.', 326334),
                ('SKU G1 | unit u-8 | Qux 500mL | list 15200 | grade B now | 348.4 h left | 85% of list | ask 12920', 12920),
                ('[sku G8] Quux | grade=A | list=87900 ask=70900 (-19%)', 70900),
                ('Foo: asking 19853. Opening quote discount included: 20466 -> 19853; saving 613.', 19853)):
            with self.subTest(text=text):
                self.assertEqual(buyer_fast.decision(text, 2000000)['price'], price)

    def test_decimals_and_malformed_numbers_still_hold(self):
        for text in ('asking 100.50', 'asking 100.50.', 'asking 100.5 now', 'asking 1,00', 'asking 1,00.',
                     'asking 1.000', 'asking 0.', 'asking 0110.', 'asking 100..', 'asking 100.Send', 'asking -100.'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))

    def test_a_malformed_price_is_not_hidden_by_a_clean_one(self):
        for text in ('asking 100.50 and asking 100', 'asking 100. Counteroffer: 100.50', 'asking 1,00. asking 100.'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))

    def test_conflicting_prices_hold_even_with_periods(self):
        for text in ('asking 100. Counteroffer: 90.', 'asking 100. Reply or take the ask: 110.',
                     'asking 100-110.', 'asking 100 to 110.', 'asking 100, or 110.', 'asking 100 -> 90.'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))
        self.assertEqual(buyer_fast.decision('asking 100. Counteroffer: 100.', 1000)['price'], 100)

    def test_a_lower_current_price_after_the_ask_holds(self):
        for text in ('asking 150. Today only 120.', 'asking 150, today only 120.', 'asking 150 (now 120)',
                     'asking 150\nNew price: 120', 'asking 150. Now 120 KRW.', 'asking 150. Now only 120 for you.',
                     'Today only 120. asking 150.', 'asking 150. 할인가 120원.', 'asking 150. 오늘만 120원',
                     'asking 150. Sale price: 120.', 'asking 150. Only $120!'):
            with self.subTest(text=text):
                self.assertEqual(buyer_fast.classify(text, 1000)[:2], (None, 'conflicting_prices'))

    def test_later_numbers_that_do_not_change_the_price_are_fine(self):
        for text in ('asking 150. 24 h warranty.', 'asking 150; shelf life left 35.68h.',
                     'asking 150. Opening discount included: 20466 -> 150; saving 613.',
                     'asking 150. Reply with a number, or ok to take the ask: 150',
                     'asking 150. Only 3 left.', 'asking 150. Now 24 h warranty.', 'asking 150. Only 6 offers.',
                     'asking 150. Today only 6-hour warranty.', 'asking 150. Now 500g.', 'asking 150. Only 10% left.',
                     'asking 150. Now 150.', '[한정특가] 120 세트: asking 150.', '[특가] 120g: asking 150.',
                     '한정특가 120: asking 150.', 'asking 150. Only 35.68 h left.'):
            with self.subTest(text=text):
                self.assertEqual(buyer_fast.classify(text, 1000)[:2][1], 'rule_offer')

    def test_budget_and_repeat_still_guarded_with_periods(self):
        self.assertIsNone(buyer_fast.decision('asking 101.', 100))
        self.assertEqual(buyer_fast.decision('Counteroffer: 100.', 100, 100)['action'], 'walk_away')

    def test_other_numbers_are_never_the_price(self):
        for text in ('Foo 110 Send your offer as a number.', 'Foo 110.', 'Item 3 closes after 6 offers.',
                     'list price 900.', 'list 900.', 'price 900.', 'The price is 900.', 'Our price is 900.',
                     'market median 900.', 'Last settled at 900.', 'budget 100.'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))

    def test_past_and_market_figures_are_not_the_current_ask(self):
        for text in ('list asking price is 900.', 'market asking 900.', 'median asking price: 900.',
                     'previous asking 900.', 'We were asking 900.', 'the asking price was 900.',
                     'used to ask 900.', 'average asking price is 900.', '시중 가격 900.', '이전 가격: 900원.',
                     'Was asking 900. Asking 800.'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))

    def test_quantity_like_followers_are_not_prices(self):
        for text in ('asking 3 packs for 100.', 'asking 10% below list.', 'asking 110kg.', 'ask 5 questions.',
                     'asking 100 was too low.', '가격은 110원입니다.', '제안가 110원입니다.'):
            with self.subTest(text=text):
                self.assertIsNone(buyer_fast.decision(text, 1000))

    def test_classify_explains_each_outcome_without_changing_decision(self):
        for text, cap, last, code in (('asking 100.', 1000, None, 'rule_offer'),
                                      ('asking 100.', 1000, 100, 'rule_repeated_offer'),
                                      ('asking 101.', 100, None, 'over_budget'),
                                      ('Our price is 90.', 1000, None, 'no_labeled_price'),
                                      ('asking 100.50', 1000, None, 'malformed_price'),
                                      ('asking 100. Counteroffer: 90.', 1000, None, 'conflicting_prices'),
                                      ('previous asking 900.', 1000, None, 'non_current_price')):
            with self.subTest(text=text):
                decided, reason, _ = buyer_fast.classify(text, cap, last)
                self.assertEqual(reason, code)
                self.assertEqual(decided, buyer_fast.decision(text, cap, last))
        self.assertEqual(buyer_fast.classify('asking 101.', 100)[2], 101)
        self.assertIsNone(buyer_fast.classify('asking 100. Counteroffer: 90.', 1000)[2])


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name) / '.market'
        self.md.mkdir()
        self.now = time.time()

    def test_model_hour_cap_and_duplicate_order(self):
        self.assertTrue(buyer_support.reserve_model(self.md, 'a', self.now))
        self.assertFalse(buyer_support.reserve_model(self.md, 'a', self.now))
        self.assertTrue(buyer_support.reserve_model(self.md, 'b', self.now))
        self.assertFalse(buyer_support.reserve_model(self.md, 'c', self.now))
        self.assertTrue(buyer_support.reserve_model(self.md, 'c', self.now+3601))

    def test_model_day_cap_survives_hour_reset(self):
        from datetime import datetime, timezone
        base = datetime.fromtimestamp(self.now, timezone.utc).replace(hour=0, minute=0, second=0).timestamp()
        for hour in range(12):
            for i in range(2):
                self.assertTrue(buyer_support.reserve_model(self.md, f'{hour}-{i}', base+hour*3601))
        self.assertFalse(buyer_support.reserve_model(self.md, 'extra', base+13*3601))

    def test_forum_negative_reactions_brake(self):
        write(self.md/'forum-intel.json', {'status': 'ok', 'observed_at': self.now,
              'summary': {'own_post_reactions': [{'likes': 1, 'dislikes': 1}]*3}})
        limits = forum_policy.posting_limits(self.md, self.now)
        self.assertTrue(limits['reaction_brake'])
        self.assertEqual((limits['buyer_daily_limit'], limits['buyer_report_limit']), (4, 3))

    def test_stale_forum_does_not_invent_negative_reactions(self):
        write(self.md/'forum-intel.json', {'status': 'ok', 'observed_at': self.now-3601,
              'summary': {'own_post_reactions': [{'dislikes': 99}]*3}})
        self.assertEqual(forum_policy.posting_limits(self.md, self.now)['buyer_daily_limit'], 12)

    def seed_reviews(self):
        records = {str(i): {'unit': f'u{i}', 'ts': self.now-60, 'round': 3,
                           'source': 'local_acp_settled'} for i in range(1, 4)}
        write(self.md/'local-settlements.json', {'active_round': 3, 'r3_anchor_found': True,
              'collected_at': self.now, 'records': records})
        for i in range(1, 4):
            write(self.md/'signed-offers'/f'{i}.json', {'unit': f'u{i}', 'sku': 'G1'})
        return [{'round': 3, 'stars': 3, 'review': f'paid 95% of list. Ledger #{i}.'} for i in range(1, 4)]

    def test_reviews_require_local_receipt_and_unique_sku(self):
        reviews = self.seed_reviews()
        reviews.append({'round': 3, 'stars': 1, 'review': 'Ledger #999'})
        result = seller_quality.observe(self.md, self.now, reviews)
        self.assertEqual(result['skus']['G1']['count'], 3)
        self.assertTrue(result['skus']['G1']['weak'])
        self.assertEqual(result['skus']['G1']['price_hints'], 3)
        self.assertEqual(result['unmatched_current_reviews'], 1)

    def test_ambiguous_unit_and_two_ledger_references_excluded(self):
        reviews = self.seed_reviews()
        write(self.md/'signed-offers'/'ambiguous.json', {'unit': 'u1', 'sku': 'OTHER'})
        reviews[1]['review'] = 'Ledger #2 and Ledger #3'
        result = seller_quality.observe(self.md, self.now, reviews)
        self.assertEqual(result['skus']['G1']['count'], 1)
        self.assertFalse(result['skus']['G1']['weak'])

    def test_stale_local_receipts_disable_review_actions(self):
        reviews = self.seed_reviews()
        data = read(self.md/'local-settlements.json')
        data['collected_at'] -= 3601
        write(self.md/'local-settlements.json', data)
        self.assertEqual(seller_quality.observe(self.md, self.now, reviews)['status'], 'unavailable')
        self.assertEqual(seller_quality.fresh(self.md, self.now), {})

    def setup_warranty(self, balance=5000000):
        self.unit = {'unit': 'u1', 'sku': 'G1', 'kind': 'goods', 'grade': 'A',
                     'cert': True, 'cost': 100, 'supplied_at': self.now}
        self.terms = {'quote': 200, 'floor': 105}
        write(self.md/'inventory.json', {'round': 3, 'balance': balance, 'units': [self.unit]})
        os.utime(self.md/'inventory.json', (self.now, self.now))
        self.task = next(f'task-{i}' for i in range(100) if int(hashlib.sha256(f'task-{i}'.encode()).hexdigest(), 16)%2 == 0)

    def test_warranty_stress_floor_and_reserved_liability(self):
        self.setup_warranty()
        value = warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now)
        self.assertEqual(value['hours'], 2)
        self.assertEqual(value['floor'], 126)
        self.assertEqual(warranty_trial.liability(self.md, self.now), 200)

    def test_promised_warranty_survives_pause_but_expires_explicitly(self):
        self.setup_warranty()
        old = warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now)
        state = read(self.md/'warranty-trial.json')
        state['paused'] = True
        write(self.md/'warranty-trial.json', state)
        self.assertEqual(warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now+1), old)
        with self.assertRaises(ValueError):
            warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now+600)

    def test_low_cash_does_not_enroll_warranty(self):
        self.setup_warranty(balance=1000100)
        self.assertEqual(warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now)['hours'], 0)

    def test_b_grade_and_low_margin_do_not_enroll(self):
        self.setup_warranty()
        self.unit['grade'] = 'B'
        self.assertFalse(warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now)['enrolled'])
        self.unit['grade'] = 'A'
        self.terms['quote'] = 125
        self.assertFalse(warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now)['enrolled'])

    def test_active_price_trial_blocks_warranty_enrollment(self):
        self.setup_warranty()
        write(self.md/'strategy-state.json', {'active': {'id': 'price-trial'}})
        self.assertFalse(warranty_trial.assignment(self.md, self.task, self.unit, self.terms, self.now)['enrolled'])

    def test_signed_liability_uses_payment_time_and_uncertain_outcome(self):
        write(self.md/'signed-offers'/'a.json', {'task': 'a', 'unit': 'u', 'issued_at': self.now-9000,
              'warranty_hours': 2, 'price': 200})
        write(self.md/'local-settlements.json', {'records': {'1': {'round': 3, 'unit': 'u', 'ts': self.now-5}}})
        self.assertEqual(warranty_trial.liability(self.md, self.now), 200)
        self.assertEqual(warranty_trial.liability(self.md, self.now+8000), 0)

    def test_scores_are_official_and_deduplicated(self):
        snap = {'round': 3, 'observed_at': self.now, 'competition': {'official_player': {'total': 1.101},
                 'own': {'shops': {'all': .569}, 'buyers': {'all': .532}}}}
        self.assertEqual(entry(snap)['player_total'], 1.101)
        self.assertEqual(len(history(self.md, snap, self.now)), 1)
        self.assertEqual(len(history(self.md, snap, self.now)), 1)


class BalancedAcceptance(unittest.TestCase):
    def run_trial(self, end_score):
        goal = {'objective': 'balanced_player_top3', 'player_total': 1.1}
        state, _ = controller.transition(None, observation(**goal), SETTINGS)
        before = [trade(i, T+i) for i in range(1, 4)]
        state, _ = controller.transition(state, observation(T+7200, 10, 2000, 1000, own_sales=before, **goal), SETTINGS)
        after = [trade(i, T+7200+i, state['active']['id'], 194) for i in range(4, 8)]
        state, _ = controller.transition(state, observation(T+14400, 24, 4800, 2400, own_sales=after,
                           **{**goal, 'player_total': end_score}), SETTINGS)
        return controller.transition(state, observation(T+36000, 60, 12000, 6000,
                         **{**goal, 'player_total': end_score}), SETTINGS)[0]

    def test_local_gain_without_official_score_gain_rolls_back(self):
        self.assertEqual(self.run_trial(1.1)['phase'], 'cooldown')

    def test_official_and_local_gain_keeps_trial(self):
        self.assertEqual(self.run_trial(1.11)['phase'], 'kept')


class SellerCoverage(unittest.IsolatedAsyncioTestCase):
    async def test_actual_grade_and_promised_warranty_reach_signed_receipt(self):
        from types import SimpleNamespace
        from unittest.mock import patch, AsyncMock, MagicMock
        import seller_r3
        from market_state import task_file
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root/'.market'
            now = time.time()
            unit = {'unit': 'u', 'sku': 'G1', 'name': 'fixture', 'category': 'g', 'grade': 'A',
                    'cert': True, 'cost': 100, 'kind': 'goods', 'list_price': 200,
                    'supplied_at': now, 'expires_at': None}
            write(md/'inventory.json', {'round': 3, 'balance': 5000000, 'units': [unit]})
            write(md/'catalog.json', {'G1': {'kind': 'goods', 'list_price': 200}})
            task = next(f'task-{i}' for i in range(100) if int(hashlib.sha256(f'task-{i}'.encode()).hexdigest(), 16)%2 == 0)
            updater = MagicMock()
            updater.requires_input = AsyncMock()
            updater.complete = AsyncMock()
            updater.add_artifact = AsyncMock()
            context = SimpleNamespace(current_task=SimpleNamespace(id=task, context_id='c'), message=None)
            seller = seller_r3.Seller()
            with patch.object(seller_r3, 'MARKET_DIR', md), patch.object(seller_r3, '__file__', str(root/'seller_r3.py')), \
                    patch.object(seller_r3, 'TaskUpdater', return_value=updater), \
                    patch.object(seller_r3, 'get_message_text', side_effect=['buy G1', '200']), \
                    patch.object(seller_r3, 'ask_json', return_value=({'action': 'counter', 'price': 200}, 'fixture')), \
                    patch.object(seller_r3, 'sign', side_effect=lambda r: {**r, 'sig_seller': 'fixture'}) as sign:
                await seller.execute(context, MagicMock())
                self.assertEqual(seller.open[task]['coverage']['hours'], 2)
                await seller.execute(context, MagicMock())
                receipt = sign.call_args.args[0]
            self.assertEqual((receipt['claim'], receipt['cert'], receipt['warranty_hours']), ('A', True, 2))
            self.assertEqual(read(task_file(md, 'signed-offers', task))['warranty_hours'], 2)
            self.assertEqual(warranty_trial.liability(md), 200)


if __name__ == '__main__':
    unittest.main()
