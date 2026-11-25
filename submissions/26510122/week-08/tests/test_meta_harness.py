import json,os,random,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import seller_meta as meta
from seller_meta_policy import active,DEFAULT,validate
from procurement_cost import ceiling
from capital_policy import operating_policy,ROUND_END
from market_scoring import VERSION
import inventory_portfolio as portfolio

class MetaHarnessTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=Path.cwd();os.chdir(self.tmp.name)
  self.addCleanup(self.tmp.cleanup);self.addCleanup(os.chdir,self.old)
  self.root=Path.cwd();self.now=10000
  for p in ['.market','state','logs']:(self.root/p).mkdir()
  self.inv={'round':3,'balance':20_000_000,'units':[],'restock':{'open':True,'opens_at':9990,'closes_at':10170}}
  self.cat={'bed':{'name':'Bed','kind':'goods','category':'beds','list_price':4_000_000}}
  (self.root/'.market/catalog.json').write_text(json.dumps(self.cat));self.fresh()
 def fresh(self,now=None):
  t=self.now if now is None else now;p=self.root/'.market/inventory.json';p.write_text(json.dumps(self.inv));os.utime(p,(t,t))
 def decision(self,value=12_000_000):return {'action':'change','parameter':'window_budget','numeric_value':value,'focus_value':None,'reason':'Observe smaller spend','expected_effect':'Controlled experiment','evidence':['risk']}
 def test_high_ticket_candidate_is_affordable_and_surge_is_reserved(self):
  self.assertEqual(ceiling(self.cat['bed']),4_500_000)
  def choose(role,prompt,schema):
   self.assertEqual(prompt['candidates'][0]['sku'],'bed')
   return {'action':'buy','skus':['bed'],'replenishment':[],'reason':'Single high ticket test'}
  result=portfolio.run(self.inv,self.cat,[],self.now,model=choose,rng=random.Random(1),refresh=lambda:(self.inv,self.now,True))
  self.assertEqual(result['buy'],[{'sku':'bed','qty':1}])
  i=json.loads(portfolio.STATE.read_text())['intents'][0];self.assertEqual(i['cost_ceiling'],4_500_000)
 def test_public_watchlist_reaches_transaction_candidates_and_expires(self):
  path=self.root/'state/seller-meta-market.json'
  path.write_text(json.dumps({'at':self.now,'rows':[{'sku':'bed','public_settles':11}]}))
  rows=portfolio.candidates(self.cat,self.inv,[],self.now,set(),random.Random(1))
  self.assertEqual(rows[0]['public_settlements_sample'],11)
  expired=portfolio.candidates(self.cat,self.inv,[],self.now+3601,set(),random.Random(1))
  self.assertEqual(expired[0]['public_settlements_sample'],0)
 def test_single_change_is_active_and_hard_market_cutoff_remains(self):
  r=meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision())
  self.assertEqual(r['status'],'changed');self.assertEqual(operating_policy(self.now)['window_budget'],12_000_000)
  self.assertEqual(operating_policy(ROUND_END)['window_budget'],0)
  self.assertEqual(active(self.now+14401)['meta_status'],'operator_default')
 def test_initial_hold_still_establishes_an_evaluation_baseline(self):
  decision=self.decision();decision['action']='hold'
  meta.review(self.root,now=self.now,public=[],model=lambda *a:decision)
  state=json.loads((self.root/'state/seller-meta-policy.json').read_text())
  self.assertEqual(state['changed_at'],self.now);self.assertEqual(state['baseline']['at'],self.now)
 def test_no_rapid_or_unobserved_retuning(self):
  meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision())
  later=self.now+1801;self.fresh(later)
  r=meta.review(self.root,now=later,public=[],model=lambda *a:self.decision(6_000_000))
  self.assertEqual(r['status'],'evaluation_pending');self.assertEqual(active(later)['window_budget'],12_000_000)
 def test_eligible_market_opportunity_is_not_a_rejection(self):
  data=meta.snapshot(self.root,self.now,[{'kind':'settle','ts':self.now-1,'data':{'sku':'bed','amount':3_000_000}}])
  self.assertEqual(data['candidate_eligibility_counts']['eligible'],1)
  self.assertEqual(data['candidate_rejection_counts'],{})
  self.assertEqual(data['market_opportunities'][0]['blocked_by'],[])
 def test_shadow_does_not_write_active_policy(self):
  r=meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision(),shadow=True)
  self.assertEqual(r['status'],'shadow');self.assertFalse((self.root/'state/seller-meta-policy.json').exists())
 def test_revenue_only_opportunities_survive_bounded_input(self):
  catalog={f'volume-{i}':{'name':f'Volume {i}','kind':'goods','list_price':100} for i in range(20)}
  catalog.update({f'revenue-{i}':{'name':f'Revenue {i}','kind':'goods','list_price':1000} for i in range(20)})
  (self.root/'.market/catalog.json').write_text(json.dumps(catalog))
  public=[]
  for sku in catalog:
   count,price=(3,1) if sku.startswith('volume') else (1,10000)
   public.extend({'kind':'settle','ts':self.now-1,'data':{'sku':sku,'amount':price}} for _ in range(count))
  data=meta.snapshot(self.root,self.now,public)
  shown=data['market_opportunities'];coverage=data['opportunity_input_coverage']
  self.assertEqual(len(shown),20)
  self.assertEqual(sum(r['sku'].startswith('revenue') for r in shown),10)
  self.assertEqual(sum(r['sku'].startswith('volume') for r in shown),10)
  self.assertEqual(coverage['catalog_matched_candidates'],40)
  self.assertEqual(len(coverage['omitted_skus']),20)
  self.assertEqual(data['candidate_eligibility_counts']['eligible'],40)
  self.assertEqual(coverage['displayed_eligibility_counts']['eligible'],20)
  self.assertEqual(shown[1]['selection_sources'],['revenue'])
  self.assertEqual(shown[1]['revenue_rank'],1)
 def test_overlapping_rankings_are_deduplicated_without_losing_coverage(self):
  data=meta.snapshot(self.root,self.now,[{'kind':'settle','ts':self.now,'data':{'sku':'bed','amount':100}}])
  self.assertEqual(len(data['market_opportunities']),1)
  self.assertEqual(data['market_opportunities'][0]['selection_sources'],['volume','revenue'])
  self.assertEqual(data['opportunity_input_coverage']['omitted_skus'],[])
 def test_invalid_bounds_never_become_live_config(self):
  for d in [dict(DEFAULT,epsilon=0),dict(DEFAULT,window_budget=1_000_000_001),dict(DEFAULT,focus='disable guards'),dict(DEFAULT,extra=1)]:
   with self.assertRaises(ValueError):validate(d)
  with self.assertRaises(ValueError):meta.revised(DEFAULT,self.decision(1_200_000))
 def test_evaluation_uses_equal_mature_windows_and_deduplicated_ledgers(self):
  events=[{'event':'settled','at':9000,'ledger':1,'price':100},
          {'event':'settled','at':10500,'ledger':2,'price':200},
          {'event':'settled','at':10501,'ledger':2,'price':200},
          {'event':'settled','at':11900,'ledger':3,'price':999}]
  (self.root/'logs/shop-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
  data=meta.snapshot(self.root,self.now,[])
  ev=meta.evaluation(self.root,{'changed_at':10000,'baseline':{'risk':{'cash_balance':123}}},data,12000)
  self.assertEqual(ev['before']['seconds'],ev['after']['seconds'])
  self.assertEqual(ev['before']['settlements'],1);self.assertEqual(ev['after']['settlements'],1)
  self.assertEqual(ev['after']['revenue'],200)
 def test_stale_evidence_never_calls_policy_model(self):
  model=Mock(side_effect=AssertionError('must not call'))
  result=meta.review(self.root,now=self.now+31,public=[],model=model)
  self.assertEqual(result['status'],'data_retry')
  self.assertEqual(result['next_review_at'],self.now+31+meta.DATA_RETRY_SECONDS)
  self.assertFalse((self.root/'state/seller-meta-policy.json').exists())
  model.assert_not_called()

 def test_old_cash_baseline_is_rebased_without_changing_tunables(self):
  (self.root/'state/seller-meta-policy.json').write_text(json.dumps({'version':'old','changed_at':1,'config':DEFAULT,
   'previous_config':DEFAULT,'at':self.now-10,'expires_at':self.now+1000,'baseline':{'at':1,'risk':{}}}))
  (self.root/'state/seller-meta-review.json').write_text(json.dumps({'last_review':{'at':self.now-1}}))
  def decide(role,prompt,schema):
   self.assertTrue(prompt['snapshot']['score_transition']['rebased'])
   self.assertIsNone(prompt['snapshot']['policy_evaluation'])
   return self.decision(12_000_000)
  result=meta.review(self.root,now=self.now,public=[],model=decide)
  self.assertEqual(result['status'],'evaluation_pending')
  policy=json.loads((self.root/'state/seller-meta-policy.json').read_text())
  self.assertEqual(policy['config'],DEFAULT);self.assertEqual(policy['score_version'],VERSION)
  self.assertEqual(policy['changed_at'],self.now)
  self.assertIn('book',policy['baseline'])

 def test_book_evaluation_uses_observed_book_not_cash_movement(self):
  data=meta.snapshot(self.root,self.now,[])
  old={'changed_at':self.now-1000,'baseline':{'book':{'book':data['book']['book']-123},'risk':{}}}
  ev=meta.evaluation(self.root,old,data,self.now)
  self.assertEqual(ev['observed_book_change'],123)

 def test_stale_attempt_recovers_after_short_retry_not_thirty_minutes(self):
  start=self.now+31
  meta.review(self.root,now=start,public=[],model=Mock(side_effect=AssertionError('stale')))
  model=Mock(side_effect=lambda *a:self.decision())
  self.fresh(start+30)
  self.assertEqual(meta.review(self.root,now=start+30,public=[],model=model)['status'],'cooldown')
  model.assert_not_called()
  later=start+61;self.fresh(later)
  self.assertEqual(meta.review(self.root,now=later,public=[],model=model)['status'],'changed')
  state=json.loads((self.root/'state/seller-meta-review.json').read_text())
  self.assertEqual(state['last_success'],later)
  self.assertEqual(state['next_review_at'],later+meta.REVIEW_SECONDS)
  self.assertNotIn('last_failure',state)

 def test_network_failure_reserves_short_backoff_without_success(self):
  with patch.object(meta,'public_sample',side_effect=OSError('temporary network failure')):
   with self.assertRaises(OSError):meta.review(self.root,now=self.now,model=Mock())
  state=json.loads((self.root/'state/seller-meta-review.json').read_text())
  self.assertEqual(state['next_review_at'],self.now+meta.FAILURE_RETRY_SECONDS)
  self.assertNotIn('last_success',state)

 def test_legacy_failed_attempt_does_not_prevent_due_review(self):
  (self.root/'state/seller-meta-review.json').write_text(json.dumps({'last_attempt':self.now-1,'last_review':{'at':self.now-2000}}))
  result=meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision())
  self.assertEqual(result['status'],'changed')

 def test_sampling_refreshes_clock_before_checking_inventory_age(self):
  clock=[self.now]
  def fetch():
   clock[0]+=100;self.fresh(clock[0]);return []
  with patch.object(meta.time,'time',side_effect=lambda:clock[0]),patch.object(meta,'public_sample',side_effect=fetch):
   result=meta.review(self.root,model=lambda *a:self.decision())
  self.assertEqual(result['status'],'changed')
  state=json.loads((self.root/'state/seller-meta-review.json').read_text())
  self.assertEqual(state['last_success'],self.now+100)

 def test_small_file_refresh_race_is_not_a_stale_snapshot(self):
  self.fresh(self.now+1)
  result=meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision())
  self.assertEqual(result['status'],'changed')
