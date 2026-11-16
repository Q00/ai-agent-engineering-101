import copy,time,unittest
import buyer_value as v
import buyer_breakage as b

class ValueTests(unittest.TestCase):
 def setUp(self):
  self.e={'same_shop_sku':{'paid':100,'mature_paid':100,'early_broken':0,'broken':5,'refunded':1,'unrefunded_within_6h':3}}
  self.t=v.terms('goods',1000,900,{'unit_cost':610},self.e,{'saved':.731})
 def test_risk_and_cushion_reduce_ceiling_below_merely_beating_current_score(self):
  self.assertEqual(self.t['target_saved'],.8)
  self.assertLess(self.t['max_price_without_warranty'],1000-.731*(1000-610))
  p=self.t['max_price_without_warranty'];self.assertGreaterEqual(v.adjusted_saved(1000,p,610,self.e),.8)
  self.assertLess(v.adjusted_saved(1000,p+1,610,self.e),.8)
 def test_cover_refunds_still_lose_saving_and_does_not_credit_late_or_unknown_failure(self):
  before=copy.deepcopy(self.e);covered=v.covered_evidence(self.e,6)
  self.assertEqual(covered['same_shop_sku']['refunded'],4);self.assertEqual(self.e,before)
  self.assertLess(v.adjusted_saved(1000,600,610,self.e,6),1)
  unknown={'same_shop_sku':{'broken':1,'refunded':0,'mature_paid':10}}
  self.assertEqual(v.covered_evidence(unknown,6),unknown)
 def test_price_and_cover_are_alternatives_not_blanket_cover_requirement(self):
  plain,covered=self.t['max_price_without_warranty'],self.t['max_price_with_6h_warranty']
  self.assertGreater(covered,plain);self.assertEqual(self.t['required_warranty_hours'],0)
  self.assertEqual(v.quote_cap(self.t,'asking 800; no warranty'),plain)
  self.assertEqual(v.quote_cap(self.t,'asking 800; warranty 6 h'),covered)
  self.assertEqual(v.quote_cap(self.t,'asking 800'),plain)
 def test_larger_price_needs_real_receipt_cover_and_closed_checkout_guard(self):
  price=self.t['max_price_with_6h_warranty']
  for hours in [0,1,True,float('nan')]:
   self.assertIsNotNone(v.receipt_terms(self.t,{'price':price,'warranty_hours':hours},{'kind':'goods'})[1])
  order,problem=v.receipt_terms(self.t,{'price':price,'warranty_hours':6},{'kind':'goods'})
  self.assertIsNone(problem);self.assertEqual(order['required_warranty_hours'],6)
 def test_cheaper_uncovered_purchase_and_issued_stricter_terms_are_preserved(self):
  order={'kind':'goods','required_warranty_hours':24};original=dict(order)
  result,problem=v.receipt_terms(self.t,{'price':self.t['max_price_without_warranty']},order)
  self.assertIsNone(problem);self.assertEqual(result['required_warranty_hours'],24);self.assertEqual(order,original)
  self.assertEqual(v.quote_cap(self.t,'warranty 6 h',required=24),0)
 def test_no_new_policy_for_food_unknown_cost_or_unattributed_single_product_failure(self):
  self.assertFalse(v.terms('food',1000,900,{'unit_cost':610},self.e))
  self.assertFalse(v.terms('goods',1000,900,None,self.e))
  self.assertFalse(v.terms('goods',1000,900,{'unit_cost':610},{'same_product':{'broken':1}}))
 def test_cover_only_case_is_possible_and_maximum_cannot_exceed_mandate(self):
  e={'same_shop_sku':{'paid':1,'mature_paid':0,'early_broken':1,'broken':1,'refunded':0,'unrefunded_within_6h':1}}
  terms=v.terms('goods',1000,650,{'unit_cost':590},e)
  self.assertIsNone(terms['max_price_without_warranty']);self.assertIsNotNone(terms['max_price_with_6h_warranty'])
  self.assertEqual(terms['required_warranty_hours'],6);self.assertLessEqual(terms['max_price_with_6h_warranty'],650)
  self.assertEqual(v.quote_cap(terms,'no warranty',required=6),0)
 def test_refund_credit_requires_actual_verified_failure_horizon(self):
  now=time.time();sale={'event':'settled','order':'a','sku':'G','shop_name':'S','ledger':1,'at':now-8*3600}
  failure={'event':'verified_breakage','source':'public_ledger_own_paid_order_sku','settled_ledger':1,'refund_amount':0,'at':now-3*3600}
  row=b.profiles([sale,failure],now=now)['shop_sku'][('S','G')]
  self.assertEqual(row['unrefunded_within_6h'],1)
  later=dict(failure,at=now-3600)
  self.assertEqual(b.profiles([sale,later],now=now)['shop_sku'][('S','G')]['unrefunded_within_6h'],0)


class ExecutionTests(unittest.TestCase):
 def task(self,message='',receipt=None):
  t={'id':'t','status':{'state':'TASK_STATE_COMPLETED' if receipt else 'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':message}]}}}
  if receipt:t['artifacts']=[{'parts':[{'data':receipt}]}]
  return t
 def test_overpriced_uncovered_counter_walks_after_one_bounded_offer(self):
  from unittest.mock import patch
  import buyer_r3 as buyer
  terms={'max_price_without_warranty':100,'max_price_with_6h_warranty':140}
  replies=[self.task('asking 200; no warranty'),self.task('counteroffer 190; no warranty')]
  with patch.object(buyer,'card_endpoint',return_value='endpoint'),patch.object(buyer,'send',side_effect=replies) as send,patch.object(buyer,'decide_json',side_effect=AssertionError('no extra model')):
   result=buyer.negotiate('url','G','Goods',{'kind':'goods'},opening_offer=150,cap=250,list_price=500,memory={'purchase_value_terms':terms},deadline=time.monotonic()+200)
  self.assertEqual(result['offers'],[100]);self.assertIsNone(result['receipt']);self.assertIn('terms unavailable',result['reason']);self.assertEqual(send.call_count,2)
 def test_quoted_cover_cannot_substitute_for_actual_receipt_cover(self):
  from unittest.mock import patch
  import buyer_r3 as buyer
  terms={'max_price_without_warranty':100,'max_price_with_6h_warranty':140}
  receipt={'price':130,'item':'G','task':'t','unit':'u','claim':'A','cert':True,'sig_seller':'test'}
  for hours,valid in [(0,False),(6,True)]:
   replies=[self.task('asking 130; warranty 6 h'),self.task('Done',dict(receipt,warranty_hours=hours))]
   with patch.object(buyer,'card_endpoint',return_value='endpoint'),patch.object(buyer,'send',side_effect=replies):
    result=buyer.negotiate('url','G','Goods',{'kind':'goods'},opening_offer=130,cap=250,list_price=500,memory={'purchase_value_terms':terms},deadline=time.monotonic()+200)
   self.assertEqual(result['receipt'] is not None,valid)
 def test_caller_rechecks_value_and_fallback_gets_own_terms_and_time(self):
  import tempfile,os
  from pathlib import Path
  from unittest.mock import patch
  import market_buyer_r3 as buyer
  from buyer_memory import PreparedHistory
  base={'name':'Goods','sku':'G','cap':250,'list_price':500,'stock':1,'category':'goods','kind':'goods','listed_shelf_hours':None,'paid':10,'refusals':0,'score_evidence':{'saved_if_usable_at_cap':.8},'first_hand':{},'price_guidance':{},'routing_evidence':{},'risk_policy':{}}
  first={**base,'id':'0','shop':'risk','url':'https://risk','value_terms':{'target_saved':.8,'max_price_without_warranty':100,'max_price_with_6h_warranty':140}}
  other={**base,'id':'1','shop':'other','url':'https://other','value_terms':{}}
  calls=[]
  def negotiate(url,sku,name,order,**kw):
   calls.append((url,dict(order),kw));return {'offers':[130],'task_id':'t','receipt':{'price':130,'item':'G','task':'t','unit':'u','claim':'A','cert':True,'sig_seller':'test'}}
  with tempfile.TemporaryDirectory() as directory:
   previous=Path.cwd();os.chdir(directory)
   try:
    with patch.object(buyer,'read_history',return_value=PreparedHistory([])),patch.object(buyer,'candidates',return_value=[first,other]),patch.object(buyer,'choose',return_value=(first,130)),patch.object(buyer,'negotiate',side_effect=negotiate),patch.object(buyer.buyer_forum_memory,'ingest'),patch.object(buyer.buyer_forum_memory,'retrieve',return_value={}):
     result=buyer.run({'order':'o','item':'goods','kind':'goods','acceptable':['G'],'budget':500,'expires':time.time()+600},[],{'G':{'list_price':500}},'own',epsilon=0)
   finally:os.chdir(previous)
  self.assertEqual(result['shop'],'https://other');self.assertEqual(len(calls),2);self.assertNotIn('warranty_hours',result['accept'])
  self.assertEqual(calls[0][2]['memory']['purchase_value_terms'],first['value_terms'])
  self.assertFalse(calls[1][2]['memory']['purchase_value_terms'])
  self.assertLessEqual(calls[0][2]['deadline']-time.monotonic(),80)
