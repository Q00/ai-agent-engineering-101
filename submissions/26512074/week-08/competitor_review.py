"""Reproducible review of public market snapshots; no credentials or model calls."""
from collections import Counter,defaultdict
from pathlib import Path
from statistics import median
import json
import time
from round3 import lines

ROOT=Path(__file__).resolve().parent


def review():
    folder=ROOT/'logs/competitor-review'
    board=json.loads((folder/'board.json').read_text(encoding='utf-8'))
    shops=json.loads((folder/'shops.json').read_text(encoding='utf-8'))['shops']
    forum=json.loads((folder/'forum.json').read_text(encoding='utf-8'))
    catalog=json.loads((ROOT/'.market/catalog.json').read_text(encoding='utf-8'))
    aliases={}
    for shop in shops+board['shops']:
        for alias in (shop.get('name'),shop.get('url'),(shop.get('card') or {}).get('url')):
            if alias:
                aliases[alias]=shop['name']
    for quote in lines(ROOT/'.market/recommendation-quotes.jsonl'):
        if quote.get('shop') and quote.get('url'):
            aliases[quote['url']]=quote['shop']
    all_events=lines(ROOT/'.market/ledger.jsonl')
    now=time.time()
    events=[]
    seen=set()
    for event in all_events:
        seq=event.get('seq')
        if seq in seen or event.get('round')!=3 or not 0<=now-event.get('ts',0)<7200:
            continue
        seen.add(seq)
        events.append(event)
    settled=[e for e in events if e.get('kind')=='settle']
    targets=['inextro','dae-yangkim','psyoung82','totato2203-r3']
    result={'at':now,'window_hours':2,'ledger_newest':max(e.get('ts',0) for e in all_events),
            'ledger_oldest':min(e.get('ts',now) for e in all_events),
            'sources':['https://market.ourolabs.ai/api/board?round=3','https://market.ourolabs.ai/api/shops',
                       'https://market.ourolabs.ai/api/forum?limit=100','https://market.ourolabs.ai/api/ledger'],
            'shops':{},'buyers':{},'forum':{}}
    def aggregate(rows):
        prices=[e['data']['amount'] for e in rows]
        ratios=[e['data']['amount']/catalog[e['data'].get('sku') or e['data'].get('item')]['list_price']
                for e in rows if (e['data'].get('sku') or e['data'].get('item')) in catalog]
        sku_counts=Counter(e['data'].get('sku') or e['data'].get('item') for e in rows)
        amounts=Counter()
        for event in rows:
            sku=event['data'].get('sku') or event['data'].get('item')
            amounts[sku]+=event['data']['amount']
        return {'deals':len(rows),'revenue':sum(prices),'average':sum(prices)/len(prices) if prices else None,
                'median':median(prices) if prices else None,'median_price_list_ratio':median(ratios) if ratios else None,
                'grades':dict(Counter(e['data'].get('grade') for e in rows)),
                'warranty_deals':sum(bool(e['data'].get('warranty_hours')) for e in rows),
                'goods_deals':sum(catalog.get(e['data'].get('sku') or e['data'].get('item'),{}).get('kind')=='goods' for e in rows),
                'goods_revenue':sum(e['data']['amount'] for e in rows if catalog.get(e['data'].get('sku') or e['data'].get('item'),{}).get('kind')=='goods'),
                'top_skus':[{'sku':sku,'name':catalog.get(sku,{}).get('name'), 'count':sku_counts[sku],
                             'revenue':amount} for sku,amount in amounts.most_common(5)]}
    for name in targets:
        shop=next(s for s in board['shops'] if s['name']==name)
        rows=[e for e in settled if aliases.get(e['data'].get('shop'),e['data'].get('shop'))==name]
        supplies=[e for e in events if e.get('kind')=='supply' and aliases.get(e['data'].get('shop'),e['data'].get('shop'))==name]
        out=aggregate(rows)
        out['supply_spend']=sum(e['data'].get('total',0) for e in supplies)
        out['supplied_units']=sum(e['data'].get('qty',0) for e in supplies)
        out['cash_before_other_costs']=out['revenue']-out['supply_spend']
        buyers=Counter(e['data'].get('buyer') for e in rows)
        out['top_buyers']=buyers.most_common(5)
        out['board']={key:shop.get(key) for key in ('joined','deals','revenue','profit','all','stars','visits','refunds','claims','honest_claims')}
        result['shops'][name]=out
        buyer=next(b for b in board['buyers'] if b['name']==name)
        rows=[e for e in settled if e['data'].get('buyer')==name]
        bought=aggregate(rows)
        sellers=Counter(aliases.get(e['data'].get('shop'),e['data'].get('shop')) for e in rows)
        bought['top_sellers']=sellers.most_common(8)
        bought['distinct_sellers']=len(sellers)
        bought['board']={key:buyer.get(key) for key in ('joined','orders','deals','paid','saved','surplus','served','all','forum','posts')}
        bought['surplus_per_served']=buyer.get('surplus',0)/max(1,buyer.get('served',0))
        result['buyers'][name]=bought
        owner=(shop.get('owner') or {}).get('login')
        result['forum'][name]=[{'id':p['id'],'role':p.get('role'),'ts':p.get('ts'),'text':p.get('text')}
                               for p in forum if p.get('author') in (name,owner)]
    (folder/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    data=review()
    print(json.dumps(data,ensure_ascii=False))
