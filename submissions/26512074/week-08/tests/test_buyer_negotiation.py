import unittest
from unittest.mock import AsyncMock,patch
import buyer_r3
from buyer_negotiation import offer_for,record,low_offer_rejection


class BidTests(unittest.TestCase):
    def test_repeated_rejections_adapt_opening_and_respect_ceiling(self):
        a,b={},{}
        self.assertEqual(offer_for(a,100,85,0,None),35)
        self.assertEqual(offer_for(a,100,85,1,0),68)
        record(a,'low_offer_rejected',100,0)
        self.assertEqual(offer_for(a,100,85,0,None),35)
        record(a,'low_offer_rejected',100,50)
        self.assertEqual(offer_for(a,100,85,0,None),35)
        self.assertEqual(offer_for(b,100,85,0,None),35)
        self.assertLessEqual(offer_for(a,1000,85,4,50),85)

    def test_stock_failure_and_transport_are_not_price_rejections(self):
        self.assertFalse(low_offer_rejection('TASK_STATE_REJECTED','out of stock',100,0))
        self.assertFalse(low_offer_rejection('TASK_STATE_FAILED','timeout',100,0))
        self.assertTrue(low_offer_rejection('TASK_STATE_REJECTED','No deal.',100,0))


class BuyerTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejection_is_recorded_once_and_next_order_starts_at_discount(self):
        def task(state,text):
            return {'id':'t','status':{'state':state,'message':{'parts':[{'text':text}]}}}
        shops=[{'name':'s','url':'http://s','offers':[{'sku':'x','qty':1}]}]
        order={'acceptable':['x'],'budget':100}
        catalog={'x':{'name':'X','list_price':100}}
        memory={}
        for expected in ('35','35'):
            client=AsyncMock()
            client.__aenter__.return_value=client
            client.send.side_effect=[task('TASK_STATE_INPUT_REQUIRED','asking 100'),task('TASK_STATE_REJECTED','No deal.')]
            with patch.object(buyer_r3,'A2AClient',return_value=client):
                result=await buyer_r3.buy(shops,order,catalog,memory=memory)
            self.assertEqual(client.send.call_args_list[1].args[0],expected)
            self.assertIsNone(result['receipt'])
        self.assertEqual(memory['http://s']['negotiation']['low_offer_rejections'],2)
        self.assertEqual(memory['http://s']['negotiation']['opening_ratio'],.35)

    def test_paid_opening_is_bounded_by_authorized_ceiling(self):
        audit={'negotiation':{'paid_opening_ratio':.6}}
        self.assertLessEqual(offer_for(audit,100,50,0,None),50)
        self.assertEqual(offer_for(audit,100,90,1,50),72)
        self.assertEqual(offer_for(audit,100,90,2,65),76)
