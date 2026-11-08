import unittest
from network_reads import retry_read
from procurement_campaign import choose,allocation_plan,capital_available,supply_result,own_registration,retry_join,join_current_registration
class PurchaseTests(unittest.TestCase):
 def setUp(self):
  self.p={'budget':2500000,'cash_floor':500000,'priority':['a','b','c','d']}
  self.cat={k:dict(kind='goods',list_price=400000) for k in 'abcd'}
 def test_cash_and_cumulative_budget_not_stock_count(self):
  self.assertEqual(choose(self.p,self.cat,0,0,4782055,0,0),('a',300000))
  self.assertIsNone(choose(self.p,self.cat,2300000,0,4782055,0,0))
  self.assertIsNone(choose(self.p,self.cat,0,2300000,4782055,0,0))
  self.assertIsNone(choose(self.p,self.cat,0,0,700000,0,0))
 def test_priority_rotation_and_food_exclusion(self):
  self.assertEqual(choose(self.p,self.cat,0,0,4782055,0,1)[0],'b')
  self.cat['b']['kind']='food'
  self.assertEqual(choose(self.p,self.cat,0,0,4782055,0,1)[0],'c')
 def test_refund_exposure_preserved(self):
  self.assertIsNone(choose(self.p,self.cat,0,0,1000000,250000,0))
 def test_sale_releases_inventory_budget_without_resetting_lifetime_spend(self):
  # Current inventory falls after settlement even if historical spend exceeds budget.
  self.assertIsNone(choose(self.p,self.cat,2400000,0,5000000,0,0))
  self.assertEqual(choose(self.p,self.cat,1900000,0,5000000,0,0),('a',300000))
 def test_reward_and_warranty_liabilities_limit_cash(self):
  self.assertIsNone(choose(self.p,self.cat,0,0,1000000,300000,0))
 def test_allocation_uses_sellthrough_holdings_and_positive_net_without_unit_cap(self):
  ranked=[dict(sku='a',expected_net=50000,score=3,upper_cost=100000,sales_1h=8,sales_6h=12,
               own_stockouts_6h=2,own_inquiries_6h=1,estimated_sale_likelihood=.8),
          dict(sku='b',expected_net=-1,score=99,upper_cost=1,sales_1h=100,sales_6h=100,
               own_stockouts_6h=0,own_inquiries_6h=0,estimated_sale_likelihood=1)]
  plan=allocation_plan(ranked,{'units':[{'sku':'a'}]},900000)
  self.assertEqual(plan[0]['sku'],'a');self.assertGreater(plan[0]['qty'],4)
  self.assertNotIn('b',[r['sku'] for r in plan])
  aggressive=allocation_plan(ranked,{'units':[{'sku':'a'}]},900000,aggression=3)
  self.assertGreater(aggressive[0]['qty'],plan[0]['qty'])
  huge=allocation_plan(ranked,{'units':[]},10000000,aggression=100)
  self.assertEqual(huge[0]['qty'],20)
 def test_capital_scales_with_balance_but_preserves_floor_liabilities_and_slots(self):
  policy={'cash_floor':500000,'max_inventory_share':.7}
  low={'balance':5000000,'bankrupt_line':500000,'units':[{'cost':1000000}]}
  high={'balance':11000000,'bankrupt_line':500000,'units':[{'cost':1000000}]}
  self.assertGreater(capital_available(policy,high,300000),capital_available(policy,low,300000))
  ranked=[dict(sku='a',expected_net=1,score=1,upper_cost=100,sales_1h=20,sales_6h=20,
               own_stockouts_6h=0,own_inquiries_6h=0,estimated_sale_likelihood=1)]
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,available_slots=2)[0]['qty'],2)
 def test_unknown_or_invalid_supply_result_stops_for_reconciliation(self):
  self.assertEqual(supply_result(503,{},100),('uncertain',None))
  self.assertEqual(supply_result(200,{'total':101},100),('reconcile',None))
  self.assertEqual(supply_result(200,{'total':90},100),('paid',90))
 def test_missing_registration_snapshot_is_retryable_read_failure(self):
  attempts=[]
  def registration():
   attempts.append(1)
   return own_registration([] if len(attempts)<3 else [{'name':'25512088'}])
  self.assertEqual(retry_read(registration,attempts=3,sleep=lambda _:None)['name'],'25512088')
  self.assertEqual(len(attempts),3)
 def test_join_card_timeout_system_exit_is_retried(self):
  attempts=[]
  def join():
   attempts.append(1)
   if len(attempts)<2:raise SystemExit('card timeout')
   return {'round':3}
  self.assertEqual(retry_join(join,attempts=2,sleep=lambda _:None)['round'],3)
  self.assertEqual(len(attempts),2)
 def test_join_retry_refreshes_changed_seller_tunnel(self):
  class Market:
   def __init__(self):self.urls=[]
   def join(self,role,name,key,url):
    self.urls.append(url)
    if len(self.urls)==1:raise SystemExit('stale tunnel')
    return {'round':3}
  market=Market();registrations=iter([{'url':'old'},{'url':'new'}])
  result=retry_join(lambda:join_current_registration(market,'key',lambda:next(registrations)),attempts=2,sleep=lambda _:None)
  self.assertEqual(result['round'],3);self.assertEqual(market.urls,['old','new'])
if __name__=='__main__':unittest.main()
