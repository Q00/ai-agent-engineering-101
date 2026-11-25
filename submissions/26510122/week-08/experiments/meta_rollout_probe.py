"""Copied-state real-model probes. No shop join, supply, checkout or forum handoff."""
import argparse,hashlib,json,os,shutil,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--tag',required=True);args=p.parse_args()
ROOT=args.root.resolve();sys.path.insert(0,str(ROOT));OUT=ROOT/'state'/('meta-probe-'+args.tag);OUT.mkdir(exist_ok=False)
from harness import decide_json
import seller_meta,buyer_forum,inventory_portfolio
REPORT={'at':time.time(),'market_writes':False,'synthetic_supply_window':True,'results':{}}
def write():
 (OUT/'result.json').write_text(json.dumps(REPORT,ensure_ascii=False,indent=2))
def stage(role,name):
 src=ROOT/'state'/f'{role}-runtime';dest=OUT/name;dest.mkdir()
 for d in ['.market','state','logs']:(dest/d).mkdir()
 for name in ['inventory.json','catalog.json']:
  f=src/'.market'/name
  if f.exists():shutil.copy2(f,dest/'.market'/name)
 for name in ['inventory-portfolio.json','seller-meta-policy.json','seller-meta-market.json']:
  f=src/'state'/name
  if f.exists():shutil.copy2(f,dest/'state'/name)
 log='shop-events.jsonl' if role=='seller' else 'buyer-events.jsonl'
 shutil.copy2(src/'logs'/log,dest/'logs'/log)
 return dest
# Fetch a selected public sample once; it is read-only market evidence.
public=seller_meta.public_sample()
for name in ['meta','inventory','buyer_forum']:
 try:
  role='buyer' if name=='buyer_forum' else 'seller';working=stage(role,name);os.chdir(working)
  if name=='meta':
   r=seller_meta.review(working,public=public,shadow=True)
   REPORT['results'][name]=r
  elif name=='inventory':
   inv=json.loads((working/'.market/inventory.json').read_text());catalog=json.loads((working/'.market/catalog.json').read_text());now=time.time()
   inv['restock']={'open':True,'opens_at':now-10,'closes_at':now+170}
   # Tool-produced public shortlist, not an active meta-policy change.
   counts={};examples=[]
   for x in public:
    if x.get('kind')=='settle':
     sku=x['data'].get('sku',x['data'].get('item'));counts[sku]=counts.get(sku,0)+1
   (working/'state/seller-meta-market.json').write_text(json.dumps({'at':now,'rows':[{'sku':k,'public_settles':v} for k,v in counts.items()]}))
   observed={}
   def model(role,prompt,schema):
    observed['candidate_count']=len(prompt['candidates']);observed['over_old_cap']=sum(x['cost_ceiling']>500000 for x in prompt['candidates']);observed['budget']=prompt['cost_ceiling_budget']
    answer=decide_json(role,prompt,schema);observed['answer']=answer;return answer
   result=inventory_portfolio.run(inv,catalog,seller_meta.events(working/'logs/shop-events.jsonl'),now,
       model=model,refresh=lambda:(inv,time.time(),True))
   REPORT['results'][name]={'result':result,**observed}
  else:
   REPORT['results'][name]=buyer_forum.run_once(working,dry_run=True)
  REPORT['results'][name]['ok']=True
 except Exception as e:
  REPORT['results'][name]={'ok':False,'error_type':type(e).__name__,'detail':str(e)[:300]}
 write()
REPORT['complete']=all(r.get('ok') for r in REPORT['results'].values());write();print(json.dumps(REPORT,ensure_ascii=False,indent=2),flush=True)
if not REPORT['complete']:sys.exit(1)
