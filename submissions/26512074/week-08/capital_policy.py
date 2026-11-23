"""Finite replenishment cover and inventory-aware cash recovery; no cash reserve."""
import math
import statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone,timedelta
from optimizer import read,atomic

ROUND_END=datetime(2026,11,17,23,59,tzinfo=timezone(timedelta(hours=9))).timestamp()


def remaining_hours(now):return max(0,(ROUND_END-now)/3600)


def count_tail(shape,exposure,hours,position):
    """Gamma-Poisson posterior predictive P(N >= integer position)."""
    if position<=0:return 1.
    if hours<=0:return 0.
    shape=max(1e-9,shape);exposure=max(1e-9,exposure)
    p=exposure/(exposure+hours);term=math.exp(shape*math.log(p));cdf=term
    for k in range(1,int(position)):
        term*=((k-1+shape)/k)*(1-p);cdf+=term
    return min(1,max(0,1-cdf))


def marginal_sale_probability(posterior,held,pending,weights,k,hours):
    """A new unit waits behind existing stock and random-grade earlier units."""
    result={};random_ahead=max(0,int(pending)+k-1)
    for grade,w in weights.items():
        d=posterior.get(grade) or posterior.get('*')
        if not d:result[grade]=0.;continue
        total=0.
        for j in range(random_ahead+1):
            mass=math.comb(random_ahead,j)*w**j*(1-w)**(random_ahead-j)
            total+=mass*count_tail(d['shape'],d['exposure_hours'],hours,int(held.get(grade,0))+j+1)
        result[grade]=total
    return result


def marginal_curve(v,inventory,now,qty,pending=0,held=None):
    """Marginal Book/cash paths, including perishing and retained goods cost."""
    hours=remaining_hours(now);weights=v.get('acquisition_weights') or {'*':1.}
    held=held if held is not None else v.get('held_by_grade',{})
    post=v.get('demand_posterior',{});life=v.get('shelf_hours') or hours
    horizon=min(hours,life) if v.get('kind')=='food' else hours
    result=[]
    for k in range(1,min(20,qty)+1):
        full=marginal_sale_probability(post,held,pending,weights,k,horizon)
        half=marginal_sale_probability(post,held,pending,weights,k,horizon/2)
        next_window=marginal_sale_probability(post,held,pending,weights,k,min(horizon,v.get('next_supply_hours',1/3)))
        book=revenue=cash_locked=holding=probability=0.
        for grade,w in weights.items():
            cost=v.get('grade_costs',{}).get(grade,v['estimated_cost']);sale=v.get('grade_expected_sale',{}).get(grade,v['expected_sale'])
            p=full[grade];duration=horizon/6*(1+4*(1-half[grade])+(1-p))
            store=cost*inventory.get('storage_per_day',.01)*duration/24 if v.get('kind')=='goods' else 0
            expiry=cost*(1-p) if v.get('kind')=='food' and life<=hours else 0
            book+=w*(p*(sale-cost)-store-expiry);revenue+=w*p*sale;holding+=w*duration;probability+=w*p
            cash_locked+=w*cost*(1-next_window[grade])
        result.append(dict(k=k,book=book,revenue=revenue,sale_probability=probability,holding_hours=holding,cash_locked_to_next_window=cash_locked))
    return result


def cover_satisfied(v,held,pending):
    """Do not restock a covered SKU merely to win another grade lottery."""
    horizon=v.get('next_supply_hours',1/3);weights=v.get('acquisition_weights') or {'*':1.}
    for grade,d in v.get('demand_posterior',{}).items():
        mean=d['shape']/max(1e-9,d['exposure_hours'])*horizon
        if held.get(grade,0)+pending*weights.get(grade,0)<mean:return False
    return bool(v.get('demand_posterior'))


def terminal_purchase_eligible(v,item,now):
    """Keep the same posterior, but require recovery evidence before the close.

    A predictive median after the close is not an immediate-sale opportunity.
    This is a sale-time test, not a cash reserve or an inventory amount cap.
    """
    hours=remaining_hours(now)
    if hours>3:return True
    recovery=v.get('actual_recovery',{})
    if not recovery.get('n') or recovery.get('median_hours',math.inf)>=hours:return False
    if item['book']<=0 or item['sale_probability']<.5:return False
    weights=v.get('acquisition_weights') or {'*':1.}
    profit=sum(w*(v.get('grade_expected_sale',{}).get(g,v['expected_sale'])-v.get('grade_costs',{}).get(g,v['estimated_cost'])) for g,w in weights.items())
    return profit>0


def cash_shadow_price(candidates,cash,slots):
    """Shadow value of an unfunded observed next-window alternative; no reserve."""
    demand=sorted((v for v in candidates if v.get('marginal_values') and v['marginal_values'][0]['book']>0),key=lambda v:-v.get('funding_priority',0))[:max(1,slots)]
    alternatives=[]
    for v in demand:
        for item in v['marginal_values']:
            gain=item.get('all_gain');cost=v['worst_cost']
            if gain is not None and gain>0:alternatives.append((gain/max(1,cost),cost))
    alternatives.sort(reverse=True);used=0
    for value,cost in alternatives:
        if used+cost>cash:return value
        used+=cost
    return 0.


def focus_inventory_audit(md,inventory,market,catalog,events,supplied,costs,now):
    """Three user-requested SKU audits in the existing collection pass."""
    from shop_profit import ownership_tags
    tags=ownership_tags();state=read(Path(md)/'strategy-v9-review.json',{});start=state.get('start',now-86400)
    refunds={}
    for e in events:
        if e['kind'] in ('broken','refund'):
            d=e['data'];refunds[d.get('unit')]=max(refunds.get(d.get('unit'),0),(d.get('refunded',0) if e['kind']=='broken' else d.get('amount',0)) or 0)
    audit={}
    for sku in ['G77834f4a','G24f5f979','G6cd0e9d4']:
        m=market.get(sku,{});units=[u for u in inventory.get('units',[]) if u['sku']==sku];g=defaultdict(lambda:dict(qty=0,cost=0))
        for u in units:g[str(u.get('grade'))]['qty']+=1;g[str(u.get('grade'))]['cost']+=u['cost']
        for grade,p in m.get('demand_posterior',{}).items():
            exponent=math.log(2)/p['shape'];median=p['exposure_hours']*math.expm1(exponent) if exponent<700 else None
            g[grade].update(next_sale_median_hours=median,posterior=p)
        sales=[e for e in events if e['kind']=='settle' and e['ts']>=start and e['data'].get('shop') in tags and e['data'].get('sku',e['data'].get('item'))==sku]
        fresh=[e for e in sales if supplied.get(e['data'].get('unit'),0)>=start]
        incoming=[e for e in events if e['kind']=='supply' and e['ts']>=start and e['data'].get('shop') in tags and e['data'].get('sku')==sku]
        known=[e for e in sales if e['data'].get('unit') in costs]
        audit[sku]=dict(grades=dict(g),bought=sum(e['data'].get('qty',0) for e in incoming),spend=sum(e['data'].get('total',0) for e in incoming),
            sold=len(sales),newly_acquired_sold=len(fresh),new_stock_cash_recovered=sum(e['data']['amount'] for e in fresh),old_stock_cash_recovered=sum(e['data']['amount'] for e in sales if e not in fresh),
            current_known_net_before_storage=sum(e['data']['amount']-costs[e['data']['unit']]-refunds.get(e['data']['unit'],0) for e in known),
            warranty_outcomes_may_be_pending=True,not_permanently_blocked=True)
    atomic(Path(md)/'v101-inventory-audit.json',dict(at=now,cohort_start=start,remaining_hours=remaining_hours(now),skus=audit))


def replenish_hours(inv,now,cycle):
    opening=inv.get('restock',{}).get('opens_at',now)
    wait=cycle/3600 if inv.get('restock',{}).get('open') or opening<=now else (opening-now)/3600
    return min(wait,remaining_hours(now))


def stock_demand_posteriors(inventory,catalog,events,market,supplied,now,hours):
    """Reuse observed sales/stock periods; pool only other SKU exposure."""
    intervals=defaultdict(list);paid=defaultdict(int);cut=now-hours*3600
    def key(sku,grade):return (sku,'*' if catalog.get(sku,{}).get('kind')=='food' else str(grade))
    for u in inventory.get('units',[]):
        a=max(cut,u.get('supplied_at',now));b=min(now,u.get('expires_at') or now)
        if b>a:intervals[key(u['sku'],u.get('grade'))].append((a,b))
    from shop_profit import ownership_tags
    tags=ownership_tags()
    for e in events:
        d=e['data']
        if e['kind']!='settle' or d.get('shop') not in tags or not cut<=e['ts']<=now:continue
        sku=d.get('sku',d.get('item'));k=key(sku,d.get('grade'));paid[k]+=1
        if d.get('unit') in supplied:
            a=max(cut,supplied[d['unit']]);b=e['ts']
            if b>a:intervals[k].append((a,b))
    exposure={}
    for k,spans in intervals.items():
        union=[]
        for a,b in sorted(spans):
            if union and a<=union[-1][1]:union[-1]=(union[-1][0],max(b,union[-1][1]))
            else:union.append((a,b))
        exposure[k]=sum(b-a for a,b in union)/3600
    pools=defaultdict(lambda:[0.,0.,0])
    from strategy_policy import category
    for (sku,g),t in exposure.items():
        for cat in (category(catalog.get(sku,{})),'*'):
            p=pools[cat,g];p[0]+=paid[sku,g];p[1]+=t;p[2]+=1
    own_share=sum(v.get('own_trades',0) for v in market.values())/max(1,sum(v.get('trades',0) for v in market.values()))
    result={}
    for sku,product in catalog.items():
        result[sku]={};m=market.get(sku,{})
        for g in (['*'] if product.get('kind')=='food' else ['A','B','C']):
            n=paid[sku,g];t=exposure.get((sku,g),0);pool=pools[category(product),g]
            if pool[1]-t<=0:pool=pools['*',g]
            other_n=max(0,pool[0]-n);other_t=max(0,pool[1]-t);other_skus=max(0,pool[2]-int(t>0))
            peer_rate=(sum(p.get('n',0) for p in m.get('prices',{}).values()) if g=='*' else m.get('prices',{}).get(g,{}).get('n',0))/max(.01,hours)*own_share
            prior_rate=other_n/other_t if other_t>0 else peer_rate
            prior_hours=other_t/other_skus if other_skus else hours
            flow=m.get('own_quote_grades',{}).get(g,{})
            if g=='*':flow=dict(offers=m.get('own_quotes',0),paid=m.get('own_quote_paid',0))
            conversion=(flow.get('paid',0)+.5)/(flow.get('offers',0)+1)
            missing=(sum(m.get('unfilled_by_grade',{}).values()) if g=='*' else m.get('unfilled_by_grade',{}).get(g,0))*conversion
            effective_exposure=hours if missing>0 else t
            result[sku][g]=dict(shape=.5+prior_rate*prior_hours+n+missing,exposure_hours=max(.01,prior_hours+effective_exposure),
                local_sales=n,stocked_hours=t,observed_hours=hours,estimated_stockout_arrivals=missing,
                prior_rate=prior_rate,prior_hours=prior_hours,model='gamma_poisson_stock_exposure_other_sku_prior',
                assumption='arrival stationarity and exchangeable grade demand; not a guaranteed sale')
    return result


def inventory_review(md,inv,catalog,market,now):
    md=Path(md);groups=defaultdict(list)
    for u in inv.get('units',[]):
        if u.get('grade')!='expired':groups[u['sku'],u.get('grade')].append(u)
    rates=[];records={};lookback=max(.01,read(md/'strategy-policy.json',{}).get('lookback_seconds',7200)/3600)
    if not (md/'strategy-policy.json').exists():
        from strategy_policy import load
        lookback=load(md)['lookback_seconds']/3600
    h=replenish_hours(inv,now,1200)
    own_share=sum(v.get('own_trades',0) for v in market.get('skus',{}).values())/max(1,sum(v.get('trades',0) for v in market.get('skus',{}).values()))
    for (sku,g),units in groups.items():
        m=market.get('skus',{}).get(sku,{});own=m.get('own_prices',{}).get(g,{})
        peer=m.get('prices',{}).get(g,{})
        rate=max(own.get('n',0)/lookback,peer.get('n',0)/lookback*own_share)
        flow=m.get('own_quote_grades',{}).get(g,{})
        offers=flow.get('offers',0);paid=flow.get('paid',0)
        conversion=(paid+1)/(offers+2)
        missed=m.get('unfilled_by_grade',{}).get(g,0)*conversion
        expected=rate*h;buffer=math.ceil(min(missed,math.sqrt(expected))) if missed and expected else 0
        target=math.ceil(expected)+buffer
        cost=sum(u.get('cost',0) for u in units)/len(units)
        price=own.get('base_median',peer.get('base_median',peer.get('median',cost)))
        storage=0 if catalog.get(sku,{}).get('kind')=='food' else cost*inv.get('storage_per_day',.01)/24
        wait=min(remaining_hours(now),len(units)/max(.001,rate))
        records[f'{sku}:{g}']=dict(sku=sku,grade=g,qty=len(units),cost=cost,realized_price=price,rate=rate,
            offers=offers,paid=paid,conversion=conversion,target=target,stockout_buffer=buffer,excess=max(0,len(units)-target),
            expected_hours_to_sell=wait,storage_per_hour=storage,expected_storage=storage*wait,
            expected_net=price-cost-storage*wait,cash_recovered=price,competitor_stock=m.get('competitor_stock',0),
            quote_at=now,remaining_round_hours=remaining_hours(now))
        if rate>0:rates.append(rate)
    high=statistics.median(rates) if rates else 0
    conversions=[r['conversion'] for r in records.values() if r['offers']]
    typical=statistics.median(conversions) if conversions else .5
    for r in records.values():
        if r['rate']>=high and r['rate']>0 and r['expected_net']>0 and r['conversion']>=typical:r['state']='high_demand_high_net'
        elif r['rate']>=high and r['rate']>0 and r['conversion']<typical:r['state']='high_demand_low_conversion'
        elif r['excess'] and r['rate']>0:r['state']='medium_demand_excess'
        elif not r['excess']:r['state']='low_demand_low_stock'
        else:r['state']='low_demand_excess'
    result=dict(at=now,groups=records,next_replenishment_hours=h,remaining_round_hours=remaining_hours(now))
    atomic(md/'inventory-plan.json',result);return result


def prepared_orders(md,inv,catalog,now,policy):
    from strategy_policy import database,portfolio,category
    from profit_strategy import surge_factor
    md=Path(md);prepared=read(md/'procurement-prepared.json',{})
    if not prepared or not 0<=now-prepared.get('at',0)<=policy['max_market_age_seconds']:return None
    if not inv.get('restock',{}).get('open'):return {'buy':[]}
    deadline=inv['restock'].get('closes_at',now+180);slots=min(10,max(0,int((deadline-now-10)/12)))
    buy=[];held=defaultdict(int);marginal_checks=[]
    for u in inv.get('units',[]):
        if u.get('grade')!='expired' and (u.get('expires_at') or math.inf)>now:held[u['sku'],u.get('grade')]+=1
    with database(md,True) as c:
        locks=[dict(r) for r in c.execute('select * from funds where until>?',(now,))]
        limits=portfolio(inv,catalog,{},locks,now,policy,md);cash=limits['budget'];pending=defaultdict(int)
        for r in locks:pending[r['sku']]+=r['qty']
        score_targets=read(md/'score-targets.json',{})
        for v in prepared.get('candidates',[]):
            if len(buy)>=slots:break
            sku=v['sku'];product=catalog.get(sku,{})
            grade_targets=v.get('grade_targets',{})
            if grade_targets:effective=sum(min(held[sku,g],n) for g,n in grade_targets.items())
            else:
                h=max(v.get('required_hours',0),v.get('service_hours',prepared.get('horizon_hours',1)))
                effective=sum(u['sku']==sku and u.get('grade')!='expired' and (u.get('expires_at') or math.inf)-now>=h*3600 for u in inv.get('units',[]))
            need=min(20,max(0,v['target']-effective-pending[sku]))
            if not need:continue
            if policy.get('terminal_slot_allocation_enabled') and v.get('terminal_book_per_unit',1)<=0 and not v.get('loss_exception'):continue
            worst=math.ceil(product['list_price']*.75*surge_factor(inv,sku,need))
            qty=min(need,max(0,int(cash/max(1,worst))))
            if not qty:continue
            curve=None
            if policy.get('marginal_demand_allocation_enabled'):
                if not v.get('demand_posterior'):continue
                if product.get('kind')=='food':
                    required=max(v.get('required_hours',0),v.get('service_hours',0))
                    queue={'*':sum(u['sku']==sku and u.get('grade')!='expired' and (u.get('expires_at') or math.inf)-now>=required*3600 for u in inv.get('units',[]))}
                else:queue={g:held[sku,g] for g in v['acquisition_weights']}
                if cover_satisfied(v,queue,pending[sku]):continue
                curve=marginal_curve(v,inv,now,qty,pending[sku],queue)
                chosen=[]
                for item in curve:
                    if item['book']<=0:break
                    if policy.get('terminal_supply_evidence_enabled') and not terminal_purchase_eligible(v,item,now):break
                    from score_coordination import all_value
                    gain=all_value(score_targets,'shop',dict(book=item['book'],revenue=item['revenue']),now)
                    tax=v.get('cash_shadow_all_per_credit',0)*item['cash_locked_to_next_window']
                    if gain is not None and gain<=tax:break
                    chosen.append(item)
                qty=len(chosen);curve=chosen
                if not qty:continue
                marginal_checks.append(dict(sku=sku,held_by_grade=queue,pending=pending[sku],units=chosen))
            if v.get('loss_exception'):
                from score_coordination import boundary_value
                crossing=boundary_value(score_targets,'shop',dict(book=v['expected_net']*qty,revenue=v['expected_sale']*qty),now)
                if crossing is None or crossing<=0:continue
            amount=qty*worst;cash-=amount;buy.append(dict(sku=sku,qty=qty))
            if 'expected_net' in v:
                from score_coordination import project_targets
                score_targets=project_targets(score_targets,'shop',dict(book=sum(u['book'] for u in curve) if curve else v.get('terminal_book_per_unit',v['expected_net'])*qty,revenue=sum(u['revenue'] for u in curve) if curve else v.get('terminal_revenue_per_unit',v['expected_sale'])*qty))
            c.execute('insert into funds values(?,?,?,?,?,?,?)',(f'supply:{sku}:{now}',sku,category(product),qty,amount,now,deadline+300))
    execution=dict(at=now,prepared_at=prepared['at'],buy=buy,slots=slots,
        estimated_net='cached forecast; actual cost and settlements must be reconciled',budget_after_reservations=cash,marginal_checks=marginal_checks)
    atomic(md/'procurement-execution.json',execution)
    if policy.get('terminal_supply_evidence_enabled') and buy:
        from optimizer import append
        append(md.parent/'logs/final3h-procurement.jsonl',execution)
    return dict(buy=buy)


def terminal_funding(v,inventory,targets,now,slots):
    """Value the finite round and scarce supply calls, not an infinite ROI cycle.

    Unsold goods retain acquisition cost in Book; storage remains a real cost.
    Cash concentration is priced through capacity, without fixed cash reserves.
    """
    from score_coordination import all_value
    if v.get('marginal_values'):
        cash=max(1,inventory.get('balance',0));qty=min(v['need'],int(cash/max(1,v['worst_cost'])))
        for unit in v['marginal_values']:unit['all_gain']=all_value(targets,'shop',dict(book=unit['book'],revenue=unit['revenue']),now)
        curve=v['marginal_values'][:qty]
        book=sum(u['book'] for u in curve);revenue=sum(u['revenue'] for u in curve)
        gain=all_value(targets,'shop',dict(book=book,revenue=revenue),now)
        capacity=max(1/max(1,slots),qty*v['worst_cost']/cash)
        return dict(terminal_book_per_unit=book/max(1,qty),terminal_revenue_per_unit=revenue/max(1,qty),
            terminal_sell_probability=sum(u['sale_probability'] for u in curve)/max(1,qty),
            modeled_all_progress=gain,funding_priority=(gain if gain is not None else book)/capacity if qty else 0,
            funding_objective='marginal_posterior_class_all_per_capacity',remaining_round_hours=remaining_hours(now))
    hours=remaining_hours(now);wait=max(.05,v['payback_hours']);cash=max(1,inventory.get('balance',0))
    qty=min(v['need'],math.floor(cash/max(1,v['worst_cost'])))
    probability=min(1,hours/wait) if hours else 0
    if v.get('kind')=='goods':
        holding=min(hours,wait)
        net=probability*(v['expected_sale']-v['estimated_cost'])-v['estimated_cost']*inventory.get('storage_per_day',.01)*holding/24
    else:net=v['expected_net']*probability
    revenue=v['expected_sale']*probability
    capacity=max(1/max(1,slots),qty*v['worst_cost']/cash)
    gain=all_value(targets,'shop',dict(book=net*qty,revenue=revenue*qty),now)
    return dict(terminal_book_per_unit=net,terminal_revenue_per_unit=revenue,terminal_sell_probability=probability,
        modeled_all_progress=gain,funding_priority=(gain if gain is not None else net*qty)/capacity if qty else 0,
        funding_objective='finite_round_class_all_per_supply_capacity',remaining_round_hours=hours)
