"""Patch 3.4 Saved accounting; scenarios are separate from confirmed outcomes."""
import math
import re
import time


def metric(board, rules=None):
    named=(rules or {}).get('buyer_metric') or board.get('buyer_metric')
    if named in {'saved','surplus'}:return named
    own=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
    value=own.get('saved')
    return 'saved' if type(value) in (int,float) and -1<=value<=1 else 'surplus'


def state(journal,config):
    board=journal.snapshot('board.json',{})
    own=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
    saved=config.get('buyer_patch34_enabled') and metric(board,journal.get('market_rules',{}))=='saved'
    return {'metric':'saved' if saved else 'surplus','saved':own.get('saved',0) or 0,
            'deals':own.get('deals',0) or 0}


def contribution(price,list_price,failure=0,covered_failure=0):
    """Healthy: 1-P/L; broken uninsured: -1; confirmed full refund: 0.

    For decisions failure is an upper risk scenario and coverage a lower one.
    """
    if list_price<=0 or not 0<=covered_failure<=failure<=1:
        raise ValueError('invalid_saved_inputs')
    return (1-failure)*(1-price/list_price)-(failure-covered_failure)


def delta(current,returned,completion=1):
    if not 0<=completion<=1:raise ValueError('invalid_completion')
    return completion*(returned-current['saved'])/(current['deals']+101)


def terms_delta(current,price,list_price,failure=0,covered=0,completion=1):
    absolute=(1-failure)*list_price-price+covered*price
    result={'surplus':completion*absolute,'deals':completion}
    if current['metric']=='saved':
        result['saved']=delta(current,contribution(price,list_price,failure,covered),completion)
    return result


def saved_price_cap(list_price,current,failure,covered,margin=.002):
    target=max(0,min(1,current['saved']+margin))
    if failure>=1:return 0,target
    ratio=1-(target+failure-covered)/(1-failure)
    return max(0,math.floor(list_price*ratio)),target


def remember_refusal(journal,order,shop,receipt,reasons,now=None):
    """Remember only proven pre-payment unit refusals, never transport ambiguity."""
    now=time.time() if now is None else now
    reason='; '.join(reasons)
    if any(r.startswith(('payment failed:','payment not completed:')) for r in reasons):return
    unit=receipt.get('unit')
    if not unit:return
    expiry=None
    match=re.search(r'not paying: ([\d.]+) h of shelf life left',reason)
    if match:expiry=now+float(match[1])*3600
    elif 'not in stock for this order' not in reason:return
    rows=[r for r in journal.get('buyer_offer_blocks',[]) if r['until']>now and r['unit']!=unit]
    rows.append({'unit':unit,'shop':shop,'sku':receipt.get('item'),'expires_at':expiry,
                 'until':min(expiry,now+3600) if expiry else now+120})
    journal.put('buyer_offer_blocks',rows[-300:])
    journal.add('buyer_offer_refusal',{'unit':unit,'shop':shop,'sku':receipt.get('item'),
                                    'reason':'shelf_life' if expiry else 'stockout'},role='buyer')


def blocked_units(journal,shop,sku,order,now=None):
    now=time.time() if now is None else now
    return [r['unit'] for r in journal.get('buyer_offer_blocks',[]) if r['until']>now and
        r['shop']==shop and r['sku']==sku and (not r.get('expires_at') or
        r['expires_at']<now+order.get('use_within_hours',0)*3600+30)]
