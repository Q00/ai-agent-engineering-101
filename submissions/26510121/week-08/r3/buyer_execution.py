"""Saved progress per unit of execution time, using observed route outcomes.

These are bounded routing priors, never a reason to weaken checkout or price caps.
"""
from collections import defaultdict
import statistics
import time
from .buyer_scoring import contribution


def route_context(journal,now=None,order=None):
    now=time.time() if now is None else now;start=now-3600
    visits=defaultdict(dict);latency=defaultdict(list)
    for event in journal.events('attempt',2000):
        if not start<=event['_ts']<=now:continue
        if not event.get('shop') or not event.get('sku') or not event.get('order'):continue
        if (order or {}).get('kind')=='food' and event.get('use_within_hours')!=order.get('use_within_hours',0):continue
        key=(event['shop'],event['sku']);oid=event['order']
        if event.get('kind')=='no_agreement' or event.get('kind') in ['ReadTimeout','HTTPStatusError','ConnectError','rfq_failed']:
            visits[key].setdefault(oid,False)
        seconds=event.get('seconds')
        if type(seconds) in (int,float) and 0<seconds<120:latency[event['shop']].append(seconds)
    for event in journal.events('purchase',2000):
        if not start<=event.get('paid_at',event['_ts'])<=now:continue
        if (order or {}).get('kind')=='food':
            expiry=event.get('expires_at');paid_at=event.get('paid_at',event['_ts'])
            if type(expiry) not in (int,float) or expiry-paid_at<(order.get('use_within_hours',0)*3600+30):continue
        if event.get('shop') and event.get('sku') and event.get('order'):
            visits[(event['shop'],event['sku'])][event['order']]=True
    for event in journal.events('buyer_quote_latency',1000):
        if start<=event['_ts']<=now:latency[event['shop']].append(event['seconds'])
    paid={r['order']:r for r in journal.events('purchase',2000) if start<=r.get('paid_at',r['_ts'])<=now}
    checkout=defaultdict(list)
    for row in journal.events('agreement',3000):
        purchase=paid.get(row.get('order'))
        if purchase and purchase.get('shop')==row.get('shop'):
            seconds=purchase.get('paid_at',purchase['_ts'])-row['_ts']
            if 0<seconds<120:checkout[row['shop']].append(seconds)
    return {'routes':{k:{'n':len(rows),'paid':sum(rows.values())} for k,rows in visits.items()},
            'checkout':{key:statistics.median(values) for key,values in checkout.items()},
            'latency':{k:max(.25,min(8,statistics.median(v))) for k,v in latency.items()}}


def execution_seconds(candidate,context):
    latency=context['latency'].get(candidate['shop'],1.5)
    return max(1,2*latency+context.get('checkout',{}).get(candidate['shop'],2))


def route_adjust(candidate,order,journal,config,context):
    row=context['routes'].get((candidate['shop'],candidate['sku']),{})
    n,paid=row.get('n',0),row.get('paid',0)
    # A Beta(1,1) completion prior is updated only with terminal failed attempts
    # or actual paid orders, one observation per order/route. Pending agreements
    # are not silently classified as failures.
    completion=(paid+1)/(n+2)
    candidate['execution_seconds']=execution_seconds(candidate,context)
    candidate['evidence']['execution_model']={'route_samples':n,'paid':paid,
        'completion_scenario':completion,'seconds_scenario':candidate['execution_seconds'],
        'window_seconds':3600,'not_causal_or_guaranteed':True}
    # Existing route terms already price quality/warranty. Discount inconsistent
    # historical claims when this route repeatedly failed to conclude our orders.
    factor=min(1,completion/.5)
    candidate['score']*=factor
    candidate['expected_delta']={k:v*factor for k,v in candidate['expected_delta'].items()}
    return candidate


def price_times(actions,candidate,assessment,config):
    base=max(1,candidate.get('execution_seconds',5))
    for action in actions:
        action['execution_seconds']=base if action['id']=='accept' else base+max(2,base*.75)
        action['time_model']='bounded request/checkout scenario; counter may need another round trip'
    return actions


def strong_offer(candidate,assessment,ask,config):
    if assessment.get('conditional_terms') or ask>assessment['cap']:return False
    state=assessment['saved_state']
    if state['metric']!='saved':return False
    value=contribution(ask,candidate['product']['list_price'],assessment['failure'],assessment['covered_failure'])
    return value>=state['saved']+config.get('buyer_fast_accept_margin',.04)
