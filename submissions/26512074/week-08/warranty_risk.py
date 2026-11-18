"""Empirical reserves from mature, actually warranted sales; no model calls."""
import json
from pathlib import Path
from optimizer import read,atomic
from shop_profit import ownership_tags


def statistics(md,now):
    md=Path(md);path=md/'warranty-risk.json';cached=read(path,{})
    if cached.get('version')==3 and 0<=now-cached.get('at',0)<300:return cached
    tags=ownership_tags();sales={};refunds={};seen=set()
    try:
        with (md/'ledger.jsonl').open(encoding='utf-8') as f:
            for line in f:
                try:e=json.loads(line)
                except ValueError:continue
                d=e.get('data',{});seq=e.get('seq')
                if e.get('round',3)!=3 or d.get('shop') not in tags or seq in seen:continue
                seen.add(seq);unit=d.get('unit') or d.get('order')
                if not unit:continue
                if e.get('kind')=='settle' and d.get('amount',0)>0:
                    sales[unit]=dict(at=e['ts'],price=d['amount'],hours=d.get('warranty_hours') or 0,sku=d.get('sku') or d.get('item'),grade=d.get('grade'))
                if e.get('kind') in ('broken','refund'):
                    amount=d.get('refunded',0) if e['kind']=='broken' else d.get('amount',0)
                    refunds[unit]=max(refunds.get(unit,0),amount)
    except FileNotFoundError:pass
    mature={u:s for u,s in sales.items() if s['hours']>0 and s['at']+s['hours']*3600+300<=now}
    def rate(group):
        amount=sum(s['price'] for s in group.values())
        return max(sum(refunds.get(u,0)>0 for u in group)/max(1,len(group)),
                   sum(min(s['price'],refunds.get(u,0)) for u,s in group.items())/max(1,amount))
    recent={u:s for u,s in mature.items() if now-s['at']<86400}
    measured=max(rate(mature),rate(recent) if len(recent)>=50 else 0)
    reserve_rate=min(1,max(.03,measured+.02)) if len(mature)>=50 else 1
    import sqlite3
    from contextlib import closing
    costs={};supplied={}
    if (md/'optimization.sqlite3').exists():
        with closing(sqlite3.connect(md/'optimization.sqlite3',timeout=2)) as c:
            costs={u:v for u,v in c.execute('select unit,cost from units')}
            supplied={u:v for u,v in c.execute('select unit,supplied from units') if v is not None}
    cohorts={}
    for u,s in mature.items():
        for key in (f"{s['sku']}:{s['grade']}:{s['hours']:g}",f"*:{s['grade']}:{s['hours']:g}",f"{s['sku']}:*:{s['hours']:g}"):
            r=cohorts.setdefault(key,dict(sales=0,refunds=0,revenue=0,refund_amount=0,known_cost=0,known_net=0))
            r['sales']+=1;r['refunds']+=int(refunds.get(u,0)>0);r['revenue']+=s['price'];r['refund_amount']+=min(s['price'],refunds.get(u,0))
            if u in costs:
                r['known_cost']+=1;r['known_net']+=s['price']-costs[u]-min(s['price'],refunds.get(u,0))
    for r in cohorts.values():
        r['refund_rate']=max(r['refunds']/r['sales'],r['refund_amount']/max(1,r['revenue']))
        r['reserve_rate']=min(1,r['refund_rate']+.02)
        r['loss_evidence']=r['sales']>=10 and r['known_cost']==r['sales'] and r['known_net']<=0
    sku_net={}
    for u,sale in sales.items():
        if sale['at']+max(6,sale['hours'])*3600+300>now or u not in costs:continue
        row=sku_net.setdefault(sale['sku'],dict(sales=0,net=0))
        row['sales']+=1
        row['net']+=sale['price']-costs[u]-min(sale['price'],refunds.get(u,0))-costs[u]*.01*max(0,sale['at']-supplied.get(u,sale['at']))/86400
    result=dict(sku_net=sku_net,at=now,version=3,cohorts=cohorts,mature_warranted_sales=len(mature),refunded_sales=sum(refunds.get(u,0)>0 for u in mature),
                measured_rate=measured,reserve_rate=reserve_rate,all_sales=len(sales),
                all_refunded_sales=sum(refunds.get(u,0)>0 for u in sales),margin_percentage_points=2)
    atomic(path,result);return result


def reserve(md,exposure,now):
    import math
    stats=statistics(md,now)
    return math.ceil(max(0,exposure)*stats['reserve_rate'])


def cohort(stats,sku,grade,hours):
    for key in (f'{sku}:{grade}:{hours:g}',f'*:{grade}:{hours:g}',f'{sku}:*:{hours:g}'):
        r=stats.get('cohorts',{}).get(key,{})
        if r.get('sales',0)>=10:return r
    return dict(reserve_rate=stats['reserve_rate'],loss_evidence=False,sales=0,
                refund_rate=stats['measured_rate'] if stats['mature_warranted_sales']>=50 else .08,
                rate_source='global_mature' if stats['mature_warranted_sales']>=50 else 'prior_not_measured')


def held_reserve(md,holds,now):
    import math
    stats=statistics(md,now)
    return math.ceil(sum(h['price']*cohort(stats,h.get('sku'),h.get('grade'),h.get('hours',6))['reserve_rate']
                         for h in holds.values() if h.get('expires_at',0)>now))


def locked_price_floor(md,task,unit,hours,now,create=False):
    import math
    from optimizer import db
    with db(md) as c:
        c.execute('create table if not exists warranty_risk_terms(task text primary key,floor real)')
        old=c.execute('select floor from warranty_risk_terms where task=?',(task,)).fetchone()
        if old:return old['floor']
        if not create:return 0  # Preserve quotes already outstanding at rollout.
        stats=statistics(md,now);r=cohort(stats,unit.get('sku'),unit.get('grade'),hours)
        risk=r.get('refund_rate',stats['measured_rate'] if stats['mature_warranted_sales']>=50 else .08)
        # Acquisition is sunk, but covers the positive-margin mandate. Only
        # incremental future storage is part of the acceptance decision.
        storage=unit['cost']*.01/24
        floor=math.ceil((unit['cost']*1.05+storage)/max(.01,1-risk))
        c.execute('insert into warranty_risk_terms values(?,?)',(task,floor))
        return floor
