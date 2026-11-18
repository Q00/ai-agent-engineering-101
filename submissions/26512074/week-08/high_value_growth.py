"""Grow high-value exposure only on fresh, paid and warranty-matured evidence."""
from pathlib import Path
from statistics import median
from optimizer import atomic,append,read,db
from shop_profit import ownership_tags


def assess(inventory,catalog,ledger,costs,policy,now,activated,after):
    ours=ownership_tags()
    def high(sku):
        product=catalog.get(sku,{})
        return product.get('kind')=='goods' and product.get('list_price',0)>=policy['pilot_min_list_price']
    sales=[];normal=[];refunds=0;refund_records=[];broken_units=set();storage=0;cohort_storage=0;supply=0;cash_revenue=0
    seen=set();all_revenue=0
    for event in ledger:
        data=event.get('data',{});seq=event.get('seq');ts=event.get('ts',0)
        if seq is None or seq in seen or event.get('round',3)!=3 or not activated<=ts<=now or data.get('shop') not in ours:
            continue
        seen.add(seq)
        sku=data.get('sku') or data.get('item');kind=event.get('kind')
        if kind=='supply' and high(sku):
            supply+=data.get('total',0)
        if kind=='storage' and (not sku or high(sku)):
            storage+=data.get('fee',0)  # Conservatively charge shared storage to this strategy.
            if ts>after:
                cohort_storage+=data.get('fee',0)
        if kind in ('broken','refund') and high(sku):
            amount=data.get('refunded',0) if kind=='broken' else data.get('amount',0)
            refunds+=amount
            refund_records.append((data.get('unit'),data.get('order'),amount))
            if kind=='broken' and data.get('unit'):
                broken_units.add(data['unit'])
        if kind!='settle':
            continue
        price=data.get('amount',0)
        all_revenue+=price
        if high(sku):
            cash_revenue+=price
        maturity=data.get('warranty_hours') or 0
        if ts<=after or ts+maturity*3600+300>now:
            continue
        cost=costs.get(data.get('unit'))
        row={'seq':seq,'at':ts,'sku':sku,'price':price,'cost':cost,'unit':data.get('unit'),'order':data.get('order')}
        (sales if high(sku) else normal).append(row)
    known=[s for s in sales if s['cost'] is not None]
    cohort_units={s['unit'] for s in sales if s['unit']}
    cohort_orders={s['order'] for s in sales if s['order']}
    cohort_refunds=[r for r in refund_records if r[0] in cohort_units or r[1] in cohort_orders]
    refunded={r[0] or r[1] for r in cohort_refunds if r[2]>0}
    net=sum(s['price']-s['cost'] for s in known)-sum(r[2] for r in cohort_refunds)-cohort_storage
    total_cost=sum(s['cost'] for s in known)
    turnover=[]
    for sale in known:
        # Caller supplies per-unit acquisition times without inventing them.
        unit_time=costs.get(('sale_time',sale['seq']))
        if unit_time is not None:
            turnover.append(max(0,(sale['at']-unit_time)/3600))
    low=[s['price']-s['cost'] for s in normal if s['cost'] is not None]
    stale=sum(high(u['sku']) and now-u.get('supplied_at',now)>12*3600 for u in inventory.get('units',[]))
    return {'mature_sales':len(sales),'known_cost_sales':len(known),'skus':len({s['sku'] for s in sales}),
            'net_after_refunds_storage':net,'cost':total_cost,'roi':net/total_cost if total_cost else None,
            'refund_rate':len(refunded)/max(1,len(sales)),
            'defect_rate':len(broken_units&cohort_units)/max(1,len(sales)),
            'median_turnover_hours':median(turnover) if turnover else None,'known_turnover_sales':len(turnover),
            'net_per_sale':net/len(sales) if sales else 0,'normal_margin_per_sale':sum(low)/len(low) if low else None,
            'cash_contribution':cash_revenue-supply-refunds-storage,'stale_high_units':stale,
            'latest_evidence_at':max((s['at'] for s in sales),default=after),
            'observed_high_sales_share':cash_revenue/all_revenue if all_revenue else None,
            'high_revenue':cash_revenue,'supply_spend':supply,'refunds':refunds,'shared_storage_charged':storage}


def update(md,inventory,catalog,ledger,now):
    from profit_strategy import settings
    policy=settings();md=Path(md)
    path=md/'high-value-growth.json'
    state=read(path,{})
    if not state:
        state={'stage':0,'activated_at':now,'evidence_after':now,'last_checked':now-policy['growth_review_seconds'],'reason':'initial observation'}
    stage=max(0,min(3,int(state['stage'])))
    if now-state['last_checked']<policy['growth_review_seconds'] and 'defect_rate' in state.get('metrics',{}) and state.get('maturity_policy_version')==2:
        return dict(state,stage=stage,share=0 if now<state.get('paused_until',0) else policy['high_value_shares'][stage])
    with db(md) as connection:
        units={r['unit']:dict(r) for r in connection.execute('select * from units')}
        costs={u:r['cost'] for u,r in units.items()}
        for row in connection.execute('select unit,cost from trials where settled_at>=? and cost is not null',(state['activated_at'],)):
            costs.setdefault(row['unit'],row['cost'])
    for unit in inventory.get('units',[]):
        costs.setdefault(unit.get('unit'),unit['cost'])
    for event in ledger:
        data=event.get('data',{})
        unit=units.get(data.get('unit'))
        if event.get('kind')=='settle' and unit and unit.get('supplied') is not None:
            costs['sale_time',event.get('seq')]=unit['supplied']
    metrics=assess(inventory,catalog,ledger,costs,policy,now,state['activated_at'],state['evidence_after'])
    sufficient=metrics['mature_sales']>=policy['growth_min_sales']
    loss=(metrics['mature_sales']>=3 and (metrics['net_after_refunds_storage']<0 or metrics['refund_rate']>.1 or metrics['defect_rate']>.1))
    stale=metrics['stale_high_units']>=2 and metrics['cash_contribution']<0
    complete=metrics['known_cost_sales']==metrics['mature_sales'] and metrics['known_turnover_sales']==metrics['mature_sales']
    latest=max((event.get('ts',0) for event in ledger if event.get('round',3)==3),default=0)
    metrics['ledger_age_seconds']=max(0,now-latest)
    quality=(metrics['ledger_age_seconds']<=180 and metrics['roi'] is not None and metrics['roi']>=policy['growth_min_roi']
             and metrics['refund_rate']<=.05 and metrics['defect_rate']<=.05 and metrics['cash_contribution']>=0
             and metrics['median_turnover_hours'] is not None
             and metrics['median_turnover_hours']<=policy['growth_max_turnover_hours']
             and (metrics['normal_margin_per_sale'] is None or metrics['net_per_sale']>=metrics['normal_margin_per_sale']*1.5))
    reason='hold: insufficient new mature evidence'
    if (loss or stale) and stage==0:
        state['paused_until']=now+6*3600
        state['evidence_after']=max(state['evidence_after'],metrics['latest_evidence_at'])
        reason='pause: losses at minimum exposure'
    elif (loss or stale) and stage>0:
        stage-=1;reason='rollback: realized loss, refunds or stale stock'
    elif now>=state.get('paused_until',0) and sufficient and complete and metrics['skus']>=policy['growth_min_skus'] and quality and stage<3:
        stage+=1;reason='promote: fresh paid profitable fast-turnover evidence'
    if stage!=state['stage']:
        state['evidence_after']=max(state['evidence_after'],metrics['latest_evidence_at'])
    state.update(stage=stage,last_checked=now,share=0 if now<state.get('paused_until',0) else policy['high_value_shares'][stage],reason=reason,metrics=metrics,maturity_policy_version=2)
    atomic(path,state)
    append(md/'optimization/high-value-growth-history.jsonl',{'at':now,**state})
    return state
