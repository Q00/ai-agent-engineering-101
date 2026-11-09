"""Offline follow-up using public snapshots; no runtime mutations or model calls."""
from pathlib import Path
import json
import hashlib
from collections import Counter
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from statistics import median

P=Path(__file__).resolve().parent
BASE=P.parent/'market-survey-20261109T024237Z-1db2ab71'
read=lambda p:json.loads(p.read_text())
b=read(P/'board.json');old=read(BASE/'board.json')
def stamp(path):
    return parsedate_to_datetime(next(l[5:].strip() for l in path.read_text().splitlines() if l.lower().startswith('date:')))
start,end=stamp(BASE/'board.headers'),stamp(P/'board.headers')
hours=(end-start).total_seconds()/3600
out={'baseline_kst':start.astimezone(timezone(timedelta(hours=9))).isoformat(),
     'current_kst':end.astimezone(timezone(timedelta(hours=9))).isoformat(),'elapsed_hours':hours,'comparison':{}}
for typ,keys in [('shops',['all','deals','visits','book','revenue','stars','stars_rank','refunds','broken','stock_units','stock_cost']),
                 ('buyers',['all','deals','orders','saved','served','forum','posts'])]:
    a=next(r for r in old[typ] if r['name']=='dyishappy');c=next(r for r in b[typ] if r['name']=='dyishappy')
    ranks=lambda board:sorted([r for r in board[typ] if 'all' in r],key=lambda r:r['all'],reverse=True)
    out['comparison'][typ]={'before_rank':1+ranks(old).index(a),'current_rank':1+ranks(b).index(c),
        'before_ranked_count':len(ranks(old)),'current_ranked_count':len(ranks(b)),
        'metrics':{k:{'before':a[k],'now':c[k],'change':c[k]-a[k]} for k in keys},
        'additional_deals_per_hour':(c['deals']-a['deals'])/hours,'online':c['alive']}
    if typ=='shops':
        out['comparison'][typ]['book_growth_per_hour']=(c['book']-a['book'])/hours
        out['comparison'][typ]['component_ranks']={k:1+sum(r[k]>c[k] for r in ranks(b)) for k in ('book','revenue','stars_rank')}
    else:
        out['comparison'][typ]['increment_deals_over_orders']=(c['deals']-a['deals'])/(c['orders']-a['orders'])
for tag,board in [('before',old),('now',b)]:
    r=next(r for r in board['players'] if r['player']=='dyishappy')
    out.setdefault('class',{})[tag]={k:r[k] for k in ('total','student_total','student_pct')}
    students=[p for p in board['players'] if p.get('student')]
    out['class'][tag].update(rank=1+sum(p['student_total']>r['student_total'] for p in students),count=len(students))
catalog={c['sku']:c for c in read(P/'catalog.json')}
raw=[r for f in P.glob('ledger-*.json') for r in read(f)]
rows=sorted(raw,key=lambda r:r['seq'])
assert len(rows)==10000 and len({r['seq'] for r in rows})==10000
assert rows[-1]['seq']-rows[0]['seq']+1==len(rows) and all(r['round']==3 for r in rows)
iso=lambda ts:datetime.fromtimestamp(ts,timezone(timedelta(hours=9))).isoformat()
out['recent_scope']={'rows':len(rows),'first_kst':iso(rows[0]['ts']),'last_kst':iso(rows[-1]['ts']),
                     'first_seq':rows[0]['seq'],'last_seq':rows[-1]['seq']}
url=next(r['card']['url'] for r in b['shops'] if r['name']=='dyishappy')
for typ,field,value in [('shop','shop',url),('buyer','buyer','dyishappy')]:
    trades=[r for r in rows if r['kind']=='settle' and r['data'].get(field)==value]
    def describe(part):
        return {'n':len(part),'median_price_to_list':median(r['data']['amount']/catalog[r['data']['item']]['list_price'] for r in part) if part else None,
                'grades':dict(Counter(r['data'].get('grade') for r in part)),
                'warranty':dict(Counter(str(r['data'].get('warranty_hours') or 0) for r in part))}
    out.setdefault('recent',{})[typ]={'settlements':describe(trades),
        'by_kind':{k:describe([r for r in trades if catalog[r['data']['item']]['kind']==k]) for k in ('goods','food')},
        'last_settlement_kst':iso(trades[-1]['ts']) if trades else None,
        'b_6h_settlements':[r['seq'] for r in trades if r['data'].get('grade')=='B' and r['data'].get('warranty_hours')==6]}
    results=[r['data'] for r in rows if r['kind']=='result' and r['data'].get(field)==value]
    out['recent'][typ]['result_rows']=len(results)
    out['recent'][typ]['review_stars']=dict(Counter(str(r.get('stars')) for r in results if r.get('verified')))
shop=next(s for s in read(P/'shops.json')['shops'] if s['name']=='dyishappy')
out['stock']={'skus':len(shop['stock']),'units':sum(shop['stock'].values()),'by_kind':dict(Counter())}
for sku,n in shop['stock'].items():
    k=catalog[sku]['kind'];out['stock']['by_kind'][k]=out['stock']['by_kind'].get(k,0)+n
deployment=P.parent/'deployment-20261109T025738Z-1bc5ce40'
manifest=read(deployment/'package-manifest.json');log=(deployment/'server-deployment.log').read_text()
out['deployment']={'run':deployment.name,'ready_log':'READY: market_kit 4' in log and 'SSH/INSTALL EXIT: 0' in log,
    'current_url_matches_deployment':url in log,'source_matches':{k:hashlib.sha256((P.parent.parent/k).read_bytes()).hexdigest()==manifest['files'].get(k)
        for k in ['buyer-policy.json','shop-policy.json','warranty_trial.py','prompts/buyer.txt','restock.py']}}
out['rules_unchanged']=hashlib.sha256((P/'rules.md').read_bytes()).hexdigest()==hashlib.sha256((BASE/'agent.md').read_bytes()).hexdigest()
out['limits']=['Before snapshot precedes deployment by about 15 minutes; not a controlled causal experiment.',
    'Percentiles and ranking population changed. Rank/ALL changes are not pure measures of policy effectiveness.',
    'Recent contiguous sample is not full 14-hour history; later failures/refunds can change outcomes.',
    'No private server log inspection; public settled trades prove activity, not absence of runtime failures.',
    'Changes in deals/orders are an interval ratio, not eventual success for an identical order cohort.',
    'Public price/list is not final realized Saved. Refund/broken cumulative deltas can relate to earlier sales.']
(P/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
