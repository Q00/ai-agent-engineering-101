"""Contention regressions: diagnostic durability and critical transaction safety."""
import sqlite3,time,threading
from r3.state import Journal,atomic_json

def test_busy_diagnostics_spool_without_blocking_and_replay_idempotently(tmp_path):
 j=Journal(tmp_path)
 with j.connect() as lock:
  lock.execute('BEGIN IMMEDIATE')
  started=time.monotonic()
  assert j.observe('prediction',{'decision':'d'},eid='same',ts=123)
  assert time.monotonic()-started<.75
  assert j.events('prediction')==[]
  assert len(list((tmp_path/'telemetry-pending').glob('*.json')))==1
  assert j.flush_observations()==0
 assert j.flush_observations()==1
 assert j.events('prediction')[0]['_ts']==123
 assert not list((tmp_path/'telemetry-pending').glob('*.json'))
 atomic_json(tmp_path/'telemetry-pending'/'replay.json',['same','prediction',123,'system','{"decision":"d"}'])
 assert j.flush_observations()==1 and len(j.events('prediction'))==1

def test_critical_event_waits_for_commit_instead_of_entering_diagnostic_queue(tmp_path):
 j=Journal(tmp_path);done=threading.Event();errors=[]
 def write():
  try:j.add('payment',{'order':'paid'},eid='payment:paid')
  except Exception as e:errors.append(e)
  finally:done.set()
 with j.connect() as lock:
  lock.execute('BEGIN IMMEDIATE');t=threading.Thread(target=write);t.start()
  assert not done.wait(.1)
 assert done.wait(2);t.join();assert not errors
 assert len(j.events('payment'))==1
 assert not list((tmp_path/'telemetry-pending').glob('*.json'))

def test_retention_prefix_uses_index_and_does_not_match_customerx(tmp_path):
 j=Journal(tmp_path)
 for kind in ('customer_turn','customerXturn','payment'):
  j.add(kind,{'task':'t'})
 with j.connect() as db:
  q="SELECT rowid FROM events WHERE kind GLOB 'customer_*' AND json_extract(data,'$.task')=?"
  plan=db.execute('EXPLAIN QUERY PLAN '+q,('t',)).fetchall()
  assert any('SEARCH' in r[3] and 'event_kind' in r[3] for r in plan)
  assert len(db.execute(q,('t',)).fetchall())==1

def test_numeric_prediction_preserves_learning_ids_without_copying_large_context(tmp_path):
 from r3.state import policy
 from r3.model import Chooser
 import json
 j=Journal(tmp_path)
 cfg=dict(policy(),llm_disabled=True,system_framework_enabled=False,autonomy_v14_enabled=False,objective_feedback_enabled=False,market_cycle_enabled=False)
 ctx={'shop_execution':True,'operation':'quote','task':'t','shop':'peer','sku':'s',
      'order':{'order':'o','large':'x'*20000},'customer_memory':{'task':'visit','large':'x'*20000}}
 result=Chooser(j,cfg).choose('shop',[{'id':'good','score':1,'price':2},{'id':'bad','score':0,'price':3}],ctx)
 row=j.events('prediction')[0]
 assert result['id']=='good' and row['context']['task']=='t'
 assert row['context']['order']=={'order':'o'} and row['context']['customer_memory']=={'task':'visit'}
 assert len(json.dumps(row))<3000

def test_worker_failure_keeps_safe_origin_without_exception_message(tmp_path):
 import json,queue
 from pathlib import Path
 from types import SimpleNamespace
 from r3.worker_bridge import BuyerBridge
 j=Journal(tmp_path);b=BuyerBridge(Path(tmp_path),lambda p:None,j)
 class Input:
  def write(self,line):
   request=json.loads(line)
   b.responses.put({'id':request['id'],'order':'o','error':'OperationalError','error_message':'SECRET_VALUE',
    'error_origin':[{'module':'state.py','function':'put','line':114,'locals':'SECRET_VALUE'}]})
  def flush(self):pass
 b.process=SimpleNamespace(poll=lambda:None,stdin=Input())
 assert b.run({'order':'o'},[],1)==[]
 row=j.events('buyer_worker_error')[0]
 assert row['worker_error_type']=='OperationalError' and row['payment_started'] is False
 assert row['error_origin']==[{'module':'state.py','function':'put','line':114}]
 assert 'SECRET_VALUE' not in json.dumps(row)

import pytest
@pytest.mark.parametrize('term',[{}, {'warranty_hours':None}, {'warranty_hours':0}, {'warranty_hours':3}])
def test_signed_missing_or_short_warranty_never_reaches_checkout_preparation(tmp_path,monkeypatch,term):
 import market_buyer as buyer
 from types import SimpleNamespace
 from r3.state import policy
 j=Journal(tmp_path);now=time.time()
 cfg=dict(policy(),max_shop_candidates=1,buyer_contract_routing_enabled=True,service_contract_v26_enabled=False,
     system_framework_enabled=False)
 rec=dict(sig_seller='synthetic',claim='A',item='s',task='t',price=100,**term)
 task={'id':'t','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':rec}]}]}
 peer=SimpleNamespace(close=lambda:None,cancel=lambda t:None)
 c={'id':'c','reference_price':100,'shop':'peer','sku':'s','product':{'kind':'goods','list_price':1000}}
 chooser=SimpleNamespace(choose=lambda role,candidates,context,**kw:candidates[0])
 monkeypatch.setattr(buyer,'buyer_assessment',lambda *a,**kw:{'cap':1000,'required_warranty':6,
     'required_grade':'A','conditional_terms':['warranty_hours']})
 monkeypatch.setattr(buyer,'verify_receipt',lambda *a,**kw:pytest.fail('must reject missing coverage before further checkout preparation'))
 empty={'receipt':None}
 out=buyer.execute({'order':'o','kind':'goods','budget':1000,'expires':now+60},j,chooser,lambda:peer,cfg,
     {'version':1},{'peer':{'pubkey':'synthetic'}},[c],now+10,{'c':(peer,task)},1,empty)
 assert out==empty and j.events('attempt')[0]['reason']=='quote_warranty_changed'
