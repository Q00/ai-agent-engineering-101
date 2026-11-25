"""Actual main startup seam, no market/network actions."""
import json,pathlib,sys,tempfile,unittest
from unittest import mock
import market_buyer as main
import buyer_firm_trial as trial
class StopAtReservation(Exception): pass
class OriginalStart(unittest.TestCase):
 def exercise(self, prior):
  with tempfile.TemporaryDirectory() as root:
   shops=pathlib.Path(root)/'shops.json';shops.write_text('[]')
   order=pathlib.Path(root)/'order.json'
   body={'acceptable':['G1'],'kind':'goods','order':'o1'}
   if prior is not None:body['_buyer_started_at']=prior
   order.write_text(json.dumps(body)); seen={}
   def capture(md,loaded,now=None):
    seen['started']=loaded['_buyer_started_at'];raise StopAtReservation()
   with mock.patch.object(sys,'argv',['market_buyer','--shops',str(shops),'--order',str(order),'--item','G1','--budget','100']), mock.patch.object(main,'MEMORY','fixture'),mock.patch.object(main.time,'time',return_value=100),mock.patch.object(main.buyer_quote_alignment,'reserve',side_effect=capture):
    with self.assertRaises(StopAtReservation):main.main()
   return seen['started']
 def test_main_preserves_old_start(self):self.assertEqual(self.exercise(99),99)
 def test_main_preserves_future_start_for_fail_closed_validation(self):self.assertEqual(self.exercise(101),101)
 def test_main_records_start_only_for_fresh_input(self):self.assertEqual(self.exercise(None),100)
 def test_huge_integer_clock_is_rejected_without_exception(self):
  with mock.patch.multiple(trial,ENABLED=True,START=100,END=1900):
   result=trial.turn(None,None,'Counteroffer: 120. This is my lowest price.', [('shop asked',120),('you offered',90)],100,order={'order':'o1','kind':'goods','_buyer_started_at':10**400},shop='S1',sku='G1',policy_ok=True,now=100,log=mock.Mock())
  self.assertIsNone(result)
if __name__=='__main__':unittest.main()
