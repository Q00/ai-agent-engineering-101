import unittest
from unittest.mock import patch
from profit_strategy import opening
from after_sales import warranty_offer
from pathlib import Path
import tempfile


class LeaderProfitTests(unittest.TestCase):
    def test_opening_chooses_expected_contribution_not_highest_eligible_price(self):
        unit=dict(cost=50,list_price=200)
        result=opening(unit,dict(floor=55,market_reference=100),[])
        self.assertLessEqual(result['ask'],120)
        self.assertGreater(result['ask'],unit['cost'])

    def test_warranty_funds_more_expensive_goods_but_keeps_cash_and_total_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            unit=dict(kind='goods',unit='a')
            self.assertTrue(warranty_offer(Path(directory),unit,1800000,4000000,1000,3))
            self.assertFalse(warranty_offer(Path(directory),dict(kind='goods',unit='b'),500000,4000000,1000,3))
