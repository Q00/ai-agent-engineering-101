import time
import unittest
from unittest.mock import patch
from buyer_compare import compare
from buyer_r3 import candidates


class BuyerScoreUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_quote_does_not_receive_huge_absolute_all_penalty(self):
        known=dict(url='http://known');unknown=dict(url='http://unknown')
        rows=[(1e-6,known,'x',100),(2e-6,unknown,'x',100)]
        memory={known['url']:dict(quotes=[dict(sku='x',ask=80,grade='A',at=time.time(),warranty_hours=6)])}
        with patch('buyer_compare.price_ceiling',return_value=100),patch('patch36.outcome',return_value=.9),patch('patch36.saved_objective',side_effect=lambda share,prob:share*prob*1e-6),patch('patch36.settled_price',return_value=None):
            ranked=await compare(rows,dict(budget=100),{'x':dict(kind='goods',list_price=200)},memory,limit=1)
        self.assertEqual(ranked[0][1]['url'],unknown['url'])
        self.assertGreater(ranked[0][0],0)

    def test_above_budget_estimate_is_probability_discount_not_negative_point_charge(self):
        shop=dict(url='http://other',name='other',offers=[dict(sku='x',qty=1)])
        memory={shop['url']:dict(quotes=[dict(sku='x',ask=120,grade='A',at=time.time())])}
        with patch('strategy_policy.active',return_value=True),patch('patch36.saved_objective',return_value=1e-6):
            rows=candidates([shop],dict(acceptable=['x'],budget=100),{'x':dict(list_price=200,kind='goods')},memory=memory)
        self.assertAlmostEqual(rows[0][0],1e-6*100/120)


if __name__=='__main__':unittest.main()
