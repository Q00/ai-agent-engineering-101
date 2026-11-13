"""Read-only analysis of bounded public snapshots; no requests or trading."""
from pathlib import Path
import json,gzip,hashlib
from collections import Counter
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
from statistics import median
P=Path(__file__).resolve().parent
load=lambda f:json.loads(gzip.decompress(f.read_bytes()) if f.suffix=='.gz' else f.read_bytes())
b=load(P/'board.json');old=load(P.parent/'market-followup-20261113T0720Z/board.json.gz')
cat={r['sku']:r for r in load(P/'catalog.json')}; shops=load(P/'shops.json')['shops']
now=parsedate_to_datetime(next(x[6:] for x in (P/'board.headers').read_text().splitlines() if x.lower().startswith('date: ')))
prior=datetime.fromisoformat(load(P.parent/'market-followup-20261113T0720Z/analysis.json')['observed_kst'])
hours=(now-prior).total_seconds()/3600
kst=lambda d:d.astimezone(timezone(timedelta(hours=9))).isoformat()
out={'observed_kst':kst(now),'baseline_kst':kst(prior),'elapsed_hours':hours,'round':b['current'],'comparison':{}}
ranked=lambda board,kind:[r for r in board[kind] if isinstance(r.get('all'),(int,float))]
for typ,weights in [('shops',{'book':.5,'revenue':.3,'stars_rank':.2}),('buyers',{'saved':.5,'deals':.1,'posts':.1})]:
 snapshots=[]
 for board in (old,b):
  rows=ranked(board,typ);o=next(r for r in rows if r['name']=='dyishappy');visible=sorted([r for r in rows if not r.get('house')],key=lambda r:-r['all'])
  # Missing component counts as zero in this reconstruction, matching displayed ALL.
  val=lambda row,k:row.get(k) or 0
  pts={k:w*(sum(val(r,k)<val(o,k) for r in rows)+(sum(val(r,k)==val(o,k) for r in rows)-1)/2)/(len(rows)-1) for k,w in weights.items()}
  if typ=='buyers':pts['forum']=.3*o['forum']
  snapshots.append({'own':{k:v for k,v in o.items() if k not in ('owner','card')},'rank_nonhouse':visible.index(o)+1,'ranked_nonhouse_count':len(visible),'scoring_population_including_house':len(rows),'components':pts,'reconstructed_all':sum(pts.values()),'component_ranks_nonhouse':{k:1+sum(val(r,k)>val(o,k) for r in visible) for k in weights}})
 a,z=snapshots
 fields=['all','book','revenue','profit','stock_cost','stock_units','deals','visits','stars','stars_rank','refunds','broken'] if typ=='shops' else ['all','saved','deals','orders','served','forum','posts']
 entry={'before':a,'now':z,'deltas':{k:z['own'][k]-a['own'][k] for k in fields},'component_point_delta':{k:100*(z['components'][k]-a['components'][k]) for k in z['components']}}
 entry['new_deals_per_hour']=entry['deltas']['deals']/hours
 if typ=='shops':entry['new_revenue_per_hour']=entry['deltas']['revenue']/hours;entry['book_growth_per_hour']=entry['deltas']['book']/hours
 entry['overtook']={}
 for k in weights:
  rows=[]
  for r in ranked(b,typ):
   before=next((x for x in old[typ] if x['name']==r['name']),None)
   if before and (before.get(k) or 0)<=a['own'][k] and (r.get(k) or 0)>z['own'][k]:rows.append({'name':r['name'],'before':before.get(k),'now':r[k]})
  entry['overtook'][k]=rows
 out['comparison'][typ]=entry
names={r['card']['url']:r['name'] for r in b['shops'] if r.get('card',{}).get('url')}
rows=sorted([r for f in sorted(P.glob('ledger-*.json')) if f.name!='ledger-latest.json' for r in load(f)],key=lambda r:r['seq'])
assert len(rows)==6500==len({r['seq'] for r in rows})
assert rows[-1]['seq']-rows[0]['seq']==6499
assert all(r['round']==3 for r in rows)
for r in rows:
 d=r['data'];receipt=d.get('receipt')
 if r['kind']=='result' and isinstance(receipt,dict) and receipt.get('shop'):
  names.setdefault(d.get('shop'),receipt['shop'])
trades=[]
for r in rows:
 if r['kind']!='settle':continue
 d=r['data'];c=cat.get(d.get('item'))
 if c is None:continue
 trades.append({'shop':names.get(d['shop'],d['shop']),'buyer':d['buyer'],'sku':d['item'],'price':d['amount'],'kind':c['kind'],'category':c['category'],'grade':d.get('grade'),'paid_to_list':d['amount']/c['list_price']})
summary=lambda ts:{'settlements':len(ts),'kinds':dict(Counter(t['kind'] for t in ts)),'grades':dict(Counter(t['grade'] for t in ts)),'median_paid_to_list':median(t['paid_to_list'] for t in ts) if ts else None,'categories':Counter(t['category'] for t in ts).most_common(6)}
out['sample']={'rows':len(rows),'start_kst':kst(datetime.fromtimestamp(rows[0]['ts'],timezone.utc)),'end_kst':kst(datetime.fromtimestamp(rows[-1]['ts'],timezone.utc)),'market':summary(trades),'participants':{}}
for typ,role in [('shops','shop'),('buyers','buyer')]:
 leaders=sorted([r for r in ranked(b,typ) if not r.get('house')],key=lambda r:-r['all'])[:5]
 out['sample']['participants'][typ]={}
 for name in ['dyishappy']+[r['name'] for r in leaders]:
  own=next(r for r in b[typ] if r['name']==name);before=next(r for r in old[typ] if r['name']==name)
  part=summary([t for t in trades if t[role]==name]);part.update(new_deals=own['deals']-before['deals'],deals_per_hour=(own['deals']-before['deals'])/hours)
  results=[r['data'] for r in rows if r['kind']=='result' and (names.get(r['data'].get('shop'))==name if role=='shop' else r['data'].get('buyer')==name)]
  part['result_events']=len(results);part['verified_reviews']=sum(d.get('verified') is True for d in results)
  part['low_reviews']=[{'stars':d.get('stars'),'review':d.get('review')} for d in results if d.get('verified') is True and d.get('stars') in (1,2)][:3]
  out['sample']['participants'][typ][name]=part
s=next(r for r in shops if r['name']=='dyishappy');cats=Counter()
for sku,n in s['stock'].items():cats[cat[sku]['category']]+=n
out['inventory']={'units':sum(s['stock'].values()),'skus':len(s['stock']),'categories':dict(cats),'category_count':len(cats),'url':s['url']}
root=P.parents[1];manifest=load(root/'logs/deployment-20261113T081622Z-8d1c0d89/package-manifest.json')
files=('buyer_selection.py','buyer_quote_accept.py','seller_r3.py','opening_strategy.py','guarded_checkout.py','restock.py','shop_diagnostics.py')
out['deployment']={'run':manifest['run'],'ready_log':True,'url_matches_deployment':s['url']=='https://timeline-regarding-invitations-neutral.trycloudflare.com','files_match_local':{f:manifest['files'][f]==hashlib.sha256((root/f).read_bytes()).hexdigest() for f in files},'note':'Package hashes and successful install log; runtime metrics require server diagnostics.'}
out['findings']=['No new rule patch observed: latest Patch 3.6 Nov 12.', 'Shop ALL decline reconstructed using all 66 scored shops including house agents; displayed nonhouse rank uses 59 shops.', 'Shop Book and revenue rose, rating stayed equal. Revenue and stars percentile positions deteriorated.', 'Buyer Saved rose and Saved rank stayed 15; deals rank improved; posts rank fell two places.', 'Buyer forum publication is limited locally to 600 seconds versus market minimum 300 seconds; confirmed reports only, no generic filler recommended.', 'Inventory remains near cap at 59; bedding and mattresses total 49 versus previous 52. Category spread improved from 9 to 11, but concentration remains.', 'Causal effect of latest deployment cannot be inferred from this observational interval; baseline predates deployment.']
out['limits']=['No strategy code or live service changed.', 'Different snapshot times and finite public ledger samples.', 'Unknown receipt/checkout outcomes are not classified as failures.', 'Public settlement prices do not reveal supply cost; paid/list is not Saved.', 'Server 3-hour diagnostics requested and pending.', 'Rank figures exclude house agents and are not Class ranks; scoring decomposition includes all scored agents.']
out['sources']={k:'https://market.ourolabs.ai/'+v for k,v in [('board','api/board'),('shops','api/shops'),('rules','rules'),('ledger','api/ledger?after=-500')]}
(P/'analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'comparison':out['comparison'],'sample':out['sample'],'inventory':out['inventory'],'deployment':out['deployment']},ensure_ascii=False,indent=2))
