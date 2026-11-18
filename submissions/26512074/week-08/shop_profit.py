"""Inventory exposure and market-based cash recovery for shop ranking."""
from statistics import median
import math
import json
from pathlib import Path

_ledger_cache={}


def recent_ledger(path,now):
    key=str(path)
    cached=_ledger_cache.get(key)
    if cached and now-cached[0]<30:
        return cached[1]
    try:
        with Path(path).open('rb') as source:
            source.seek(0,2)
            size=source.tell()
            # Eight MB covered less than one hour in the live market, silently
            # starving the two-hour same-grade benchmark of observations.
            source.seek(max(0,size-64*1024*1024))
            if size>64*1024*1024:
                source.readline()
            rows=[]
            for line in source:
                try:
                    event=json.loads(line)
                except ValueError:
                    continue
                if now-event.get('ts',0)<7200:
                    rows.append(event)
    except FileNotFoundError:
        rows=[]
    _ledger_cache[key]=(now,rows)
    return rows

REGISTRATION='totato2203-r3'
DISPLAY='tomato&potato'
ALIASES={REGISTRATION,DISPLAY}


def exposure(inventory):
    return sum(u['cost'] for u in inventory.get('units',[]))


def buy_budget(inventory):
    from profit_strategy import capital
    return capital(inventory)['budget']


def own_events(ledger):
    tags=ownership_tags()
    return [e for e in ledger if e.get('data',{}).get('shop') in tags]


def ownership_tags():
    path=Path(__file__).resolve().parent/'.market/shop-identity.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
    except (FileNotFoundError,ValueError):
        data={}
    return ALIASES | set(data.get('urls',[]))


def recovery_price(unit,inventory,ledger,now):
    from strategy_policy import active,ROOT
    if active(ROOT/'.market'):
        from strategy_feedback import market_price
        return market_price(ROOT/'.market',unit,inventory,now)
    """Use actual same-SKU/grade settlements; never label estimates as quotes."""
    if unit.get('grade')=='expired' or (unit.get('expires_at') is not None and unit['expires_at']<=now):
        return None
    cost=unit['cost']
    prices=[]
    freshness_prices=[]
    tags=ownership_tags()
    seen=set()
    for e in ledger:
        d=e.get('data',{})
        if e.get('kind')!='settle' or e.get('round',3)!=3 or e.get('seq') in seen:
            continue
        seen.add(e.get('seq'))
        price=d.get('amount')
        if (d.get('sku') or d.get('item'))!=unit['sku'] or d.get('grade')!=unit.get('grade'):
            continue
        if d.get('shop') in tags:
            continue
        if not 0<=now-e.get('ts',0)<7200 or isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:
            continue
        prices.append(price)
        if unit.get('kind')=='food' and unit.get('expires_at') is not None and isinstance(d.get('expires_at'),(int,float)):
            remaining=unit['expires_at']-now
            sold_remaining=d['expires_at']-e['ts']
            if remaining*.8<=sold_remaining<=remaining*1.2:freshness_prices.append(price)
    freshness_matched=len(freshness_prices)>=3
    if freshness_matched:prices=freshness_prices
    from profit_strategy import capital,quantile,settings
    limits=capital(inventory)
    stressed=inventory.get('balance',0)<limits['cash_reserve'] or exposure(inventory)>limits['inventory_cap']
    # Reserve covers acquisition and a positive unit margin, without adding
    # seven speculative future storage days to today's acceptance threshold.
    floor=math.ceil(cost*1.01)
    reference=median(prices)*settings()['market_opening_buffer'] if len(prices)>=3 else unit['list_price']
    ask=max(floor,math.ceil(reference))
    if unit.get('expires_at') is not None:
        hours=(unit['expires_at']-now)/3600
        if hours<6:
            ask=max(1,math.floor(ask*max(.1,hours/6)))
            floor=max(1,math.ceil(cost*(.1 if hours<1 else .2)))
            ask=max(floor,ask)
    return {'ask':ask,'floor':floor,'market_reference':reference,'market_samples':len(prices),
            'market_median':median(prices) if len(prices)>=3 else None,
            'cash_recovery':stressed,'freshness_matched':freshness_matched}
