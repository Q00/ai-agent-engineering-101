"""Routing uses paid outcomes and bounded recent conditions, never unsigned receipts."""
from datetime import datetime,timezone
import unittest
from buyer_memory import PreparedHistory
from buyer_policy import routing_profiles,routing_evidence

class RoutingTests(unittest.TestCase):
    now=1800000000
    def at(self,offset):
        return datetime.fromtimestamp(self.now-offset,timezone.utc).isoformat()

    def trade(self,shop,number,paid=True,kind='goods',horizon=None):
        order=f'{shop}-{number}'
        rows=[{'event':'selection_opportunities','order':order,'at':self.at(100),
               'order_kind':kind,'use_within_hours':horizon},
              {'event':'buyer_policy_selection','order':order,'at':self.at(90),
               'shop_name':shop,'shop':shop,'sku':'G'},
              {'event':'negotiation','order':order,'at':self.at(60),
               'shop_name':shop,'shop':shop,'sku':'G',
               'result':'receipt_pending' if paid else 'no_receipt'}]
        if paid:
            e={'event':'settled','order':order,'at':self.at(59),'shop_name':shop,
               'shop':shop,'sku':'G','ledger':number,'value':1000}
            rows.extend([e,dict(e),{**e,'event':'payment','paid':True}])
        return rows

    def test_completed_successes_outweigh_same_saving_with_repeated_failures(self):
        rows=[]
        for n in range(6):
            rows+=self.trade('reliable',n,True)
            rows+=self.trade('rejecting',n,False)
        p=routing_profiles(PreparedHistory(rows),{'kind':'goods'},now=self.now)
        self.assertEqual(p[('reliable','G')]['completed_orders_6h'],6)
        self.assertEqual(p[('reliable','G')]['paid_orders_6h'],6)
        self.assertEqual(p[('reliable','G')]['median_observed_seconds'],30)
        good=routing_evidence(p[('reliable','G')],.8)
        bad=routing_evidence(p[('rejecting','G')],.8)
        self.assertGreater(good['estimated_saved_per_minute'],bad['estimated_saved_per_minute'])

    def test_unsettled_receipt_is_not_a_success_or_completed_failure(self):
        rows=self.trade('pending',1,True)[:3]
        p=routing_profiles(PreparedHistory(rows),{'kind':'goods'},now=self.now)
        self.assertNotIn(('pending','G'),p)
        self.assertEqual(routing_evidence(None,.8)['smoothed_paid_probability'],.6)

    def test_harder_food_horizon_does_not_penalize_easier_order(self):
        rows=self.trade('shop',1,False,kind='food',horizon=48)
        p=routing_profiles(PreparedHistory(rows),{'kind':'food','use_within_hours':4},now=self.now)
        self.assertNotIn(('shop','G'),p)
        p=routing_profiles(PreparedHistory(rows),{'kind':'food','use_within_hours':48},now=self.now)
        self.assertEqual(p[('shop','G')]['completed_orders_6h'],1)

    def test_old_failure_expires_and_known_unusable_settlement_is_not_good_value(self):
        rows=self.trade('shop',1,False)
        for row in rows:row['at']=self.at(7*3600)
        self.assertFalse(routing_profiles(PreparedHistory(rows),{'kind':'goods'},now=self.now))
        rows=self.trade('shop',2,True)
        for row in rows:
            if row['event']=='settled':row['value']=0
        p=routing_profiles(PreparedHistory(rows),{'kind':'goods'},now=self.now)
        e=routing_evidence(p[('shop','G')],.8)
        self.assertEqual(e['unusable_at_settlement_6h'],1)
        self.assertLess(e['estimated_saved_per_minute'],routing_evidence(None,.8)['estimated_saved_per_minute'])
