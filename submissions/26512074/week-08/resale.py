"""Forum sale leads and verified resale provenance; no unsupported asset transfers."""
from collections import defaultdict
import math
import re
from statistics import median
from pathlib import Path
from shop_profit import ownership_tags


def leads(forum,shops,catalog,now,limit=6):
    ours=ownership_tags()|{'totato2203'}
    sellers={s['name']:s for s in shops if s['name'] not in ours and s.get('url') not in ours}
    result=[]
    seen=set()
    for post in sorted(forum,key=lambda p:p.get('ts',0),reverse=True):
        if not 0<=now-post.get('ts',0)<=3600 or post.get('author') in ours:
            continue
        text=post.get('text','')
        if not any(word in text.lower() for word in ('특가','할인','떨이','중앙값','discount','below','cheap','clearance','sale')):
            continue
        for line in text.splitlines():
            shop=sellers.get(post.get('author')) if post.get('role')=='shop' else None
            if shop is None:
                mentioned=[s for name,s in sellers.items() if s.get('url') in line or
                           re.search(r'(?<![\w-])'+re.escape(name)+r'(?![\w-])',line)]
                if len(mentioned)!=1:
                    continue
                shop=mentioned[0]
            for sku in re.findall(r'\bG[0-9a-fA-F]{8}\b',line):
                key=(shop['name'],sku)
                if sku not in catalog or key in seen or (shop.get('stock') or {}).get(sku,0)<=0:
                    continue
                seen.add(key)
                result.append({'shop':shop,'sku':sku,'post_id':post.get('id'),'post_at':post['ts']})
                if len(result)>=limit:
                    return result
    return result


def inspect(md,forum,shops,catalog,inventory,ledger,quotes,now):
    from optimizer import atomic
    wanted=leads(forum,shops,catalog,now)
    sources={(lead['shop']['name'],lead['sku']):lead for lead in wanted}
    groups=defaultdict(list)
    own=ownership_tags()|{'totato2203'}
    owned={u['unit']:u for u in inventory.get('units',[]) if u.get('unit') and u.get('grade')!='expired'
           and (u.get('expires_at') is None or u['expires_at']>now)}
    seen=set()
    verified={}
    for event in ledger:
        data=event.get('data',{});seq=event.get('seq');price=data.get('amount')
        if (seq is None or seq in seen or event.get('kind')!='settle' or event.get('round',3)!=3
                or not isinstance(price,(int,float)) or isinstance(price,bool) or not math.isfinite(price) or price<=0):
            continue
        seen.add(seq)
        sku=data.get('sku') or data.get('item')
        if 0<=now-event.get('ts',0)<7200 and data.get('shop') not in own:
            groups[sku,data.get('grade')].append(price)
        unit=owned.get(data.get('unit'))
        if (unit and data.get('buyer') in own and data.get('shop') not in own
                and sku==unit['sku'] and 0<=now-event.get('ts',0)
                and unit['cost']>0):
            verified[unit['unit']]={'sku':sku,'purchase_seq':seq,'acquisition_cost':unit['cost'],
                                    'paid_price':price,'opening_markup':1.0}
    opportunities=[]
    for quote in quotes:
        source=sources.get((quote['shop'],quote['sku']))
        if not source or not 0<=now-quote['at']<=300:
            continue
        if catalog.get(quote['sku'],{}).get('kind')=='food':
            # A cheap quote alone cannot establish remaining certified life.
            continue
        samples=groups.get((quote['sku'],quote.get('grade')),[])
        if len(samples)<3:
            continue
        baseline=median(samples)
        if 0<quote['price']<=baseline*.50:
            opportunities.append({'sku':quote['sku'],'shop':quote['shop'],'url':quote['url'],
                'forum_post':source['post_id'],'quote':quote['price'],'quote_at':quote['at'],
                'observed_grade':quote.get('grade'),'median':baseline,'samples':len(samples),
                'discount':1-quote['price']/baseline,'planned_opening':math.ceil(quote['price']*2),
                'status':'requires_verified_shop_inventory_transfer'})
    atomic(Path(md)/'resale-units.json',{'at':now,'units':verified})
    result={'at':now,'forum_leads':len(wanted),'opportunities':opportunities,
            'verified_inventory_units':len(verified),'inventory_transfer_api_verified':False,
            'purchase_executed':False,'reason':'Official kit v4 has order-bound checkout; shop inventory transfer is not documented.'}
    atomic(Path(md)/'resale-opportunities.json',result)
    return result


def purchase_intent(md,url,sku,now):
    """Only market-issued orders can fund checkout; an intent cannot create one."""
    from round3 import read
    state=read(Path(md)/'resale-opportunities.json',{})
    return next((r for r in state.get('opportunities',[]) if r.get('url')==url
                 and r.get('sku')==sku and r.get('observed_grade') in ('A','B')
                 and 0<=now-r.get('quote_at',0)<=300 and 0<r.get('quote',0)<=r.get('median',0)*.5),None)
