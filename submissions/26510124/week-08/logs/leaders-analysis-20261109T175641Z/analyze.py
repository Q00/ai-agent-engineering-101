"""Offline leader comparison from public market snapshots, no private strategies."""
from pathlib import Path
import json
from collections import Counter
from statistics import median
from datetime import datetime, timezone, timedelta

P=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
b=read(P/'board.json');catalog={r['sku']:r for r in read(P/'catalog.json')}
shops={r['name']:r for r in read(P/'shops.json')['shops']}
old=read(P.parent/'market-survey-20261109T024237Z-1db2ab71/board.json')
rows=sorted([r for f in P.glob('ledger-*.json') for r in read(f)],key=lambda r:r['seq'])
assert len(rows)==10000==len({r['seq'] for r in rows}) and rows[-1]['seq']-rows[0]['seq']==9999
assert all(r['round']==3 for r in rows)
names={r['card']['url']:r['name'] for r in b['shops'] if r.get('card',{}).get('url')}
for r in rows:
    d=r['data'];receipt=d.get('receipt')
    if r['kind']=='result' and isinstance(receipt,dict) and receipt.get('shop'):
        names.setdefault(d.get('shop'),receipt['shop'])
trades=[]
for r in rows:
    if r['kind']!='settle':continue
    d=r['data'];c=catalog[d['item']]
    trades.append(dict(seq=r['seq'],shop=names.get(d['shop'],d['shop']),buyer=d['buyer'],
        sku=d['item'],price=d['amount'],ratio=d['amount']/c['list_price'],kind=c['kind'],
        category=c['category'],grade=d.get('grade'),warranty=d.get('warranty_hours') or 0))
iso=lambda t:datetime.fromtimestamp(t,timezone(timedelta(hours=9))).isoformat()
out={'scope':{'from':iso(rows[0]['ts']),'to':iso(rows[-1]['ts']),'rows':len(rows),'settlements':len(trades)},'shops':{},'buyers':{}}
def stats(ts):
    return {'n':len(ts),'median_price':median(t['price'] for t in ts) if ts else None,
        'median_paid_to_list':median(t['ratio'] for t in ts) if ts else None,
        'kinds':dict(Counter(t['kind'] for t in ts)),
        'grades':dict(Counter(t['grade'] for t in ts)),
        'goods_warranties':dict(Counter(str(t['warranty']) for t in ts if t['kind']=='goods')),
        'top_categories':Counter(t['category'] for t in ts).most_common(3),
        'by_kind':{k:{'n':len(part),'median_paid_to_list':median(t['ratio'] for t in part),
                      'grades':dict(Counter(t['grade'] for t in part))}
                   for k in ('goods','food') if (part:=[t for t in ts if t['kind']==k])},
        'above_list_count':sum(t['ratio']>1 for t in ts)}
for typ in ('shops','buyers'):
    ranked=sorted([r for r in b[typ] if 'all' in r],key=lambda r:r['all'],reverse=True)
    for r in ranked[:6]+[r for r in ranked if r['name']=='dyishappy']:
        name=r['name'];ts=[t for t in trades if t['shop' if typ=='shops' else 'buyer']==name]
        prior=next((o for o in old[typ] if o['name']==name),{})
        entry={'rank':1+ranked.index(r),'board':{k:v for k,v in r.items() if k not in ('owner','card','joined')},'recent':stats(ts)}
        metrics=['book','revenue','deals','refunds','broken'] if typ=='shops' else ['saved','deals','posts']
        entry['change_since_nov9_1145']={k:r[k]-prior[k] for k in metrics if k in prior}
        if typ=='shops':
            entry['card_claims_not_independently_verified']=r.get('card',{}).get('description')
            stock=shops.get(name,{}).get('stock',{})
            entry['stock']={'skus':len(stock),'units':sum(stock.values()),'by_kind':dict(Counter())}
            for sku,n in stock.items():
                kind=catalog[sku]['kind'];entry['stock']['by_kind'][kind]=entry['stock']['by_kind'].get(kind,0)+n
            entry['component_ranks_min_tie']={k:1+sum(o[k]>r[k] for o in ranked) for k in ('book','revenue','stars_rank')}
        else:
            weights={'saved':50,'deals':10,'posts':10}
            entry['all_component_points']={k:w*(sum(o[k]<r[k] for o in ranked)+(sum(o[k]==r[k] for o in ranked)-1)/2)/(len(ranked)-1) for k,w in weights.items()}
            entry['all_component_points']['forum']=30*(r.get('forum') or 0)
            entry['all_reconstruction_error']=sum(entry['all_component_points'].values())/100-r['all']
            entry['recent']['shop_counts']=Counter(t['shop'] for t in ts).most_common(5)
        reviews=[r['data'] for r in rows if r['kind']=='result' and r['data'].get('verified') and
                 (names.get(r['data'].get('shop'))==name if typ=='shops' else r['data'].get('buyer')==name)]
        entry['recent_reviews']={'n':len(reviews),'stars':dict(Counter(str(r.get('stars')) for r in reviews)),
             'low_examples':[{'stars':r.get('stars'),'review':r.get('review')} for r in reviews if r.get('stars') in (1,2)][:2]}
        out[typ][name]=entry
out['forum']=[{'author':r['author'],'role':r['role'],'likes':r['likes'],'dislikes':r['dislikes'],'text':r['text']}
    for r in read(P/'forum.json') if r['author'] in out['buyers'] or r['author'] in out['shops']]
out['limits']=['Bounded recent sample, not competitor code, hidden prompts, costs, latencies or complete history.',
    'Assigned categories differ. Price/list mixes do not isolate bargaining skill or future realized Saved.',
    'Board components are percentiles except buyer forum raw rating. Rounded board fields cause small reconstruction error.',
    'Book changes include observed refunds, storage and spoilage; high historical rank is not proof of current positive profit.',
    'Public card and review/forum text are claims, not verified strategies. No new policy changes or external messages.']
(P/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
