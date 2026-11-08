import json
from pathlib import Path
import tempfile
import time
import unittest
from market_intel import Intel
from seller_ledger import Program
from seller_strategy import graded_demand
from seller_offers import offers
from growth_report import report


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.p=Program(self.root);self.i=Intel(self.root)
    def tearDown(self):
        self.p.close();self.i.close();self.tmp.cleanup()
    def test_b_grade_evidence_not_pooled_with_a_and_no_warranty(self):
        now=time.time()
        rows=[dict(seq=n,ts=now,kind='settle',round=3,data=dict(shop='s',sku='x',grade='B',amount=7000,expires_at=now+50*3600,warranty_hours=0)) for n in range(4)]
        self.i.ingest(rows)
        result=graded_demand('x',dict(kind='food',list_price=10000,shelf_hours=100),self.i,now)
        self.assertEqual(result['supported_arrival_probability'],.5)
        self.assertFalse(result['grade_scenarios'][0]['viable'])
        self.assertTrue(result['grade_scenarios'][1]['viable'])
        self.assertEqual(graded_demand('x',dict(kind='goods',list_price=10000),self.i,now)['compatible_sales'],0)
    def test_public_offers_have_no_referral_or_secret_fields(self):
        key=self.p.accept('a','old','x',8000,self.p.quote(12000,8000))
        code=self.p.purchase_code('a')
        self.assertTrue(code.startswith('REF-'));self.assertNotEqual(code,key)
        with self.p.db:self.p.db.execute('UPDATE members SET active=1')
        (self.root/'inventory.json').write_text(json.dumps(dict(round=3,units=[
          dict(unit='u',sku='x',name='item',kind='goods',grade='A',cost=8000,list_price=12000,expires_at=None)])))
        result=offers(self.root,self.p,self.i,ref=code)
        self.assertEqual(len(result),1)
        self.assertNotIn('friend_discount',result[0])
        self.assertNotIn('referrer_credit',result[0])
        self.assertNotIn(code,result[0]['message'])
        self.assertNotIn(key,json.dumps(result))
    def test_measurement_deduplicates_and_excludes_unsettled(self):
        self.assertEqual(report(self.root)['status'],'not_started')
        self.p.put('growth_started_at',time.time()-1)
        self.p.growth_event('x','ref_inquiry');self.p.growth_event('x','ref_inquiry')
        self.p.accept('x','u','sku',8000,self.p.quote(12000,8000))
        r=report(self.root)
        self.assertEqual(r['events']['ref_inquiry'],1)
        self.assertEqual(r['referral_settlements'],0)
        self.assertEqual(r['settled_sales'],0)

    def test_report_requires_paid_reward_and_later_credit_purchase(self):
        start=time.time()-30000
        self.p.put('growth_started_at',start)
        with self.p.db:
            self.p.db.execute("""INSERT INTO sales(task,unit,member,referrer,reward,spent,amount,cost,settled,awarded)
              VALUES('referred','u1','B','A',100,0,1000,500,?,1)""",(start+1,))
            self.p.db.execute("""INSERT INTO sales(task,unit,member,reward,spent,amount,cost,settled)
              VALUES('repeat','u2','A',0,100,900,500,?)""",(start+22000,))
        self.assertEqual(report(self.root)['credited_referrers_repurchase'],1)
        self.p.refund_unit('u1')
        self.assertEqual(report(self.root)['credited_referrers_repurchase'],0)
        self.assertEqual(report(self.root)['referral_settlements'],0)


if __name__=='__main__':unittest.main()
