import json,collections,pathlib,sys,datetime
sys.path.insert(0,str(pathlib.Path.cwd()))
from tools.supply_audit.tool import audit,read_rows
import patch_notes
pre='meta/evidence/astra-1110-021612'; pack=json.load(open(pre+'.json'));now=datetime.datetime.fromisoformat(pack['time']).timestamp()
shops=json.load(open(pre+'-public-shops.json'))['shops']; url={r['url']:r['name'] for r in shops}
rows=[];aliases={};seen=set()
for line in open('.market/ledger_rows.jsonl'):
 try:r=json.loads(line)
 except:continue
 if r.get('shop') in url and r.get('seller'):aliases[r['seller']]=url[r['shop']]
 if now-43200<=r.get('ts',0)<=now and r.get('k')=='settle' and r.get('seq') not in seen: rows.append(r);seen.add(r.get('seq'))
cat=json.load(open('.market/catalog.json'));names=['yms979','inextro','25620027','moss-and-oak','seochanit'];result=[]
for name in names:
 rr=[r for r in rows if (url.get(r.get('shop')) or aliases.get(r.get('seller')))==name];g=collections.defaultdict(list)
 for r in rr:g[r.get('sku')].append(r)
 result.append(dict(name=name,paid=len(rr),revenue=sum(r.get('amount') or 0 for r in rr),buyers=len(set(r.get('buyer') for r in rr)),dear_revenue=sum(r.get('amount') or 0 for r in rr if cat.get(r.get('sku'),{}).get('list_price',0)>=600000),margin=None,top=[dict(sku=k,paid=len(v),buyers=len(set(x.get('buyer') for x in v)),revenue=sum(x.get('amount') or 0 for x in v),prices=[min(x.get('amount') or 0 for x in v),max(x.get('amount') or 0 for x in v)]) for k,v in sorted(g.items(),key=lambda kv:-sum(x.get('amount') or 0 for x in kv[1]))[:6]],mapping='public current URL plus same seller-name historical URLs; coverage may be incomplete'))
with open(pre+'-rivals-v2.jsonl','w') as f:
 for r in result:f.write(json.dumps(r,ensure_ascii=False)+'\n')
print(json.dumps(result,ensure_ascii=False))
measure=pathlib.Path('.market/measure'); inputs=[read_rows(measure/(n+'.jsonl')) for n in ['supply_windows','units_in','units_out']]
with open(pre+'-supply.jsonl','w') as f:
 for sku in json.load(open('pricing.json'))['restock']['sku_target_units']:
  r=audit(*inputs,now,sku);r['mature']=pack['profit_cohorts']['skus'].get(sku,{}).get('mature_6h');f.write(json.dumps(r,ensure_ascii=False)+'\n')
# Raw original citations for findings; use a small set rather than the live files' changing line totals.
with open(pre+'-findings.jsonl','w') as f:
 for r in pack['market_opportunities']['candidates']:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 for sku in ['G2c023a8c','G03d0199f','G8ec51935']:
  f.write(json.dumps(dict(sku=sku,mature=pack['profit_cohorts']['skus'][sku]['mature_6h']),ensure_ascii=False)+'\n')
