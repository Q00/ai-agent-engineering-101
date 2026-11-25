import json
from pathlib import Path
import tempfile
import unittest

from seller_promotion import active_event, discount_quote


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'event.json';self.now=1800000000
        self.event={'id':'open','starts_at':self.now-100,'ends_at':self.now+3600,
                    'discount_starts_at':self.now-1,'discount_bps':300}
        self.path.write_text(json.dumps(self.event))
        self.unit={'cost':500,'kind':'food','supplied_at':self.now-100}

    def quote(self,price,unit=None):
        return discount_quote(unit or self.unit,price,path=self.path,now=self.now)

    def test_three_percent_and_integer_rounding(self):
        self.assertEqual(self.quote(10000)['price'],9700)
        self.assertEqual(self.quote(1001)['saving'],30)

    def test_cost_floor_reduces_discount_instead_of_losing_money(self):
        q=self.quote(510)
        self.assertEqual(q['price'],501)
        self.assertEqual(q['saving'],9)
        self.assertEqual(self.quote(400)['price'],501)
        self.assertEqual(self.quote(400)['saving'],0)

    def test_goods_floor_includes_observed_age_holding_cost(self):
        q=self.quote(510,{'kind':'goods','cost':500,'supplied_at':self.now-2*86400})
        self.assertEqual(q['estimated_accrued_holding'],10)
        self.assertEqual(q['price_floor'],511)

    def test_ends_on_original_event_deadline(self):
        self.assertIsNone(discount_quote(self.unit,1000,path=self.path,now=self.event['ends_at']))
        event=self.event | {'discount_starts_at':self.now+1};self.path.write_text(json.dumps(event))
        self.assertIsNone(self.quote(1000))

    def test_refund_reserve_floor_is_preserved_after_discount(self):
        quote=discount_quote({'kind':'goods','cost':900},1000,path=self.path,now=self.now,minimum_price=1001)
        self.assertEqual(quote['price'],1001)
        self.assertEqual(quote['price_floor'],1001)

    def test_cannot_raise_campaign_above_authorized_rate(self):
        self.path.write_text(json.dumps(self.event | {'discount_bps':1000}))
        self.assertEqual(active_event(self.path,self.now)['discount_bps'],0)


if __name__=='__main__':unittest.main()
