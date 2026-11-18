import json
import unittest
from tests.test_integrated_strategy import Case
from demand_strategy import plan
from buyer_compare import quoted_warranty


class CashCycleTests(Case):
    def test_own_sale_prices_prevent_phantom_market_profit(self):
        market=self.market(self.events(own=True,price=40000,cost=50000))
        # Other sellers' attractive prices cannot override our repeated losses.
        market['skus']['x']['prices']['A']['base_median']=90000
        market['acquisition_grade_weights']={'A':1}
        self.write('strategy-market.json',market)
        result=plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        self.assertEqual(result['buy'],[])

    def test_existing_queue_increases_payback_and_storage(self):
        market=self.market(self.events(own=True));market['acquisition_grade_weights']={'A':1}
        self.write('strategy-market.json',market)
        plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        first=json.loads((self.md/'procurement-preview.json').read_text())['candidates'][0]
        self.inv['units']=[dict(unit=str(i),sku='x',cost=50000,grade='A') for i in range(1)]
        plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        second=json.loads((self.md/'procurement-preview.json').read_text())['candidates'][0]
        self.assertGreater(second['payback_hours'],first['payback_hours'])
        self.assertLess(second['expected_net'],first['expected_net'])


class WarrantyDaysTests(unittest.TestCase):
    def test_day_warranties_are_hours(self):
        for text,expected in [('three-day warranty',72),('2 day full-refund warranty',48),('warranty: 1 day',24),('six-hour warranty',6)]:
            with self.subTest(text=text):self.assertEqual(quoted_warranty(text),expected)


if __name__=='__main__':unittest.main()
