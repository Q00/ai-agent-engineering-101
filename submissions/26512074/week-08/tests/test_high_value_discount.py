import unittest
from unittest.mock import patch
from buyer_compare import price_ceiling


class HighValueDiscountTests(unittest.TestCase):
    @patch('shop_profit.ownership_tags',return_value={'ours'})
    @patch('buyer_compare.time.time',return_value=10000)
    @patch('shop_profit.recent_ledger')
    def test_market_anchor_allows_small_premium_only_with_reliable_warranty(self,ledger,*_):
        ledger.return_value=[dict(kind='settle',round=3,ts=9999,
            data=dict(sku='x',grade='A',shop='other',buyer='other-buyer',amount=p)) for p in (300000,400000,500000)]
        for warranty in (0,3,6):
            self.assertLessEqual(price_ceiling(600000,600000,warranty,1,'url','x','A'),408000 if warranty>=3 else 400000)
        self.assertLessEqual(price_ceiling(600000,600000,6,1,'url','x','B'),588000)
