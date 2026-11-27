"""Goods reserves survive age/endgame; isolated fixture keys and blocked sockets."""
import math
import unittest
from unittest.mock import patch

import seller
import seller_cash_policy as cash
import test_seller_food_offline as fixture


class GoodsFloorTests(unittest.TestCase):
    setUp = fixture.FoodSafetyTests.setUp
    turn = fixture.FoodSafetyTests.turn
    receipt = fixture.FoodSafetyTests.receipt

    def goods(self, grade='A', hours=0, cost=100000):
        sku = 'G00000001'
        self.catalog[sku] = dict(kind='goods', category='goods>sofa', list_price=250000)
        return dict(unit='goods-1', sku=sku, kind='goods', name='sofa',
                    category='goods>sofa', grade=grade, cost=cost, list_price=250000,
                    supplied_at=self.now-hours*3600, cert={'proof': 'offline'})

    def quote(self, unit, factor=1, hub=None):
        return seller.compute_unit_pricing_and_terms(
            unit, {} if hub is None else hub, now=self.now, catalog=self.catalog, clearance=factor)

    def test_aged_A_never_has_below_cost_floor(self):
        unit = self.goods(hours=24)
        self.assertGreaterEqual(self.quote(unit)['hard_floor'], unit['cost'])

    def test_endgame_A_keeps_normal_floor(self):
        unit = self.goods()
        self.assertGreaterEqual(self.quote(unit, .1)['hard_floor'], self.quote(unit)['hard_floor'])

    def evidence(self, grade):
        return {'demand_radar': {'sku_comparables': {'G00000001': [
            dict(grade=grade, amount=100000, ts=self.now-i,
                 warranty_hours={'A': 72, 'B': 6, 'C': 0}[grade]) for i in range(5)]}}}

    def test_grade_age_factor_evidence_matrix(self):
        for grade in ('A', 'B', 'C'):
            for evidence in (False, True):
                hub = self.evidence(grade) if evidence else {}
                fresh = self.quote(self.goods(grade), hub=hub)
                previous_floor = 0
                for age in (0, 7.99, 8, 11.99, 12, 24, 240):
                    unit = self.goods(grade, age)
                    normal = self.quote(unit, hub=hub)
                    self.assertGreaterEqual(normal['hard_floor'], previous_floor)
                    previous_floor = normal['hard_floor']
                    for factor in (1, .5, .1, 0):
                        with self.subTest(grade=grade, age=age, factor=factor, evidence=evidence):
                            q = self.quote(unit, factor, hub)
                            floor = cash.profit_floor(unit, hub, self.catalog, self.now)
                            self.assertGreaterEqual(q['hard_floor'], max(fresh['hard_floor'], normal['hard_floor'], floor))
                            self.assertGreaterEqual(q['open_ask'], q['r1_target'])
                            self.assertGreaterEqual(q['r1_target'], q['r2_target'])
                            self.assertGreaterEqual(q['r2_target'], q['hard_floor'])
                            self.assertEqual(q['warranty_hours'], {'A': 72, 'B': 6, 'C': 0}[grade])
                            self.assertEqual(q['cash_first_quote'], evidence and age < 8 and factor == 1)

    def test_C_zero_refund_floor_and_storage_counted_once(self):
        for grade in ('A', 'B', 'C'):
            unit = self.goods(grade, 240)
            hub = self.evidence(grade)
            q = cash.refund_probability(hub, unit['sku'], grade, self.catalog, self.now)
            contribution = 5000
            expected = math.ceil((100000+100000*.01*10+contribution)/(1-q))
            self.assertEqual(cash.profit_floor(unit, hub, self.catalog, self.now), expected)
            if grade == 'C':
                self.assertEqual(q, 0)
                self.assertEqual(self.quote(unit)['hard_floor'], expected)
                self.assertEqual(self.quote(unit, 0)['hard_floor'], expected)

    def test_fresh_cash_floors_unchanged(self):
        for grade in ('A', 'B', 'C'):
            unit = self.goods(grade)
            hub = self.evidence(grade)
            self.assertEqual(self.quote(unit, hub=hub)['hard_floor'], cash.quote(unit, hub, self.catalog, self.now)['hard_floor'])

    def test_instant_and_negotiated_receipts_keep_floor_and_terms(self):
        for grade in ('A', 'B', 'C'):
            for age in (0, 8, 12, 24):
                for factor in (1, .5, 0):
                    for evidence in (False, True):
                        hub = self.evidence(grade) if evidence else {}
                        for instant in (False, True):
                            with self.subTest(grade=grade, age=age, factor=factor, evidence=evidence, instant=instant):
                                self.agent = fixture.fresh_seller(self.tmp)
                                unit = self.goods(grade, age)
                                self.inventory['units'] = [unit]
                                q = self.quote(unit, factor, hub)
                                with patch.object(seller, 'load_hub', return_value=hub), patch.object(seller, 'clearance_factor', return_value=factor):
                                    events = self.turn(unit['sku'] + (' offer 999999' if instant else ''))
                                    if not instant:
                                        events = self.turn(str(q['open_ask']))
                                receipt = self.receipt(events)
                                self.assertGreaterEqual(receipt['price'], q['hard_floor'])
                                self.assertEqual(receipt['warranty_hours'], q['warranty_hours'])

    def test_low_offers_cannot_sign_receipt(self):
        for grade in ('A', 'B', 'C'):
            self.agent = fixture.fresh_seller(self.tmp)
            unit = self.goods(grade, 24)
            self.inventory['units'] = [unit]
            self.turn(unit['sku'])
            for _ in range(4):
                events = self.turn(str(self.quote(unit)['hard_floor']-1))
                self.assertNotIn('receipt', [kind for kind, _ in events])
            self.assertFalse(self.agent.committed_units)

    def test_stale_ask_rejected_without_silent_price_increase(self):
        for grade in ('A', 'B', 'C'):
            for no_care in (False, True):
                for change in ('storage', 'risk'):
                    with self.subTest(grade=grade, no_care=no_care, change=change):
                        self.agent = fixture.fresh_seller(self.tmp)
                        unit = self.goods(grade)
                        self.inventory['units'] = [unit]
                        self.turn(unit['sku'])
                        deal = self.agent.open['task-1']
                        unit = deal['unit']  # execute stores an enriched copy of inventory.
                        deal['no_care'] = no_care  # Legacy stored option; switching remains disabled.
                        old_ask = deal['ask']
                        hub = {}
                        if change == 'storage':
                            unit['supplied_at'] -= 100000*3600
                        else:
                            if grade == 'C' or no_care:
                                # No paid refund risk: storage still provides the stale-floor case.
                                unit['supplied_at'] -= 100000*3600
                            else:
                                hub = {'break_stats': {unit['sku']: {grade: {'broken': 1000, 'sold': 1000}}}}
                        with patch.object(seller, 'load_hub', return_value=hub):
                            events = self.turn('offer 999999999' + (' without care' if no_care else ''))
                        self.assertNotIn('receipt', [kind for kind, _ in events])
                        self.assertIn('reject', [kind for kind, _ in events])
                        self.assertEqual(deal['ask'], old_ask)
                        self.assertFalse(self.agent.committed_units)

    def test_stored_warranty_zero_custom_and_no_care_floor(self):
        for grade, stored, no_care in (('A', 0, False), ('A', 12, False), ('B', 0, False),
                                      ('B', 3, False), ('A', 72, True), ('B', 6, True), ('C', 0, False)):
            with self.subTest(grade=grade, stored=stored, no_care=no_care):
                self.agent = fixture.fresh_seller(self.tmp)
                unit = self.goods(grade, 240)
                self.inventory['units'] = [unit]
                self.turn(unit['sku'])
                deal = self.agent.open['task-1']
                deal['warranty_hours'] = stored
                deal['no_care'] = no_care
                actual = 0 if no_care else stored
                current = cash.profit_floor(unit, {}, self.catalog, self.now, warranty=actual)
                declared = deal['hard_floor']
                if no_care:
                    declared = seller.no_care_price(declared, unit['cost'], deal['break_rate'])
                floor = max(declared, current)
                # Force the normal acceptance route while keeping the live expected floor binding.
                deal.update(ask=floor, r1_target=1, r2_target=1, rounds=2)
                events = self.turn('offer 999999' + (' without care' if no_care else ''))
                receipt = self.receipt(events) if not no_care else None
                if no_care:
                    # ask is stored in care terms: converting it below the live reserve must reject.
                    self.assertNotIn('receipt', [kind for kind, _ in events])
                else:
                    self.assertGreaterEqual(receipt['price'], floor)
                    self.assertEqual(receipt['warranty_hours'], actual)

    def late_cash_turn(self, offer):
        for grade in ('A', 'B', 'C'):
            with self.subTest(grade=grade, offer=offer):
                self.agent = fixture.fresh_seller(self.tmp)
                unit = self.goods(grade)
                self.inventory['units'] = [unit]
                hub = self.evidence(grade)
                with patch.object(seller, 'load_hub', return_value=hub):
                    self.turn(unit['sku'])
                    deal = self.agent.open['task-1']
                    self.assertTrue(deal['cash_first_quote'])
                    old_ask = deal['ask']
                    self.now += 60
                    self.assertIs(self.agent.open['task-1'], deal)
                    self.assertLess(self.now-deal['created_at'], 90)
                    live_floor = cash.profit_floor(deal['unit'], hub, self.catalog, self.now,
                                                   warranty=deal['warranty_hours'])
                    self.assertGreater(live_floor, old_ask)
                    events = self.turn(str(old_ask if offer == 'old_ask' else offer))
                self.assertIn('reject', [kind for kind, _ in events])
                self.assertIn('profit floor', str(events))
                self.assertNotIn('input', [kind for kind, _ in events])
                self.assertNotIn('receipt', [kind for kind, _ in events])
                self.assertNotIn('task-1', self.agent.open)
                self.assertEqual(deal['ask'], old_ask)
                self.assertFalse(self.agent.committed_units)

    def test_sixty_second_late_cash_low_offer_rejects_instead_of_counter(self):
        self.late_cash_turn(1)

    def test_sixty_second_late_cash_old_ask_acceptance_rejects_for_floor(self):
        self.late_cash_turn('old_ask')

    def test_live_floor_counter_preserves_stored_terms_without_raising_quote(self):
        for grade, stored, no_care in (('A', 0, False), ('A', 12, False), ('B', 3, False),
                                      ('C', 0, False), ('A', 72, True), ('B', 6, True)):
            with self.subTest(grade=grade, stored=stored, no_care=no_care):
                self.agent = fixture.fresh_seller(self.tmp)
                unit = self.goods(grade)
                self.inventory['units'] = [unit]
                self.turn(unit['sku'])
                deal = self.agent.open['task-1']
                deal.update(warranty_hours=stored, no_care=no_care,
                            hard_floor=1, r1_target=1, r2_target=1)
                in_mode = (lambda p: seller.no_care_price(p, unit['cost'], deal['break_rate'])) if no_care else (lambda p: p)
                old_ask = in_mode(deal['ask'])
                self.now += 60
                self.assertIs(self.agent.open['task-1'], deal)
                self.assertLess(self.now-deal['created_at'], 90)
                floor = cash.profit_floor(deal['unit'], {}, self.catalog, self.now,
                                         warranty=0 if no_care else stored)
                events = self.turn('offer 1')
                self.assertIn('input', [kind for kind, _ in events])
                counter = in_mode(deal['ask'])
                self.assertGreaterEqual(counter, floor)
                self.assertLessEqual(counter, old_ask)
                self.assertIn(f'asking price {counter}', str(events))
                receipt = self.receipt(self.turn(str(counter)))
                self.assertGreaterEqual(receipt['price'], floor)
                self.assertEqual(receipt['warranty_hours'], 0 if no_care else stored)

    def test_stale_stored_terms_reject_low_offer_and_old_ask_within_ttl(self):
        for grade, stored, no_care in (('A', 0, False), ('A', 12, False), ('B', 3, False),
                                      ('A', 72, True), ('B', 6, True)):
            for low_offer in (True, False):
                with self.subTest(grade=grade, stored=stored, no_care=no_care, low_offer=low_offer):
                    self.agent = fixture.fresh_seller(self.tmp)
                    unit = self.goods(grade)
                    self.inventory['units'] = [unit]
                    self.turn(unit['sku'])
                    deal = self.agent.open['task-1']
                    floor = cash.profit_floor(deal['unit'], {}, self.catalog, self.now,
                                             warranty=0 if no_care else stored)
                    # Stored legacy quote with its disclosed option already priced at reserve.
                    deal.update(warranty_hours=stored, no_care=no_care, ask=floor,
                                hard_floor=floor, r1_target=floor, r2_target=floor)
                    self.now += 60
                    self.assertIs(self.agent.open['task-1'], deal)
                    self.assertLess(self.now-deal['created_at'], 90)
                    events = self.turn(str(1 if low_offer else floor))
                    self.assertIn('reject', [kind for kind, _ in events])
                    self.assertIn('profit floor', str(events))
                    self.assertNotIn('input', [kind for kind, _ in events])
                    self.assertNotIn('receipt', [kind for kind, _ in events])
                    self.assertNotIn('task-1', self.agent.open)
                    self.assertEqual(deal['ask'], floor)
                    self.assertEqual(deal['warranty_hours'], stored)
                    self.assertFalse(self.agent.committed_units)

    def test_no_care_signed_receipt_uses_undiscounted_live_floor(self):
        for grade in ('A', 'B'):
            self.agent = fixture.fresh_seller(self.tmp)
            unit = self.goods(grade, 240)
            self.inventory['units'] = [unit]
            self.turn(unit['sku'])
            deal = self.agent.open['task-1']
            deal['no_care'] = True  # Exercise legacy state without enabling new warranty switches.
            floor = max(seller.no_care_price(deal['hard_floor'], unit['cost'], deal['break_rate']),
                        cash.profit_floor(unit, {}, self.catalog, self.now, warranty=0))
            events = self.turn('offer 999999 without care')
            receipt = self.receipt(events)
            self.assertGreaterEqual(receipt['price'], floor)
            self.assertEqual(receipt['warranty_hours'], 0)

    def test_current_profit_floor_rechecked_after_no_care_conversion(self):
        for grade in ('A', 'B'):
            self.agent = fixture.fresh_seller(self.tmp)
            unit = self.goods(grade, 240)
            self.inventory['units'] = [unit]
            self.turn(unit['sku'])
            deal = self.agent.open['task-1']
            deal['no_care'] = True
            # Legacy/stale targets cannot bypass the current storage+contribution floor.
            deal.update(hard_floor=1, r1_target=1, r2_target=1, rounds=2)
            current = cash.profit_floor(unit, {}, self.catalog, self.now, warranty=0)
            events = self.turn(f'offer {current-1} without care')
            self.assertNotIn('receipt', [kind for kind, _ in events])
            self.assertFalse(self.agent.committed_units)

    def test_food_price_numbers_unchanged(self):
        for hours, expected in ((6, (100, 84, 35, 35)), (18, (130, 110, 55, 55)),
                                (36, (144, 128, 80, 80)), (60, (160, 148, 100, 100)),
                                (96, (190, 138, 113, 110))):
            unit = fixture.FoodSafetyTests.food(self, hours)
            for factor in (1, .5, .1, 0):
                with self.subTest(hours=hours, factor=factor):
                    q = self.quote(unit, factor)
                    prices = tuple(q[k] for k in ('open_ask', 'r1_target', 'r2_target', 'hard_floor'))
                    self.assertEqual(prices, expected if factor == 1 else tuple(int(25+(p-25)*factor) for p in expected))
                    self.assertEqual(q['warranty_hours'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
