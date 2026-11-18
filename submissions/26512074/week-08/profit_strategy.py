"""Response-based opening prices and demand-led working capital; no model calls."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parent


def turnover_target(target,hourly,held_units,now,deep_discount=False):
    """Keep fast sellers funded; stop four-hour cover compounding aged stock."""
    if deep_discount or not held_units:return target
    aged=sum(now-u.get('supplied_at',now)>=21600 for u in held_units)
    if aged and aged/len(held_units)>=.25:
        return min(target,max(1,math.ceil(hourly*2)))
    return target


def settings():
    policy=json.loads((ROOT/'profit-policy.json').read_text(encoding='utf-8'))
    limits={'minimum_continuation':(.3,.9),'maximum_list_multiple':(1,3),
            'market_opening_buffer':(1,1.5),'cash_reserve_fraction':(0,1),
            'inventory_equity_fraction':(0,1),'order_cash_fraction':(0,1),
            'sku_equity_fraction':(0,1),'new_sku_market_share':(0,1),
            'reinvestment_fraction':(0,1),'stock_cover_hours':(.25,24),
            'regular_reinvestment_fraction':(0,1),'pilot_min_list_price':(100000,500000),
            'pilot_max_list_price':(500000,3000000),'pilot_max_skus':(1,8),
            'pilot_min_trades':(5,50),'pilot_min_buyers':(2,10),'pilot_min_roi':(.02,.2),
            'default_warranty_hours':(0,6),'buyer_quote_comparisons':(2,3),
            'growth_min_sales':(5,30),'growth_min_skus':(2,5),'growth_review_seconds':(900,21600),
            'growth_max_turnover_hours':(1,12),'growth_min_roi':(.03,.2)}
    limits.update(premium_capital_share=(.3,.9),premium_min_margin=(.05,.3),premium_market_floor=(.8,1.2),premium_concession=(.03,.2))
    limits.update(normal_inventory_fraction=(.2,.8),opportunity_cash_fraction=(.1,.5),deep_discount_ratio=(.4,.75))
    limits.update(verified_restock_fraction=(0,1))
    for key,(low,high) in limits.items():
        value=policy[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError('Invalid profit policy: '+key)
    shares=policy['high_value_shares']
    if policy.get('capital_mode') not in ('premium_offensive','sales_funded'):
        raise ValueError('Invalid capital allocation mode')
    if not isinstance(shares,list) or len(shares)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not .1<=v<=.7 for v in shares) or shares!=sorted(set(shares)):
        raise ValueError('Invalid high value allocation stages')
    return policy


def quantile(values,fraction):
    values=sorted(values)
    return values[min(len(values)-1,math.ceil((len(values)-1)*fraction))]


def surge_factor(inventory,sku,qty=20):
    rows=inventory.get('surge',[])
    row=next((r for r in rows if r.get('sku')==sku),{}) if isinstance(rows,list) else rows.get(sku,{})
    if not row and 'surge' not in inventory:return 1.0
    return min(1.5,max(1,float(row.get('factor',1)),1+.002*max(0,row.get('units',0)+qty-50)))


def opening(unit,base,history):
    """Highest supported grid price; continuation probabilities are estimates.

    Settled competitor prices are anchors, not buyers' private budgets. Own
    matured reply observations calibrate that prior and bound further probes.
    """
    policy=settings()
    rollout=json.loads((ROOT/'.market/operating-policy.json').read_text(encoding='utf-8-sig')) if (ROOT/'.market/operating-policy.json').exists() else {}
    if rollout.get('expected_profit_pricing'):
        # Empirical settled fractions only; no invented demand curve. Exclude
        # old extreme-price and guidance cohorts from normal-price calibration.
        valid=[r for r in history if r.get('ask') and r.get('reference') and r.get('at',0)>=rollout['effective_at']]
        floor=math.ceil(base['floor'])
        reference=(base.get('market_reference') or unit['list_price'])
        default=max(floor,math.ceil(min(base['ask'],unit['list_price'])))
        candidates=[]
        storage=unit['cost']*.01/24 if unit.get('kind')=='goods' else 0
        for ratio in (.65,.70,.75,.80,.85,.90,.95,1.0):
            price=max(floor,math.ceil(reference*ratio))
            nearby=[r for r in valid if abs(r['ask']/r['reference']-price/max(1,reference))<=.05]
            if len(nearby)<12:continue
            probability=sum(r.get('settled_at') is not None for r in nearby)/len(nearby)
            candidates.append((probability*(price*(1+base.get('warranty_fee',0))*(1-base.get('estimated_refund_rate',0))-unit['cost']-storage),price,probability,len(nearby)))
        best=max(candidates,default=None)
        price=best[1] if best and best[0]>0 else default
        return dict(ask=price,floor=floor,reference=reference,arm=round(price/max(1,unit['cost'])-1,6),
                    pricing_evidence=dict(strategy=rollout['version'],nearby_mature_observations=best[3] if best else 0,
                                          settlement_estimate=best[2] if best else None,fallback=not bool(best)))
    reference=(base.get('market_reference') or unit['list_price'])*base.get('opening_multiplier',1)
    floor=base['floor']
    mature=[r for r in history if r.get('reference') and r.get('ask')]
    # A rejected high band must also constrain higher, untried bands.
    failed=[]
    supported=[]
    for band in sorted({round(r['ask']/r['reference'],1) for r in mature}):
        sample=[r for r in mature if abs(r['ask']/r['reference']-band)<=.051]
        if len(sample)<8:
            continue
        rate=sum(bool(r.get('responded')) or r.get('settled_at') is not None for r in sample)/len(sample)
        if rate<policy['minimum_continuation']:
            failed.append(band)
        elif rate>=.75 and sum(r.get('settled_at') is not None for r in sample)>=2:
            supported.append(band)
    center=max([1.15]+[band+.10 for band in supported])
    ceiling=min(failed)-.05 if failed else 2.0
    candidates=[];closing_probabilities={}
    for step in range(10,41):
        ratio=step*.05
        price=math.ceil(reference*ratio-1e-9)
        if price<floor or price>unit['list_price']*policy['maximum_list_multiple'] or ratio>ceiling:
            continue
        prior=1/(1+math.exp(8*(ratio-center)))
        sample=[r for r in mature if abs(r['ask']/r['reference']-ratio)<=.075]
        probability=(sum(bool(r.get('responded')) or r.get('settled_at') is not None for r in sample)+6*prior)/(len(sample)+6)
        if probability>=policy['minimum_continuation']:
            candidates.append((price,probability,len(sample)))
            closing_probabilities[price]=(sum(r.get('settled_at') is not None for r in sample)+2*prior)/(len(sample)+4)
    # Arrival rate is shared across prices: compare expected contribution per
    # opportunity, including zero proceeds from failed negotiations.
    price,probability,samples=max(candidates,key=lambda r:((r[0]-unit['cost']+.05*r[0])*closing_probabilities[r[0]],r[1]),
                                  default=(math.ceil(floor),0,0))
    if not candidates:
        # A failed statistical band is not proof no profitable buyer exists.
        # Offer a bounded cost-positive recovery quote instead of refusing to quote.
        price=max(math.ceil(floor),math.ceil(unit['cost']*1.12),math.ceil(min(reference,unit['cost']*1.25)))
    return {'ask':price,'floor':min(floor,price),'reference':reference,
            'arm':round(price/max(1,unit['cost'])-1,6),
            'pricing_evidence':{'strategy':policy['version'],'continuation_estimate':round(probability,4),
                                'settlement_estimate':round(closing_probabilities.get(price,0),4),
                                'nearby_mature_observations':samples,'reference':reference,
                                'supported':bool(candidates),'fallback':not bool(candidates),'failed_bands':failed}}


def capital(inventory,warranty_exposure=0,policy=None):
    policy=settings() if policy is None else policy
    cash=max(0,inventory.get('balance',0))
    stock=sum(max(0,u['cost']) for u in inventory.get('units',[]))
    equity=cash+stock
    # All cash is deployable; old bankruptcy, storage and refund reserves removed.
    return {'cash_reserve':0,'inventory_cap':equity,'purchase_cap':cash,'budget':cash}



def replenish(inventory,catalog,ledger,now,md):
    from strategy_policy import active
    if active(md):
        from demand_strategy import plan
        return plan(md,inventory,catalog,now)
    from optimizer import db,config,read
    from shop_profit import ownership_tags
    if inventory.get('round',3)<3 or not inventory.get('restock',{}).get('open',False):
        return {'buy':[]}
    policy=settings()
    from high_value_growth import update as growth_update
    growth=growth_update(md,inventory,catalog,ledger,now)
    from adaptive_capital import allocation
    policy,adaptive=allocation(Path(md),inventory,growth,policy,now)
    sku_proof=read(Path(md)/'leader-sku-evidence.json',{})
    proof_fresh=0<=now-sku_proof.get('at',0)<=21900
    holds=read(Path(md)/'warranty-holds.json',{})
    warranty=sum(h['price'] for h in holds.values() if h.get('expires_at',0)>now)
    from warranty_risk import held_reserve,statistics as warranty_statistics,cohort as warranty_cohort
    risk_stats=warranty_statistics(md,now)
    funded_warranty=held_reserve(md,holds,now)
    funded_warranty=max(funded_warranty,max((h['price'] for h in holds.values() if h.get('expires_at',0)>now),default=0))
    limits=capital(inventory,funded_warranty,policy)
    limits.update(warranty_full_exposure=warranty,warranty_risk_reserve=funded_warranty)
    limits.update(adaptive_stage=adaptive['stage'],equity_drawdown=adaptive['drawdown'],
                  inventory_fraction=policy['inventory_equity_fraction'],stock_cover_hours=policy['stock_cover_hours'])
    cash=limits['budget']
    stock_cost=sum(u['cost'] for u in inventory.get('units',[]))
    equity=inventory.get('balance',0)+stock_cost
    normal_budget=max(0,math.floor(min(equity*policy['normal_inventory_fraction']-stock_cost,
                                     cash*(1-policy['opportunity_cash_fraction']))))
    limits.update(normal_budget=normal_budget,opportunity_budget=cash-normal_budget)
    if cash<=0:
        from optimizer import atomic
        atomic(Path(md)/'procurement-preview.json',{'at':now,'limits':limits,'reason':'cash_or_total_inventory_cap','growth':growth,'candidates':[],'excluded':[],'buy':[]})
        return {'buy':[]}
    tags=ownership_tags()
    groups=defaultdict(list)
    public_acquisition=defaultdict(list)
    public_suppliers=defaultdict(set)
    seen=set()
    sales_cash=0
    purchases=0
    high_purchases=0
    for event in ledger:
        seq=event.get('seq');data=event.get('data',{});amount=data.get('amount')
        if (event.get('round',3)!=3 or seq is None or seq in seen
                or not 0<=now-event.get('ts',0)<7200):
            continue
        seen.add(seq)
        if event.get('kind')=='supply' and data.get('shop') not in tags:
            total=data.get('total');qty=data.get('qty')
            if isinstance(total,(int,float)) and isinstance(qty,(int,float)) and total>0 and qty>0:
                public_acquisition[data.get('sku')].append(total/qty)
                public_suppliers[data.get('sku')].add(data.get('shop'))
        if event.get('kind')=='supply' and data.get('shop') in tags:
            total=data.get('total')
            if isinstance(total,(int,float)) and not isinstance(total,bool) and math.isfinite(total) and total>0:
                purchases+=total
                item=catalog.get(data.get('sku'),{})
                if item.get('kind')=='goods' and item.get('list_price',0)>=policy['pilot_min_list_price']:
                    high_purchases+=total
        if (event.get('kind')!='settle' or isinstance(amount,bool)
                or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<=0):
            continue
        if data.get('shop') in tags:
            sales_cash+=amount
        sku=data.get('sku') or data.get('item')
        if sku in catalog:
            groups[sku].append(event)
    limits.update(capital_budget=limits['budget'],sales_cash_2h=sales_cash,
                  purchases_2h=purchases,reinvestment_fraction=policy['reinvestment_fraction'])
    offensive=policy.get('capital_mode')=='premium_offensive'
    if not offensive:
        limits['budget']=min(limits['budget'],max(0,math.floor(sales_cash*policy['reinvestment_fraction']-purchases)))
    cash=limits['budget']
    held=Counter(u['sku'] for u in inventory.get('units',[]) if u.get('grade')!='expired'
                 and (u.get('expires_at') or math.inf)>now)
    held_cost=Counter()
    for unit in inventory.get('units',[]):
        held_cost[unit['sku']]+=unit['cost']
    with db(md) as connection:
        acquisition=defaultdict(list)
        for row in connection.execute('select sku,cost from units where supplied>? and cost>0',(now-7*86400,)):
            acquisition[row['sku']].append(row['cost'])
    for unit in inventory.get('units',[]):
        acquisition[unit['sku']].append(unit['cost'])
    candidates=[];excluded=[]
    # Verify every SKU from our own paid history, not just the four leader SKUs.
    own_performance={}
    with db(md) as connection:
        recent_paid={r['sku']:dict(r) for r in connection.execute('select sku,count(*) as count,sum(price-cost) as gross from trials where inquiry=0 and settled_at>? and cost>0 and price>0 group by sku',(now-21600,))}
        connection.execute('create table if not exists premium_terms(task text primary key,sku text,hours real,fee real)')
        mature=connection.execute('select t.sku,t.unit,t.cost,t.price,t.settled_at,u.supplied,p.hours from trials t join units u on u.unit=t.unit left join premium_terms p on p.task=t.task where t.inquiry=0 and t.settled_at>? and t.cost>0 and t.price>0 and t.settled_at+300<=? and u.supplied is not null',(now-21600,now)).fetchall()
        settled_hours={e.get('data',{}).get('unit'):e.get('data',{}).get('warranty_hours') or 0 for e in ledger if e.get('kind')=='settle' and e.get('data',{}).get('shop') in tags}
        for row in mature:
            hours=row['hours'] if row['hours'] is not None else settled_hours.get(row['unit'],6)
            if row['settled_at']+hours*3600+300>now:continue
            r=own_performance.setdefault(row['sku'],{'count':0,'cost':0,'net':0,'hours':[]})
            elapsed=max(0,row['settled_at']-row['supplied'])/3600
            r['count']+=1;r['cost']+=row['cost'];r['net']+=row['price']-row['cost']-row['cost']*inventory.get('storage_per_day',.01)*elapsed/24
            r['hours'].append(elapsed)
    for event in ledger:
        d=event.get('data',{});sku=d.get('sku') or d.get('item')
        if sku in own_performance and d.get('shop') in tags and 0<=now-event.get('ts',0)<21600:
            if event.get('kind')=='broken':own_performance[sku]['net']-=d.get('refunded',0)
            if event.get('kind')=='refund':own_performance[sku]['net']-=d.get('amount',0)
    verified={sku for sku,r in own_performance.items() if r['count']>=5 and r['net']/r['cost']>=.08 and quantile(r['hours'],.5)<=2}
    verified_budget=math.floor(cash*policy['verified_restock_fraction'])
    limits.update(verified_restock_budget=verified_budget,warranty_exposure=warranty)
    pilot_state=read(Path(md)/'pilot-state.json',{'skus':{}})
    pilot_spend=0
    supply_seen=set()
    for event in ledger:
        data=event.get('data',{});seq=event.get('seq')
        if (seq is not None and seq not in supply_seen and event.get('kind')=='supply'
                and event.get('round',3)==3 and data.get('shop') in tags
                and 0<=now-event.get('ts',0)<7200 and data.get('sku') in pilot_state['skus']):
            pilot_spend+=data.get('total',0)
            supply_seen.add(seq)
    regular_spend=max(0,purchases-high_purchases)
    share=growth['share']
    allocation=sales_cash*policy['reinvestment_fraction']
    high_budget=max(0,math.floor(allocation*share-high_purchases))
    regular_budget=max(0,math.floor(min(allocation*(1-share),sales_cash*policy['regular_reinvestment_fraction'])-regular_spend))
    limits.update(regular_budget=regular_budget,pilot_spend_2h=pilot_spend,
                  high_value_budget=high_budget,high_value_share=share,growth_stage=growth['stage'])
    if offensive:
        from premium import policy as premium_policy
        allocation=limits['budget']
        share=premium_policy(md).get('capital_share',policy['premium_capital_share'])
        if now<growth.get('paused_until',0):
            share=0
        high_budget=math.floor(allocation*share)
        regular_budget=normal_budget
        limits.update(mode='premium_offensive',high_value_budget=high_budget,regular_budget=regular_budget,
                      high_value_share=share,sales_fraction_cap_applied=False)
    metrics=growth.get('metrics',{})
    risk_trial=(now<growth.get('paused_until',0) and metrics.get('mature_sales',0)>=10 and metrics.get('net_after_refunds_storage',0)>0
                and (metrics.get('roi') or 0)>=.08 and metrics.get('refund_rate',1)<=.05
                and metrics.get('stale_high_units',1)==0 and metrics.get('defect_rate',1)<=.20)
    exploration_budget=math.floor(min(cash*.35,equity*.10)) if risk_trial else 0
    if risk_trial:
        high_budget=exploration_budget
        limits.update(high_value_budget=high_budget,exploration_budget=exploration_budget,
                      exploration_mode='positive_profit_bounded_trial',exploration_defect_limit=.20)
    active_pilots={sku for sku in held if sku in pilot_state['skus'] and held[sku]>0 and sku not in verified}
    pilot_slots=(max(0,int(policy['pilot_max_skus'])-len(active_pilots))
                 if policy.get('pilot_sku_limit_enabled',False) else None)
    multiplier=config(md)['stock_multiplier']
    recovery_budget=min(normal_budget,math.floor(cash*.25))
    limits['profitable_recovery_budget']=recovery_budget
    for sku,events in groups.items():
        product=catalog[sku];listed=product['list_price']
        mature_net=risk_stats.get('sku_net',{}).get(sku,{})
        if mature_net.get('sales',0)>=3 and mature_net.get('net',0)<=0:
            excluded.append({'sku':sku,'reason':'mature_refund_adjusted_loss','evidence':mature_net})
            continue
        if listed<=0:
            excluded.append({'sku':sku,'reason':'invalid_list_price'})
            continue
        surge=surge_factor(inventory,sku,1)
        worst=math.ceil(listed*.75*surge)
        own=[e for e in events if e['data'].get('shop') in tags]
        public=[e for e in events if e['data'].get('shop') not in tags]
        stale_held=sum(u['sku']==sku and u.get('kind')=='goods' and now-u.get('supplied_at',now)>=21600 for u in inventory.get('units',[]))
        if stale_held and not any(now-e['ts']<3600 for e in own):
            excluded.append({'sku':sku,'reason':'stale_inventory_without_recent_sale','held':held[sku],'stale_units':stale_held})
            continue
        # Fresh public prices replace stale low historical own prices.
        food=product.get('kind')=='food'
        price_events=public or own
        if food:
            min_life=max(6,product.get('shelf_hours',24)*.25)
            price_events=[e for e in price_events if isinstance(e['data'].get('expires_at'),(int,float))
                          and e['data']['expires_at']-e['ts']>=min_life*3600]
        prices=[e['data']['amount'] for e in price_events]
        if not prices:
            excluded.append({'sku':sku,'reason':'no_eligible_fresh_price_samples'})
            continue
        expected=quantile(prices,.50)
        cost_source='own_unit_costs' if acquisition[sku] else 'worst_case'
        cost=quantile(acquisition[sku],.75) if acquisition[sku] else worst
        if len(public_acquisition[sku])>=5 and len(public_suppliers[sku])>=2:
            # Replacement purchases use current supply evidence. Old held
            # units' costs are sunk costs, not today's acquisition price.
            cost=min(worst,quantile(public_acquisition[sku],.65))
            cost_source='public_supply_batch_cost_q65'
        elif not acquisition[sku] and public_acquisition[sku]:
            cost=min(worst,quantile(public_acquisition[sku],.75)*1.10)
            cost_source='public_supply_sparse_plus_10pct'
        elif not acquisition[sku]:
            # Comparable public supply batches provide a market acquisition
            # prior when this exact SKU has no local purchase history.
            ratios=[v/catalog[s]['list_price'] for s,values in public_acquisition.items()
                    if s in catalog and catalog[s].get('kind')==product.get('kind')
                    and catalog[s].get('list_price',0)>0
                    and .25*listed<=catalog[s]['list_price']<=listed*4 for v in values]
            if len(ratios)>=20:
                cost=min(worst,listed*quantile(ratios,.65)*1.10)
                cost_source='comparable_public_supply_q65_plus_10pct'
        # Historical cheap stock does not establish today's replacement cost.
        if cost_source!='worst_case':cost=min(worst,cost*surge)
        # Supply costs are random until purchase. Only the worst-case charge,
        # not a cheap historical unit, may justify spending opportunity cash.
        deep_discount=not food and worst<=expected*policy['deep_discount_ratio']
        # Public settlements establish demand for new SKUs; own paid history
        # refines it after entry. Profit and inventory coverage still gate orders.
        paid=recent_paid.get(sku,{})
        paid_support=not food and paid.get('count',0)>=3 and paid.get('gross',0)>0
        recovery_restock=(now<growth.get('paused_until',0) and paid_support
                          and mature_net.get('sales',0)>=3 and mature_net.get('net',0)>0 and not stale_held)
        proven_history=not food and sku in verified
        hourly=len(own)/2 or (paid['count']/6 if paid_support else own_performance[sku]['count']/6 if proven_history else 0)
        # Follow accelerating paid demand, bounded against a short burst.
        recent_hourly=sum(now-e['ts']<1800 for e in own)*2
        if proven_history and hourly:
            hourly=max(hourly,min(recent_hourly,hourly*1.5))
        food=product.get('kind')=='food'
        pilot=not own and not proven_history and not paid_support
        if pilot:
            pending=pilot_state['skus'].get(sku,{})
            if not held[sku] and 0<=now-pending.get('planned_at',-1000000)<180:
                excluded.append({'sku':sku,'reason':'supply_attempt_in_flight'})
                continue
            buyers={e['data'].get('buyer') for e in public if e['data'].get('buyer')}
            sellers={e['data'].get('shop') for e in public}
            trial_target=max(1,min(20,math.ceil(len(public)/2*policy['new_sku_market_share']*policy['stock_cover_hours'])))
            trial_min=policy.get('market_trial_min_list_price',10000)
            trial_max=policy.get('market_trial_max_list_price',20000000)
            if ((not food and not trial_min<=listed<=trial_max)
                    or len(public)<policy['pilot_min_trades'] or len(buyers)<policy['pilot_min_buyers']
                    or len(sellers)<2 or held[sku]>=trial_target or pilot_slots is not None and pilot_slots<=0):
                blockers=[]
                if not food and not trial_min<=listed<=trial_max:blockers.append('outside_trial_price_band')
                if len(public)<policy['pilot_min_trades']:blockers.append('insufficient_public_trades')
                if len(buyers)<policy['pilot_min_buyers']:blockers.append('insufficient_distinct_buyers')
                if len(sellers)<2:blockers.append('insufficient_distinct_sellers')
                if held[sku]:blockers.append('trial_stock_already_held')
                if pilot_slots is not None and pilot_slots<=0:blockers.append('trial_slots_full')
                excluded.append({'sku':sku,'reason':'new_sku_trial_gate','blockers':blockers,'held':held[sku],'pilot_slots':pilot_slots,'public_trades':len(public)})
                continue
            hourly=len(price_events)/2*policy['new_sku_market_share']
            if food:
                # Fresh eligible transactions, not clearance sales, establish
                # the demand and short-lived inventory target for food trials.
                if len(price_events)<policy['pilot_min_trades']:
                    excluded.append({'sku':sku,'reason':'insufficient_fresh_food_demand'})
                    continue
                trial_target=max(1,min(20,math.ceil(hourly*min(.75,product.get('shelf_hours',24)/4))))
        lifetime=product.get('shelf_hours',24)
        horizon=min(.75,policy['stock_cover_hours'],lifetime/4) if food else policy['stock_cover_hours']
        high_value=not food and listed>=policy['pilot_min_list_price']
        proven=not food and sku in verified
        if risk_trial and high_value and not proven:
            target_limit=1
        else:target_limit=None
        proof=sku_proof.get('skus',{}).get(sku,{}) if proof_fresh else {}
        own_proof=proof.get('per_shop',{}).get('totato2203-r3',{})
        if own_proof.get('exact_mature_sales',0)>=3 and own_proof.get('net',0)<=0:
            excluded.append({'sku':sku,'reason':'observed_mature_loss'})
            continue
        if high_value and not pilot:
            horizon=min(4,horizon*(1+growth['stage']*.25))
        capacity=hourly*horizon
        target=max(1,math.ceil(capacity*min(1,multiplier)))
        if pilot:target=max(target,trial_target)
        if proof.get('eligible'):
            # Market success permits a test, not immediate bulk replication.
            if own_proof.get('exact_mature_sales',0)<5:
                target=min(target,trial_target if pilot else 1)
            elif own_proof.get('roi',0)>=.08 and own_proof.get('turnover_hours',99)<=2:
                target=min(3,max(target,math.ceil(hourly)))
                # Cover the 20-minute supply cycle plus a short demand buffer.
                target=max(target,min(3,math.ceil(hourly*.75)))
        if not deep_discount:
            target=min(target,max(1,math.ceil(hourly*policy['stock_cover_hours'])))
            if proof.get('eligible') and own_proof.get('exact_mature_sales',0)>=5 and own_proof.get('roi',0)>=.08:
                target=max(target,min(3,math.ceil(hourly*.75)))
            if proven:target=max(target,min(16,math.ceil(hourly*4)))
        else:
            target=min(3,max(target,math.ceil(hourly*min(1.5,policy['stock_cover_hours']*2))))
        if target_limit is not None:target=min(target,target_limit)
        elif pilot:target=max(target,trial_target)
        target=turnover_target(target,hourly,[u for u in inventory.get('units',[]) if u['sku']==sku],now,deep_discount)
        wait=1/hourly
        survival=1-math.exp(-hourly*lifetime) if food else 1
        storage=0 if food else cost*inventory.get('storage_per_day',.01)*min(7,wait/24)
        net=expected*survival-cost-storage
        if not food and listed>=policy['pilot_min_list_price']:
            profile=read(Path(md)/'premium-policy.json',{}).get('skus',{}).get(sku,{})
            planned_hours=profile.get('hours',policy['default_warranty_hours'])
            if planned_hours:
                warranty_profile=warranty_cohort(risk_stats,sku,None,planned_hours)
                if warranty_profile.get('loss_evidence'):
                    excluded.append({'sku':sku,'reason':'mature_warranty_net_loss'})
                    continue
                net-=expected*min(1,warranty_profile['refund_rate']+.02)
        if net<=0:
            excluded.append({'sku':sku,'reason':'non_positive_expected_net','expected_net':net})
            continue
        if (pilot or high_value) and net/cost<policy['pilot_min_roi']:
            excluded.append({'sku':sku,'reason':'expected_roi_below_threshold','expected_roi':net/cost})
            continue
        equity=inventory.get('balance',0)+sum(u['cost'] for u in inventory.get('units',[]))
        sku_room=max(0,math.floor((equity*policy['sku_equity_fraction']-held_cost[sku])/worst))
        need=min(20,max(0,target-held[sku]),sku_room)
        if recovery_restock:
            # An unrelated slow SKU must not stop a currently paid, profitable
            # SKU. Cover one supply cycle while retaining its individual gates.
            need=min(need,max(1,math.ceil(hourly*.75)))
        if pilot:
            need=min(trial_target,need)
        if need:
            batch_surge=surge_factor(inventory,sku,need)
            cost*=batch_surge/surge
            net-=cost*(1-surge/batch_surge)
            surge=batch_surge
            worst=math.ceil(listed*.75*surge)
            if net<=0 or (pilot or high_value) and net/max(1,cost)<policy['pilot_min_roi']:
                excluded.append({'sku':sku,'reason':'batch_surge_erases_margin'})
                continue
            candidates.append({'sku':sku,'need':need,'worst_cost':worst,'expected_sale':expected,
                               'estimated_cost':cost,'cost_source':cost_source,'surge_factor':surge,'public_cost_samples':len(public_acquisition[sku]),'expected_net':net,'sales_per_hour':hourly,
                               'score':net*min(hourly,need/horizon),'capital_roi':net/max(1,cost),'new_sku':pilot,'high_value':high_value,
                               'public_buyers':len({e['data'].get('buyer') for e in public}),
                               'public_trades':len(public),'leader_evidence':bool(proof.get('eligible')),
                               'own_mature_sales':own_proof.get('exact_mature_sales',0),'deep_discount':deep_discount,'verified_restock':proven,
                               'profitable_recovery':recovery_restock})
        else:excluded.append({'sku':sku,'reason':'sku_cap' if not sku_room else 'inventory_covers_demand','held':held[sku],'target':target})
    buy=[]
    deadline=inventory.get('restock',{}).get('closes_at')
    order_slots=10 if deadline is None else min(10,max(0,math.floor((deadline-now-30)/12)))
    limits['window_order_slots']=order_slots
    # Select pilot slots first; both pilot and regular orders share the same
    # sales-funded total ceiling. Ordinary purchases cannot consume the reserve.
    for item in sorted(candidates,key=lambda r:(-r['score'],-r['capital_roi'],not r['verified_restock'],-r['public_buyers'],r['sku'])):
        if len(buy)>=order_slots:break
        qty=min(item['need'],cash//item['worst_cost'])
        if item['verified_restock']:
            qty=min(qty,verified_budget//item['worst_cost'])
        elif risk_trial and item['high_value']:
            qty=min(qty,1,exploration_budget//item['worst_cost'])
        elif not item['deep_discount']:
            qty=min(qty,normal_budget//item['worst_cost'])
        if item['profitable_recovery']:
            qty=min(qty,recovery_budget//item['worst_cost'])
        elif item['high_value'] and not item['verified_restock'] and (not item['deep_discount'] or now<growth.get('paused_until',0)):
            qty=min(qty,high_budget//item['worst_cost'])
        if item['new_sku'] and pilot_slots is not None:
            qty=min(qty,int(pilot_slots>0))
        elif not item['high_value'] and not item['deep_discount'] and not item['verified_restock']:
            qty=min(qty,regular_budget//item['worst_cost'])
        if qty:
            buy.append({'sku':item['sku'],'qty':int(qty)})
            cash-=qty*item['worst_cost']
            if item['profitable_recovery']:recovery_budget-=qty*item['worst_cost']
            normal_budget=max(0,normal_budget-qty*item['worst_cost'])
            verified_budget=max(0,verified_budget-qty*item['worst_cost'])
            if item['new_sku']:
                if pilot_slots is not None:pilot_slots-=1
                pilot_state['skus'][item['sku']]={'planned_at':now,'max_cost':item['worst_cost']}
            elif not item['high_value'] and not item['deep_discount'] and not item['verified_restock']:
                regular_budget-=qty*item['worst_cost']
            if item['high_value']:
                high_budget=max(0,high_budget-qty*item['worst_cost'])
                if risk_trial and not item['verified_restock']:
                    exploration_budget=max(0,exploration_budget-qty*item['worst_cost'])
            if len(buy)>=10:break  # Official kit sends at most ten SKU orders per call.
        else:excluded.append({'sku':item['sku'],'reason':'allocation_budget_below_unit_cost','unit_cost':item['worst_cost'],'cash_remaining':cash,'verified':item['verified_restock']})
    from optimizer import atomic
    atomic(Path(md)/'pilot-state.json',pilot_state)
    atomic(Path(md)/'procurement-preview.json',{'at':now,'strategy':'funded-high-value-v1','limits':limits,
           'reason':'sales-funded budget exhausted' if limits['budget']<=0 else 'funded allocation',
           'growth':growth,'candidates':candidates,'excluded':excluded,'verified_skus':sorted(verified),'buy':buy})
    return {'buy':buy}
