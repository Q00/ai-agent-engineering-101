import json
import tempfile
import unittest
from pathlib import Path
from profit_strategy import replenish


class FoodMarketEntryTests(unittest.TestCase):
    def plan(self,remaining):
        with tempfile.TemporaryDirectory() as d:
            now=10000
            events=[dict(seq=i,kind='settle',round=3,ts=now-100,
                         data=dict(sku='x',shop=f's{i%3}',buyer=f'b{i%5}',amount=9000,
                                   expires_at=now-100+remaining*3600)) for i in range(40)]
            events += [dict(seq=100+i,kind='supply',round=3,ts=now-50,
                            data=dict(sku='x',shop=f's{i%2}',total=5000,qty=1)) for i in range(6)]
            result=replenish(dict(balance=6000000,units=[],restock=dict(open=True)),
                             {'x':dict(kind='food',list_price=10000,shelf_hours=24)},events,now,d)
            preview=json.loads((Path(d)/'procurement-preview.json').read_text())
            return result,preview

    def test_fresh_profitable_food_can_enter_without_own_history(self):
        result,preview=self.plan(12)
        self.assertEqual(result['buy'],[dict(sku='x',qty=2)])
        self.assertGreater(preview['candidates'][0]['expected_net'],0)

    def test_clearance_transactions_do_not_establish_fresh_food_margin(self):
        result,preview=self.plan(1)
        self.assertEqual(result['buy'],[])
        self.assertTrue(any(x['reason']=='no_eligible_fresh_price_samples' for x in preview['excluded']))
