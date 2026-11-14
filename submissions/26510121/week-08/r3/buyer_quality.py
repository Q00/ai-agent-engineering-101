"""Buyer risk scenarios from mature, exact-unit public settlement/break records.

    Public cohorts are observational. SKU/grade and seller cohorts are preferred;
    uncertainty and a shift buffer remain, and checkout must enforce the grade.
"""
from contextlib import nullcontext
import json
import time
from .risk import goods_risk,wilson

_CACHE={}


def cohorts(journal,now=None):
    now=time.time() if now is None else now
    feed=journal.get('public_feed_caught_up_at',0)
    if not 0<=now-feed<=180:return {'groups':{},'unavailable':'public_feed_not_current'}
    cache_key=str(getattr(journal,'md',id(journal)))
    cached=_CACHE.get(cache_key) or journal.get('buyer_public_quality_v11',{})
    if 0<=now-cached.get('at',-1000)<300:
        _CACHE[cache_key]=cached
        return cached
    catalog=journal.snapshot('catalog.json',{})
    manager=journal.connect() if hasattr(journal,'connect') else nullcontext(journal.c)
    with manager as c:
        earliest=c.execute('SELECT min(ts) FROM events WHERE kind=?',('public_settle',)).fetchone()[0]
        # Allow ten minutes beyond the published six-hour horizon for feed lag.
        end=now-22200;start=max(now-172800,earliest or now)
        rows=c.execute('SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? '
                       'ORDER BY ts DESC LIMIT 60000',('public_settle',start,end)).fetchall()
        breaks={}
        for data, in c.execute('SELECT data FROM events WHERE kind=? AND ts>=? AND ts<=?',
                               ('public_broken',start,now)):
            event=json.loads(data)
            if event.get('unit'):breaks[event['unit']]=event
    groups={};seen=set();valid=0
    for ts,data in rows:
        e=json.loads(data);sku=e.get('sku') or e.get('item');grade=e.get('grade');unit=e.get('unit')
        product=catalog.get(sku,{})
        if not unit or unit in seen or grade not in ('A','B','C') or product.get('kind')!='goods':continue
        seen.add(unit);valid+=1;broken=breaks.get(unit)
        category=product.get('category','');shop=e.get('shop','').rstrip('/')
        keys=[f'sku|{sku}|{grade}',f'category|{category}|{grade}',
              f'seller_sku|{shop}|{sku}|{grade}',f'seller_category|{shop}|{category}|{grade}']
        for key in keys:
            row=groups.setdefault(key,{'n':0,'broken':0,'within3':0})
            row['n']+=1
            if broken:
                row['broken']+=1;hours=broken.get('hours_after_sale')
                if type(hours) in (int,float) and 0<=hours<=3:row['within3']+=1
    result={'at':now,'groups':groups,'mature_goods_units':valid,'scanned_settlements':len(rows),
            'sample_limit':60000,'earliest_selected':min((ts for ts,_ in rows),default=None),
            'latest_eligible':end,'feed_checked_at':feed,
            'source':'exact unit public settle/broken; 6h+10m mature, last 48h capped sample'}
    journal.put('buyer_public_quality_v11',result)
    if len(_CACHE)>16:_CACHE.clear()
    _CACHE[cache_key]=result
    return result


def estimate(journal,product,grade,warranty,shop,config,now=None):
    now=time.time() if now is None else now
    own=goods_risk(journal,grade,warranty,config,now)
    if grade not in ('A','B','C'):return own
    public=cohorts(journal,now);groups=public['groups']
    sku=product.get('sku');category=product.get('category','');shop=(shop or '').rstrip('/')
    options=[(f'seller_sku|{shop}|{sku}|{grade}',20,.02),
             (f'sku|{sku}|{grade}',50,.03),
             (f'category|{category}|{grade}',150,.05)]
    selected=next(((key,groups[key],buffer) for key,minimum,buffer in options
                   if key in groups and groups[key]['n']>=minimum),None)
    if not selected:return dict(own,public_quality_available=bool(groups))
    key,row,buffer=selected;n=row['n'];k=row['broken']
    upper=min(1,max(.05,wilson(k,n)+buffer))
    # A poorly performing seller category is a conservative cross-check, not
    # a reason to select whichever pooled cohort gives the lowest bound.
    seller=groups.get(f'seller_category|{shop}|{category}|{grade}',{})
    if seller.get('n',0)>=80:upper=max(upper,min(1,wilson(seller['broken'],seller['n'])+.02))
    if own['mature_samples']>=20:upper=max(upper,own['failure_upper'])
    covered=upper if warranty>=6 else (wilson(row['within3'],n,False) if warranty>=3 else 0)
    mean=(k+1)/(n+2)
    if own['mature_samples']>=40 and own.get('failure_mean') is not None:mean=max(mean,own['failure_mean'])
    return {'failure_upper':upper,'failure_mean':min(upper,mean),
            'mean_validated':own.get('mean_validated',False),
            'mean_validation':own.get('mean_validation',{}),
            'covered_lower':min(upper,covered),'grade':grade,
            'mature_samples':n,'observed_broken':k,'cohort':key,'shift_buffer':buffer,
            'own_mature_samples':own['mature_samples'],'source':public['source'],
            'own_risk_crosscheck':own['failure_upper'] if own['mature_samples']>=20 else None,
            'latest_eligible':public.get('latest_eligible'),'checkout_must_verify_grade':True,
            'scenario_not_causal_probability':True}
