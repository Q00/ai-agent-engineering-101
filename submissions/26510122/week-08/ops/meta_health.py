"""Read-only health and paid/publication evidence for the meta release."""
import argparse,json,os,sqlite3,sys,time,urllib.request
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from supply_reconciliation import pending_quantity
from inventory_observation import timestamp

def read(path,default=None):
 try:return json.loads(path.read_text())
 except (OSError,ValueError):return {} if default is None else default

def lines(path):
 result=[]
 if path.exists():
  for line in path.open():
   try:result.append(json.loads(line))
   except ValueError:pass
 return result

def alive(pid):
 try:os.kill(pid,0);return True
 except (OSError,TypeError):return False

def health(since):
 now=time.time();seller=ROOT/'state/seller-runtime';buyer=ROOT/'state/buyer-runtime';sr=read(seller/'running.json');br=read(buyer/'running.json');inv=read(seller/'.market/inventory.json');ps=read(seller/'state/inventory-portfolio.json');m=read(seller/'state/seller-meta-policy.json')
 se=[x for x in lines(seller/'logs/shop-events.jsonl') if (timestamp(x.get('at')) or 0)>=since]
 be=[x for x in lines(buyer/'logs/buyer-events.jsonl') if (timestamp(x.get('at')) or 0)>=since]
 me=lines(seller/'logs/seller-meta-events.jsonl');review=[x for x in me if x.get('event')=='meta_review' and (timestamp(x.get('at')) or 0)>=since]
 bf=read(buyer/'state/buyer-forum.json',{'posts':[]});sf=read(seller/'state/seller-forum.json',{'posts':[]})
 counts=None
 db=seller/'state/seller-meta.sqlite3'
 if db.exists():
  with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as connection:counts=connection.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
 report={'at':now,'since':since,'seller_source':sr.get('source_commit'),'buyer_source':br.get('source_commit'),
   'worker_pids':{k:{'pid':sr.get(k),'alive':alive(sr.get(k))} for k in ['supervisor_pid','kit_pid','forum_pid','meta_pid','buyer_forum_pid']},
   'buyer_kit_pid':br.get('kit_pid'),'buyer_kit_alive':alive(br.get('kit_pid')),'roleplay_enabled':br.get('roleplay_enabled'),
   'inventory':{'age_seconds':time.time()-(seller/'.market/inventory.json').stat().st_mtime,'cash':inv.get('balance'),
     'units':len(inv.get('units',[])),'cost':sum(u.get('cost',0) for u in inv.get('units',[])),
     'high_cost_units':sum(u.get('cost',0)>500000 for u in inv.get('units',[]))},
   'seller_events':dict(Counter(x.get('event') for x in se)),'buyer_events':dict(Counter(x.get('event') for x in be)),
   'pending_units':sum(pending_quantity(i) for i in ps.get('intents',[])),
   'new_intents':[{**{k:i.get(k) for k in ['at','sku','qty','cost_ceiling','status','kind']},'confirmed_units':i.get('qty',1)-pending_quantity(i)} for i in ps.get('intents',[]) if i.get('at',0)>=since],
   'meta_policy':m,'meta_db_reviews':counts,'meta_last_review':review[-1] if review else None,
   'meta_errors':[{k:x.get(k) for k in ['at','error','detail']} for x in me if x.get('event')=='meta_error' and (timestamp(x.get('at')) or 0)>=since],
   'buyer_posts':[{k:p.get(k) for k in ['id','at','status','post_id','refs','http_status']} for p in bf.get('posts',[]) if p.get('at',0)>=since],
   'seller_posts':[{k:p.get(k) for k in ['at','status','post_id','campaign','http_status']} for p in sf.get('posts',[]) if p.get('at',0)>=since]}
 try:
  with urllib.request.urlopen('https://market.ourolabs.ai/api/board',timeout=10) as r:b=json.load(r)
  report['board']={role:{k:x.get(k) for k in ['name','deals','posts','revenue','profit','stock_cost','all','forum']} for role in ['shops','buyers'] for x in b.get(role,[]) if x.get('name')=='outseop-26510122'}
 except Exception as e:report['board_error']=type(e).__name__
 return report

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--since',type=float,required=True);p.add_argument('--output',type=Path);a=p.parse_args();r=health(a.since);s=json.dumps(r,ensure_ascii=False,indent=2)
 if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(s+'\n')
 print(s,flush=True)
