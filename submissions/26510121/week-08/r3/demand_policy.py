"""Background empirical demand shrinkage and supply-access protection periods.

All rates and geometric windows are planning scenarios. No purchase, payment,
price-cap change, or inference of hidden orders is authorized by this packet.
"""
from collections import Counter,defaultdict
from bisect import bisect_right
import math
import time
from .state import identity

KEY='shop-demand-policy:v21'
REVISION='adaptive-public-prior-and-replenishment-v21-1'


def coverage_hours(inv, probability, config, now):
    cycle=max(1,config.get('supply_cycle_seconds',1200))
    level=config.get('shop_replenishment_service_level',.9)
    if not .5<=level<1 or not 0<probability<=1:raise ValueError('invalid_supply_probability')
    windows=1 if probability==1 else max(1,math.ceil(math.log1p(-level)/math.log1p(-probability)))
    opens=(inv.get('restock') or {}).get('opens_at')
    wait=max(cycle,opens-now) if type(opens) in (int,float) and math.isfinite(opens) else cycle
    protection=(wait+(windows-1)*cycle+config.get('shop_supply_delivery_seconds',30))/3600
    # The economic model cannot justify units beyond its evaluated time horizon.
    return min(max(1,config.get('planning_horizon_hours',6)),max(cycle/3600,protection))


def supply_access(events,now):
    windows=defaultdict(lambda:defaultdict(set))
    for r in events:
        at=r.get('_ts',r.get('finished_at',0));window=r.get('window');sku=r.get('sku')
        if (type(window) not in (int,float) or not isinstance(sku,str) or not 0<=now-at<=21600
                or r.get('status') not in ('complete','retryable','uncertain')):continue
        # An unfinished/uncertain call is not a confirmed supply failure.
        windows[window][sku].add(r['status'])
    own={};global_success=global_failure=0
    for window,skus in windows.items():
        any_complete=False;any_failure=False
        for sku,status in skus.items():
            s,f=own.get(sku,(0,0))
            if 'complete' in status:s+=1;any_complete=True
            elif window+180<=now and 'retryable' in status and 'uncertain' not in status:f+=1;any_failure=True
            own[sku]=(s,f)
        if any_complete:global_success+=1
        elif window+180<=now and any_failure:global_failure+=1
    global_p=(2+global_success)/(3+global_success+global_failure)
    return global_p,own,{'successful_attempted_windows':global_success,'failed_attempted_windows':global_failure,
        'unknown_and_unattempted_windows_are_not_failures':True}


def fit(catalog, public, timeline, sales, supply, competition, inv, config, now):
    half=max(.25,config.get('shop_demand_half_life_hours',1))
    decay=math.log(2)/(half*3600)
    exposure=Counter();clean_exposure=Counter();intervals=defaultdict(list)
    timeline=sorted((r for r in timeline if 0<=now-r.get('at',0)<=21600),key=lambda r:r['at'])
    for index,row in enumerate(timeline):
        finish=min(now,timeline[index+1]['at'] if index+1<len(timeline) else now,row['at']+60)
        if finish<=row['at'] or row.get('reachable') is False:continue
        hours=(math.exp(-decay*(now-finish))-math.exp(-decay*(now-row['at'])))/(decay*3600)
        for sku,qty in row.get('counts',{}).items():
            if qty>0 and sku in catalog:
                exposure[sku]+=hours;intervals[sku].append((row['at'],finish))
                if row.get('reachable') is True:clean_exposure[sku]+=hours
    paid=Counter();seen=set()
    for sale in sales:
        sku=sale.get('sku');at=sale.get('paid_at',sale.get('_ts',0));key=sale.get('unit') or sale.get('task') or sale.get('_event_id')
        if (sku not in intervals or not key or key in seen or not 0<=now-at<=21600
                or type(sale.get('price')) not in (int,float) or type(sale.get('cost')) not in (int,float)
                or sale['price']<=sale['cost']):continue
        seen.add(key);bounds=intervals[sku];i=bisect_right(bounds,(at,float('inf')))-1
        if i>=0 and bounds[i][0]<=at<=bounds[i][1]:paid[sku]+=math.exp(-decay*(now-at))
    current=competition.get('counts',{}) if 0<=now-competition.get('at',0)<120 else {}
    raw={};groups=defaultdict(lambda:[0.,0.,0]);kinds=defaultdict(lambda:[0.,0.,0])
    for sku in set(public)|set(exposure):
        if sku not in catalog:continue
        p=public.get(sku,{})
        competitors=max(1,current.get(sku,p.get('sellers',0))+1)
        samples=p.get('external_samples',p.get('samples',0))
        raw[sku]=max(0,samples)/6/competitors
        cat=(catalog[sku].get('kind'),catalog[sku].get('category'))
        for aggregate in (groups[cat],kinds[catalog[sku].get('kind')]):
            aggregate[0]+=paid[sku];aggregate[1]+=raw[sku]*exposure[sku];aggregate[2]+=exposure[sku]>0
    global_p,access,access_facts=supply_access(supply,now)
    prior_events=max(1,config.get('shop_demand_calibration_prior_events',5))
    prior_hours=max(.01,config.get('shop_demand_prior_hours',.25))
    rows={};factors=[]
    for sku,base in raw.items():
        cat=(catalog[sku].get('kind'),catalog[sku].get('category'))
        aggregate=groups[cat] if groups[cat][2]>=3 else kinds[catalog[sku].get('kind')]
        # Leave this SKU out of the group prior, then update it exactly once.
        other_paid=max(0,aggregate[0]-paid[sku]);other_expected=max(0,aggregate[1]-base*exposure[sku])
        group_factor=(other_paid+prior_events)/(other_expected+prior_events)
        proxy=1-clean_exposure[sku]/exposure[sku] if exposure[sku]>0 else 1
        strength=prior_hours*(1+proxy)
        rate=(paid[sku]+strength*base*group_factor)/(exposure[sku]+strength)
        successes,failures=access.get(sku,(0,0))
        probability=(2+successes)/(3+successes+failures) if successes+failures>=3 else global_p
        hours=coverage_hours(inv,probability,config,now)
        if catalog[sku].get('kind')=='food' and type(catalog[sku].get('shelf_hours')) in (int,float):
            hours=min(hours,max(0,catalog[sku]['shelf_hours']))
        if not all(math.isfinite(v) and v>=0 for v in (base,rate,group_factor,hours)):continue
        factor=rate/base if base>0 else None
        if factor is not None:factors.append(factor)
        rows[sku]={'rate_per_hour':rate,'raw_public_rate':base,'posterior_multiplier':factor,
            'group_multiplier':group_factor,'weighted_paid':paid[sku],'weighted_exposure_hours':exposure[sku],
            'proxy_exposure_fraction':proxy,'supply_success_scenario':probability,'coverage_hours':hours,
            'supply_success_windows':successes,'supply_failed_windows':failures}
    ordered=sorted(factors)
    return {'schema':1,'revision':REVISION,'at':now,'expires_at':now+180,'skus':rows,
        'summary':{'tracked_skus':len(rows),'calibration_multiplier_is_fixed':False,
            'median_posterior_multiplier':ordered[len(ordered)//2] if ordered else None,
            'observed_supply_access':access_facts,'default_supply_success_scenario':global_p,
            'coverage_hours_default':coverage_hours(inv,global_p,config,now),
            'rate_method':'leave-one-SKU-out group shrinkage, then time-decayed own positive payments/exposure',
            'availability_limit':'Older exposure is physical-stock proxy; reachable telemetry improves future evidence.',
            'uncertainty':'Conditional sales and geometric supply scenarios; not latent-demand identification.'}}


def context(journal,config,now=None):
    if not config.get('shop_adaptive_demand_enabled'):return None
    now=time.time() if now is None else now
    row=journal.get(KEY,{})
    if (row.get('revision')!=REVISION or not 0<=now-row.get('at',0)<180 or now>=row.get('expires_at',0)
            or row.get('rules_hash')!=journal.get('market_rules',{}).get('sha256')):return None
    return row


def update(journal,config,now=None):
    if not config.get('shop_adaptive_demand_enabled'):return None
    now=time.time() if now is None else now
    old=context(journal,config,now)
    if old and now-old['at']<60:return old
    rules=journal.get('market_rules',{})
    if (not rules.get('compatible') or not 0<=now-journal.get('public_feed_caught_up_at',0)<180
            or not 0<=now-journal.get('board_checked_at',0)<180):return None
    from .market_memory import market_book
    catalog=journal.snapshot('catalog.json',{});inv=journal.snapshot('inventory.json',{})
    row=fit(catalog,market_book(journal,catalog,now),journal.get('merchant_stock_timeline',[]),
        journal.events('shop_settled',10000),journal.events('supply_execution_status',10000),
        journal.snapshot('market_stock_counts.json',{}),inv,config,now)
    row['rules_hash']=rules['sha256'];row['id']=identity(row)
    journal.put(KEY,row);journal.add('shop_demand_calibrated',{'at':now,'id':row['id'],'summary':row['summary']})
    return row
