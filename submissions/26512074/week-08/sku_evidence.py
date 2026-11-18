"""Gate four leader SKUs using exact one-unit supply costs and mature sales."""
import json,time
from pathlib import Path
from collections import defaultdict,Counter
from statistics import median

TARGETS={'Gb5d0dde7','G8ec51935','G200e39a3','G1515d951'}


def evaluate(ledger,catalog,now,aliases=None):
    aliases=aliases or {};unique={e['seq']:e for e in ledger if e.get('seq') is not None and e.get('round')==3}
    supplies={};refunds=Counter();sales=defaultdict(list);recent=defaultdict(list);sold=set()
    for e in unique.values():
        d=e.get('data',{})
        if e['kind']=='broken' and d.get('unit'):refunds[d['unit']]+=d.get('refunded',0)
        if e['kind']=='refund' and d.get('unit'):refunds[d['unit']]+=d.get('amount',0)
    for e in sorted(unique.values(),key=lambda e:e['seq']):
        d=e.get('data',{});sku=d.get('sku') or d.get('item')
        if sku not in TARGETS:continue
        if e['kind']=='supply' and len(d.get('units',[]))==1:
            supplies[d['units'][0]]=(d['total'],e['ts'],d.get('shop'),sku)
        if e['kind']!='settle' or not 0<=now-e['ts']<86400:continue
        sold.add(d.get('unit'))
        recent[sku].append(e)
        source=supplies.get(d.get('unit'))
        seller=aliases.get(d.get('shop'),d.get('shop'))
        if not source or seller!=aliases.get(source[2],source[2]) or source[3]!=sku:continue
        cost,at,_,_=source;hours=d.get('warranty_hours',0) or 0
        if e['ts']+hours*3600+300>now:continue
        storage=cost*.01*max(0,e['ts']-at)/86400 if catalog.get(sku,{}).get('kind')=='goods' else 0
        sales[sku].append(dict(shop=seller,cost=cost,revenue=d['amount'],net=d['amount']-cost-storage-refunds[d['unit']],
                               turnover_hours=max(0,e['ts']-at)/3600,grade=d.get('grade'),warranty=hours))
    result={'at':now,'skus':{}}
    for sku in sorted(TARGETS):
        rows=sales[sku];cost=sum(r['cost'] for r in rows);net=sum(r['net'] for r in rows)
        fresh=[e for e in recent[sku] if now-e['ts']<7200]
        buyers={e['data'].get('buyer') for e in fresh};sellers={aliases.get(e['data'].get('shop'),e['data'].get('shop')) for e in fresh}
        turnover=median([r['turnover_hours'] for r in rows]) if rows else None
        roi=net/cost if cost else None
        stale=[v for unit,v in supplies.items() if v[3]==sku and unit not in sold and 7200<now-v[1]<86400]
        supplied_cost=sum(v[0] for v in supplies.values() if v[3]==sku and 0<=now-v[1]<86400)
        stale_cost=sum(v[0] for v in stale)
        eligible=len(rows)>=5 and len(fresh)>=10 and len(buyers)>=3 and len(sellers)>=2 and roi>=.08 and turnover<=2 and stale_cost/max(1,supplied_cost)<=.25
        per_shop={}
        for shop in {r['shop'] for r in rows}:
            rs=[r for r in rows if r['shop']==shop];c=sum(r['cost'] for r in rs)
            per_shop[shop]=dict(exact_mature_sales=len(rs),net=sum(r['net'] for r in rs),roi=sum(r['net'] for r in rs)/c,
                turnover_hours=median(r['turnover_hours'] for r in rs),warranty_share=sum(bool(r['warranty']) for r in rs)/len(rs))
        result['skus'][sku]=dict(name=catalog.get(sku,{}).get('name',sku),eligible=eligible,
            exact_mature_sales=len(rows),modeled_net=net,roi=roi,median_turnover_hours=turnover,
            stale_unsold_cost=stale_cost,stale_cost_share=stale_cost/max(1,supplied_cost),
            recent_trades=len(fresh),recent_buyers=len(buyers),recent_sellers=len(sellers),per_shop=per_shop,
            reason='mature positive unit economics and diverse recent demand' if eligible else 'insufficient mature evidence, demand, return or turnover')
    return result


def update(md,now=None):
    md=Path(md);now=time.time() if now is None else now
    catalog=json.loads((md/'catalog.json').read_text(encoding='utf-8-sig'));events=[];aliases={}
    for name in ('shops.json','baseline-shops.json'):
        path=md/name
        if path.exists():
            data=json.loads(path.read_text(encoding='utf-8-sig'))
            for s in data.get('shops',[]) if isinstance(data,dict) else data:
                if s.get('url'):aliases[s['url']]=s['name']
    for l in (md/'recommendation-quotes.jsonl').open(encoding='utf-8'):
        try:q=json.loads(l)
        except ValueError:continue
        if q.get('shop') and q.get('url'):aliases[q['url']]=q['shop']
    for l in (md/'ledger.jsonl').open(encoding='utf-8'):
        try:events.append(json.loads(l))
        except ValueError:pass
    result=evaluate(events,catalog,now,aliases)
    from optimizer import atomic
    atomic(md/'leader-sku-evidence.json',result)
    return result

if __name__=='__main__':
    r=update(Path(__file__).resolve().parent/'.market')
    for sku,v in r['skus'].items():print(sku,json.dumps(v,ensure_ascii=False))
