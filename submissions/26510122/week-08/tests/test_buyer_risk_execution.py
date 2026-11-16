import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import buyer_breakage as b
from buyer_memory import PreparedHistory
from buyer_policy import acceptance,receipt_problem
from buyer_r3 import opening_request,quoted_condition_problem
import market_buyer_r3 as buyer

class RiskPolicyTests(unittest.TestCase):
 def test_immature_intact_purchases_do_not_dilute_verified_loss(self):
  now=time.time();sale={'event':'settled','order':'old','sku':'G','ledger':1,'price':600,'shop_name':'known','shop':'u','at':now-1000,'grade':'A'}
  failure={'event':'verified_breakage','source':'public_ledger_own_paid_order_sku','settled_ledger':1,'at':now-10,'refund_amount':0}
  base=b.profiles([sale,failure],now=now)
  expanded=b.profiles([sale,failure]+[dict(sale,order=str(i),ledger=i,at=now-20) for i in range(2,102)],now=now)
  self.assertEqual(b.contribution_debit(b.for_choice(base,'known','G'),.8),b.contribution_debit(b.for_choice(expanded,'known','G'),.8))
  self.assertEqual(expanded['shop_sku'][('known','G')]['mature_paid'],0)
  mature=b.profiles([sale,failure,dict(sale,order='mature',ledger=999,at=now-7*3600)],now=now)
  self.assertLess(b.contribution_debit(b.for_choice(mature,'known','G'),.8),b.contribution_debit(b.for_choice(base,'known','G'),.8))
 def test_local_unrefunded_loss_changes_terms_without_shop_wide_ban(self):
  e={'same_shop_sku':{'broken':1,'refunded':0},'same_product':{'broken':1,'refunded':0}}
  self.assertEqual(b.purchase_policy(e,'goods')['required_warranty_hours'],6)
  self.assertEqual(b.purchase_policy({'same_product':{'broken':1,'refunded':0}},'goods')['required_warranty_hours'],0)
  self.assertEqual(b.purchase_policy({'same_product':{'broken':2,'refunded':0}},'goods')['required_warranty_hours'],6)
  self.assertFalse(b.purchase_policy(e,'food'))
  self.assertEqual(b.purchase_policy({'same_shop_sku':{'broken':2,'refunded':2}},'goods')['required_warranty_hours'],0)
 def test_required_cover_is_requested_and_hard_checked_at_checkout(self):
  order={'kind':'goods','acceptable':['G'],'required_warranty_hours':6}
  self.assertIn('6 h market warranty is required',opening_request('Goods','G',order))
  self.assertEqual(acceptance(order)['warranty_hours'],6)
  self.assertIsNotNone(quoted_condition_problem('asking 600; no warranty',order))
  self.assertIsNotNone(quoted_condition_problem('Warranty 1 h included',order))
  self.assertIsNone(quoted_condition_problem('Warranty 6 h included',order))
  rec={'item':'G','price':600,'task':'t','sig_seller':'sig'}
  for hours in [0,1,True,float('nan')]:
   self.assertIsNotNone(receipt_problem(dict(rec,warranty_hours=hours),selected_sku='G',order=order,final_offer=600,task_id='t',cap=750))
  self.assertIsNone(receipt_problem(dict(rec,warranty_hours=6),selected_sku='G',order=order,final_offer=600,task_id='t',cap=750))
  self.assertEqual(acceptance(dict(order,required_warranty_hours=0)),{'cert':True,'grades':['A','B']})

 def test_sparse_unrefunded_loss_does_not_force_cover_for_a_strong_paid_history(self):
  e={'same_shop_sku':{'paid':126,'mature_paid':103,'early_broken':0,'broken':1,'refunded':0},'same_product':{'paid':143,'mature_paid':120,'early_broken':0,'broken':1,'refunded':0}}
  p=b.purchase_policy(e,'goods',saving=.82,saved_baseline={'saved':.731})
  self.assertEqual(p['required_warranty_hours'],0);self.assertEqual(p['preferred_warranty_hours'],6)
  self.assertGreater(p['risk_adjusted_usable_saved_estimate'],.731)
 def test_material_risk_below_current_saved_still_requires_protection(self):
  e={'same_shop_sku':{'paid':4,'mature_paid':4,'early_broken':0,'broken':2,'refunded':0}}
  p=b.purchase_policy(e,'goods',saving=.85,saved_baseline={'saved':.731})
  self.assertEqual(p['required_warranty_hours'],6);self.assertLess(p['risk_adjusted_usable_saved_estimate'],.731)

class RiskExecutionTests(unittest.TestCase):
 def run_case(self,covered):
  now=time.time();sale={'event':'settled','order':'old','sku':'G','ledger':1,'price':600,'shop_name':'risk','shop':'https://risk','at':now-3600,'grade':'A'}
  fact={'event':'verified_breakage','source':'public_ledger_own_paid_order_sku','settled_ledger':1,'at':now-10,'refund_amount':0}
  history=PreparedHistory([sale,fact]);order={'order':'new','item':'goods','kind':'goods','acceptable':['G'],'budget':1000,'expires':now+600}
  shops=[{'name':s,'url':'https://'+s,'offers':[{'sku':'G','qty':2}]} for s in ['risk','other']];catalog={'G':{'kind':'goods','name':'Goods','list_price':1000}}
  def choose(rows,*args,**kw):return next(c for c in rows if c['shop']=='risk'),600
  calls=[]
  def negotiate(url,sku,name,attempt_order,**kw):
   calls.append(dict(attempt_order));rec={'item':'G','price':600,'task':'t','sig_seller':'sig'}
   if covered and url=='https://risk':rec['warranty_hours']=6
   return {'receipt':rec,'offers':[600],'task_id':'t','reason':'receipt received'}
  with tempfile.TemporaryDirectory() as directory:
   previous=Path.cwd();os.chdir(directory)
   try:
    with patch.object(buyer,'read_history',return_value=history),patch.object(buyer,'choose',side_effect=choose),patch.object(buyer,'negotiate',side_effect=negotiate):result=buyer.run(order,shops,catalog,'own',epsilon=0)
   finally:os.chdir(previous)
  self.assertNotIn('required_warranty_hours',order)
  return result,calls
 def test_protected_choice_reaches_closed_checkout_policy(self):
  result,calls=self.run_case(True);self.assertEqual(calls[0]['required_warranty_hours'],6);self.assertEqual(result['accept']['warranty_hours'],6)
 def test_unprotected_receipt_cannot_pay_and_fallback_gets_own_policy(self):
  result,calls=self.run_case(False);self.assertEqual(len(calls),2);self.assertEqual(calls[0]['required_warranty_hours'],6)
  self.assertNotIn('required_warranty_hours',calls[1]);self.assertEqual(result['shop'],'https://other');self.assertNotIn('warranty_hours',result['accept'])

class RiskContextTests(unittest.TestCase):
 def test_risk_is_not_duplicated_in_selector_but_negotiation_memory_is_preserved(self):
  memory={'verified_breakage_evidence':{'same_shop_sku':{'paid':100,'broken':1,'refunded':0}},'cases':[]}
  self.assertIn('verified_breakage_evidence',buyer.selection_memory(memory))
  self.assertNotIn('verified_breakage_evidence',buyer.selection_memory(memory,include_breakage=False))
  self.assertIn('verified_breakage_evidence',memory)
 def test_sixteen_quality_candidates_fit_after_deduplicating_the_same_risk(self):
  from market_scoring import buyer_candidate,buyer_cost_reference
  from buyer_policy import purchase_price_guidance,routing_evidence
  product={'kind':'goods','list_price':100000,'category':'가구/침구>침구'};cost=buyer_cost_reference(product)
  evidence={'same_shop_sku':{'paid':100,'mature_paid':80,'early_broken':0,'unrefunded_within_6h':1,'broken':1,'refunded':0},'same_product':{'paid':200,'mature_paid':150,'early_broken':0,'unrefunded_within_6h':2,'broken':2,'refunded':0},'source':'own_paid_order+public_broken_ledger','censored':True}
  rows=[]
  for i in range(16):
   policy=b.purchase_policy(evidence,'goods',saving=.82,saved_baseline={'saved':.731});route=routing_evidence(None,.82,evidence);route['purchase_risk_policy']=policy
   rows.append({'id':str(i),'shop':f'shop-{i}','sku':f'G{i}','name':'침구 상품','list_price':100000,'cap':75000,'stock':3,'reviews':buyer.review_summary([]),'first_hand':{'shop_payment_count':100,'same_sku_payment_outcomes':{'paid':90,'refused':10},'verified_breakage_evidence':evidence,'cases':[]},'routing_evidence':route,'score_evidence':buyer_candidate(100000,75000,cost_reference=cost),'price_guidance':purchase_price_guidance(75000,(65000,68000,70000),list_price=100000,saved_baseline={'saved':.731,'observed_at':1000},cost_reference=cost)})
  import buyer_value
  for row in rows:row['price_guidance']['risk_price_terms']=buyer_value.summary(buyer_value.terms('goods',100000,75000,cost,evidence,{'saved':.731}))
  with patch.object(buyer,'decide_json',return_value={'candidate_id':'0','opening_offer':60000,'reason':'compare risk'}) as model:
   buyer.choose(rows,{'kind':'goods','item':'침구'})
  prompt=model.call_args.args[1]
  self.assertEqual(len(prompt['candidate_shop_skus']),16)
  self.assertIn('post_sale_breakage',prompt['candidate_shop_skus'][0]['routing_evidence'])
