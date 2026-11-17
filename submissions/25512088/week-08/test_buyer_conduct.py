import tempfile,time,json,unittest
from buyer_memory import Memory
from rival_feedback import refresh_feedback

class ConductTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.m=Memory(self.tmp.name)
 def tearDown(self):self.m.close();self.tmp.cleanup()
 def test_rejected_shop_reopens_after_cooldown_and_restart(self):
  self.m.defer_rival('shop','sku','rejected')
  self.assertFalse(self.m.rival_ready('shop','sku'))
  self.m.close();self.m=Memory(self.tmp.name)
  self.assertTrue(self.m.rival_ready('shop','sku',time.time()+61))
 def test_unknown_payment_stays_blocked_until_reconciled(self):
  self.m.begin(dict(order='o'));self.m.state('o','uncertain')
  self.m.defer_rival('shop','sku','uncertain',pending='o')
  self.assertFalse(self.m.rival_ready('shop','sku',time.time()+99999))
  self.m.state('o','paid')
  self.assertTrue(self.m.rival_ready('shop','sku',time.time()+61))
 def test_paid_feedback_fetch_failure_retries_without_claiming_zero_stock(self):
  with self.m.db:self.m.db.execute('INSERT INTO outcomes VALUES(?,?)',('o',json.dumps(dict(sku='X',ledger=7))))
  def failed():raise TimeoutError()
  refresh_feedback(self.m,fetch=failed)
  self.assertEqual(self.m.db.execute('select error from rival_feedback').fetchone()[0],'TimeoutError')
  refresh_feedback(self.m,fetch=lambda:[dict(name='other',url='u',stock={'X':2}),dict(name='25512088',url='own',stock={'X':9})])
  r=self.m.db.execute('select stock_json,error from rival_feedback').fetchone()
  self.assertIsNone(r[1]);self.assertEqual(json.loads(r[0]),[dict(shop='other',url='u',qty=2)])
if __name__=='__main__':unittest.main()
