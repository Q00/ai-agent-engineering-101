import unittest
from recommendations import purchase_exclusions
from shop_profit import recovery_price


class FreshnessPricingTests(unittest.TestCase):
    def test_fresh_unit_uses_comparable_lifetime_sales(self):
        unit=dict(sku='x',kind='food',grade='B',cost=80,list_price=200,expires_at=11000)
        ledger=[]
        for i,(price,remaining) in enumerate([(90,4000),(95,4000),(100,4000),(150,10000),(160,10000),(170,10000)]):
            ledger.append(dict(seq=i,kind='settle',round=3,ts=900,data=dict(sku='x',grade='B',shop='other',amount=price,expires_at=900+remaining)))
        result=recovery_price(unit,dict(balance=1000000,units=[]),ledger,1000)
        self.assertTrue(result['freshness_matched'])
        self.assertEqual(result['market_median'],160)

    def test_food_quote_without_comparable_grade_is_not_called_overpriced(self):
        q=dict(sku='x',shop='other',url='http://other',price=300,at=1000,comparison_required=True)
        metrics={'x':dict(median=100,refs=[])}
        self.assertEqual(purchase_exclusions([q],metrics,{},[],[],1000),[])
        q['comparison_median']=200
        rows=purchase_exclusions([q],metrics,{},[],[],1000)
        self.assertIn('50.0%',rows[0]['reason'])
        self.assertIn('협상 전 호가',rows[0]['reason'])
