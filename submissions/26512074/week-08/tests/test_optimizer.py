import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock,AsyncMock
import optimizer as o
import seller_r3 as seller
import buyer_r3
from round3 import procurement

class OptimizationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.md=Path(self.tmp.name)
  self.unit={'unit':'u','sku':'x','cost':100,'list_price':170,'kind':'goods','grade':'B','supplied_at':1000}
  self.inv={'round':3,'balance':3000000,'units':[self.unit]}
 def tearDown(self):self.tmp.cleanup()
 def quote(self,task='t',now=1000):
  return o.choose_quote(self.md,task,self.unit,self.inv,{'floor':101,'ask':115},now,SimpleNamespace(random=lambda:1))
 def test_quote_bounds_and_idempotent(self):
  first=self.quote()
  self.assertEqual(first['ask'],115)  # No mandatory premium above list.
  self.assertEqual(self.quote()['ask'],first['ask'])
  with o.db(self.md) as c:self.assertEqual(c.execute('select count(*) from trials').fetchone()[0],1)
 def test_high_opening_is_bounded_without_forced_list_cap(self):
  for i in range(8):
   q=o.choose_quote(self.md,str(i),self.unit,self.inv,{'floor':101,'ask':115},1000,SimpleNamespace(random=lambda:0))
   with o.db(self.md) as c:
    c.execute('update trials set at=?,settled_at=?,margin=? where task=?',(0,100,20,str(i)))
   self.assertLessEqual(q['ask'],340)
   self.assertGreaterEqual(q['ask'],101)
 def test_unanswered_quotes_reduce_ceiling_without_changing_old_quotes(self):
  with o.db(self.md) as c:
   for i in range(12):
    c.execute('insert into trials(task,sku,kind,grade,at,ask,reference,strategy,responded) values(?,?,?,?,?,?,?,?,?)',
      ('failed'+str(i),'x','goods','B',1000,187,170,'response-ceiling-v1',0))
  lowered=self.quote('new',5000)
  self.assertLess(lowered['ask'],187)
  self.assertEqual(self.quote('new',6000)['ask'],lowered['ask'])
 def test_only_settlement_counts_and_deduplicates(self):
  self.quote();o.agreement(self.md,'t',140)
  self.assertEqual(o.interval_metrics(self.md,0,2000)['settled'],0)
  e={'seq':1,'kind':'settle','round':3,'ts':1100,'data':{'shop':'totato2203-r3','sku':'x','unit':'u','amount':140}}
  o.observe(self.md,self.inv,[e,e],1200)
  m=o.interval_metrics(self.md,0,2000)
  self.assertEqual(m['settled'],1);self.assertEqual(m['gross_margin_known'],40)
 def test_missing_cost_not_profit(self):
  e={'seq':2,'kind':'settle','ts':1100,'data':{'shop':'totato2203-r3','sku':'x','unit':'missing','amount':140}}
  o.observe(self.md,self.inv,[e],1200)
  self.assertFalse(o.interval_metrics(self.md,0,2000)['economic_profit_complete'])
 def test_settlement_time_window(self):
  self.quote(now=100)
  with o.db(self.md) as c:c.execute('update trials set settled_at=1200,price=130,margin=30')
  self.assertEqual(o.interval_metrics(self.md,1000,2000)['gross_margin_known'],30)
 def test_unagreed_later_quote_cannot_steal_settlement(self):
  self.quote('signed');o.agreement(self.md,'signed',130)
  self.quote('inquiry',1001)
  e={'seq':10,'kind':'settle','ts':1100,'data':{'shop':'totato2203-r3','unit':'u','sku':'x','amount':130}}
  o.observe(self.md,self.inv,[e],1200)
  with o.db(self.md) as c:
   self.assertIsNotNone(c.execute("select settled_at from trials where task='signed'").fetchone()[0])
   self.assertIsNone(c.execute("select settled_at from trials where task='inquiry'").fetchone()[0])
 def test_open_tasks_restore_before_restart(self):
  from seller_handoff import restore
  from a2a.server.tasks import InMemoryTaskStore
  import time
  original=self.quote()
  o.atomic(self.md/'inventory.json',self.inv)
  o.atomic(self.md/'seller-handoff.json',{'at':time.time(),'tasks':[{'id':'t','contextId':'c','status':{'state':'TASK_STATE_INPUT_REQUIRED'}}]})
  agent=seller.Seller();store=InMemoryTaskStore()
  self.assertEqual(restore(agent,store,self.md),1)
  self.assertEqual(agent.open['t']['ask'],original['ask'])
  self.assertIsNotNone(asyncio.run(store.get('t',__import__('a2a.server.context',fromlist=['ServerCallContext']).ServerCallContext())))
 def test_sparse_negotiation_accepts_floor_not_private_budget(self):
  self.quote()
  d={'unit':self.unit,'ask':150,'offer':110,'lines':['buyer: 110']}
  self.assertIn(o.negotiate(self.md,'t',d,{'floor':101,'ask':150},1001)['action'],('ask','accept'))
  d['lines'] += ['buyer: 110','buyer: 110'];d['previous_offer']=110
  move=o.negotiate(self.md,'t',d,{'floor':101,'ask':150},1001)
  self.assertIn(move['action'],('ask','accept'))
  if move['action']=='ask':self.assertGreater(move['price'],110)
  d['offer']=90
  self.assertNotEqual(o.negotiate(self.md,'t',d,{'floor':101,'ask':150},1001)['action'],'accept')
 def test_evidence_can_counter_profitable_offer(self):
  for i in range(20):
   self.quote(str(i),now=100)
   with o.db(self.md) as c:c.execute('update trials set settled_at=200,margin=45 where task=?',(str(i),))
  self.quote('now',now=2000)
  d={'unit':self.unit,'ask':160,'offer':102,'lines':['buyer: 102']}
  self.assertEqual(o.negotiate(self.md,'now',d,{'floor':101,'ask':160},2000)['action'],'ask')
 def test_market_reference_survives_quote_selection_and_old_quote_is_honored(self):
  base={'floor':101,'ask':153,'market_reference':153}
  q=o.choose_quote(self.md,'market',self.unit,self.inv,base,1000,SimpleNamespace(random=lambda:1))
  self.assertGreaterEqual(q['ask'],153)
  old=o.choose_quote(self.md,'market',self.unit,self.inv,dict(base,floor=165),1001)
  self.assertEqual(old['ask'],q['ask']);self.assertLessEqual(old['floor'],q['ask'])
 def test_repeated_lowball_cannot_force_normal_stock_to_cost(self):
  base={'floor':120,'ask':153,'market_reference':153}
  o.choose_quote(self.md,'market',self.unit,self.inv,base,1000,SimpleNamespace(random=lambda:1))
  d={'unit':self.unit,'ask':153,'offer':110,'lines':['buyer: 110']*6}
  self.assertEqual(o.negotiate(self.md,'market',d,base,1001)['action'],'reject')
  d['offer']=144;d['lines']=['buyer: 144']*3
  d['previous_offer']=144
  self.assertEqual(o.negotiate(self.md,'market',d,base,1001)['action'],'accept')
  d['unit']={**self.unit,'expires_at':1100};d['offer']=110
  self.assertNotEqual(o.negotiate(self.md,'market',d,base,1001)['action'],'accept')
 def fixture(self,at,profit=100):
  return {'at':at,'seller':{'stars':4.8,'deals':100,'refunds':0},'cash_profit_per_hour':profit,
          'local':{'attempts':40,'settled':20,'conversion':.5,'partial_realized_net':1000,'economic_profit_complete':True,
                   'skus':{'x':{'arms':{'a':{'attempts':15,'settled':7,'margin':100},'b':{'attempts':15,'settled':8,'margin':200}}}}}}
 def test_apply_hold_and_rollback(self):
  p=o.config(self.md);prev=self.fixture(1000);cur=self.fixture(22600,150)
  result,reason=o.evaluate(prev,cur,p)
  self.assertEqual(reason,'apply');self.assertLessEqual(sum(result[k]!=p[k] for k in p),2)
  cur['seller']['stars']=4.5
  self.assertTrue(o.evaluate(prev,cur,p)[1].startswith('rollback'))
  cur=self.fixture(22600);cur['local']['attempts']=2
  self.assertTrue(o.evaluate(prev,cur,p)[1].startswith('hold'))
 def test_duplicate_cycle_and_restart(self):
  (self.md/'inventory.json').write_text(json.dumps(self.inv))
  (self.md/'ledger.jsonl').write_text('')
  board={'shops':[{'name':'totato2203-r3','all':.5,'profit':100,'stars':4.8,'deals':1}], 'buyers':[]}
  out=o.review(self.md,board,{'shops':[]},30000)
  self.assertEqual(len(out['top5']),1)
  self.assertEqual(o.review(self.md,board,{'shops':[]},30001)['status'],'not_due')
  self.assertEqual(o.read(self.md/'optimization/state.json',{})['last_at'],30000)
 def test_interrupted_policy_commit_recovers(self):
  p=o.config(self.md);p['exploration']=.14
  out=self.md/'optimization'
  o.atomic(out/'pending-policy.json',{'policy':p,'snapshot':{'at':30000},'state':{'last_at':30000}})
  self.assertEqual(o.review(self.md,{}, {},31000)['status'],'recovered_committed_cycle')
  self.assertEqual(o.config(self.md)['exploration'],.14)
  self.assertFalse((out/'pending-policy.json').exists())
 def test_lock_rejects_duplicate(self):
  with o.lock(self.md):
   with self.assertRaises(OSError):
    with o.lock(self.md):pass
 def test_review_persists_last_good_rollback(self):
  p=o.config(self.md);changed=dict(p,exploration=.14)
  o.atomic(self.md/'adaptive-policy.json',changed)
  prev=self.fixture(1000);prev['buyer']={};prev['top5']=[]
  o.atomic(self.md/'optimization/latest.json',prev)
  o.atomic(self.md/'optimization/state.json',{'last_at':1000,'last_good':p})
  (self.md/'ledger.jsonl').write_text('')
  board={'shops':[{'name':'totato2203-r3','all':.5,'stars':4.3,'profit':100,'deals':120}], 'buyers':[]}
  with patch.object(o,'interval_metrics',return_value=self.fixture(22600)['local']):
   result=o.review(self.md,board,{'shops':[]},22600)
  self.assertTrue(result['status'].startswith('rollback'))
  self.assertEqual(o.config(self.md)['exploration'],p['exploration'])
 def test_policy_rejects_caps_and_invalid_values(self):
  self.assertNotIn('purchase_cap',o.BOUNDS)
  p=o.config(self.md);p['exploration']=float('inf')
  with self.assertRaises(ValueError):o.validate(p)
 def test_buyer_expected_surplus_uses_checkout_history(self):
  shops=[{'url':u,'offers':[{'sku':'x','qty':1}]} for u in ['a','b']]
  payments=[{'shop':'a','item':'x','price':50,'paid':True}]*5
  self.assertEqual(buyer_r3.candidates(shops,{'acceptable':['x'],'budget':150},{'x':{'list_price':170}},payments)[0][1]['url'],'a')

class ZeroModelTests(unittest.IsolatedAsyncioTestCase):
 async def test_full_open_offer_receipt_without_model(self):
  with tempfile.TemporaryDirectory() as d:
   md=Path(d);unit={'unit':'u','sku':'G11111111','name':'X','category':'X','cost':50,'list_price':100,'kind':'goods','grade':'B','supplied_at':1,'expires_at':None}
   (md/'inventory.json').write_text(json.dumps({'round':3,'balance':3000000,'units':[unit]}))
   agent=seller.Seller();up=MagicMock();up.requires_input=AsyncMock();up.add_artifact=AsyncMock();up.complete=AsyncMock()
   context=SimpleNamespace(current_task=SimpleNamespace(id='t',context_id='c'),message=None)
   with patch.object(seller,'MARKET_DIR',md),patch.object(seller,'TaskUpdater',return_value=up),patch.object(seller,'sign',side_effect=lambda r:r),patch('harness.decide',new=AsyncMock(side_effect=AssertionError('model called'))) as model:
    with patch.object(seller,'get_message_text',return_value='G11111111'):await agent.execute(context,None)
    self.assertEqual(len(agent.open),1)
    with patch.object(seller,'get_message_text',return_value='I offer 10'):await agent.execute(context,None)
    self.assertEqual(len(agent.open),1)
    with patch.object(seller,'get_message_text',return_value=str(agent.open['t']['ask'])):await agent.execute(context,None)
    up.complete.assert_awaited_once();model.assert_not_awaited()
 async def test_unrelated_numbers_do_not_call_model(self):
  with patch('policy.decide',new=AsyncMock(side_effect=AssertionError('model called'))) as model:
   self.assertIsNone(await seller.offer_price('deliver in 100 days',80));model.assert_not_awaited()
 async def test_deterministic_quote_formats(self):
  from policy import read_price
  with patch('policy.decide',new=AsyncMock(side_effect=AssertionError('model called'))):
   for text in ['27,409','asking 27,409','가격: 27409원','KRW 27409']:
    self.assertEqual(await read_price(text,role='ask'),27409)

if __name__=='__main__':unittest.main()
