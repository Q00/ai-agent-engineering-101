import unittest
from seller_strategy import public_price, receipt_grade


class StockPricingTests(unittest.TestCase):
    def unit(self, grade='A', kind='goods', hours=48):
        return dict(sku='x', grade=grade, kind=kind, list_price=10000, cost=8000,
                    expires_at=1000+hours*3600 if kind == 'food' else None)

    def test_goods_grade_changes_price_even_with_same_cost(self):
        self.assertEqual([public_price(self.unit(g), now=1000) for g in 'ABC'], [9500,8000,6000])

    def test_food_grade_changes_price(self):
        self.assertEqual([public_price(self.unit(g, 'food'), now=1000) for g in 'ABC'], [9500,7500,4500])

    def test_remaining_life_reduces_price_and_can_go_below_cost(self):
        prices = [public_price(self.unit('C', 'food', h), now=1000) for h in [24,12,6,1]]
        self.assertEqual(prices[:3], [4500,2700,1800])
        self.assertTrue(all(a>b for a,b in zip(prices,prices[1:])))

    def test_stale_grade_is_recomputed_from_catalog(self):
        u = self.unit('A','food',36)
        self.assertEqual(public_price(u, {'x': {'shelf_hours':100}}, now=1000),4500)

    def test_no_upgrade_of_market_grade(self):
        self.assertEqual(public_price(self.unit('C','food',90), {'x':{'shelf_hours':100}}, now=1000),4500)

    def test_expired_missing_expiry_or_unknown_grade_cannot_sell(self):
        for u in [self.unit('C','food',0), self.unit('C','food',-1), self.unit('X'),
                  dict(self.unit('A','food'),expires_at=None)]:
            with self.assertRaises(ValueError):
                public_price(u, now=1000)

    def test_receipt_claim_uses_current_grade_and_omits_uncertainty(self):
        cat={'x':{'shelf_hours':100}}
        self.assertEqual(receipt_grade(self.unit('B'),now=1000),'B')
        self.assertEqual(receipt_grade(self.unit('A','food',60),cat,now=1000),'B')
        self.assertEqual(receipt_grade(self.unit('B','food',30),cat,now=1000),'C')
        self.assertIsNone(receipt_grade(self.unit('A','food',80.01),cat,now=1000))
        self.assertIsNone(receipt_grade(self.unit('C','food',90),cat,now=1000))
        self.assertIsNone(receipt_grade(self.unit('A','food',60),{},now=1000))
        self.assertIsNone(receipt_grade(self.unit('C','food',0),cat,now=1000))


if __name__ == '__main__':
    unittest.main()
