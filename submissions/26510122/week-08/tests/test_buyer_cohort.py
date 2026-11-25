import copy,json,tempfile,time,unittest
from pathlib import Path
from buyer_memory import PreparedHistory
import buyer_cohort as c

class CohortTests(unittest.TestCase):
 def setUp(self):
  self.now=1000000.;self.catalog={'G':{'kind':'goods','name':'Goods','list_price':1000},'F':{'kind':'food','name':'Food','list_price':1000}}
  self.cover={'trusted':True,'since':0,'as_of':self.now}
 def sale(self,i,**kw):
  return {'event':'settled','ledger':i,'order':str(i),'sku':'G','shop_name':'S','at':self.now-7*3600,'price':500,'grade':'A','value':1000,'warranty_hours':0,**kw}
 def fail(self,i,**kw):
  return {'event':'verified_breakage','source':'public_ledger_own_paid_order_sku','settled_ledger':i,'order':str(i),'sku':'G','at':self.now-5*3600,'broke_at':self.now-5*3600,'refund_amount':0,**kw}
 def build(self,rows,notices=(),coverage=None):return c.build(PreparedHistory(rows),self.catalog,notices,now=self.now,coverage=self.cover if coverage is None else coverage)
 def test_only_fixed_mature_cohort_and_unique_paid_ledger_count(self):
  sale=self.sale(1);r=self.build([sale,dict(sale),self.sale(2,at=self.now-2*3600),self.fail(2)])
  self.assertEqual(r['stats']['mature_paid'],1);g=r['groups'][0];self.assertEqual((g['n'],g['net_lower'],g['net_upper'],g['exact']),(1,1.,1.,1))
 def test_exact_failure_and_refund_losses_count_in_denominator(self):
  r=self.build([self.sale(i) for i in range(1,4)]+[self.fail(2),self.fail(3,refund_amount=500)])['groups'][0]
  self.assertEqual((r['n'],r['unrefunded'],r['refunded'],r['net_lower'],r['net_upper'],r['net_scenario']),(3,1,1,0.,0.,0.))
 def test_missing_stream_is_unknown_not_an_intact_positive(self):
  r=self.build([self.sale(1)],coverage={})['groups'][0]
  self.assertEqual((r['unknown'],r['net_lower'],r['net_upper'],r['net_scenario']),(1,-1.,1.,None))
 def test_unmatched_name_notice_does_not_guess_seller(self):
  r=self.build([self.sale(1),self.sale(2,shop_name='Other')],[{'name':'Goods','broke_at':self.now-5*3600}])
  self.assertEqual(r['notice_audit']['unmatched'],1);self.assertTrue(all(g['unknown']==1 and g['unrefunded']==0 for g in r['groups']))
 def test_exact_notice_proof_marks_only_its_actual_order(self):
  r=self.build([self.sale(1),self.sale(2,shop_name='Other'),self.fail(1)],[{'name':'Goods','broke_at':self.now-5*3600}])
  self.assertEqual(r['notice_audit']['matched'],1);g={g['shop']:g for g in r['groups']};self.assertEqual(g['S']['unrefunded'],1);self.assertEqual(g['Other']['net_lower'],1)
 def test_food_horizons_are_separate_and_use_not_current_grade_cost(self):
  rows=[self.sale(1,sku='F',grade='C'),self.sale(2,sku='F',value=0),{'event':'selection_opportunities','at':self.now-7*3600,'order':'1','order_kind':'food','use_within_hours':2},{'event':'selection_opportunities','at':self.now-7*3600,'order':'2','order_kind':'food','use_within_hours':6}]
  gs=self.build(rows)['groups'];self.assertEqual(len(gs),2);a=next(g for g in gs if g['use_within_hours']==2);self.assertAlmostEqual(a['net_lower'],2/3,places=5)
  self.assertEqual(next(g for g in gs if g['use_within_hours']==6)['net_lower'],-1)
 def test_normal_bounds_are_not_labelled_exact_saved(self):
  g=self.build([self.sale(1,price=700)])['groups'][0];self.assertAlmostEqual(g['net_lower'],2/3,places=5);self.assertEqual(g['net_upper'],1);self.assertEqual(g['exact'],0);self.assertNotEqual(g['net_scenario'],g['net_lower'])
 def test_lag_buffer_does_not_mature_a_just_six_hour_purchase(self):
  self.assertFalse(self.build([self.sale(1,at=self.now-6*3600-1)])['groups'])
 def test_sealed_observations_survive_receiver_restart_and_new_proof_overrides(self):
  r=self.build([self.sale(1)])
  g=self.build([self.sale(1)],coverage={'sealed_ledgers':r['sealed_ledgers']})['groups'][0];self.assertEqual(g['net_lower'],1)
  g=self.build([self.sale(1),self.fail(1)],coverage={'sealed_ledgers':r['sealed_ledgers']})['groups'][0];self.assertEqual(g['net_upper'],-1)
 def test_small_sample_has_no_forced_priority_and_price_cover_changes_can_recheck_bad_history(self):
  bad=self.build([x for i in range(1,6) for x in [self.sale(i),self.fail(i)]])
  choice={'shop':'S','sku':'G','price_guidance':{'median_paid':500},'risk_policy':{}}
  r=c.for_choice(bad,choice,{'kind':'goods'},{'saved':.7});self.assertTrue(r['historical_below_current'])
  cheaper={**choice,'price_guidance':{'median_paid':400}};self.assertFalse(c.for_choice(bad,cheaper,{'kind':'goods'},{'saved':.7})['historical_below_current'])
  cover={**choice,'risk_policy':{'required_warranty_hours':6}};self.assertFalse(c.for_choice(bad,cover,{'kind':'goods'},{'saved':.7})['historical_below_current'])
  small=self.build([self.sale(1)]);self.assertFalse(c.for_choice(small,choice,{'kind':'goods'},{'saved':.7})['qualified'])
 def test_cache_version_and_freshness_are_required(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'state';p.mkdir();f=p/'buyer-cohort.json'
   for data,valid in [({'version':c.VERSION,'generated_at':self.now},True),({'version':'old','generated_at':self.now},False),({'version':c.VERSION,'generated_at':self.now-901},False)]:
    f.write_text(json.dumps(data));self.assertEqual(bool(c.load(d,now=self.now)),valid)

 def test_zero_value_goods_without_refund_proof_is_not_invented_unrefunded_loss(self):
  g=self.build([self.sale(1,value=0,warranty_hours=72)])['groups'][0]
  self.assertEqual(g['unknown'],1);self.assertEqual(g['unrefunded'],0)
 def test_large_prices_do_not_round_a_bound_into_exact_one(self):
  p={'kind':'goods','list_price':1000000000};sale=self.sale(1,price=550000001)
  low,high,scenario=c.outcome_bounds(sale,p,None,True)
  self.assertLess(low,1);self.assertEqual(high,1)
 def test_future_breakage_cannot_rewrite_a_past_snapshot(self):
  g=self.build([self.sale(1),self.fail(1,at=self.now+30,broke_at=self.now+30)])['groups'][0]
  self.assertEqual(g['net_lower'],1);self.assertEqual(g['unrefunded'],0)

 def test_optional_old_order_context_is_compact_and_default_reader_unchanged(self):
  from buyer_memory import read_history
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'logs/buyer-events.jsonl';p.parent.mkdir()
   import datetime
   at=datetime.datetime.fromtimestamp(self.now-7*3600,datetime.timezone.utc).isoformat()
   e={'event':'selection_opportunities','at':at,'order':'old','order_kind':'food','use_within_hours':72,'eligible_candidates':[{'irrelevant':'x'*1000}]}
   p.write_text(json.dumps(e)+'\n')
   default=read_history(p,now=self.now);extended=read_history(p,now=self.now,order_age_seconds=c.WINDOW)
   self.assertFalse(any(x['event']=='selection_opportunities' for x in default))
   context=next(x for x in extended if x['event']=='selection_opportunities')
   self.assertEqual(context['use_within_hours'],72);self.assertNotIn('eligible_candidates',context)

 def test_nonfinite_value_is_unknown(self):
  g=self.build([self.sale(1,value=float('nan'))])['groups'][0]
  self.assertEqual(g['unknown'],1);self.assertIsNone(g['net_scenario'])
 def test_mature_reference_can_be_used_without_a_fresh_price_sample(self):
  r=self.build([self.sale(i) for i in range(1,7)])
  e=c.for_choice(r,{'shop':'S','sku':'G','price_guidance':{},'risk_policy':{}},{'kind':'goods'},{'saved':.7})
  self.assertEqual(e['preference'],'positive');self.assertEqual(e['median_paid'],500)


class SelectionIntegrationTests(unittest.TestCase):
 def row(self,i,**kw):
  return {'id':str(i),'shop':f'S{i}','url':f'https://S{i}','sku':'G','stock':1,'list_price':1000,'cap':750,
   'name':'Goods','first_hand':{},'reviews':{'count':0,'average':None,'low_rating_count':0,'examples':[]},
   'score_evidence':{'saved_if_usable_at_cap':.8},'price_guidance':{},'routing_evidence':{},**kw}
 def test_promising_mature_candidates_are_visible_without_deleting_ordinary_pool(self):
  from market_buyer_r3 import shortlist
  rows=[self.row(i) for i in range(20)]
  for row in rows[-4:]:row['mature_outcomes']={'preference':'positive','selection_estimate':.85,'n':10}
  selected=shortlist(rows)
  self.assertEqual(len(selected),16);self.assertEqual({r['id'] for r in selected[:4]},{str(i) for i in range(16,20)})
  self.assertTrue(any(int(r['id'])<16 for r in selected));self.assertEqual(len(rows),20)
 def test_real_candidate_builder_uses_cohort_after_all_existing_checkout_guards(self):
  import tempfile,os,time
  from unittest.mock import patch
  from market_buyer_r3 import candidates
  now=time.time();rows=[{'event':'settled','ledger':i,'order':str(i),'sku':'G','shop_name':'S','at':now-7*3600,'price':500,'grade':'A','value':1000} for i in range(1,7)]
  cache=c.build(PreparedHistory(rows),{'G':{'kind':'goods','name':'Goods','list_price':1000}},[],now=now,coverage={'trusted':True,'since':0,'as_of':now})
  shops=[{'name':'S','url':'https://new','offers':[{'sku':'G','qty':1}]},{'name':'Other','url':'https://other','offers':[{'sku':'G','qty':1}]}]
  order={'order':'new','kind':'goods','acceptable':['G'],'budget':1000}
  with tempfile.TemporaryDirectory() as d:
   previous=Path.cwd();os.chdir(d)
   try:found=candidates(order,shops,{'G':{'kind':'goods','name':'Goods','list_price':1000}},'own',history=PreparedHistory(rows),cohort_cache=cache,full_pool=True,saved_baseline={'saved':.7,'observed_at':now})
   finally:os.chdir(previous)
  own=next(x for x in found if x['shop']=='S');self.assertEqual(own['mature_outcomes']['preference'],'positive');self.assertEqual(own['mature_outcomes']['n'],6)
  self.assertIn('mature_6h',own['routing_evidence']);self.assertEqual(len(found),2)
 def test_sixteen_mature_candidates_fit_and_model_sees_realized_vs_estimated_fields(self):
  from market_buyer_r3 import choose
  from unittest.mock import patch
  rows=[]
  for i in range(16):
   mature={'n':12,'unrefunded':1,'refunded':1,'unknown':0,'net_lower':.5,'net_upper':.75,'net_scenario':.7,'median_paid':600,'cover_share':.5,'use_within_hours':None}
   route={'estimated_saved_per_attempt':.7,'post_sale_breakage':{'same_shop_sku':{'paid':100,'mature_paid':80,'early_broken':0,'broken':1,'refunded':0,'unrefunded_within_6h':1}},'mature_6h':mature}
   rows.append(self.row(i,mature_outcomes=mature,routing_evidence=route))
  with patch('market_buyer_r3.decide_json',return_value={'candidate_id':'0','opening_offer':600,'reason':'mature outcomes'}) as model:
   telemetry={};choose(rows,{'kind':'goods','item':'Goods'},telemetry=telemetry)
  prompt=model.call_args.args[1];self.assertEqual(len(prompt['candidate_shop_skus']),16)
  e=prompt['candidate_shop_skus'][0]['routing_evidence']['mature_6h'];self.assertEqual(e['net_scenario'],.7);self.assertEqual(e['net_range'][0],.5)
  self.assertEqual(len(telemetry['mature_displayed_candidate_ids']),16)
 def test_malformed_cohort_does_not_block_a_candidate(self):
  self.assertEqual(c.for_choice({'groups':[{'broken':'row'}]},self.row(1),{'kind':'goods'}),{})
