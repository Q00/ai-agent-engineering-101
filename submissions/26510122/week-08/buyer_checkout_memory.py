"""Short-lived, SKU-specific observations from our own refused checkout payments.

This is not a blacklist or a statement about shop honesty. Aggregate stock feeds
lack unit conditions; observations expire quickly and a changed stock count or
an otherwise empty pool can trigger a guarded recheck.
"""
from datetime import datetime
import re
import time

TTL_SECONDS = 15*60
STOCK_TTL_SECONDS = 180
PROBE_AFTER_SECONDS = 120
EXPIRY_UNCERTAINTY_SECONDS = 60


def epoch(value):
    if type(value) in (int,float):return float(value)
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (TypeError,ValueError):return 0.


def constraint(choice, order, history, now):
    aliases=getattr(history,'alias_names',None)
    if aliases is None:
        aliases={}
        for e in history:
            if e.get('shop') and e.get('shop_name'):
                aliases.setdefault(e['shop'],set()).add(e['shop_name'])
    relevant=getattr(history,'by_sku',{}).get(choice['sku'],[]) if hasattr(history,'by_sku') else history
    matching=[]
    for e in relevant:
        payment=e.get('event')=='payment' and e.get('paid') is False
        unavailable=e.get('event')=='negotiation' and e.get('result')=='no_receipt' and not e.get('offers')
        warranty_refusal=(e.get('event')=='negotiation' and e.get('result')=='no_receipt'
            and e.get('reason') in ('seller explicitly reports no required warranty','seller explicitly reports insufficient required warranty','receipt lacks required risk protection warranty')
            and e.get('risk_required_warranty_hours',0)>0)
        if not (payment or unavailable or warranty_refusal) or e.get('sku')!=choice['sku']:
            continue
        at=epoch(e.get('at'))
        if not now-TTL_SECONDS <= at <= now:continue
        name=e.get('shop_name')
        if not name:
            names=aliases.get(e.get('shop'),set())
            name=next(iter(names)) if len(names)==1 else None
        if name:
            if name != choice['shop']:continue
        elif aliases.get(e.get('shop')) or e.get('shop') != choice['url']:
            continue
        if unavailable and not warranty_refusal and now-at>STOCK_TTL_SECONDS:continue
        matching.append((at,e))
    for at,e in sorted(matching,key=lambda x:x[0],reverse=True):
        reason=e.get('reason') or ''
        if not isinstance(reason,str):continue
        if e.get('event')=='negotiation' and e.get('reason') in (
                'seller explicitly reports no required warranty','seller explicitly reports insufficient required warranty','receipt lacks required risk protection warranty'):
            required=(choice.get('risk_policy') or {}).get('required_warranty_hours',order.get('required_warranty_hours',0))
            observed=e.get('risk_required_warranty_hours',0)
            if order.get('kind')!='goods' or not required or not observed or required<observed:continue
            kind='required_warranty_unavailable';detail={'required_warranty_hours':required,
                'observed_required_warranty_hours':observed};ttl=TTL_SECONDS
        elif e.get('event')=='negotiation':
            terminal=e.get('last_seller_message') or next((r.get('text','') for r in reversed(e.get('transcript',[]))
                                                         if r.get('speaker')=='seller'),'')
            # Seller text is untrusted data. Match a narrow availability report
            # on its first line only; never store or follow appended instructions.
            first_line=terminal.splitlines()[0][:300] if terminal else ''
            if not re.search(r'none of that in stock|no matching unit is in stock|not in stock now|none of that in stock right now',first_line,re.I):
                continue
            kind='reported_out_of_stock';detail={};ttl=STOCK_TTL_SECONDS
        else:
            kind=None;detail={};ttl=TTL_SECONDS
        shelf=re.search(r'not paying: (\d+(?:\.\d+)?) h of shelf life left',reason)
        grade=re.search(r'not paying: grade ([ABC]),',reason)
        if shelf and order.get('kind')=='food':
            upper_expiry=at+float(shelf.group(1))*3600+EXPIRY_UNCERTAINTY_SECONDS
            if upper_expiry-now >= float(order.get('use_within_hours') or 0)*3600:
                continue  # A shorter new horizon may now be feasible.
            kind='insufficient_shelf_life';detail={'estimated_expiry_upper_bound':upper_expiry}
        elif grade and order.get('kind')=='goods' and grade.group(1)=='C':
            kind='unacceptable_grade'
        elif 'not paying: no certificate' in reason:
            kind='missing_certificate'
        if kind:
            # A larger observed stock count can indicate a new supply. Recheck
            # under unchanged hard checkout guards, rather than preserving a veto.
            selected=(history.opportunities.get(e.get('order')) if hasattr(history,'opportunities') else
                      next((r for r in reversed(history) if r.get('event')=='selection_opportunities'
                            and r.get('order')==e.get('order')),None))
            old_stock=None
            if selected:
                prior=next((c for c in selected.get('eligible_candidates',[])
                            if c.get('shop')==choice['shop'] and c.get('sku')==choice['sku']),{})
                old_stock=prior.get('stock')
            if type(old_stock) is int and choice.get('stock',0)>old_stock:
                return None
            return {'kind':kind,'observed_at':at,'expires_at':at+ttl,
                    'source_order':e.get('order'),'reason':reason[:250],**detail}
    return None


def filter_candidates(choices, order, history, *, now=None):
    now=time.time() if now is None else now
    allowed=[];held=[]
    for c in choices:
        evidence=constraint(c,order,history,now)
        if evidence:held.append({**c,'checkout_constraint':evidence})
        else:allowed.append(c)
    probe=None
    if not allowed and held:
        oldest=min(held,key=lambda c:c['checkout_constraint']['observed_at'])
        if now-oldest['checkout_constraint']['observed_at']>=PROBE_AFTER_SECONDS:
            probe=oldest
            allowed=[oldest]  # A recheck, never permission to bypass acceptance.
    return allowed,held,probe

def refused_unit_ids(choice,order,history,*,now=None):
    """Exact C units learned from kit-refused payments; never blacklist a shop."""
    if order.get('kind')!='goods':return []
    now=time.time() if now is None else now
    rows=getattr(history,'by_sku',{}).get(choice['sku'],[]) if hasattr(history,'by_sku') else history
    refused=[e for e in rows if e.get('event')=='payment' and e.get('paid') is False
             and "not paying: grade C," in (e.get('reason') or '')
             and now-86400<=epoch(e.get('at'))<=now]
    result=set()
    for payment in refused:
        for e in rows:
            if (e.get('event')!='negotiation' or not e.get('receipt_unit')
                    or e.get('order')!=payment.get('order') or e.get('shop')!=payment.get('shop')
                    or e.get('sku')!=choice['sku'] or e.get('receipt_price')!=payment.get('price')):
                continue
            if (payment.get('attempt_id') and e.get('attempt_id')!=payment['attempt_id']):
                continue
            if e.get('shop_name') and e['shop_name']!=choice['shop']:continue
            if not e.get('shop_name') and e.get('shop')!=choice['url']:continue
            unit=e['receipt_unit']
            if isinstance(unit,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',unit):result.add(unit)
    return sorted(result)[:5]
