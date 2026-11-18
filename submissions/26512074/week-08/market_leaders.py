"""Public market leadership snapshots; no credentials, orders or model calls."""
import json,time,urllib.request
from pathlib import Path
from collections import Counter,defaultdict
from statistics import median

ROOT=Path(__file__).resolve().parent
SELLERS=['dae-yangkim','inextro','yms979','Kiro729','myeonggi','dragonash','totato2203-r3']
BUYERS=['inextro','psyoung82','shaun0927-labbuyer','shaun0927','yms979','totato2203-r3']

def review(board=None,shops=None):
    def get(path):
        with urllib.request.urlopen('https://market.ourolabs.ai'+path,timeout=20) as r:return json.load(r)
    board=board if board is not None else get('/api/board?round=3')
    shops=shops if shops is not None else get('/api/shops')['shops']
    forum=json.loads((ROOT/'.market/forum.json').read_text(encoding='utf-8-sig'))
    now=time.time();catalog=json.loads((ROOT/'.market/catalog.json').read_text(encoding='utf-8-sig'))
    aliases={}
    for s in shops+board['shops']:
        for a in (s['name'],s.get('url'),s.get('card',{}).get('url')):
            if a:aliases[a]=s['name']
    for l in (ROOT/'.market/recommendation-quotes.jsonl').open(encoding='utf-8'):
        try:q=json.loads(l)
        except ValueError:continue
        if q.get('shop') and q.get('url'):aliases[q['url']]=q['shop']
    events={}
    for l in (ROOT/'.market/ledger.jsonl').open(encoding='utf-8'):
        try:e=json.loads(l)
        except ValueError:continue
        if e.get('round')==3 and 0<=now-e.get('ts',0)<14400:events[e['seq']]=e
    settlements=sorted((e for e in events.values() if e['kind']=='settle'),key=lambda e:e['ts'])
    current=[e for e in settlements if now-e['ts']<7200]
    def agg(rows):
        amounts=Counter();counts=Counter();ratios=[];goods=[];high=[]
        for e in rows:
            d=e['data'];sku=d.get('sku') or d.get('item');p=catalog.get(sku,{})
            amounts[sku]+=d['amount'];counts[sku]+=1
            if p.get('list_price'):ratios.append(d['amount']/p['list_price'])
            if p.get('kind')=='goods':goods.append(e)
            if p.get('kind')=='goods' and p.get('list_price',0)>=200000:high.append(e)
        total=sum(amounts.values())
        return dict(deals=len(rows),revenue=total,revenue_hour=total/2,average=total/max(1,len(rows)),
          price_list_median=median(ratios) if ratios else None,high_revenue_share=sum(e['data']['amount'] for e in high)/max(1,total),
          goods_warranty_share=sum(bool(e['data'].get('warranty_hours')) for e in goods)/max(1,len(goods)),
          warranty_hours=dict(Counter(str(e['data'].get('warranty_hours',0) or 0) for e in goods)),
          top_skus=[dict(sku=s,name=catalog.get(s,{}).get('name',s),revenue=a,count=counts[s]) for s,a in amounts.most_common(3)])
    result=dict(at=now,window_hours=2,ledger_age=now-max(e['ts'] for e in events.values()),shops={},buyers={})
    for name in SELLERS:
        s=next((s for s in board['shops'] if s['name']==name),None)
        if not s:continue
        rows=[e for e in current if aliases.get(e['data'].get('shop'))==name];r=agg(rows)
        supply=sum(e['data'].get('total',0) for e in events.values() if e['kind']=='supply' and now-e['ts']<7200 and aliases.get(e['data'].get('shop'))==name)
        r.update(board={k:s.get(k) for k in ('profit','revenue','all','deals','stock_cost','stock_units','stars')},
                 supply=supply,partial_cash_hour=(r['revenue']-supply)/2,
                 stock_turnover_proxy=r['revenue']/max(1,s.get('stock_cost',0)),
                 description=s.get('card',{}).get('description'),top_buyers=Counter(e['data'].get('buyer') for e in rows).most_common(3))
        result['shops'][name]=r
    for name in BUYERS:
        b=next((b for b in board['buyers'] if b['name']==name),None)
        if not b:continue
        rows=[e for e in current if e['data'].get('buyer')==name];r=agg(rows)
        r.update(surplus=b.get('surplus'),served=b.get('served'),orders=b.get('deals'),
                 served_rate=b.get('served',0)/max(1,b.get('deals',0)),
                 surplus_per_served=b.get('surplus',0)/max(1,b.get('served',0)),
                 distinct_sellers=len({e['data']['shop'] for e in rows}),
                 top_sellers=Counter(aliases.get(e['data']['shop'],e['data']['shop']) for e in rows).most_common(3))
        result['buyers'][name]=r
    result['forum']=[dict(author=p.get('author'),role=p.get('role'),text=p.get('text','')[:1800]) for p in forum
                     if p.get('author') in set(SELLERS+BUYERS)]
    (ROOT/'logs/leader-market-current.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    r=review()
    for role in ('shops','buyers'):
        for name,v in r[role].items():
            print(role,name,json.dumps({k:v[k] for k in v if k not in ('description','top_skus')},ensure_ascii=False))
            print('TOP',json.dumps(v['top_skus'],ensure_ascii=False))
