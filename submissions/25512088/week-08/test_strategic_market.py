import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from strategic_market import rank_products,owned_targets,restrict_order,snapshot,own_goods_hazard

class StrategyTests(unittest.TestCase):
 def test_mature_own_breakage_cohort_replaces_conservative_prior(self):
  hazard,basis=own_goods_hazard({'shops':[{'name':'25512088','claims':139,'broken':11}]})
  self.assertAlmostEqual(hazard,13/143);self.assertIn('139',basis)
  self.assertEqual(own_goods_hazard({'shops':[{'name':'25512088','claims':10,'broken':0}]})[0],.15)
 def test_selection_waits_until_three_minutes_before_supply(self):
  self.assertIsNone(snapshot('/must-not-be-created',{},time.time()+1000,'https://unused'))
 def test_top10_accepts_evidence_backed_food_and_prefers_short_supply(self):
  now=time.time();cat={str(i):dict(kind='goods',name=str(i),list_price=100000,category='c') for i in range(15)}
  cat['food']=dict(kind='food',name='food',list_price=900000,category='f')
  trades=[dict(sku=k,price=95000,warranty=0 if k=='food' else 6,hours=12 if k=='food' else None,
               honest=1,grade=g,ts=now-100,buyer='b'+str(n)) for k in cat for n,g in enumerate('ABCAB')]
  for row in trades:
   if row['sku']=='food':row['price']=850000
  shops=[dict(name='other',url='other',offers=[{'sku':'0','qty':100}]),dict(name='25512088',url='own',offers=[{'sku':'1','qty':100}])]
  rows=rank_products(cat,trades,shops,now=now)
  self.assertEqual(len(rows),10)
  self.assertNotIn('0',[r['sku'] for r in rows])
  self.assertTrue(any(r['sku']=='food' for r in rank_products(cat,trades,shops,now=now)))
  self.assertEqual(next(r for r in rows if r['sku']=='1')['competitor_same_units'],0)
  self.assertEqual(rank_products(cat,trades[:1],shops),[])
 def test_active_distinct_buyers_and_substitute_competition_are_explicit(self):
  now=time.time();cat={'x':dict(kind='goods',name='x',list_price=100000,category='chairs'),
   'y':dict(kind='goods',name='y',list_price=110000,category='chairs')}
  trades=[dict(sku='x',price=95000,warranty=6,honest=1,grade=g,ts=now-60,buyer=b)
          for g,b in [('A','alive'),('B','recent'),('C','alive')]]
  rows=rank_products(cat,trades,[dict(name='rival',url='r',offers=[dict(sku='y',qty=4)])],alive_buyers={'alive'},now=now)
  x=next(r for r in rows if r['sku']=='x')
  self.assertEqual(x['distinct_buyers_1h'],2);self.assertEqual(x['alive_recent_buyers_6h'],1)
  self.assertEqual(x['active_preference_buyers'],1)
  self.assertEqual(x['preference_repeat_signals'],2)
  self.assertEqual(x['competitor_substitute_units'],4)
  self.assertEqual(x['exact_grade_warranty_trades'],3)
 def test_high_price_is_not_gate_and_negative_net_is_not_forced(self):
  now=time.time();cat={'low':dict(kind='goods',name='low',list_price=100000,category='c'),
   'high':dict(kind='goods',name='high',list_price=1000000,category='h')}
  good=[dict(sku='low',price=95000,warranty=6,honest=1,grade=g,ts=now-10,buyer='b'+g) for g in 'ABC']
  bad=[dict(sku='high',price=100000,warranty=6,honest=1,grade=g,ts=now-10,buyer='h'+g) for g in 'ABC']
  rows=rank_products(cat,good+bad,[],now=now)
  self.assertEqual([r['sku'] for r in rows],['low'])
  self.assertGreater(rows[0]['expected_net'],0)
 def test_buyer_intersection_held_paid_not_signed_including_food(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);inv=dict(round=3,units=[dict(unit='u',sku='X',kind='goods',grade='A'),dict(unit='v',sku='Y',kind='goods',grade='B')])
   (p/'inventory.json').write_text(json.dumps(inv))
   with sqlite3.connect(p/'procurement.sqlite3') as db:
    db.execute('create table purchases(sku,status)');db.executemany('insert into purchases values(?,?)',[('X','paid'),('Y','sent')])
   self.assertEqual(owned_targets(p),{'X'})
   self.assertEqual(restrict_order(dict(kind='goods',acceptable=['X','Z']),p)['acceptable'],['X'])
   self.assertEqual(restrict_order(dict(kind='food',acceptable=['X']),p)['acceptable'],['X'])
   self.assertEqual(owned_targets(p,time.time()+130),set())
   with sqlite3.connect(p/'referrals.sqlite3') as db:
    db.execute('create table sales(unit)');db.execute("insert into sales values('u')")
   self.assertEqual(owned_targets(p),set())
if __name__=='__main__':unittest.main()
