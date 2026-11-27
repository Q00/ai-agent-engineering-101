"""Counteroffer/receipt warranty agreement through real execute; isolated, offline I/O."""
import unittest
from unittest.mock import patch

import seller
import test_seller_food_offline as fixture
import test_seller_cash_offline as cash_fixture


class WarrantyDisclosureTests(unittest.TestCase):
    setUp = fixture.FoodSafetyTests.setUp
    turn = fixture.FoodSafetyTests.turn
    receipt = fixture.FoodSafetyTests.receipt
    food = fixture.FoodSafetyTests.food
    prepare = cash_fixture.CashQuoteTests.prepare

    def goods(self, grade, stored=None, no_care=False, missing=False):
        sku, unit, hub = self.prepare(30000)
        unit['grade'] = grade
        hub['demand_radar']['sku_comparables'][sku] = [
            {'grade': grade, 'amount': 60000, 'ts': self.now-i,
             'warranty_hours': 0 if grade == 'C' else 6} for i in range(5)]
        # Avoid even the fixture's mocked background analytics worker.
        self.stack.enter_context(patch.object(seller, 'queue_inbound_inquiry', lambda *a, **kw: None))
        self.turn(sku)
        deal = self.agent.open['task-1']
        if stored is not None:
            deal['warranty_hours'] = stored
        if missing:
            del deal['warranty_hours']
        deal['no_care'] = no_care
        return deal

    def concession(self):
        events = self.turn('offer 1')
        self.assertEqual([kind for kind, _ in events], ['input'])
        self.assertEqual(self.agent.open['task-1']['rounds'], 1)
        text = events[0][1]
        self.assertIn('Special concession', text)
        return text

    def assertDisclosure(self, text, hours):
        if hours:
            for value in seller.care_terms('goods', hours).values():
                self.assertIn(value, text)
        else:
            self.assertIn('warranty_hours=0', text)
            self.assertIn('no warranty', text)
            self.assertIn('no refund if the item breaks', text)
            self.assertNotIn('with Safe-Care', text)
            self.assertNotIn('including the stated certificate and warranty', text)

    def check_deal(self, grade, hours, **kwargs):
        deal = self.goods(grade, **kwargs)
        text = self.concession()
        # First accept the actual counter price and verify the signed receipt,
        # even when the disclosure assertion is expected to fail before the fix.
        price = seller.no_care_price(deal['ask'], deal['unit']['cost'], deal['break_rate']) if deal['no_care'] else deal['ask']
        receipt = self.receipt(self.turn(str(price)))
        self.assertEqual(receipt['price'], price)
        self.assertEqual(receipt['warranty_hours'], hours)
        self.assertEqual(receipt['unit'], deal['unit']['unit'])
        self.assertEqual(receipt['claim'], grade)
        for key, value in seller.care_terms('goods', hours).items():
            self.assertEqual(receipt[key], value)
        self.assertDisclosure(text, hours)
        return text

    def test_A_concession_then_receipt_72h(self):
        self.check_deal('A', 72)

    def test_B_concession_then_receipt_6h(self):
        self.check_deal('B', 6)

    def test_C_concession_then_receipt_zero_no_paid_care_alternative(self):
        self.assertNotIn('optional Safe-Care', self.check_deal('C', 0))

    def test_stored_24h_overrides_current_A_policy(self):
        self.check_deal('A', 24, stored=24)

    def test_explicit_zero_overrides_current_A_policy(self):
        self.assertNotIn('optional Safe-Care', self.check_deal('A', 0, stored=0))

    def test_no_care_overrides_positive_stored_warranty(self):
        text = self.check_deal('A', 0, no_care=True)
        self.assertIn('72h', text)

    def test_C_no_care_does_not_advertise_unavailable_warrantied_option(self):
        self.assertNotIn('optional Safe-Care', self.check_deal('C', 0, no_care=True))

    def test_legacy_missing_warranty_keeps_grade_fallback(self):
        for grade, hours in [('A', 72), ('B', 6), ('C', 0)]:
            with self.subTest(grade=grade):
                self.agent = fixture.fresh_seller(self.tmp)
                self.check_deal(grade, hours, missing=True)

    def test_food_concession_then_receipt_has_no_goods_warranty(self):
        self.inventory['units'] = [self.food(500)]
        self.stack.enter_context(patch.object(seller, 'queue_inbound_inquiry', lambda *a, **kw: None))
        self.turn('f00000001 use within 72h')
        text = self.concession()
        for word in ('warranty', 'Safe-Care', 'refund'):
            self.assertNotIn(word, text)
        receipt = self.receipt(self.turn(str(self.agent.open['task-1']['ask'])))
        for field in ('warranty_hours', 'care_plan', 'refund_policy'):
            self.assertNotIn(field, receipt)

    def check_rejection(self, grade, hours, **kwargs):
        self.goods(grade, **kwargs)
        self.concession()
        events = self.turn('offer 1')
        self.assertEqual([kind for kind, _ in events], ['reject'])
        self.assertFalse(self.agent.open)
        self.assertFalse(self.agent.committed_units)
        self.assertDisclosure(events[0][1], hours)

    def test_final_rejection_discloses_A_72h(self):
        self.check_rejection('A', 72)

    def test_final_rejection_C_does_not_imply_included_warranty(self):
        self.check_rejection('C', 0)

    def test_final_rejection_no_care_does_not_imply_included_warranty(self):
        self.check_rejection('A', 0, no_care=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
