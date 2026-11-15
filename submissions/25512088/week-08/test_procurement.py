import math
import json
import tempfile
import unittest
from network_reads import retry_read
from seller_procurement import choose,allocation_plan,capital_available,supply_result,own_registration,own_board_registration,retry_join,join_current_registration,preserve_stale_pending,breadth_window_blocks,refresh_window_blocks,competitor_breadth_target,warranty_risk_reserve,observed_warranty_hazard,active_warranty_amounts,supply_interval_wait,own_sales_evidence,recent_refund_cooldown_skus,mature_refund_risk_skus,own_a_conversion_evidence,own_quote_conversion_evidence
from seller_market import dominant_goods_profit_probability
class PurchaseTests(unittest.TestCase):
 def test_successful_supply_cooldown_avoids_predictable_rate_limit(self):
  self.assertAlmostEqual(supply_interval_wait(100,101,10.05),9.05)
  self.assertEqual(supply_interval_wait(100,110.05,10.05),0)
  self.assertEqual(supply_interval_wait(0,101,10.05),0)
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
 def test_warranty_reserve_covers_single_claim_and_bounded_tail(self):
  policy={'warranty_reserve_hazard':.15,'warranty_reserve_sigma':4}
  self.assertEqual(warranty_risk_reserve([2000000],policy),2000000)
  amounts=[2000000,1000000]+[200000 for _ in range(50)]
  reserve=warranty_risk_reserve(amounts,policy)
  self.assertGreaterEqual(reserve,max(amounts))
  self.assertLess(reserve,sum(amounts))
  expected=.15*sum(amounts)
  deviation=(.15*.85*sum(value*value for value in amounts))**.5
  self.assertEqual(reserve,math.ceil(expected+4*deviation))
 def test_warranty_hazard_uses_only_fresh_mature_official_cohort(self):
  policy={'warranty_reserve_hazard':.15,'warranty_hazard_min_claims':200,
          'warranty_hazard_max_age_seconds':90,'warranty_hazard_wilson_z':3.29,
          'warranty_hazard_safety_multiplier':1.5,'warranty_hazard_floor':.08}
  with tempfile.TemporaryDirectory() as root:
   path=root+'/report.json'
   def write(at,claims,broken):
    with open(path,'w') as f:json.dump({'at':at,'board':{'shops':[
      {'name':'25512088','claims':claims,'broken':broken}]}},f)
   write(1000,1306,33)
   self.assertEqual(observed_warranty_hazard(path,policy,now=1010),.08)
   write(1000,199,0)
   self.assertEqual(observed_warranty_hazard(path,policy,now=1010),.15)
   write(900,1306,33)
   self.assertEqual(observed_warranty_hazard(path,policy,now=1010),.15)
 def test_warranty_hazard_uses_fresh_official_intel_during_report_handoff(self):
  import sqlite3
  policy={'warranty_reserve_hazard':.15,'warranty_hazard_min_claims':200,
          'warranty_hazard_max_age_seconds':90,'warranty_hazard_wilson_z':3.29,
          'warranty_hazard_safety_multiplier':1.25,'warranty_hazard_floor':.04}
  with tempfile.TemporaryDirectory() as root:
   path=root+'/market-report.json'
   with open(path,'w') as f:json.dump({'at':1000,'board':{'shops':[
     {'name':'25512088','claims':0,'broken':0}]}},f)
   db=sqlite3.connect(root+'/market_intel.sqlite3')
   db.execute('create table sources(name text primary key,ts real,body text)')
   board={'shops':[{'name':'25512088','claims':3304,'broken':72}]}
   db.execute('insert into sources values(?,?,?)',('board',1005,json.dumps(board)))
   db.commit();db.close()
   self.assertEqual(observed_warranty_hazard(path,policy,now=1010),.04)
 def test_active_warranty_reconstructs_legacy_expansion_and_new_six_hours(self):
  import sqlite3
  db=sqlite3.connect(':memory:')
  db.execute('create table sales(task text,sku text,settled real,refunded integer)')
  db.execute('create table warranties(task text,amount integer,issued real)')
  # The sixth old sale had five predecessors, so the old code granted 24 h.
  for n in range(6):
   db.execute('insert into sales values(?,?,?,0)',(f'old{n}','sku',100+n))
   db.execute('insert into warranties values(?,?,?)',(f'old{n}',1000,100+n))
  db.execute('insert into sales values(?,?,?,0)',('new','sku',1001))
  db.execute('insert into warranties values(?,?,?)',('new',2000,1001))
  policy={'fixed_warranty_hours_at':1000}
  amounts=active_warranty_amounts(db,policy,now=100+10*3600)
  self.assertEqual(amounts,[1000])
 def test_refunded_sale_is_negative_principal_in_own_evidence(self):
  import sqlite3
  db=sqlite3.connect(':memory:')
  db.execute('create table sales(sku text,amount integer,cost integer,reward integer,settled real,refunded integer)')
  db.executemany('insert into sales values(?,?,?,?,?,?)',[
   ('x',120,100,0,1000,0),('x',120,100,0,1000,1)])
  recent,total,gross,profitable=own_sales_evidence(db,now=1100)
  self.assertEqual(recent,{'x':1})
  self.assertEqual(total,{'x':2})
  self.assertEqual(gross,{'x':-80})
  self.assertEqual(profitable,{'x':1})
 def test_recent_official_refund_cools_only_matching_local_sku(self):
  import sqlite3
  local=sqlite3.connect(':memory:')
  local.execute('create table sales(sku text,unit text,refunded integer)')
  local.executemany('insert into sales values(?,?,?)',[
   ('ours','unit-ours',1),('old','unit-old',1),('not-refunded','unit-safe',0)])
  with tempfile.TemporaryDirectory() as root:
   path=root+'/intel.sqlite3';intel=sqlite3.connect(path)
   intel.execute('create table breakages(unit text,refunded integer,ts real)')
   intel.executemany('insert into breakages values(?,?,?)',[
    ('unit-ours',100,990),('unit-foreign',100,995),('unit-old',100,700)])
   intel.commit();intel.close()
   self.assertEqual(recent_refund_cooldown_skus(
    local,path,now=1000,cooldown_hours=.05),{'ours'})
   self.assertEqual(recent_refund_cooldown_skus(
    local,path,now=1000,cooldown_hours=0),set())
 def test_repeated_official_refunds_extend_only_that_skus_cooldown(self):
  import sqlite3
  local=sqlite3.connect(':memory:')
  local.execute('create table sales(sku text,unit text,refunded integer)')
  local.executemany('insert into sales values(?,?,?)',[
   ('repeat','unit-a',1),('repeat','unit-b',1),
   ('isolated','unit-c',1),('not-refunded','unit-d',0)])
  with tempfile.TemporaryDirectory() as root:
   path=root+'/intel.sqlite3';intel=sqlite3.connect(path)
   intel.execute('create table breakages(unit text,refunded integer,ts real)')
   # All events are older than the 180-second ordinary cooldown.  Only the
   # SKU with two official failures remains blocked by the 24-hour window.
   intel.executemany('insert into breakages values(?,?,?)',[
    ('unit-a',100,700),('unit-b',100,710),
    ('unit-c',100,720),('unit-d',100,730)])
   intel.commit();intel.close()
   self.assertEqual(recent_refund_cooldown_skus(
    local,path,now=1000,cooldown_hours=.05,
    repeat_cooldown_hours=24,repeat_refunds=2),{'repeat'})
 def test_mature_repeat_refund_veto_requires_low_book_yield(self):
  import sqlite3
  db=sqlite3.connect(':memory:')
  db.execute('create table sales(sku text,amount integer,cost integer,reward integer,refunded integer)')
  # Both cohorts are mature and have repeated refunds. Only the cohort whose
  # refund-aware Book yield has fallen below 3% of principal is vetoed.
  for sku,normal_margin in [('thin',2),('healthy',20)]:
   db.executemany('insert into sales values(?,?,?,?,?)',
    [(sku,100+normal_margin,100,0,0) for _ in range(18)]+
    [(sku,100,100,0,1) for _ in range(2)])
  self.assertEqual(mature_refund_risk_skus(
   db,min_sales=20,min_refunds=2,min_book_margin=.03),{'thin'})
  self.assertEqual(mature_refund_risk_skus(
   db,min_sales=21,min_refunds=2,min_book_margin=.03),set())
 def test_a_conversion_evidence_and_veto_use_rolling_structured_events(self):
  import sqlite3
  with tempfile.TemporaryDirectory() as root:
   path=root+'/adaptive.sqlite3';db=sqlite3.connect(path)
   db.execute('create table runtime_events(kind text,body text,correlation text,ts real)')
   for n in range(10):
    task=f'q{n}';body=json.dumps({'task':task,'sku':'low','grade':'A'})
    db.execute('insert into runtime_events values(?,?,?,?)',('quote_created',body,task,1000))
   for n in range(2):
    task=f'h{n}';body=json.dumps({'task':task,'sku':'sample','grade':'A'})
    db.execute('insert into runtime_events values(?,?,?,?)',('quote_created',body,task,1000))
    db.execute('insert into runtime_events values(?,?,?,?)',
               ('sale_signed',json.dumps({'task':task}),task,1001))
   for n in range(2):
    task=f'b{n}';body=json.dumps({'task':task,'sku':'low','grade':'B'})
    db.execute('insert into runtime_events values(?,?,?,?)',('quote_created',body,task,1000))
    db.execute('insert into runtime_events values(?,?,?,?)',
               ('sale_signed',json.dumps({'task':task}),task,1001))
   db.commit();db.close()
   evidence=own_a_conversion_evidence(path,now=1100,hours=1)
   self.assertEqual(evidence['low'],{'quotes':10,'signed':0,'rate':0})
   self.assertEqual(evidence['sample'],{'quotes':2,'signed':2,'rate':1})
   all_evidence=own_quote_conversion_evidence(path,now=1100,hours=1)
   self.assertEqual(all_evidence['low'],{'quotes':12,'signed':2,'rate':1/6})
  base=dict(kind='goods',expected_net=50000,score=3,upper_cost=100,
            sales_1h=8,sales_6h=12,distinct_buyers_6h=4,
            estimated_sale_likelihood=.8,dominant_grade_samples=10,
            dominant_grade_profit_probability=.9,own_sales_total=5,
            own_profitable_sales=5,own_gross_total=1000)
  ranked=[dict(base,sku='foodkeep',kind='food',own_a_quotes=10,
               own_a_conversion_rate=0,own_quotes=12,own_conversion_rate=1/6),
          dict(base,sku='foodlow',kind='food',own_quotes=10,own_conversion_rate=.09),
          dict(base,sku='goodslow',own_a_quotes=10,own_a_conversion_rate=.09,
               own_quotes=20,own_conversion_rate=.8),
          dict(base,sku='sample',own_a_quotes=9,own_a_conversion_rate=0)]
  policy={'max_sku_capital_share':1,'min_own_a_conversion_samples':10,
          'min_own_a_conversion_rate':.10}
  self.assertEqual([x['sku'] for x in allocation_plan(
   ranked,{'units':[]},10000,policy=policy)],['foodkeep','sample'])
 def test_mature_negative_local_goods_cohort_blocks_restock(self):
  ranked=[dict(sku='lossy',kind='goods',expected_net=500000,score=999,
               upper_cost=100000,sales_1h=20,sales_6h=50,distinct_buyers_6h=10,
               own_sales_1h=5,own_sales_total=11,own_gross_total=-206555,
               own_profitable_sales=9,dominant_grade_samples=50,
               dominant_grade_profit_probability=.99,estimated_sale_likelihood=.9)]
  policy={'max_sku_capital_share':1,'min_own_loss_veto_sales':5}
  self.assertEqual(allocation_plan(ranked,{'units':[]},1000000,policy=policy),[])
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

 def test_goods_procurement_requires_dominant_a_grade_profit_evidence(self):
  base=dict(kind='goods',expected_net=50000,score=3,upper_cost=100,
            sales_1h=8,sales_6h=12,distinct_buyers_6h=4,
            estimated_sale_likelihood=.8,dominant_grade_samples=10)
  ranked=[dict(base,sku='risky',dominant_grade_profit_probability=.69),
          dict(base,sku='safe',dominant_grade_profit_probability=.70)]
  policy={'max_sku_capital_share':1,'min_goods_a_profit_samples':3,
          'min_goods_a_profit_probability':.70}
  plan=allocation_plan(ranked,{'units':[]},10000,policy=policy)
  self.assertEqual([row['sku'] for row in plan],['safe'])
  ranked[0].update(own_sales_total=10,own_profitable_sales=9,own_gross_total=1000)
  plan=allocation_plan(ranked,{'units':[]},10000,policy=policy)
  self.assertIn('risky',[row['sku'] for row in plan])
  self.assertAlmostEqual(dominant_goods_profit_probability(10000,6969,0),.70)
 def test_food_cap_remains_two_even_with_fast_hourly_sales(self):
  ranked=[dict(sku='food',kind='food',expected_net=50000,score=3,upper_cost=100,
               sales_1h=12,sales_6h=20,distinct_buyers_6h=4,estimated_sale_likelihood=.8)]
  policy={'max_food_units_per_sku':2,'max_sku_capital_share':1}
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],2)
  ranked[0]['sales_1h']=30
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],2)
  ranked[0]['sales_1h']=11
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],2)
  over={'units':[{'sku':'food','kind':'food','cost':100} for _ in range(3)]}
  self.assertEqual(allocation_plan(ranked,over,10000,aggression=4,policy=policy),[])
 def test_repeated_own_food_stockouts_do_not_break_two_unit_cap(self):
  ranked=[dict(sku='food',kind='food',expected_net=50000,score=3,upper_cost=100,
               sales_1h=4,sales_6h=8,distinct_buyers_6h=4,
               own_stockouts_6h=2,estimated_sale_likelihood=.8)]
  policy={'max_food_units_per_sku':2,'max_sku_capital_share':1}
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],2)
  ranked[0]['own_stockouts_6h']=1
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],2)
 def test_fast_goods_remain_at_three_unit_absolute_cap(self):
  ranked=[dict(sku='goods',kind='goods',expected_net=50000,score=3,upper_cost=100,
               sales_1h=6,own_sales_1h=6,sales_6h=12,distinct_buyers_6h=4,estimated_sale_likelihood=.8)]
  policy={'max_units_per_sku':3,'max_sku_capital_share':1}
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],3)
  ranked[0]['own_sales_1h']=5
  self.assertEqual(allocation_plan(ranked,{'units':[]},10000,aggression=4,policy=policy)[0]['qty'],3)
  over={'units':[{'sku':'goods','kind':'goods','cost':100} for _ in range(5)]}
  self.assertEqual(allocation_plan(ranked,over,10000,aggression=4,policy=policy),[])
 def test_cold_goods_capital_pauses_only_unproven_goods_trials(self):
  common=dict(expected_net=5000,score=3,upper_cost=100,sales_1h=8,sales_6h=12,
              distinct_buyers_6h=4,estimated_sale_likelihood=.8)
  ranked=[dict(common,sku='cold-new',kind='goods',own_sales_1h=0),
          dict(common,sku='proven',kind='goods',own_sales_1h=2),
          dict(common,sku='historical-proven',kind='goods',own_sales_1h=0,own_sales_total=1),
          dict(common,sku='food',kind='food',own_sales_1h=0)]
  inventory={'units':[{'sku':'old-cold','kind':'goods','cost':800}]}
  policy={'max_cold_goods_capital_share':.30,'cold_goods_min_own_sales_1h':1,
          'max_sku_capital_share':1}
  plan=allocation_plan(ranked,inventory,1000,policy=policy)
  self.assertNotIn('cold-new',[row['sku'] for row in plan])
  self.assertIn('proven',[row['sku'] for row in plan])
  self.assertIn('historical-proven',[row['sku'] for row in plan])
  self.assertIn('food',[row['sku'] for row in plan])
  inventory={'units':[{'sku':'old-cold','kind':'goods','cost':100}]}
  plan=allocation_plan(ranked,inventory,1000,policy=policy)
  self.assertIn('cold-new',[row['sku'] for row in plan])
 def test_cold_goods_allows_one_new_dominant_safe_market_trial(self):
  common=dict(expected_net=100,score=100,upper_cost=100,sales_1h=3,sales_6h=9,
              distinct_buyers_6h=3,estimated_sale_likelihood=1,own_sales_1h=0,
              dominant_grade_samples=3,dominant_grade_profit_probability=.7)
  ranked=[dict(common,sku='safe-new',kind='goods'),
          dict(common,sku='unsafe-new',kind='goods',dominant_grade_profit_probability=.69)]
  inventory={'units':[{'sku':'old','kind':'goods','cost':900}]}
  policy={'max_cold_goods_capital_share':.30,'cold_goods_min_own_sales_1h':1,
          'min_goods_a_profit_samples':3,'min_goods_a_profit_probability':.65,
          'cold_safe_trial_sales_1h':3,'cold_safe_trial_profit_probability':.7,
          'max_cold_safe_trial_units':1,'max_sku_capital_share':1,
          'max_units_per_sku':3}
  plan=allocation_plan(ranked,inventory,1000,aggression=4,policy=policy)
  self.assertEqual([(r['sku'],r['qty']) for r in plan],[('safe-new',1)])
  inventory['units'].append({'sku':'safe-new','kind':'goods','cost':100})
  self.assertEqual(allocation_plan(ranked,inventory,1000,aggression=4,policy=policy),[])
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
 def test_live_competitor_breadth_is_bonus_not_absolute_lane(self):
  ranked=[dict(sku='hot',expected_net=10000,score=100,upper_cost=100,
               sales_1h=20,sales_6h=30,distinct_buyers_6h=5,estimated_sale_likelihood=1),
          dict(sku='new',expected_net=100,score=1,upper_cost=100,
               sales_1h=2,sales_6h=4,distinct_buyers_6h=2,estimated_sale_likelihood=.5)]
  inv={'units':[{'sku':'hot','cost':100}]}
  policy={'breadth_target_skus':2,'max_units_per_sku':3,'new_sku_bonus':5}
  first=allocation_plan(ranked,inv,10000,policy=policy)[0]
  self.assertEqual(first['sku'],'hot')
  ranked[0]['score']=2
  first=allocation_plan(ranked,inv,10000,policy=policy)[0]
  self.assertEqual((first['sku'],first['qty']),('new',1))
  policy['breadth_target_skus']=1
  self.assertEqual(allocation_plan(ranked,inv,10000,policy=policy)[0]['sku'],'hot')
 def test_capital_risk_exponent_can_make_revenue_opportunity_compete(self):
  common=dict(kind='food',expected_net=1,sales_1h=8,sales_6h=12,
              distinct_buyers_6h=4,estimated_sale_likelihood=.8)
  ranked=[dict(common,sku='cheap',score=1,upper_cost=100),
          dict(common,sku='revenue',score=28,upper_cost=10000)]
  conservative={'max_sku_capital_share':1,'capital_risk_exponent':.75}
  measured={'max_sku_capital_share':1,'capital_risk_exponent':.70}
  self.assertEqual(allocation_plan(
      ranked,{'units':[]},30000,policy=conservative)[0]['sku'],'cheap')
  self.assertEqual(allocation_plan(
      ranked,{'units':[]},30000,policy=measured)[0]['sku'],'revenue')
 def test_one_grade_proven_high_value_opportunity_gets_window_first_slot(self):
  cheap=dict(sku='cheap',kind='food',expected_net=1000,score=100,
             upper_cost=100,sales_1h=8,sales_6h=12,
             distinct_buyers_6h=4,estimated_sale_likelihood=.8)
  high=dict(sku='high',kind='goods',expected_net=50000,score=1000,
            upper_cost=600000,sales_1h=1,sales_6h=6,
            distinct_buyers_6h=4,estimated_sale_likelihood=.5,
            own_sales_total=1,own_gross_total=100000,own_profitable_sales=1,
            dominant_grade_samples=4,dominant_grade_profit_probability=.75)
  policy={'max_sku_capital_share':1,'high_value_lane_min_unit_cost':500000,
          'min_proven_scale_grade_samples':3,
          'min_proven_scale_grade_profit_probability':.70}
  plan=allocation_plan([cheap,high],{'units':[],'balance':1000000},1000000,
                       policy=policy)
  self.assertEqual(plan[0]['sku'],'high')
  self.assertTrue(plan[0]['evidence']['high_value_priority'])
  policy['high_value_window_used']=True
  self.assertEqual(allocation_plan(
      [cheap,high],{'units':[],'balance':1000000},1000000,
      policy=policy)[0]['sku'],'cheap')
  policy['high_value_window_used']=False
  high['dominant_grade_profit_probability']=.69
  self.assertEqual(allocation_plan(
      [cheap,high],{'units':[],'balance':1000000},1000000,
      policy=policy)[0]['sku'],'cheap')
 def test_evidence_backed_policy_can_open_exactly_three_high_value_slots(self):
  cheap=dict(sku='cheap',kind='food',expected_net=1000,score=100,
             upper_cost=100,sales_1h=8,sales_6h=12,
             distinct_buyers_6h=4,estimated_sale_likelihood=.8)
  common=dict(kind='goods',expected_net=50000,sales_1h=1,sales_6h=6,
              distinct_buyers_6h=4,estimated_sale_likelihood=.5,
              own_sales_total=1,own_gross_total=100000,own_profitable_sales=1,
              dominant_grade_samples=4,dominant_grade_profit_probability=.75,
              upper_cost=600000)
  high1=dict(common,sku='high1',score=1000)
  high2=dict(common,sku='high2',score=900)
  high3=dict(common,sku='high3',score=800)
  policy={'max_sku_capital_share':1,'high_value_lane_min_unit_cost':500000,
          'high_value_window_limit':3,'high_value_window_count':0,
          'min_proven_scale_grade_samples':3,
          'min_proven_scale_grade_profit_probability':.70}
  plan=allocation_plan([cheap,high1,high2,high3],{'units':[],'balance':2500000},
                       2500000,policy=policy)
  self.assertEqual([row['sku'] for row in plan[:3]],['high1','high2','high3'])
  policy['high_value_window_count']=2
  self.assertEqual(allocation_plan(
      [cheap,high1,high2,high3],{'units':[],'balance':2500000},2500000,
      blocked={'high1','high2'},policy=policy)[0]['sku'],'high3')
  policy['high_value_window_count']=3
  self.assertEqual(allocation_plan(
      [cheap,high1,high2,high3],{'units':[],'balance':2500000},2500000,
      policy=policy)[0]['sku'],'cheap')
 def test_sold_out_proven_sku_scales_during_breadth_expansion(self):
  ranked=[dict(sku='proven',kind='goods',expected_net=10000,score=100,upper_cost=100,
               sales_1h=12,sales_6h=24,own_sales_1h=2,own_sales_total=2,
               distinct_buyers_6h=5,estimated_sale_likelihood=1)]
  inv={'units':[{'sku':'held','cost':100}]}
  policy={'breadth_target_skus':5,'max_units_per_sku':3,
          'max_breadth_trial_units':1,'max_sku_capital_share':1}
  plan=allocation_plan(ranked,inv,10000,aggression=4,policy=policy)
  self.assertEqual([(row['sku'],row['qty']) for row in plan],[('proven',3)])

  ranked[0]['own_sales_1h']=0;ranked[0]['own_sales_total']=0
  self.assertEqual(allocation_plan(ranked,inv,10000,aggression=4,policy=policy)[0]['qty'],1)
 def test_competitor_breadth_target_uses_leader_median(self):
  self.assertEqual(competitor_breadth_target({'competition_breadth':{
      'top10_min_skus':61,'top10_median_skus':131.5}}),132)
  self.assertEqual(competitor_breadth_target({}),0)
 def test_breadth_expansion_blocks_paid_sku_for_same_supply_window(self):
  rows=[{'sku':'new-a','status':'paid','created':101},
        {'sku':'old','status':'paid','created':99},
        {'sku':'refused','status':'refused','created':102}]
  inv={'units':[{'sku':'held'}]}
  self.assertEqual(breadth_window_blocks(rows,100,inv,3),{'new-a'})
  self.assertEqual(breadth_window_blocks(rows,100,inv,1),{'new-a'})
 def test_new_window_clears_old_blocks_but_never_pending_unknown_posts(self):
  self.assertEqual(refresh_window_blocks({'old-refusal','pending'},100,200,{'pending'}),
                   {'pending'})
  self.assertEqual(refresh_window_blocks({'same-window'},200,200,{'pending'}),
                   {'same-window','pending'})
 def test_expensive_single_unit_cannot_bypass_portfolio_share(self):
  ranked=[dict(sku='expensive',expected_net=500000,expected_revenue=1000000,score=999,
               upper_cost=600000,sales_1h=8,sales_6h=12,distinct_buyers_6h=4,
               estimated_sale_likelihood=.8)]
  policy={'max_units_per_sku':3,'initial_trial_units':3,'max_sku_capital_share':.08}
  plan=allocation_plan(ranked,{'units':[]},1000000,aggression=4,policy=policy)
  self.assertEqual(plan,[])
 def test_only_proven_profitable_goods_receive_larger_capital_share(self):
  common=dict(kind='goods',expected_net=100000,score=1000,upper_cost=900000,
              sales_1h=3,sales_6h=6,distinct_buyers_6h=3,
              estimated_sale_likelihood=1,dominant_grade_samples=3,
              dominant_grade_profit_probability=.8)
  policy={'max_units_per_sku':3,'max_sku_capital_share':.10,
          'max_proven_sku_capital_share':.20,
          'min_goods_a_profit_samples':3,
          'min_goods_a_profit_probability':.60,
          'min_proven_scale_grade_samples':3,
          'min_proven_scale_grade_profit_probability':.70}
  cold=allocation_plan([dict(common,sku='cold',own_sales_total=0)],
                       {'units':[],'balance':10000000},10000000,policy=policy)
  proven=allocation_plan([dict(common,sku='proven',own_sales_total=3,
                               own_sales_1h=3,own_gross_total=300000,
                               own_profitable_sales=3)],
                         {'units':[],'balance':10000000},10000000,policy=policy)
  self.assertEqual([(row['sku'],row['qty']) for row in cold],[('cold',1)])
  self.assertEqual([(row['sku'],row['qty']) for row in proven],[('proven',2)])
  marginal=dict(common,sku='marginal',own_sales_total=17,own_sales_1h=4,
                own_gross_total=1853251,own_profitable_sales=16,
                dominant_grade_samples=15,dominant_grade_profit_probability=.5313)
  policy['high_value_lane_min_unit_cost']=500000
  limited=allocation_plan([marginal],{'units':[],'balance':10000000},10000000,
                          policy=policy)
  self.assertEqual(limited,[])
  marginal['upper_cost']=400000
  limited=allocation_plan([marginal],{'units':[],'balance':10000000},10000000,
                          policy=policy)
  self.assertEqual([(row['sku'],row['qty']) for row in limited],[('marginal',2)])
  self.assertTrue(limited[0]['evidence']['own_profit_proven'])
  self.assertFalse(limited[0]['evidence']['proven_scale_ready'])
 def test_active_unproven_good_blocks_another_cold_safe_trial(self):
  held=dict(sku='held',kind='goods',expected_net=0,score=0,upper_cost=100,
            sales_1h=0,sales_6h=1,own_sales_total=0,distinct_buyers_6h=2,
            dominant_grade_samples=3,dominant_grade_profit_probability=.8,
            estimated_sale_likelihood=.5)
  candidate=dict(sku='candidate',kind='goods',expected_net=100,score=100,upper_cost=100,
                 sales_1h=3,sales_6h=6,own_sales_total=0,distinct_buyers_6h=3,
                 dominant_grade_samples=3,dominant_grade_profit_probability=.8,
                 estimated_sale_likelihood=.5)
  inventory={'balance':1000,'units':[{'sku':'held','kind':'goods','cost':100}]}
  policy={'max_cold_goods_capital_share':0,'cold_safe_trial_sales_1h':3,
          'cold_safe_trial_profit_probability':.7,'max_units_per_sku':3,
          'max_sku_capital_share':1}
  self.assertEqual(allocation_plan([held,candidate],inventory,1000,policy=policy),[])
  held['own_sales_total']=1
  self.assertEqual(allocation_plan([held,candidate],inventory,1000,policy=policy)[0]['sku'],
                   'candidate')
 def test_concentration_uses_total_wealth_not_cash_after_liability_reserve(self):
  ranked=[dict(sku='high',kind='goods',expected_net=500000,score=999,
               upper_cost=1749000,sales_1h=3,sales_6h=20,distinct_buyers_6h=8,
               own_sales_1h=1,estimated_sale_likelihood=.6)]
  inventory={'balance':6500000,'units':[{'sku':'old','cost':13000000}]}
  policy={'max_units_per_sku':3,'max_sku_capital_share':.10}
  plan=allocation_plan(ranked,inventory,1950000,aggression=2,policy=policy)
  self.assertEqual([(row['sku'],row['qty']) for row in plan],[('high',1)])
 def test_recent_realized_profit_prioritizes_a_proven_sold_out_good(self):
  common=dict(kind='goods',expected_net=1000,upper_cost=100,sales_1h=2,
              sales_6h=6,distinct_buyers_6h=3,estimated_sale_likelihood=1,
              dominant_grade_samples=3,dominant_grade_profit_probability=.8)
  ranked=[dict(common,sku='market-prior',score=1000),
          dict(common,sku='realized',score=100,own_sales_1h=2,own_sales_total=10,
               own_gross_total=10000,own_profitable_sales=9)]
  policy={'max_sku_capital_share':1,'max_units_per_sku':3}
  plan=allocation_plan(ranked,{'units':[],'balance':10000},10000,policy=policy)
  self.assertEqual(plan[0]['sku'],'realized')
  self.assertEqual(plan[0]['evidence']['realized_hourly_contribution'],2000)
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
