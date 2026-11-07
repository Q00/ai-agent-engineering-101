import json
import tempfile
import unittest
from pathlib import Path
from market_intel import Intel
from buyer_memory import Memory
from buyer_publicist import latest_paid_post
from shop_copy import price_line, paid_post, SHOP_DESCRIPTION

class CopyTests(unittest.TestCase):
    def test_price_and_conditions_are_not_exaggerated(self):
        u=dict(name='식품',sku='X',grade='B',list_price=10000,expires_at=1000+46.9*3600)
        s=price_line(u,{'price':8760},1000)
        self.assertIn('12%↓',s)
        self.assertIn('46h',s)
        self.assertNotIn('%↓',price_line(u,{'price':11000},1000))
        self.assertIn('evidence-scaled warranty',SHOP_DESCRIPTION)
        self.assertLess(len(SHOP_DESCRIPTION), 320)
        self.assertIn('honest A/B/C', SHOP_DESCRIPTION)
    def test_only_paid_evidence_and_no_duplicate_purchase_post(self):
        with tempfile.TemporaryDirectory() as d:
            intel=Intel(d);m=Memory(d)
            try:
                with m.db:
                    for order,paid,seq in [('failed',0,20),('paid',1,10)]:
                        m.db.execute('insert into payments values(?,?,?,?,?,?,?)',(order,'shop','X',1000,paid,seq,None))
                        m.db.execute('insert into outcomes values(?,?)',(order,json.dumps({'name':'실제 상품','grade':'B','claim_honest':False})))
                text,seq=latest_paid_post(intel)
                self.assertEqual(seq,10);self.assertIn('등급 주장 불일치',text)
                intel.put('posted_purchase_ledgers',[10])
                self.assertIsNone(latest_paid_post(intel))
            finally:m.close();intel.close()

if __name__=='__main__':unittest.main()
