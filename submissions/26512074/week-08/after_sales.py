"""Perishable clearance and optional market-enforced warranty support."""
import json,math,re
from pathlib import Path
from round3 import read,write,lines
from shop_profit import DISPLAY,ownership_tags,recovery_price


def warranty_request(text):
    return any(w in text.lower() for w in ('warranty','보증','a/s','as 서비스','사후지원'))


def without_warranty(text):
    return bool(re.search(r'(?:no|without)\s+(?:paid\s+)?warranty|\b0\s*[- ]?\s*(?:hours?|h)\b|warranty\s*[:=]?\s*0\b|보증\s*0|0\s*시간|보증\s*없이|무보증',text,re.I))


def warranty_offer(md,unit,price,cash,now,hours=6):
    from strategy_policy import active
    if active(md):
        from strategy_policy import warranty_offer as unified_offer
        return unified_offer(md,unit,price,cash,now,hours)
    if unit.get('kind')!='goods' or hours not in (3,6,12,24,48):
        return False
    path=Path(md)/'warranty-holds.json'
    holds={k:v for k,v in read(path,{}).items() if v['expires_at']>now}
    # Grandfather existing contracts; never create a new B/C warranty hold.
    if unit.get('grade')!='A' and unit.get('unit') not in holds:
        return False
    from warranty_risk import statistics,cohort
    risk=cohort(statistics(md,now),unit.get('sku'),unit.get('grade'),hours)
    if risk['loss_evidence'] and not holds.get(unit['unit'],{}).get('issued'):return False
    existing=holds.get(unit['unit'],{})
    holds[unit['unit']]={'price':price,'expires_at':existing.get('expires_at',now+hours*3600+900),
                        'issued':existing.get('issued',False),'sku':unit.get('sku'),'grade':unit.get('grade'),'hours':hours}
    write(path,holds)
    return True


def release_warranty(md,unit,issued=False,price=None,now=None):
    from strategy_policy import active
    if active(md):
        from strategy_policy import release_warranty as unified_release
        return unified_release(md,unit,issued,price,now)
    path=Path(md)/'warranty-holds.json'
    holds=read(path,{})
    if unit not in holds:
        return
    if issued:
        holds[unit]['issued']=True
        if price is not None:
            holds[unit]['price']=price
    elif not holds[unit].get('issued'):
        del holds[unit]
    write(path,holds)


def clearance(inventory,ledger,now):
    tags=ownership_tags()
    sold={}
    seen=set()
    for e in ledger:
        if e.get('kind')!='settle' or e.get('seq') in seen or e.get('data',{}).get('shop') not in tags:
            continue
        seen.add(e['seq'])
        if 0<=now-e.get('ts',0)<3600:
            sku=e['data'].get('sku') or e['data'].get('item')
            sold[sku]=sold.get(sku,0)+1
    groups={}
    for u in inventory.get('units',[]):
        if u.get('kind')=='food' and 0<(u.get('expires_at') or 0)-now<=6*3600 and u.get('grade')!='expired':
            groups.setdefault(u['sku'],[]).append(u)
    offers=[]
    for sku,units in groups.items():
        hours=min(u['expires_at']-now for u in units)/3600
        if len(units)<=sold.get(sku,0)*hours:
            continue
        u=min(units,key=lambda u:u['expires_at'])
        p=recovery_price(u,inventory,ledger,now)
        offers.append({'sku':sku,'name':u['name'],'units':len(units),'hours':round(hours,1),
                       'ask':p['ask'],'lot':u['unit']})
    return sorted(offers,key=lambda o:o['hours'])[:3]


def service(md,inventory,ledger,now,enabled=False):
    return {'status':'support_posts_disabled_shop_slot_reserved_for_analysis'}
