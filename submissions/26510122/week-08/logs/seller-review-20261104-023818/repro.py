"""Offline audit probes: fake model, local A2A, ephemeral keys, no market writes."""
import asyncio
from contextlib import contextmanager
from datetime import datetime,timezone
import json,os
from pathlib import Path
import random,sys,tempfile,time,uuid
from unittest.mock import patch
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import inventory_portfolio as portfolio
import replenishment_plan as plans
import restock,seller_r3

@contextmanager
def isolated():
 previous=Path.cwd()
 with tempfile.TemporaryDirectory(prefix='seller-review-',dir=ROOT/'state') as d:
  os.chdir(d)
  try:yield
  finally:os.chdir(previous)

def products():
 return {f's{i}':{'name':f'Product {i}','kind':'goods' if i%2 else 'food','category':f'c{i}','list_price':10000,'shelf_hours':96 if i%2==0 else None} for i in range(60)}
def inventory(n,now):
 return {'round':3,'balance':5_000_000,'units':[{'sku':f's{i}','unit':f'u{i}','category':f'c{i}','cost':6500,'grade':'A'} for i in range(n)],'restock':{'open':True,'opens_at':now-10,'closes_at':now+180}}
def buy(role,prompt,schema):
 a=prompt['exploration_anchor_sku'];ids=[a]+[r['sku'] for r in prompt['candidates'] if r['sku']!=a][:prompt['maximum_new_skus']-1]
 return {'action':'buy','skus':ids,'reason':'offline audit fixture'}

reports={}
with isolated():
 now=time.time();inv=inventory(23,now);cat=products();md=Path('.market');md.mkdir();Path('state').mkdir()
 (md/'inventory.json').write_text(json.dumps(inv));(md/'catalog.json').write_text(json.dumps(cat))
 Path('state/inventory-exploration.json').write_text(json.dumps({'version':1,'round':3,'completed':True,'intents':[],'initial_skus':[],'initial_cost':0,'reserved':0,'basket':[]}))
 obs={'observed_visits':10,'observed_no_stock_visits':0,'observed_visit_rate_while_stocked':None}
 cached=plans.create(sku='s0',product=cat['s0'],candidate={'qty':3,'target_stock':4},approval={'valid_for_seconds':600,'max_total_qty':3},observation=obs,now=now)
 plans.save(restock.PLAN,cached)
 with patch.object(restock,'decide_json',side_effect=buy),patch.object(plans,'execute',wraps=plans.execute) as execute:
  result=restock.run(md)
  reports['coverage_preempts_popular_sku_replenishment']={'stocked_variety':23,'target_variety':24,'cached_profitable_sku':'s0','cached_missing_qty':3,'cached_execute_calls':execute.call_count,'new_exploration_intents':result['buy']}
with isolated():
 inv=inventory(0,1000);cat=products()
 first=portfolio.run(inv,cat,[],1000,model=buy,refresh=lambda:(inv,1000,True),rng=random.Random(1))
 settled=[{'event':'settled','at':datetime.fromtimestamp(1050,timezone.utc).isoformat(),'sku':b['sku'],'unit':f'sold-{i}','ledger':i+1,'price':10000} for i,b in enumerate(first['buy'])]
 second=inventory(0,2000)
 portfolio.run(second,cat,settled,2000,model=lambda *args:{'action':'hold','skus':[],'reason':'audit stops additional purchases'},refresh=lambda:(second,2000,True),rng=random.Random(1))
 state=json.loads(Path('state/inventory-portfolio.json').read_text())
 reports['sold_before_next_window_remains_unknown']={'explicit_settlement_evidence':len(settled),'unknown_intents_after_settlements':sum(i['status']=='issued_unknown' for i in state['intents']),'pending_cost_despite_settlements':plans.pending_exploration_cost(second)}

async def exchange():
 app=seller_r3.build(9999)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as c:
  async def send(text,task=None):
   message={'role':'ROLE_USER','messageId':str(uuid.uuid4()),'parts':[{'text':text}]}
   if task:message['taskId']=task
   r=await c.post('/',json={'jsonrpc':'2.0','id':'1','method':'SendMessage','params':{'message':message}},headers={'A2A-Version':'1.0'})
   result=r.json();assert 'error' not in result,result
   return result['result'].get('task',result['result'])
  first=await send('I want F1. It must last at least 84 hours.')
  nonnumeric=await send('800, but only if it lasts 84 hours',first['id'])
  paid=await send('800',first['id'])
  return first,nonnumeric,paid
with isolated():
 md=Path('.market');md.mkdir();now=time.time()
 (md/'inventory.json').write_text(json.dumps({'round':3,'units':[{'sku':'F1','unit':'f1','name':'Food','category':'food','kind':'food','grade':'C','list_price':1000,'cost':500,'supplied_at':now-100,'expires_at':now+36*3600}]}))
 (md/'catalog.json').write_text(json.dumps({'F1':{'name':'Food','kind':'food','list_price':1000,'shelf_hours':96}}))
 key=Ed25519PrivateKey.generate();Path('shop_key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 answers=[{'action':'ask','price':900,'unit_id':'f1','reason':'fixture intentionally ignores required hours'},{'action':'accept','price':None,'unit_id':'f1','reason':'fixture accept'}]
 with patch.object(seller_r3,'decide_json',side_effect=answers) as model:
  first,nonnumeric,paid=asyncio.run(exchange())
 follow=model.call_args_list[1].args[1]
 reports['buyer_conditions_not_preserved_or_hard_checked']={'required_hours':84,'offered_unit_hours_left':36,'nonnumeric_reply':nonnumeric['status']['message']['parts'][0]['text'],'follow_up_prompt_has_original_requirement':'84' in json.dumps(follow),'receipt_signed':paid['status']['state']=='TASK_STATE_COMPLETED','receipt_warranty_capability':'warranty_hours' in seller_r3.decision_schema(['ask'])['properties']}
print('AUDIT_REPORT '+json.dumps(reports,ensure_ascii=False))
(ROOT/'logs/seller-review-20261104-023818/repro-results.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
