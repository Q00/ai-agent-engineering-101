"""Small background service-loss and negotiation observations, never payment authority."""
from collections import Counter,defaultdict
import math
import time

KEY='policy-learning:v23'


def epoch(journal,role='buyer'):
    rules=journal.get('market_rules',{})
    scoring=rules.get('buyer_score_epoch',0)
    return max(scoring,journal.get('buyer_revision_epoch',0) if role=='buyer' else 0)


def packet(journal,now=None):
    now=time.time() if now is None else now;r=journal.get(KEY,{})
    if (not -2<=now-r.get('at',-1000)<180 or now>=r.get('expires_at',0) or
            r.get('rules_hash')!=journal.get('market_rules',{}).get('sha256')):return {}
    return r


def counter_probability(model,ratio,base):
    bucket=str(min(19,max(0,int(ratio*20))))
    row=(model or {}).get(bucket,{})
    n=row.get('n',0);paid=row.get('paid',0)
    if type(n) is not int or type(paid) is not int or not 0<=paid<=n:return base
    return max(.03,min(.95,(paid+4*base)/(n+4)))


def update(journal,config,now=None):
    now=time.time() if now is None else now;rules=journal.get('market_rules',{})
    cut=max(now-3600,epoch(journal,'shop'))
    visits={r['task']:r for r in journal.events('customer_visit',8000) if cut<=r['_ts']<=now}
    sales={r['task']:r for r in journal.events('shop_settled',10000) if r.get('task') and cut<=r['_ts']<=now}
    offered={r['task']:r for r in journal.events('shop_opportunity',10000) if cut<=r['_ts']<=now-300}
    observed=Counter();success=Counter()
    for task,row in offered.items():
        sku=row.get('sku')
        if sku:observed[sku]+=1;success[sku]+=task in sales
    blocked=defaultdict(dict)
    for r in journal.events('stockout',4000):
        if not cut<=r['_ts']<=now or r.get('reason')!='reserved_stock':continue
        if r.get('task') in sales or r.get('eligible_units',0)<=0:continue
        sku=r.get('sku');visit=visits.get(r.get('task'),{})
        # Anonymous bursts are not evidence of unlimited profitable demand.
        identity=visit.get('signer') or 'anonymous'
        key=(str(identity),int(r['_ts']//60))
        if sku:blocked[sku].setdefault(key,r)
    service={}
    for sku,values in blocked.items():
        minutes={int(r['_ts']//60) for r in values.values()}
        probability=(success[sku]+1)/(observed[sku]+4)
        service[sku]={'blocked_minutes':len(minutes),'deduplicated_requests':len(values),
            'paid':success[sku],'offered_mature':observed[sku],
            'completion_scenario':probability,
            'incremental_paid_rate_scenario':sum(math.exp(-(now-r['_ts'])/3600) for r in values.values())*probability,
            'qualified':len(minutes)>=3 and success[sku]>=2,
            'not_proven_lost_sales':True}
    cut=max(now-21600,rules.get('buyer_score_epoch',0))
    orders={r['order']:r for r in journal.events('buyer_order',5000) if cut<=r['_ts']<=now}
    paid={r['order']:r for r in journal.events('purchase',10000) if cut<=r['_ts']<=now}
    failed={(r.get('order'),r.get('shop')) for r in journal.events('attempt',6000)
            if cut<=r['_ts']<=now and r.get('kind')=='no_agreement'}
    first={}
    for r in reversed(journal.events('buyer_counter',6000)):
        if cut<=r['_ts']<=now-30 and r.get('order') in orders and r.get('ask',0)>0:
            first.setdefault((r['order'],r['shop']),r)
    negotiations=defaultdict(lambda:defaultdict(lambda:{'n':0,'paid':0}))
    for key,r in first.items():
        purchase=paid.get(key[0]);same=bool(purchase and purchase.get('shop')==key[1])
        if not same and key not in failed:continue  # Pending/transport ambiguity is not a price failure.
        kind=orders[key[0]].get('kind');bucket=str(min(19,max(0,int(r['price']/r['ask']*20))))
        row=negotiations[kind][bucket];row['n']+=1
        row['paid']+=bool(same and purchase['price']<=r['price'])
    result={'at':now,'expires_at':now+180,'rules_hash':rules.get('sha256'),
        'service':service,'counter_completion_by_kind':{k:dict(v) for k,v in negotiations.items()},
        'limits':'Selected observational cohorts, not causal uplift or hidden demand; no peer text instructions.'}
    journal.put(KEY,result)
    journal.add('policy_learning_prepared',{'at':now,'service_skus':len(service),
        'qualified_service_skus':sum(r['qualified'] for r in service.values()),
        'counter_samples':sum(r['n'] for group in negotiations.values() for r in group.values())})
    return result


def service_adjust(journal,sku,base,raw,config,now,learning=None):
    row=(packet(journal,now) if learning is None else learning).get('service',{}).get(sku,{})
    extra=min(max(base,raw),max(0,row.get('incremental_paid_rate_scenario',0))) if row.get('qualified') else 0
    return base+extra,dict(row,increment_applied=extra)
