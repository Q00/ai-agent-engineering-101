"""Three-SKU terms pilot: synthetic inventory, temporary signing key, no market I/O."""
import inspect
import unittest
from unittest.mock import patch

import seller
import test_seller_food_offline as fixture


class GoodsTermsPilotTests(unittest.TestCase):
    setUp = fixture.FoodSafetyTests.setUp
    turn = fixture.FoodSafetyTests.turn
    receipt = fixture.FoodSafetyTests.receipt
    SKUS = ('G021aae64', 'G0be9ed07', 'G81ade83e')

    def stock(self, sku='G021aae64', grades=('A', 'B', 'C')):
        self.agent = fixture.fresh_seller(self.tmp)
        self.catalog[sku] = dict(kind='goods', category='goods>sofa', list_price=100000)
        self.inventory['units'] = [dict(
            sku=sku, unit=sku+'-'+g, name='sofa', category='goods>sofa', kind='goods',
            grade=g, cost=30000, list_price=100000, supplied_at=self.now-3600,
            cert={'proof': 'offline'}) for g in grades]
        self.stack.enter_context(patch.object(seller, 'queue_inbound_inquiry', lambda *a, **kw: None))
        return sku

    def open_deal(self, text):
        events = self.turn(text)
        self.assertEqual([k for k, _ in events], ['input'])
        return self.agent.open['task-1'], str(events)

    def test_three_skus_unconstrained_prefer_C0(self):
        for sku in self.SKUS:
            with self.subTest(sku=sku):
                self.stock(sku)
                deal, _ = self.open_deal(sku)
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('C', 0))

    def test_A_only_new_quote_is_A72_without_no_warranty_option(self):
        for sku in self.SKUS:
            with self.subTest(sku=sku):
                self.stock(sku)
                deal, text = self.open_deal(sku+' grade A')
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))
                self.assertGreater(deal['break_rate'], 0)
                self.assertNotIn('warranty_hours=0', text)
                self.assertIn('72h', text)
                self.assertNotIn('choose', text.lower())
                self.assertNotIn('without warranty', text.lower())
                self.assertNotIn('pilot_A0_option', deal)
                self.assertNotIn('Grade C refurbished', text)

    def test_A_question_is_not_permission_to_sell_C(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' A인가요?')
        self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_explicit_A72_preserved(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade=A warranty_hours=72')
        self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_B_minimum_not_C_even_when_cheap(self):
        for text in ('B급 이상 저렴', 'B이상 저렴', 'B 이상 저렴', 'minimum grade B cheap', 'grade>=B cheap'):
            with self.subTest(text=text):
                sku = self.stock(grades=('B', 'C'))
                deal, _ = self.open_deal(sku+' '+text)
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('B', 6))

    def test_explicit_C0_exact_selection(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade=C warranty_hours=0')
        self.assertEqual((deal['grade'], deal['warranty_hours']), ('C', 0))

    def test_six_hour_minimum_uses_warrantied_stock(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' warranty 6h')
        self.assertGreaterEqual(deal['warranty_hours'], 6)
        self.assertNotEqual(deal['grade'], 'C')

    def test_C_missing_uses_existing_A_fallback(self):
        sku = self.stock(grades=('A', 'B'))
        deal, _ = self.open_deal(sku)
        self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_ambiguous_quality_refund_and_cert_keep_conservative_terms(self):
        for text in ('품질 중요', '환불 가능?', 'good quality', 'cert=true', 'after-sales service', '고장 적은 것', 'A급 품질 보증 필요'):
            with self.subTest(text=text):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+text)
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_other_sku_default_and_A_remain_A72(self):
        for text in ('', ' grade A'):
            sku = self.stock('G00000001')
            deal, _ = self.open_deal(sku+text)
            self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_invalid_or_conflicting_conditions_never_sign(self):
        for text in ('grade=D', 'warranty_hours=-1', 'warranty_hours=nan',
                     'warranty_hours=73', 'grade=C warranty 6h', 'grade=A grade=C',
                     'cert=false', 'grade=A warranty_hours=0 warranty 6h', 'grades=[B,C]',
                     'grade=unknown', 'not grade A'):
            with self.subTest(text=text):
                sku = self.stock()
                events = self.turn(sku+' '+text+' offer 999999')
                self.assertIn('reject', [k for k, _ in events])
                self.assertNotIn('receipt', [k for k, _ in events])

    def test_A72_numeric_acceptance_receipt_and_completion_match(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A')
        events = self.turn(str(deal['ask']))
        receipt = self.receipt(events)
        self.assertEqual((receipt['unit'], receipt['claim'], receipt['warranty_hours'], receipt['price']),
                         (deal['unit']['unit'], 'A', 72, deal['ask']))
        self.assertIn('warranty=72h', str(events))
        self.assertNotEqual(receipt['refund_policy'], 'no refund if the item breaks')

    def test_A72_initial_price_can_close_without_new_option_flow(self):
        sku = self.stock()
        receipt = self.receipt(self.turn(sku+' grade A offer 999999'))
        self.assertEqual((receipt['claim'], receipt['warranty_hours']), ('A', 72))

    def test_explicit_C0_instant_receipt_matches(self):
        sku = self.stock()
        receipt = self.receipt(self.turn(sku+' grade=C warranty_hours=0 offer 999999'))
        self.assertEqual((receipt['claim'], receipt['warranty_hours']), ('C', 0))

    def test_existing_A72_promise_numeric_reply_never_downgrades(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A warranty 72h')
        self.assertNotIn('pilot_A0_option', deal)
        receipt = self.receipt(self.turn(str(deal['ask'])))
        self.assertEqual(receipt['warranty_hours'], 72)

    def test_later_minimum_warranty_without_price_cannot_be_ignored(self):
        sku = self.stock()
        self.open_deal(sku)
        events = self.turn('warranty 6h')
        self.assertIn('reject', [k for k, _ in events])
        self.assertFalse(self.agent.open)

    def test_later_conflicting_grade_with_offer_never_signs(self):
        sku = self.stock()
        self.open_deal(sku)
        events = self.turn('grade=A offer 999999')
        self.assertIn('reject', [k for k, _ in events])
        self.assertNotIn('receipt', [k for k, _ in events])

    def test_A72_requirement_keeps_existing_quote_without_option_repricing(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A')
        before = {key: deal[key] for key in ('unit', 'ask', 'initial_ask', 'hard_floor', 'r1_target', 'r2_target')}
        events = self.turn('warranty 72h')
        self.assertEqual([k for k, _ in events], ['input'])
        self.assertEqual({key: deal[key] for key in before}, before)
        self.assertEqual(deal['warranty_hours'], 72)
        receipt = self.receipt(self.turn(str(deal['ask'])))
        self.assertEqual(receipt['warranty_hours'], 72)

    def test_A72_pricing_is_unchanged_default_grade_pricing(self):
        sku = self.stock()
        unit = self.inventory['units'][0]
        standard = seller.compute_unit_pricing_and_terms(unit, {}, now=self.now, catalog=self.catalog, clearance=1)
        deal, _ = self.open_deal(sku+' grade A')
        self.assertEqual(deal['ask'], standard['open_ask'])
        self.assertEqual(deal['hard_floor'], standard['hard_floor'])
        self.assertEqual(deal['break_rate'], standard['break_rate'])
        self.assertGreater(deal['break_rate'], 0)

    def test_A72_keeps_existing_comparable_pricing_path(self):
        sku = self.stock()
        hub = {'demand_radar': {'sku_comparables': {sku: [dict(
            grade='A', amount=77777, warranty_hours=72, ts=self.now-i) for i in range(5)]}}}
        standard = seller.compute_unit_pricing_and_terms(
            self.inventory['units'][0], hub, now=self.now, catalog=self.catalog, clearance=1)
        with patch.object(seller, 'load_hub', return_value=hub):
            deal, _ = self.open_deal(sku+' grade A')
        self.assertEqual(deal['cash_first_quote'], standard['cash_first_quote'])
        self.assertEqual(deal['ask'], standard['open_ask'])
        self.assertEqual(deal['break_rate'], standard['break_rate'])

    def test_A_wording_variants_never_sell_C(self):
        for text in ('A only', 'only A', 'A-grade', 'A급만', '등급 A'):
            with self.subTest(text=text):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+text)
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_ambiguous_refund_with_only_C_needs_new_inquiry(self):
        sku = self.stock(grades=('C',))
        events = self.turn(sku+' 환불 가능? offer 999999')
        self.assertIn('reject', [k for k, _ in events])
        self.assertNotIn('receipt', [k for k, _ in events])

    def test_A0_pricing_override_and_option_helper_are_removed(self):
        self.assertNotIn('warranty_override', inspect.signature(seller.compute_unit_pricing_and_terms).parameters)
        self.assertFalse(hasattr(seller, 'explicit_A72_choice'))
        sku = self.stock()
        with self.assertRaises(TypeError):
            seller.compute_unit_pricing_and_terms(self.inventory['units'][0], {}, now=self.now,
                catalog=self.catalog, clearance=1, warranty_override=0)

    def test_A72_concession_receipt_same_conditions_and_no_floor_bypass(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A')
        events = self.turn('offer 1')
        self.assertIn('72h', str(events))
        self.assertGreaterEqual(deal['ask'], deal['hard_floor'])
        receipt = self.receipt(self.turn(str(deal['ask'])))
        self.assertEqual((receipt['price'], receipt['warranty_hours']), (deal['ask'], 72))

    def test_explicit_B6_and_no_matching_A_are_not_substituted(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade=B warranty_hours=6')
        self.assertEqual((deal['grade'], deal['warranty_hours']), ('B', 6))
        sku = self.stock(grades=('B', 'C'))
        events = self.turn(sku+' grade=A offer 999999')
        self.assertIn('reject', [k for k, _ in events])
        self.assertNotIn('receipt', [k for k, _ in events])

    def test_numeric_72_never_changes_A72_terms(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A')
        self.turn('72')
        self.assertEqual(deal['warranty_hours'], 72)

    def test_no_care_request_cannot_downgrade_existing_A72(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A')
        events = self.turn('without care')
        self.assertEqual([k for k, _ in events], ['input'])
        self.assertIs(self.agent.open['task-1'], deal)
        self.assertEqual(deal['warranty_hours'], 72)

    def test_natural_zero_days_and_invalid_warranty_durations_are_not_ignored(self):
        for text in ('warranty 6 days', '6일 보증', 'warranty -1h', 'warranty nan h'):
            with self.subTest(text=text):
                sku = self.stock()
                events = self.turn(sku+' '+text+' offer 999999')
                self.assertIn('reject', [k for k, _ in events])
                self.assertNotIn('receipt', [k for k, _ in events])
        for text in ('grade=C warranty 0h', 'grade=C 0h warranty'):
            with self.subTest(text=text):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+text)
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('C', 0))

    def test_review_grade_rejection_never_issues_initial_receipt(self):
        for text in ('grade C is not acceptable', 'C급 말고', 'C급은 안 돼요',
                     'grade C is unacceptable', 'I do not want grade C'):
            with self.subTest(text=text):
                sku = self.stock()
                events = self.turn(sku+' '+text+' offer 999999')
                self.assertEqual([k for k, _ in events], ['reject'])
                self.assertIn('clarify', str(events))
                self.assertIn('No receipt issued', str(events))
                self.assertFalse(self.agent.open)
                self.assertFalse(self.agent.committed_units)

    def test_review_grade_rejection_never_issues_later_receipt(self):
        for text in ('grade C is not acceptable', 'C급 말고', 'C급은 안 돼요'):
            with self.subTest(text=text):
                sku = self.stock()
                self.open_deal(sku)
                events = self.turn(text+' offer 999999')
                self.assertEqual([k for k, _ in events], ['reject'])
                self.assertFalse(self.agent.committed_units)

    def test_review_minimum_warranty_cannot_close_B6_or_C0_initially(self):
        for text in ('보증 최소 72시간', 'warranty of at least 72h', 'warranty covering at least 72h'):
            with self.subTest(text=text):
                sku = self.stock(grades=('B', 'C'))
                events = self.turn(sku+' '+text+' offer 999999')
                self.assertEqual([k for k, _ in events], ['reject'])
                self.assertFalse(self.agent.committed_units)

    def test_review_minimum_warranty_checked_after_B6_quote_without_price_too(self):
        for text in ('보증 최소 72시간', 'warranty of at least 72h', 'warranty covering at least 72h'):
            for offer in ('', ' offer 999999'):
                with self.subTest(text=text, offer=offer):
                    sku = self.stock(grades=('B', 'C'))
                    deal, _ = self.open_deal(sku+' grade=B warranty_hours=6')
                    events = self.turn(text+offer)
                    self.assertEqual([k for k, _ in events], ['reject'])
                    self.assertEqual(deal['warranty_hours'], 6)
                    self.assertFalse(self.agent.open)
                    self.assertFalse(self.agent.committed_units)

    def test_review_minimum_warranty_fulfilled_by_actual_A72(self):
        for text in ('보증 최소 72시간', 'warranty of at least 72h'):
            with self.subTest(text=text):
                sku = self.stock()
                receipt = self.receipt(self.turn(sku+' '+text+' offer 999999'))
                self.assertEqual((receipt['claim'], receipt['warranty_hours']), ('A', 72))

    def test_explicit_zero_warranty_never_creates_A0(self):
        for text in ('grade=A warranty_hours=0', 'grade A warranty 0h',
                     'grade A without care', 'A급 보증 0시간'):
            with self.subTest(text=text):
                for sku in self.SKUS:
                    self.stock(sku)
                    deal, wording = self.open_deal(sku+' '+text)
                    self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))
                    self.assertNotIn('warranty_hours=0', wording)
                    self.assertNotIn('choose', wording.lower())
                    receipt = self.receipt(self.turn(str(deal['ask'])))
                    self.assertEqual((receipt['claim'], receipt['warranty_hours']), ('A', 72))

    def test_zero_warranty_with_no_grade_retains_default_A72(self):
        for text in ('warranty_hours=0', 'warranty 0h', 'without care'):
            with self.subTest(text=text):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+text)
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))

    def test_explicit_zero_warranty_initial_price_uses_A72_receipt(self):
        sku = self.stock()
        receipt = self.receipt(self.turn(sku+' grade=A warranty_hours=0 offer 999999'))
        self.assertEqual((receipt['claim'], receipt['warranty_hours']), ('A', 72))

    def test_C0_concession_receipt_matches_stored_conditions(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku)
        self.turn('offer 1')
        self.assertGreaterEqual(deal['ask'], deal['hard_floor'])
        receipt = self.receipt(self.turn(str(deal['ask'])))
        self.assertEqual((receipt['unit'], receipt['claim'], receipt['warranty_hours'], receipt['price']),
                         (deal['unit']['unit'], 'C', 0, deal['ask']))
        self.assertEqual(receipt['refund_policy'], 'no refund if the item breaks')

    def test_explicit_B_cannot_meet_72h_even_when_A_exists(self):
        sku = self.stock()
        events = self.turn(sku+' grade=B warranty 72h offer 999999')
        self.assertEqual([k for k, _ in events], ['reject'])
        self.assertFalse(self.agent.committed_units)

    def test_later_zero_warranty_with_price_keeps_promised_A72(self):
        sku = self.stock()
        deal, _ = self.open_deal(sku+' grade A')
        receipt = self.receipt(self.turn('warranty_hours=0 offer '+str(deal['ask'])))
        self.assertEqual((receipt['claim'], receipt['warranty_hours']), ('A', 72))

    def assert_rejected_conditions(self, text):
        events = self.turn(text)
        self.assertEqual([kind for kind, _ in events], ['reject'])
        self.assertFalse(self.agent.committed_units)
        self.assertFalse(self.agent.open)

    def test_review_partial_grade_clause_initial_with_and_without_price(self):
        for price in ('', ' offer 999999'):
            with self.subTest(price=price):
                sku = self.stock()
                self.assert_rejected_conditions(sku+' grade=C grade!=C'+price)

    def test_review_partial_grade_clause_later_with_and_without_price(self):
        for price in ('', ' offer 999999'):
            with self.subTest(price=price):
                self.stock()
                self.open_deal(self.SKUS[0]+' grade=C')
                self.assert_rejected_conditions('grade=C grade!=C'+price)

    def test_review_partial_warranty_clause_initial_with_and_without_price(self):
        for price in ('', ' offer 999999'):
            with self.subTest(price=price):
                sku = self.stock()
                self.assert_rejected_conditions(sku+' grade=B warranty_hours>=72'+price)

    def test_review_partial_warranty_clause_later_with_and_without_price(self):
        for price in ('', ' offer 999999'):
            with self.subTest(price=price):
                sku = self.stock()
                self.open_deal(sku+' grade=B warranty_hours=6')
                self.assert_rejected_conditions('grade=B warranty_hours>=72'+price)

    def test_review_all_grade_clauses_and_suffixes_must_be_consumed(self):
        conditions = ('grade=C grade!=C', 'grade=C grade<>C', 'grade=C grade=unknown',
                      'grade=C grade:C/D', 'grade=C grade=C', 'grade>=B grade=B',
                      'grade=C or D', 'grade=C, D', 'grade=C !=B',
                      'grade=C grade==C', 'grade=C grade~C', 'C급 grade!=C',
                      '등급 C 등급!=C', 'grade=C grade C 이상')
        for condition in conditions:
            for later in (False, True):
                with self.subTest(condition=condition, later=later):
                    sku = self.stock()
                    if later:
                        self.open_deal(sku+' grade=C')
                    self.assert_rejected_conditions(('' if later else sku+' ')+condition+' offer 999999')

    def test_review_all_warranty_clauses_and_suffixes_must_be_consumed(self):
        conditions = ('warranty_hours>=72', 'warranty_hours!=6', 'warranty_hours==6',
                      'warranty_hours=6 warranty_hours!=6', 'warranty_hours=6!=72',
                      'warranty 6h warranty>=72h', 'warranty 6h 보증>=72시간',
                      'warranty_hours=6/72', 'warranty_hours=6 or 72',
                      'warranty_hours=6 warranty_hours=72', 'warranty_hours=6 warranty>=72',
                      'warranty_hours=6 warranty 72', 'warranty_hours=6 보증 최소 72',
                      'warranty_hours=6 warranty_hours=6',
                      'warranty_hours=6 warranty≥six', 'warranty_hours=6 보증≤six')
        for condition in conditions:
            for later in (False, True):
                with self.subTest(condition=condition, later=later):
                    sku = self.stock()
                    if later:
                        self.open_deal(sku+' grade=B warranty_hours=6')
                    self.assert_rejected_conditions(('' if later else sku+' ')+'grade=B '+condition+' offer 999999')

    def test_review_field_suffixes_and_unconsumed_grade_sets_fail_closed(self):
        for condition in ('grade=C grade_min=C', 'grade=C grade_condition=C',
                          'grade=C or Z', 'grade=C or unknown', 'grade=C, Z',
                          'grade=C D', 'grade=C [B,C]', 'grade=C ≠A',
                          'grade=C ≤B', 'grade=C %A',
                          'grade=B warranty_hours_min=72', 'grade=B warrantyhours>=72'):
            for later in (False, True):
                with self.subTest(condition=condition, later=later):
                    sku = self.stock()
                    if later:
                        self.open_deal(sku+' grade='+('B' if 'grade=B' in condition else 'C'))
                    self.assert_rejected_conditions(('' if later else sku+' ')+condition+' offer 999999')

    def test_review_supported_single_clauses_retain_exact_terms(self):
        for condition, expected in (
                ('grade=C', ('C', 0)), ('grade=C warranty_hours=0', ('C', 0)),
                ('grade>=B cheap', ('B', 6)), ('grade B or above cheap', ('B', 6)),
                ('grade=B warranty_hours=6', ('B', 6)), ('grade B and warranty 6h', ('B', 6)),
                ('grade=A warranty_hours=0', ('A', 72)), ('A등급', ('A', 72)),
                ('보증 최소 72시간', ('A', 72)), ('warranty of at least 72h', ('A', 72))):
            with self.subTest(condition=condition):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+condition)
                self.assertEqual((deal['grade'], deal['warranty_hours']), expected)
                receipt = self.receipt(self.turn(str(deal['ask'])))
                self.assertEqual((receipt['claim'], receipt['warranty_hours']), expected)

    COMPOUND_GRADE_CONDITIONS = (
        'grade B or above except A', 'grade B or above excluding A',
        'grade B or above but not A', 'except A, grade B or above',
        'grade B or above sofa, excluding A', 'grade B or above with exception of A',
        'B급 이상 A 제외', 'B급 이상 A 빼고', 'B급 이상 A빼고요',
        'B급 이상 A 말고', 'grade B or above != A', 'grade B or above / C',
    )

    def test_review_compound_minimum_grade_initial_with_and_without_price(self):
        for condition in self.COMPOUND_GRADE_CONDITIONS:
            for price in ('', ' offer 999999'):
                with self.subTest(condition=condition, price=price):
                    sku = self.stock()
                    self.assert_rejected_conditions(sku+' '+condition+price)

    def test_review_compound_minimum_grade_later_with_and_without_price(self):
        for condition in self.COMPOUND_GRADE_CONDITIONS:
            for price in ('', ' offer 999999'):
                with self.subTest(condition=condition, price=price):
                    sku = self.stock()
                    self.open_deal(sku+' grade A')
                    self.assert_rejected_conditions(condition+price)

    def test_review_consumed_minimum_suffix_keeps_supported_single_condition(self):
        for condition, expected in (
                ('grade B or above', ('A', 72)), ('grade B or above cheap', ('B', 6)),
                ('B급 이상', ('B', 6)), ('B급 이상 저렴', ('B', 6))):
            with self.subTest(condition=condition):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+condition)
                self.assertEqual((deal['grade'], deal['warranty_hours']), expected)
                receipt = self.receipt(self.turn(str(deal['ask'])))
                self.assertEqual((receipt['claim'], receipt['warranty_hours']), expected)

    def test_review_compound_negation_words_do_not_match_harmless_name_substrings(self):
        for name in ('Exceptional sofa', 'Excludington table', '빼고테이블', 'A'*200+'sofa'):
            with self.subTest(name=name):
                sku = self.stock()
                deal, _ = self.open_deal(sku+' '+name+' grade A')
                self.assertEqual((deal['grade'], deal['warranty_hours']), ('A', 72))
                self.assertFalse(seller.goods_request_terms(sku+' '+name)['invalid'])

    def test_review_explicit_grade_clause_length_is_bounded_not_whole_name(self):
        self.assertTrue(seller.goods_request_terms('grade B'+' '*129+'or above')['invalid'])
        self.assertFalse(seller.goods_request_terms('A'*200+'sofa grade B or above')['invalid'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
