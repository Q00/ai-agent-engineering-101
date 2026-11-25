"""Economic fault checks for the internal simulator, not live market tests."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from buyer_policy import acceptance
from experiments.offline_market import OfflineMarket

CATALOG={'F':{'kind':'food','name':'Example food','list_price':1000,'shelf_hours':96},
         'G':{'kind':'goods','name':'Example sofa','list_price':1000}}


class OfflineCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.market=OfflineMarket(CATALOG,Path(self.temp.name))
        self.url=next(iter(self.market.keys));self.now=1800000000

    def outcome(self,sku='F'):
        order={'order':'synthetic-order','kind':CATALOG[sku]['kind'],'acceptable':[sku],
               'budget':1000,'use_within_hours':6,'shelf_hours':CATALOG[sku].get('shelf_hours')}
        trade=self.market.negotiate(self.url,sku,CATALOG[sku]['name'],order,
            opening_offer=700,cap=850,list_price=1000,memory={},deadline=time.monotonic()+200)
        return order,{'shop':self.url,'receipt':trade['receipt'],'accept':acceptance(order,trade['receipt'])}

    def test_signed_valid_checkout_is_a_simulated_payment(self):
        order,out=self.outcome();deal,row=self.market.checkout(order,out,self.now)
        self.assertTrue(row['paid']);self.assertTrue(deal['settled']['simulated'])
        self.assertEqual(self.market.metrics()['gross_spend'],700)
        self.assertEqual(deal['settled']['value'],1000)

    def test_short_shelf_life_receipt_does_not_become_a_payment(self):
        self.market.fault='short_shelf_life'
        order,out=self.outcome();deal,row=self.market.checkout(order,out,self.now)
        self.assertIsNone(deal);self.assertFalse(row['paid'])
        self.assertIn('shelf life',row['reason'])
        self.assertEqual(self.market.metrics()['gross_spend'],0)

    def test_checkout_price_change_cannot_charge_signed_price_or_grant_goods(self):
        self.market.fault='checkout_price_changed'
        order,out=self.outcome();deal,row=self.market.checkout(order,out,self.now)
        self.assertIsNone(deal);self.assertFalse(row['paid'])
        self.assertEqual(row['reason'],'checkout_price_differs_from_signed_receipt')
        self.assertFalse(self.market.settlements)

    def test_tampered_signature_is_not_a_payment(self):
        order,out=self.outcome();out['receipt']=dict(out['receipt'],price=701)
        deal,row=self.market.checkout(order,out,self.now)
        self.assertIsNone(deal);self.assertEqual(row['reason'],'invalid_seller_signature')

    def test_breakage_inside_warranty_refunds_price_and_zeroes_surplus(self):
        order,out=self.outcome('G');self.market.checkout(order,out,self.now)
        # buyer evidence stays inside this temporary directory for this test.
        with patch('experiments.offline_market.record'):
            refund=self.market.break_goods('G',self.now+7200)
        self.assertTrue(refund['covered']);self.assertEqual(refund['amount'],700)
        self.assertEqual(self.market.metrics()['net_spend'],0)
        self.assertEqual(self.market.metrics()['simulated_surplus'],0)
        self.assertIsNone(self.market.break_goods('G',self.now+7300))
