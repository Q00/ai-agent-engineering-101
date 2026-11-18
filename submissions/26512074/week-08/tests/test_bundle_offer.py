import unittest
from unittest.mock import patch
from bundle_offer import discount,first_purchase_orders,FIRST_PROMO


class BundleTests(unittest.TestCase):
    def test_first_paid_order_after_actual_post(self):
        posts=[dict(author='ours',ts=1000,text=FIRST_PROMO)]
        events=[dict(seq=i,kind='settle',round=3,ts=ts,data=dict(order=str(i),shop='ours'))
                for i,ts in [(1,999),(2,1001),(3,1002)]]
        self.assertEqual(first_purchase_orders(events+events,posts,1010,{'ours'}),{'2'})
        self.assertEqual(first_purchase_orders(events,posts,3000,{'ours'}),set())
    def records(self,n):
        events=[];refs=[]
        for i in range(n):
            ref=f'00000000-0000-0000-0000-{i:012d}';refs.append(ref)
            events.extend([
                dict(kind='settle',round=3,ts=1000,data=dict(order=str(i),shop='ours')),
                dict(kind='result',round=3,ts=1000,data=dict(order=str(i),buyer='buyer',verified=True,
                    receipt=dict(task=ref,item=f'sku{i}')))])
        return ' '.join(refs),events

    @patch('bundle_offer.ownership_tags',return_value={'ours'})
    def test_tiers_cap_floor_duplicates_and_expiry(self,_):
        for n in range(1,8):
            text,events=self.records(n)
            r=discount(text,'new',1000,900,events+events,1000)
            self.assertEqual(r['rate'],min(n,5)*.01)
            self.assertEqual(r['price'],1000-min(n,5)*10)
            self.assertEqual(discount(text,'new',1000,980,events,1000)['price'],max(980,r['price']))
            self.assertEqual(discount(text,'new',1000,900,events,3000)['rate'],0)

    @patch('bundle_offer.ownership_tags',return_value={'ours'})
    def test_unpaid_or_same_sku_does_not_qualify(self,_):
        text,events=self.records(1)
        self.assertEqual(discount(text,'new',1000,900,events[1:],1000)['rate'],0)
        self.assertEqual(discount(text,'sku0',1000,900,events,1000)['rate'],0)
