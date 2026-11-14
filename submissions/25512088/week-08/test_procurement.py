import unittest
from network_reads import retry_read
from seller_procurement import choose,allocation_plan,capital_available,supply_result,own_registration,own_board_registration,retry_join,join_current_registration,preserve_stale_pending,breadth_window_blocks
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
 def test_allocation_uses_sellthrough_and_small_diversified_lots(self):
  ranked=[dict(sku='a',expected_net=50000,score=3,upper_cost=100000,sales_1h=8,sales_6h=12,
               distinct_buyers_6h=4,own_stockouts_6h=2,own_inquiries_6h=1,estimated_sale_likelihood=.8),
          dict(sku='b',expected_net=-1,score=99,upper_cost=1,sales_1h=100,sales_6h=100,
               distinct_buyers_6h=4,own_stockouts_6h=0,own_inquiries_6h=0,estimated_sale_likelihood=1)]
  policy={'max_sku_capital_share':1}
  plan=allocation_plan(ranked,{'units':[{'sku':'a'}]},900000,policy=policy)
  self.assertEqual(plan[0]['sku'],'a');self.assertEqual(plan[0]['qty'],2)
  self.assertNotIn('b',[r['sku'] for r in plan])
  aggressive=allocation_plan(ranked,{'units':[{'sku':'a'}]},900000,aggression=3,policy=policy)
  self.assertEqual(aggressive[0]['qty'],2)
  huge=allocation_plan(ranked,{'units':[]},10000000,aggression=100,policy=policy)
  self.assertEqual(huge[0]['qty'],3)
 def test_food_is_capped_at_two_units_per_sku(self):
  ranked=[dict(sku='food',kind='food',expected_net=50000,score=3,upper_cost=100,
               sales_1h=8,sales_6h=12,distinct_buyers_6h=4,estimated_sale_likelihood=.8)]
  plan=allocation_plan(ranked,{'units':[]},10000)
  self.assertEqual(plan[0]['qty'],2)
  one={'units':[{'sku':'food','kind':'food','cost':100}]}
  self.assertEqual(allocation_plan(ranked,one,10000)[0]['qty'],1)
  full={'units':[{'sku':'food','kind':'food','cost':100} for _ in range(2)]}
  self.assertEqual(allocation_plan(ranked,full,10000),[])
 def test_sofa_is_blocked_until_zero_then_returns_under_normal_cap(self):
  ranked=[dict(sku='Gc2f7811b',expected_net=999999,score=99,upper_cost=100000,sales_1h=20,sales_6h=20,
               distinct_buyers_6h=10,estimated_sale_likelihood=1),
          dict(sku='fresh',expected_net=50000,score=3,upper_cost=100000,sales_1h=8,sales_6h=12,
               distinct_buyers_6h=4,own_stockouts_6h=0,own_inquiries_6h=0,estimated_sale_likelihood=.8)]
  inv={'units':[{'sku':'Gc2f7811b','cost':100000}]+[{'sku':'fresh','cost':100000} for _ in range(2)]}
  plan=allocation_plan(ranked,inv,10000000)
  self.assertEqual([(x['sku'],x['qty']) for x in plan],[('fresh',1)])
  inv={'units':[{'sku':'Gc2f7811b','cost':100000}]+[{'sku':'fresh','cost':100000} for _ in range(5)]}
  self.assertEqual(allocation_plan(ranked,inv,10000000),[])
  plan=allocation_plan(ranked,{'units':[]},10000000)
  self.assertEqual((plan[0]['sku'],plan[0]['qty']),('Gc2f7811b',3))
 def test_capital_scales_with_balance_but_preserves_floor_liabilities_and_slots(self):
  policy={'cash_floor':500000,'max_inventory_share':.7}
  low={'balance':5000000,'bankrupt_line':500000,'units':[{'cost':1000000}]}
  high={'balance':11000000,'bankrupt_line':500000,'units':[{'cost':1000000}]}
  self.assertGreater(capital_available(policy,high,300000),capital_available(policy,low,300000))
  ranked=[dict(sku='a',expected_net=1,score=1,upper_cost=100,sales_1h=20,sales_6h=20,
               distinct_buyers_6h=3,own_stockouts_6h=0,own_inquiries_6h=0,estimated_sale_likelihood=1)]
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,available_slots=2)[0]['qty'],2)
 def test_anonymous_quote_flood_and_single_sku_concentration_are_bounded(self):
  ranked=[dict(sku='a',expected_net=5000,score=3,upper_cost=100,sales_1h=0,sales_6h=2,
               distinct_buyers_6h=1,own_stockouts_6h=0,own_inquiries_6h=10000,
               estimated_sale_likelihood=.8)]
  policy={'max_exposed_units':20,'max_sku_share':.2,'max_anonymous_inquiries':10,'min_trial_units':2}
  plan=allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)
  self.assertLessEqual(plan[0]['qty'],4)
  self.assertEqual(plan[0]['evidence']['trusted_inquiries_6h'],2)
  full={'units':[{'sku':'a'} for _ in range(4)]}
  self.assertEqual(allocation_plan(ranked,full,10000,aggression=4,policy=policy),[])
 def test_portfolio_breadth_scales_past_old_fixed_250_unit_limit(self):
  ranked=[dict(sku=f's{i}',expected_net=5000,score=3,upper_cost=100,
               sales_1h=8,sales_6h=12,distinct_buyers_6h=4,
               own_stockouts_6h=0,own_inquiries_6h=0,estimated_sale_likelihood=.8)
          for i in range(100)]
  plan=allocation_plan(ranked,{'units':[]},100000,aggression=100)
  self.assertEqual(len(plan),100)
  self.assertEqual(sum(row['qty'] for row in plan),300)
 def test_live_competitor_breadth_floor_prioritizes_new_sku(self):
  ranked=[dict(sku='hot',expected_net=10000,score=100,upper_cost=100,
               sales_1h=20,sales_6h=30,distinct_buyers_6h=5,estimated_sale_likelihood=1),
          dict(sku='new',expected_net=100,score=1,upper_cost=100,
               sales_1h=2,sales_6h=4,distinct_buyers_6h=2,estimated_sale_likelihood=.5)]
  inv={'units':[{'sku':'hot','cost':100}]}
  policy={'breadth_target_skus':2,'max_units_per_sku':3,'new_sku_bonus':1}
  self.assertEqual(allocation_plan(ranked,inv,10000,policy=policy)[0]['sku'],'new')
  policy['breadth_target_skus']=1
  self.assertEqual(allocation_plan(ranked,inv,10000,policy=policy)[0]['sku'],'hot')
 def test_breadth_expansion_blocks_paid_sku_for_same_supply_window(self):
  rows=[{'sku':'new-a','status':'paid','created':101},
        {'sku':'old','status':'paid','created':99},
        {'sku':'refused','status':'refused','created':102}]
  inv={'units':[{'sku':'held'}]}
  self.assertEqual(breadth_window_blocks(rows,100,inv,3),{'new-a'})
  self.assertEqual(breadth_window_blocks(rows,100,inv,1),set())
 def test_expensive_single_unit_cannot_bypass_portfolio_share(self):
  ranked=[dict(sku='expensive',expected_net=500000,expected_revenue=1000000,score=999,
               upper_cost=600000,sales_1h=8,sales_6h=12,distinct_buyers_6h=4,
               estimated_sale_likelihood=.8)]
  policy={'max_units_per_sku':3,'initial_trial_units':3,'max_sku_capital_share':.08}
  plan=allocation_plan(ranked,{'units':[]},1000000,aggression=4,policy=policy)
  self.assertEqual(plan,[])
 def test_accepted_supply_records_actual_cost_even_above_estimate(self):
  self.assertEqual(supply_result(503,{},100),('uncertain',None))
  self.assertEqual(supply_result(200,{'total':101},100),('paid',101))
  self.assertEqual(supply_result(200,{'total':90},100),('paid',90))
  self.assertEqual(supply_result(200,{},100),('reconcile',None))
 def test_stale_unknown_is_preserved_once_and_never_retried(self):
  import sqlite3
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
  db.execute('CREATE TABLE purchases(id INTEGER PRIMARY KEY,campaign TEXT,sku TEXT,upper_cost INTEGER,cost INTEGER,status TEXT,created REAL,detail TEXT)')
  db.executemany('INSERT INTO purchases(campaign,sku,upper_cost,status,created,detail) VALUES(?,?,?,?,?,?)',[
   ('c','old',100,'uncertain',100,'timeout'),('c','fresh',200,'sent',390,None)])
  rows=preserve_stale_pending(db,'c',now=400,hold=300)
  self.assertEqual([r['sku'] for r in rows],['old'])
  self.assertEqual(db.execute("SELECT status FROM purchases WHERE sku='old'").fetchone()[0],'preserved')
  self.assertEqual(db.execute("SELECT status FROM purchases WHERE sku='fresh'").fetchone()[0],'sent')
  self.assertEqual(preserve_stale_pending(db,'c',now=800,hold=300)[0]['sku'],'fresh')
 def test_missing_registration_snapshot_is_retryable_read_failure(self):
  attempts=[]
  def registration():
   attempts.append(1)
   return own_registration([] if len(attempts)<3 else [{'name':'25512088'}])
  self.assertEqual(retry_read(registration,attempts=3,sleep=lambda _:None)['name'],'25512088')
  self.assertEqual(len(attempts),3)
 def test_board_card_is_authoritative_registration_url(self):
  body={'shops':[{'name':'25512088','url':'stale','card':{'url':'https://live.example'}}]}
  self.assertEqual(own_board_registration(body)['url'],'https://live.example')
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
