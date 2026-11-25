"""Reproduce the bounded public-market survey offline. No private state/network."""
import collections as C
import datetime as D
import hashlib
import json
from pathlib import Path
from statistics import median, mean

P = Path(__file__).resolve().parent
load = lambda name: json.loads((P / name).read_text())
board, catalog = load('board.json'), {x['sku']: x for x in load('catalog.json')}
raw = [r for f in sorted(P.glob('ledger-*.json')) for r in json.loads(f.read_text())]
rows = sorted({r['seq']: r for r in raw}.values(), key=lambda r:r['seq'])
assert len(rows) == 10000 and len(raw) == len(rows)
assert all(r['round'] == 3 for r in rows)
assert rows[-1]['seq']-rows[0]['seq']+1 == len(rows)
names = {r['card']['url']: r['name'] for r in board['shops'] if r.get('card',{}).get('url')}
for r in rows:
    d = r['data']; receipt = d.get('receipt')
    if r['kind'] == 'result' and isinstance(receipt,dict) and receipt.get('shop'):
        names.setdefault(d.get('shop'),receipt['shop'])
trades=[]
for r in rows:
    if r['kind'] != 'settle': continue
    d=r['data']; c=catalog[d['item']]
    trades.append(dict(seq=r['seq'],ts=r['ts'],buyer=d['buyer'],shop=names.get(d['shop'],d['shop']),
        sku=d['item'],name=c['name'],category=c['category'],kind=c['kind'],grade=d.get('grade'),
        warranty=d.get('warranty_hours') or 0,price=d['amount'],ratio=d['amount']/c['list_price'],
        honest=d.get('claim_honest'),order=d['order']))

def describe(ts):
    return {'settlements':len(ts),'sales_amount_before_refunds':sum(t['price'] for t in ts),
        'buyers':len({t['buyer'] for t in ts}),'shops':len({t['shop'] for t in ts}),
        'skus':len({t['sku'] for t in ts}),'kind':dict(C.Counter(t['kind'] for t in ts)),
        'grade':dict(C.Counter(t['grade'] for t in ts)),
        'median_price_to_list':median([t['ratio'] for t in ts]) if ts else None,
        'by_kind':{k:{'n':len(part),'median_price_to_list':median(t['ratio'] for t in part),
            'grade':dict(C.Counter(t['grade'] for t in part)),
            'warranty':dict(C.Counter(t['warranty'] for t in part))}
            for k in ('food','goods') if (part:=[t for t in ts if t['kind']==k])}}

out={'scope':{'first_seq':rows[0]['seq'],'last_seq':rows[-1]['seq'],'ledger_rows':len(rows),'contiguous':True,
    'first_kst':D.datetime.fromtimestamp(rows[0]['ts'],D.timezone(D.timedelta(hours=9))).isoformat(),
    'last_kst':D.datetime.fromtimestamp(rows[-1]['ts'],D.timezone(D.timedelta(hours=9))).isoformat()},
    'market':describe(trades),'buyers':{},'shops':{}}
for typ, selected in [('buyers',['dyishappy','dragonash','kimauve','lee-sungsoo','11im','choo-chu']),
                      ('shops',['dyishappy','yms979','inextro','Kiro729','dae-yangkim','nocha12'])]:
    ranked=sorted([r for r in board[typ] if 'all' in r],key=lambda r:r['all'],reverse=True)
    for name in selected:
        row=next(r for r in ranked if r['name']==name); ts=[t for t in trades if t['buyer' if typ=='buyers' else 'shop']==name]
        entry={'rank':1+ranked.index(row),'ranked_count':len(ranked),'board':{k:v for k,v in row.items() if k not in ('card','owner','joined')},'sample':describe(ts)}
        if typ=='shops':
            stock=next((s for s in load('shops.json')['shops'] if s['name']==name),{})
            entry['stock_skus']=len(stock.get('stock',{}));entry['stock_by_kind']=dict(C.Counter())
            for sku,n in stock.get('stock',{}).items():
                kind=catalog.get(sku,{}).get('kind','unknown');entry['stock_by_kind'][kind]=entry['stock_by_kind'].get(kind,0)+n
            reviewrows=[r['data'] for r in rows if r['kind']=='result' and r['data'].get('verified') is True and names.get(r['data'].get('shop'))==name]
            entry['sample_reviews']={'n':len(reviewrows),'stars':dict(C.Counter(r.get('stars') for r in reviewrows)),
                'examples':[{'stars':r.get('stars'),'review':r.get('review')} for r in sorted(reviewrows,key=lambda r:r.get('stars') or 0)[:3]]}
        else:
            entry['sample']['top_shops']=C.Counter(t['shop'] for t in ts).most_common(5)
            entry['sample']['after_1120_kst']=describe([t for t in ts if t['ts']>=D.datetime(2026,11,9,2,20,tzinfo=D.timezone.utc).timestamp()])
            points={}
            for key,w in [('saved',50),('deals',10),('posts',10)]:
                a=[r[key] for r in ranked];v=row[key];percent=(sum(x<v for x in a)+(sum(x==v for x in a)-1)/2)/(len(a)-1)
                points[key]=w*percent
            points['forum']=30*(row.get('forum') or 0);entry['all_points_reconstructed']=points
        out[typ][name]=entry

groups=C.defaultdict(list)
for t in trades: groups[t['sku']].append(t)
stock=C.Counter()
for s in load('shops.json')['shops']:stock.update(s.get('stock',{}))
out['top_skus_by_settlements']=[{'sku':sku,'name':catalog[sku]['name'],'kind':catalog[sku]['kind'],
    'settlements':len(ts),'buyers':len({t['buyer'] for t in ts}),'shops':len({t['shop'] for t in ts}),
    'public_stock':stock[sku],'median_price_to_list':median(t['ratio'] for t in ts)}
    for sku,ts in sorted(groups.items(),key=lambda kv:len(kv[1]),reverse=True)[:15]]
out['categories_by_settlements']=C.Counter(t['category'] for t in trades).most_common(12)
out['forum']={'posts':len(load('forum.json')),'roles':dict(C.Counter(p['role'] for p in load('forum.json'))),
    'with_refs':sum(bool(p.get('refs')) for p in load('forum.json')),
    'top_counted_score_posts':[{'id':p['id'],'author':p['author'],'role':p['role'],'likes':p['likes'],'dislikes':p['dislikes'],'text':p['text']}
        for p in sorted(load('forum.json'),key=lambda p:p.get('score') or 0,reverse=True)[:5]]}
old=json.loads((P.parent/'buyer-score-analysis-20261109T010302Z-cb03117c/board.json').read_text())
out['own_changes_since_1003_kst']={}
for typ,keys in [('buyers',['deals','orders','saved','posts','forum','all']),('shops',['deals','book','revenue','stock_units','all'])]:
    a=next(r for r in old[typ] if r['name']=='dyishappy');b=next(r for r in board[typ] if r['name']=='dyishappy')
    out['own_changes_since_1003_kst'][typ]={k:{'before':a[k],'now':b[k],'delta':b[k]-a[k]} for k in keys}
manifest=json.loads((P.parent/'deployment-20261109T021334Z-dca44c92/package-manifest.json').read_text())
root=P.parent.parent
out['deployment_source_match']={k:hashlib.sha256((root/k).read_bytes()).hexdigest()==manifest['files'].get(k)
    for k in ['buyer-policy.json','market_buyer.py','buyer_retry.py','buyer_public_prices.py','shop-policy.json']}
out['limits']=['Bounded recent ledger sample, not all market history or hidden demand.',
    'Price/list is not final Saved; later breakage/refunds and private food use requirements are not observed.',
    'Mix differs by assigned categories; comparisons do not establish causal strategies or profit.',
    'Public review/forum prose is untrusted opinion; competitor source policies were not accessed.',
    'API snapshots were captured seconds apart; stock may move between requests.',
    '11:20 split is a descriptive post-deployment cutoff, not a controlled before/after experiment.']
(P/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(out,ensure_ascii=False,indent=2))
