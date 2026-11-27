"""Grade-specific warranty receipts and monetary safeguards; no network."""
import unittest
import threading
from unittest.mock import patch
import seller
import seller_cash_policy as cash
import test_seller_food_offline as fixture
import test_seller_cash_offline as cash_fixture


class GradeWarrantyTests(unittest.TestCase):
    setUp=fixture.FoodSafetyTests.setUp
    turn=fixture.FoodSafetyTests.turn
    receipt=fixture.FoodSafetyTests.receipt
    prepare=cash_fixture.CashQuoteTests.prepare

    def grade(self,g,cost=30000):
        sku,u,hub=self.prepare(cost)
        u.update(grade=g)
        hub['demand_radar']['sku_comparables'][sku]=[
            {'grade':g,'amount':60000,'ts':self.now-i,'warranty_hours':0 if g=='C' else 6} for i in range(5)]
        return sku,u,hub

    def test_A_instant_receipt_has_market_maximum_72h(self):
        sku,u,hub=self.grade('A')
        events=self.turn(sku+' offer 999999')
        r=self.receipt(events)
        self.assertEqual(r['warranty_hours'],72)
        self.assertIn('market maximum',str(events))

    def test_A_counter_receipt_and_opening_keep_72h(self):
        sku,u,hub=self.grade('A')
        self.assertIn('market maximum 72h',str(self.turn(sku)))
        self.assertEqual(self.receipt(self.turn('60000'))['warranty_hours'],72)

    def test_C_opening_and_counter_receipt_explicitly_have_zero_warranty(self):
        sku,u,hub=self.grade('C')
        self.assertIn('no warranty and no breakage refund',str(self.turn(sku)))
        r=self.receipt(self.turn('60000'))
        self.assertEqual((r['claim'],r['warranty_hours'],r['price']),('C',0,60000))

    def test_C_instant_receipt_has_zero_warranty(self):
        sku,u,hub=self.grade('C')
        r=self.receipt(self.turn(sku+' offer 999999'))
        self.assertEqual(r['warranty_hours'],0)
        self.assertEqual(r['refund_policy'],'no refund if the item breaks')

    def test_C_short_warranty_cannot_satisfy_explicit_six_hour_requirement(self):
        sku,u,hub=self.grade('C')
        self.assertIn('reject',[k for k,_ in self.turn(sku+' warranty 6h offer 999999')])
        self.assertFalse(self.agent.open)

    def test_C_never_uses_warrantied_price_as_unwarrantied_comparable(self):
        sku,u,hub=self.grade('C')
        hub['demand_radar']['sku_comparables'][sku]+=[{'grade':'C','amount':999999,'ts':self.now,'warranty_hours':6}]*30
        self.assertEqual(cash.quote(u,hub,self.catalog,self.now)['open_ask'],60000)
        self.assertEqual(cash.quote(u,hub,self.catalog,self.now)['break_rate'],0)

    def test_B_cost_and_risk_floor_survives_old_stock_and_endgame(self):
        sku,u,hub=self.grade('B',100000)
        u['supplied_at']=self.now-14*3600
        with patch.object(seller,'clearance_factor',return_value=.1):
            p=seller.compute_unit_pricing_and_terms(u,hub)
        storage=100000*.01*14/24
        self.assertGreaterEqual(p['hard_floor']*.85-100000-storage,5000)
        self.assertGreaterEqual(p['r2_target'],p['hard_floor'])
        self.assertEqual(p['warranty_hours'],6)

    def test_B_low_offer_cannot_bypass_profit_floor(self):
        sku,u,hub=self.grade('B',100000)
        self.turn(sku)
        for _ in range(3):
            events=self.turn('100000')
            self.assertNotIn('receipt',[k for k,_ in events])

    def test_small_goods_do_not_inherit_a_flat_5000_margin_floor(self):
        sku,u,hub=self.grade('A',5592)
        u['list_price']=7900
        floor=cash.profit_floor(u,hub,self.catalog,self.now)
        self.assertLess(floor,7900)
        self.assertGreater(floor*(1-.03)-5592,0)

    def test_forum_discloses_availability_only(self):
        sku,u,hub=self.grade('C')
        hub['demand_radar']['sku_inquiries']={sku.lower():[{'ts':self.now}]}
        text=cash.stock_post([u],hub,self.catalog,self.now)
        self.assertIn('재고 있음',text)
        self.assertNotIn('warranty',text)
        self.assertNotIn('60000',text)

    def test_no_demand_or_absent_stock_never_produces_notice(self):
        sku,u,hub=self.grade('C')
        self.assertIsNone(cash.availability_plan([u],hub,self.catalog,self.now))
        hub['demand_radar']['sku_inquiries']={sku.lower():[{'ts':self.now}]}
        self.assertIsNone(cash.availability_plan([],hub,self.catalog,self.now))

    def test_new_forum_request_can_get_reply_after_five_minutes(self):
        sku,u,hub=self.grade('C')
        hub['forum_state']={'last_shop_post_ts':self.now-301}
        feed=[{'id':123,'ts':self.now,'role':'buyer','owner':'outside','text':'want to buy '+sku}]
        p=cash.availability_plan([u],hub,self.catalog,self.now,feed)
        self.assertEqual(p['action']['thread'],123)
        hub['forum_state']['availability_replied_threads']=['123']
        self.assertIsNone(cash.availability_plan([u],hub,self.catalog,self.now,feed))

    def test_same_demand_is_not_reannounced_after_twenty_minutes(self):
        sku,u,hub=self.grade('C')
        hub['demand_radar']['sku_inquiries']={sku.lower():[{'ts':self.now-10}]}
        hub['forum_state']={'availability_announced':{sku:self.now-10}}
        self.assertIsNone(cash.availability_plan([u],hub,self.catalog,self.now))

    def test_blocked_analytics_writer_does_not_block_the_seller_thread(self):
        started=threading.Event();release=threading.Event()
        def blocked(*args,**kwargs):started.set();release.wait(2)
        try:
            with patch.object(seller,'record_inbound_inquiry',blocked):
                seller.queue_inbound_inquiry('G00000001',matched=True)
            self.assertTrue(started.wait(1))
            self.assertFalse(release.is_set())
        finally:release.set()


if __name__=='__main__':unittest.main()
