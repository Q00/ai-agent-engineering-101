"""Read-only evidence for the final-day company release."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,sqlite3,time
import httpx
from r3.state import ROOT,read_json

def main():
 runtime=read_json(ROOT/'.market/runtime.json',{});health=read_json(ROOT/'.market/shop-health.json',{});cfg=read_json(ROOT/'policy.json')
 def get(url):
  try:
   r=httpx.get(url,timeout=12,trust_env=False,headers={'Cache-Control':'no-cache'});return {'status':r.status_code,'data':r.json() if r.status_code==200 else {}}
  except Exception as e:return {'error':type(e).__name__}
 urls=['https://market.ourolabs.ai/api/board','https://market.ourolabs.ai/api/shops',health.get('url','').rstrip('/')+'/.well-known/agent-card.json']
 with ThreadPoolExecutor(max_workers=3) as p:res=list(p.map(get,urls))
 with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
  def kv(k):
   r=db.execute('select value from kv where key=?',(k,)).fetchone();return json.loads(r[0]) if r else {}
  counts={k:db.execute('select count(*) from events where kind=? and ts>=?',(k,runtime.get('started_at',time.time()))).fetchone()[0] for k in ('shop_settled','purchase','model_usage','cycle_supervisor_error','analytical_role_error')}
  company={k:kv(k) for k in ('company-accounting:v30','company-procurement:v30')}
  company['company-accounting:v30']={k:v for k,v in company['company-accounting:v30'].items() if k!='skus'}
  revisions={k:kv(k) for k in ('shop_strategy_revision','buyer_strategy_revision','cycle-heartbeat')}
 board=res[0].get('data',{});own={r:next((x for x in board.get(r,[]) if x.get('name')=='seochanit'),{}) for r in ('shops','buyers')}
 out={'at':time.time(),'runtime':runtime,'health':health,'config':{k:cfg.get(k) for k in ('execution_revision','shop_strategy_revision','buyer_strategy_revision','llm_disabled','research_enabled','capability_llm_enabled','finalday_departments_enabled')},'own':own,'card_status':res[2].get('status'),'registered':any(s.get('name')=='seochanit' and s.get('url','').rstrip('/')==health.get('url','').rstrip('/') for s in res[1].get('data',{}).get('shops',[])),'counts_since_restart':counts,'departments':company,'runtime_revisions':revisions}
 path=ROOT/'logs'/('finalday-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json');path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
 print('SOURCE',path);print(json.dumps({k:v for k,v in out.items() if k not in ('own','health','departments')},ensure_ascii=True))
if __name__=='__main__':main()
