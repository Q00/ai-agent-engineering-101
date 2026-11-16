import time,unittest
from buyer_memory import PreparedHistory
import buyer_breakage as b
from buyer_checkout_memory import constraint,filter_candidates
from market_buyer_r3 import candidates

class WarrantyMemoryTests(unittest.TestCase):
 def setUp(self):
  self.now=time.time();self.choice={'shop':'risk','url':'https://new','sku':'G','stock':1,'risk_policy':{'required_warranty_hours':6}}
  self.order={'kind':'goods','acceptable':['G'],'budget':1000}
  self.failure={'event':'negotiation','order':'failed','sku':'G','shop_name':'risk','shop':'https://old','at':self.now-30,'result':'no_receipt','offers':[],
   'reason':'seller explicitly reports no required warranty','risk_required_warranty_hours':6}
 def test_exact_reported_terms_are_temporarily_held_even_after_a_price_offer(self):
  for offers in [[],[600]]:
   h=PreparedHistory([dict(self.failure,offers=offers)]);c=constraint(self.choice,self.order,h,self.now)
   self.assertEqual(c['kind'],'required_warranty_unavailable');self.assertEqual(c['required_warranty_hours'],6)
 def test_other_shop_sku_or_weaker_requirement_is_not_blocked(self):
  h=PreparedHistory([self.failure])
  for change in [{'shop':'other'},{'sku':'OTHER'},{'risk_policy':{}},{'risk_policy':{'required_warranty_hours':3}}]:
   self.assertIsNone(constraint(dict(self.choice,**change),self.order,h,self.now))
 def test_expiry_and_new_stock_allow_recheck(self):
  h=PreparedHistory([dict(self.failure,at=self.now-901)])
  self.assertIsNone(constraint(self.choice,self.order,h,self.now))
  start={'event':'selection_opportunities','order':'failed','eligible_candidates':[{'shop':'risk','sku':'G','stock':1}]}
  self.assertIsNone(constraint(dict(self.choice,stock=2),self.order,PreparedHistory([start,self.failure]),self.now))
 def test_empty_pool_probe_preserves_required_terms(self):
  h=PreparedHistory([dict(self.failure,at=self.now-121)])
  allowed,held,probe=filter_candidates([self.choice],self.order,h,now=self.now)
  self.assertEqual(len(allowed),1);self.assertEqual(probe['risk_policy']['required_warranty_hours'],6)
  allowed,held,probe=filter_candidates([self.choice],self.order,PreparedHistory([self.failure]),now=self.now)
  self.assertFalse(allowed);self.assertIsNone(probe)
 def test_real_candidate_pool_uses_required_terms_before_filtering(self):
  sale={'event':'settled','order':'paid','sku':'G','ledger':1,'price':600,'shop_name':'risk','shop':'https://old','at':self.now-3600,'grade':'A'}
  broken={'event':'verified_breakage','source':'public_ledger_own_paid_order_sku','settled_ledger':1,'at':self.now-60,'refund_amount':0}
  h=PreparedHistory([sale,broken,self.failure]);shops=[{'name':s,'url':'https://'+s,'offers':[{'sku':'G','qty':1}]} for s in ['risk','other']]
  pool=candidates(self.order,shops,{'G':{'kind':'goods','name':'Goods','list_price':1000}},'own',full_pool=True,history=h)
  self.assertEqual([c['shop'] for c in pool],['other'])

 def test_missing_cover_on_received_receipt_is_also_a_temporary_terms_observation(self):
  failure=dict(self.failure,offers=[600],reason='receipt lacks required risk protection warranty')
  c=constraint(self.choice,self.order,PreparedHistory([failure]),self.now)
  self.assertEqual(c['kind'],'required_warranty_unavailable')
