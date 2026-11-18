import unittest
from round3 import DAY,forecast,pricing,procurement,review
class Round3Tests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.workspace=tempfile.TemporaryDirectory()
    def tearDown(self):
        self.workspace.cleanup()
    def plan(self,inv,catalog,ledger,now):
        return procurement(inv,catalog,ledger,now,md=self.workspace.name)
    def test_ledger(self):
        r={'seq':1,'kind':'settle','ts':DAY,'data':{'item':'x'}}
        self.assertEqual(forecast([r],DAY),forecast([r,r,{**r,'seq':2,'kind':'order'}],DAY))
    def test_procurement(self):
        cat={str(i):{'list_price':10000,'kind':'goods'} for i in range(20)}
        inv={'balance':3000000,'restock':{'open':True},'units':[]}
        self.assertEqual(self.plan(inv,cat,[],DAY)['buy'],[])
        inv['units']=[{'sku':str(i),'cost':0} for i in range(6)]
        self.assertEqual(self.plan(inv,cat,[],DAY)['buy'],[])
        inv.update(balance=750000,units=[])
        self.assertEqual(self.plan(inv,cat,[],DAY)['buy'],[])
        inv.update(balance=1000000,restock={'open':False})
        self.assertEqual(self.plan(inv,cat,[],DAY)['buy'],[])
    def test_pricing(self):
        u={'cost':80,'list_price':100,'kind':'food','expires_at':3600}
        self.assertLess(pricing(u,0)['ask'],80)
        self.assertIsNone(pricing(u,3600))
        u.update(kind='goods',expires_at=None)
        self.assertGreater(pricing(u,0,warranty_hours=6)['floor'],pricing(u,0)['floor'])
    def test_review(self):
        self.assertEqual(review({'settled':{'claim_honest':False}})['stars'],2)
        self.assertEqual(review({'settled':{'claim_honest':True}})['stars'],5)
    def test_popular_stock_target_and_spend_limit(self):
        cat={'x':{'list_price':100000,'kind':'goods'}}
        inv={'balance':5000000,'restock':{'open':True},'units':[]}
        events=[{'seq':i,'kind':'settle','ts':DAY,'data':{'item':'x','shop':'totato2203-r3','amount':90000}} for i in range(1,5)]
        buy=self.plan(inv,cat,events,DAY)['buy']
        self.assertGreater(buy[0]['qty'],0)
        self.assertLessEqual(sum(item['qty']*75000 for item in buy),inv['balance'])
        inv['balance']=600000
        self.assertTrue(self.plan(inv,cat,events,DAY)['buy'])
if __name__=='__main__':
    unittest.main()
