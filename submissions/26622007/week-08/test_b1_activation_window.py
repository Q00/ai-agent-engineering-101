"""Reviewed fixed future B1 study boundaries; no market I/O."""
import tempfile,unittest,datetime,pathlib
import buyer_stock_evidence as b
class ActivationWindow(unittest.TestCase):
 def test_future_window_is_exact(self):
  z=datetime.timezone(datetime.timedelta(hours=9))
  self.assertIs(b.ENABLED,True)
  self.assertEqual(b.STUDY,"b1-stock-order-20261108-0210")
  self.assertEqual(b.START,datetime.datetime(2026,11,8,2,10,tzinfo=z).timestamp())
  self.assertEqual(b.END-b.START,1800)
 def test_start_end_and_durable_record(self):
  with tempfile.TemporaryDirectory() as td:
   o={"order":"o-0123456789ab","kind":"goods","_buyer_started_at":b.START}
   self.assertIsNone(b.reserve(td,o,now=b.START-1))
   r=b.reserve(td,o,now=b.START+1)
   self.assertIsNotNone(r)
   self.assertEqual(r["order"],o["order"])
   p=pathlib.Path(r["directory"])/"assignment.json"
   self.assertTrue(p.is_file())
   self.assertIsNone(b.reserve(td,o,now=b.END))
   self.assertTrue(p.is_file())
if __name__=="__main__":unittest.main()
