"""Capital/time-aware shop planning using documented supply and observed sales.

Quantiles and marginal sale probabilities are explicit Poisson scenarios, not
identified demand distributions. Cash affordability remains worst-case bounded.
"""
import math
from .limits import capital_caps,fraction as capital_fraction


def poisson_tail(arrivals, quantity):
    if quantity<=0:return 1.0
    if arrivals<=0:return 0.0
    # Sum from the requested boundary toward the smaller tail. Starting at
    # exp(-lambda) underflows for large demand once fixed quantity caps are gone.
    if not math.isfinite(arrivals):raise ValueError('nonfinite_poisson_mean')
    k=int(quantity)
    if k>arrivals:
        term=math.exp(k*math.log(arrivals)-arrivals-math.lgamma(k+1));total=term
        while term>max(1e-300,total*1e-14):
            k+=1;term*=arrivals/k;total+=term
        return max(0.,min(1.,total))
    k-=1;term=math.exp(k*math.log(arrivals)-arrivals-math.lgamma(k+1));cdf=term
    while k>0 and term>max(1e-300,cdf*1e-14):
        term*=k/arrivals;k-=1;cdf+=term
    return max(0.,min(1.,1-cdf))


def stock_caps_enabled(config):
    return config.get('shop_inventory_caps_enabled',True)


def poisson_target(arrivals,service):
    """Finite demand quantile, without an arbitrary inventory-unit ceiling."""
    if not math.isfinite(arrivals) or arrivals<0:raise ValueError('invalid_demand_mean')
    low=1;high=max(1,math.ceil(arrivals+10*math.sqrt(arrivals)+10))
    while low<high:
        mid=(low+high)//2
        if poisson_tail(arrivals,mid+1)>1-service:low=mid+1
        else:high=mid
    return low


def target_stock(rate, paid, config, public_qualified=False, public_limit=6,coverage_hours=None):
    arrivals=max(0,rate)*config.get('supply_cycle_seconds',1200)/3600
    supported=paid>=3 or public_qualified and config.get('objective_feedback_enabled')
    if config.get('shop_adaptive_demand_enabled'):
        hours=coverage_hours if type(coverage_hours) in (int,float) and math.isfinite(coverage_hours) and coverage_hours>0 else config.get('supply_cycle_seconds',1200)/3600
        if config.get('book_growth_v23_enabled'):
            multiplier=max(1,config.get('shop_demand_horizon_cycles',1))
            if capital_caps(config):multiplier=min(3,multiplier)
            hours=min(config.get('planning_horizon_hours',6),hours*multiplier)
        arrivals=max(0,rate)*hours
    elif supported and config.get('objective_feedback_enabled'):
        arrivals*=max(1,min(3 if paid>=3 else 2,config.get('shop_demand_horizon_cycles',1)))
    # Proven repeat demand can use the legal 20-unit lot capacity. A raw
    # request or peer announcement alone never qualifies as paid evidence.
    service=config.get('shop_stock_service_level',.9) if paid>=3 else .8 if supported else .65
    if not stock_caps_enabled(config):return poisson_target(arrivals,service)
    maximum=min(20,config['restock_max_qty'])
    target=1
    while target<maximum and poisson_tail(arrivals,target+1)>1-service:
        target+=1
    # Diverse, recent external payments can fund a measured first batch even
    # before our own stock was visible. Public activity alone is capped at six;
    # repeated own paid demand can use all twenty legal units per supply call.
    return min(maximum,target if paid>=3 else min(max(1,min(20,public_limit)) if supported else 2,target))


def resale_terms(product,public,journal,config,now):
    """Match grade and warranty instead of taxing every goods sale as insured."""
    base=min(product['list_price'],public.get('median',.85*product['list_price']))
    if product.get('kind')!='goods':
        if config.get('shop_championship_enabled'):
            terms=[]
            for grade,weight in [('A',.25),('B',.5),('C',.25)]:
                term=next((t for t in public.get('term_prices',[]) if t.get('grade')==grade and
                           not t.get('warranty_hours') and t.get('n',0)>=3),None)
                terms.append({'grade':grade,'weight':weight,'price':min(product['list_price'],term['median']) if term else base})
            return {'price':sum(r['weight']*r['price'] for r in terms),'refund_stress':0,'terms':terms}
        return {'price':base,'refund_stress':0,'terms':[]}
    from .risk import goods_risk
    rows=[]
    for grade,weight in [('A',.7),('B',.2),('C',.1)]:
        choices=[]
        for term in public.get('term_prices',[]):
            allowed=(0,3,6)
            if config.get('shop_reputation_v24_enabled'):
                minimum=config.get('shop_new_goods_warranty_hours',6)
                allowed=tuple(h for h in (6,24,72) if h>=minimum)
            if term.get('grade')!=grade or term.get('n',0)<3 or term.get('warranty_hours') not in allowed:continue
            warranty=term['warranty_hours'];price=min(product['list_price'],term['median'])
            if config.get('shop_championship_enabled'):
                from .risk import shop_refund_risk
                from .decision_view import fact
                refund=fact(journal,('stock-seller-risk',grade,warranty),lambda:shop_refund_risk(journal,grade,warranty,config,now))['refund_upper']
            else:
                refund=goods_risk(journal,grade,warranty,config,now)['failure_upper'] if warranty else 0
            # This is an opportunity-cost scenario, not a market fee.
            liquidity=price*(warranty/6)*config.get('shop_warranty_liquidity_penalty',.02)
            choices.append({'grade':grade,'weight':weight,'price':price,'refund_stress':refund,
                'warranty_hours':warranty,'observations':term['n'],
                'selection_value':price*(1-refund)-liquidity})
        if not choices and config.get('shop_reputation_v24_enabled'):
            # A new insured contract cannot borrow an old uninsured margin.
            from .risk import shop_refund_risk
            hours=config.get('shop_new_goods_warranty_hours',6)
            from .decision_view import fact
            risk=fact(journal,('stock-seller-risk',grade,hours),lambda:shop_refund_risk(journal,grade,hours,config,now))['refund_upper']
            choices=[{'grade':grade,'weight':weight,'price':base,'refund_stress':risk,
                'warranty_hours':hours,'observations':0,'selection_value':base*(1-risk),
                'fallback':'mixed public price; actual insured seller-risk scenario'}]
        rows.append(max(choices,key=lambda r:r['selection_value']) if choices else
                    {'grade':grade,'weight':weight,'price':base,'refund_stress':.15,
                     'warranty_hours':None,'observations':0,'fallback':'mixed-term conservative prior'})
    price=sum(r['weight']*r['price'] for r in rows)
    refund=sum(r['weight']*r['price']*r['refund_stress'] for r in rows)/max(1,price)
    return {'price':price,'refund_stress':refund,'terms':rows}


def marginal(candidate,quantity,held=0):
    evidence=candidate['evidence']
    rate=evidence.get('arrival_rate_scenario',0)
    horizon=evidence.get('planning_hours',6)
    if evidence.get('exploration') and not evidence.get('uncapped_inventory'):
        probability=evidence.get('sale_probability_scenario',.25)
    else:
        arrivals=max(0,rate)*horizon
        if evidence.get('exploration'):
            prior=max(0.,min(.999999,evidence.get('sale_probability_scenario',.25)))
            # Unknown is not known-zero demand. Apply the first-unit discovery
            # prior once to an arrival process, then price additional depth.
            arrivals=max(arrivals,-math.log1p(-prior))
        probability=poisson_tail(arrivals,held+quantity)
        if evidence.get('exploration'):
            probability=min(evidence.get('sale_probability_scenario',.25),probability)
    holding=min(horizon,max(1/3,(held+quantity)/max(.01,rate)))
    price=evidence['resale_price_scenario'];cost=evidence['scenario_unit_cost']
    strict=evidence.get('service_eligibility')
    if strict:
        from .service_policy import strict_sale_probability
        strict_probability=strict_sale_probability(strict,quantity,horizon)
        if strict_probability>probability:
            probability=strict_probability
            # Eligible fresh draws can be a costlier selected grade cohort.
            cost=max(cost,evidence['list_price']*evidence.get('supply_surge_factor',1)*strict['conditional_cost_ratio'])
            holding=min(horizon,max(1/3,(strict['eligible_held']+quantity*strict['eligible_draw_probability'])/max(.01,strict['need_rate'])))
    revenue=probability*price*(1-evidence['refund_stress'])
    storage=cost*.01*holding/24 if evidence['kind']=='goods' else 0
    expiry=(1-probability)*cost*.25 if evidence['kind']=='food' else 0
    return {'book':revenue-probability*cost-storage-expiry,'revenue':revenue,
            'profit':revenue-storage-expiry,'probability':probability,'holding':holding}


def allocate_velocity(candidates,inv,capacity,config,existing,merchant):
    total=capacity['spendable'];slots=planning_slots(config,existing,candidates)
    # If even the most expensive legal set of lines fits, scarce resources
    # are supply lines/time, not cash. Do not optimize pennies per credit then.
    lines=min(10,config['restock_max_lines'])
    cash_abundant=bool(config.get('book_growth_v23_enabled') and
        sum(sorted((c['capital_commitment'] for c in candidates),reverse=True)[:lines])<=total)
    held={};coverage=set();weights=merchant.get('coverage_weights',{})
    for unit in existing:
        held[unit['sku']]=held.get(unit['sku'],0)+1
        coverage.update(merchant.get('skus',{}).get(unit['sku'],{}).get('coverage_keys',[]))
    selected={};used=exploration=0;categories={}
    def unit_cost(c):return c['capital_commitment']/c['qty']
    def marginal_row(c):return marginal(c,selected.get(c['sku'],{}).get('qty',0)+1,held.get(c['sku'],0))
    def gain(c):return sum(weights.get(k,0) for k in c['evidence'].get('growth',{}).get('coverage_keys',[]) if k not in coverage)
    def feasible(c):
        quantity=selected.get(c['sku'],{}).get('qty',0);cost=unit_cost(c)
        return (slots>0 and quantity<c['qty'] and used+cost<=total+1e-6 and
            (c['sku'] in selected or len(selected)<min(10,config['restock_max_lines'])) and
            (not capital_caps(config) or categories.get(c['category'],0)+cost<=total*.6+1e-6) and
            (not c['evidence']['exploration'] or exploration+cost<=total*capital_fraction(config,'exploration_fraction',.1)+1e-6) and
            marginal_row(c)['book']>0)
    def add(c):
        nonlocal used,exploration,slots
        delta=marginal_row(c);cost=unit_cost(c)
        row=selected.setdefault(c['sku'],{'source':c,'qty':0,'deltas':[]})
        row['qty']+=1;row['deltas'].append(delta)
        used+=cost;slots-=1;categories[c['category']]=categories.get(c['category'],0)+cost
        if c['evidence']['exploration']:exploration+=cost
        coverage.update(c['evidence'].get('growth',{}).get('coverage_keys',[]))
    breadth=total*capital_fraction(config,'assortment_coverage_fraction',.35)
    while True:
        if cash_abundant:break
        available=[c for c in candidates if feasible(c) and c['sku'] not in selected and gain(c)>0 and
                   len(selected)<max(1,config['restock_max_lines']//3) and used+unit_cost(c)<=breadth]
        if not available:break
        add(max(available,key=lambda c:(gain(c)/max(1,unit_cost(c))**.5,marginal_row(c)['book'])))
    def priority(c):
        delta=marginal_row(c)
        if cash_abundant:
            # A new line is valued as its feasible positive-margin lot. One
            # tiny unit must not crowd out a valuable multi-unit supply slot.
            if c['sku'] not in selected:
                lot=[marginal(c,q,held.get(c['sku'],0)) for q in range(1,c['qty']+1)]
                value=sum(max(0,r['book']) for r in lot)
                duration=max(1/3,max(r['holding'] for r in lot))
            else:value=delta['book'];duration=delta['holding']
            return (value/max(1/3,duration),delta['book']/max(1/3,delta['holding']),delta['book'])
        slot_cost=0 if c['sku'] in selected else config.get('shop_supply_slot_capital',100000)
        capital_time=(unit_cost(c)+slot_cost)*delta['holding']
        scaled=(c.get('rank_progress_surrogate') or 0)*delta['book']/max(1,c['expected_delta']['book'])
        return (scaled/max(1,capital_time),delta['book']/max(1,capital_time),delta['book'])
    while True:
        available=[c for c in candidates if feasible(c)]
        if not available:break
        add(max(available,key=priority))
    result=[]
    for row in selected.values():
        c=row['source'];qty=row['qty'];fraction=qty/c['qty'];deltas=row['deltas']
        result.append(dict(c,qty=qty,max_cost=c['max_cost']*fraction,
            capital_commitment=c['capital_commitment']*fraction,
            score=sum(d['book'] for d in deltas),
            expected_delta={key:sum(d[key] for d in deltas) for key in ['book','profit','revenue']},
            allocation_book='capital_velocity',evidence=dict(c['evidence'],
                scarce_resource='supply_lines_and_time' if cash_abundant else 'capital_and_time',
                marginal_units=deltas,portfolio_used=used,
                capital_velocity_scenario=sum(d['book']/d['holding'] for d in deltas)/max(1,unit_cost(c)*qty))))
    return sorted(result,key=lambda c:(c['expected_delta']['book']/
        max(1/3,c['evidence']['expected_holding_hours']),c['expected_delta']['book']),reverse=True)


def inventory_limit(config):
    """A bounded capacity ceiling, never a requirement to buy or spend cash."""
    if not stock_caps_enabled(config):return None
    base=config['inventory_target_units']
    return max(base,min(240,config.get('shop_inventory_ceiling',180))) if config.get('shop_championship_enabled') else base


def planning_slots(config,existing,candidates):
    limit=inventory_limit(config)
    return sum(c['qty'] for c in candidates) if limit is None else max(0,limit-len(existing))


def demand_diagnostics(inv,merchant,config):
    """Expose target-setting mechanisms to background research, never commands."""
    from collections import Counter
    counts=Counter(u['sku'] for u in inv.get('units',[]))
    rows=[];low=repeat=shortfall=0
    for sku,held in counts.items():
        g=merchant.get('skus',{}).get(sku,{})
        paid=g.get('positive_paid_while_available',0)
        trend=config.get('shop_sku_trend_factors',{}).get(sku,1) if not config.get('shop_adaptive_demand_enabled') else 1
        rate=g.get('paid_arrival_rate_scenario',0) if paid>=3 else g.get('arrival_rate_scenario',0)
        rate=max(.01,rate*trend) if paid>=3 else max(0,rate*trend)
        public=sku in config.get('shop_public_demand_skus',[])
        calibration=g.get('demand_calibration') or {}
        coverage=calibration.get('coverage_hours')
        target=target_stock(rate,paid,config,public,config.get('shop_public_demand_caps',{}).get(sku,6),coverage_hours=coverage)
        low+=target<=2;repeat+=paid>=3;shortfall+=max(0,target-held)
        rows.append({'sku':sku,'held':held,'target_scenario':target,'rate_per_hour_scenario':rate,
            'rate_source':'own_positive_paid' if paid>=3 else 'public_prior_or_request_conversion',
            'own_positive_paid':paid,'public_qualified':public,
            'observed_availability_hours':g.get('available_hours_observed'),
            'adaptive_calibration':calibration,
            'lost_exact_requests_not_directly_added_to_rate':g.get('lost_exact_requests',0)})
    return {'inventory_unit_caps_enabled':stock_caps_enabled(config),
        'total_unit_ceiling':inventory_limit(config),'held_skus':len(counts),
        'held_skus_target_at_most_two':low,'held_skus_with_repeat_paid_evidence':repeat,
        'target_shortfall_units_scenario':shortfall,'merchant_context_at':merchant.get('at'),
        'supply_cycle_seconds':config.get('supply_cycle_seconds',1200),
        'repeat_coverage_cycles':None if config.get('shop_adaptive_demand_enabled') and not config.get('book_growth_v23_enabled') else max(1,min(3,config.get('shop_demand_horizon_cycles',1))) if capital_caps(config) else max(1,config.get('shop_demand_horizon_cycles',1)),
        'public_only_coverage_cycles':None if config.get('shop_adaptive_demand_enabled') else max(1,min(2,config.get('shop_demand_horizon_cycles',1))),
        'public_equal_share_prior_multiplier':'data-derived' if config.get('shop_adaptive_demand_enabled') else .5,
        'demand_policy_id':merchant.get('demand_policy_id'),
        'examples':sorted(rows,key=lambda r:r['target_scenario']-r['held'],reverse=True)[:4],
        'interpretation':'Low rate/coverage can retain small targets without a unit cap. Prior and Poisson arrivals are scenarios, not identified hidden demand. Lost requests require feasibility attribution before adding demand.'}


def allocate_championship(candidates,inv,capacity,config,existing,merchant):
    """Compare absolute progress with capital velocity on identical unit economics.

    Cash is a constraint. A cheap item's ROI cannot consume the last legal call
    when a feasible alternative makes more expected progress per holding hour.
    The velocity portfolio remains a candidate for capital-constrained markets.
    """
    cfg=dict(config,inventory_target_units=inventory_limit(config))
    velocity=allocate_velocity(candidates,inv,capacity,cfg,existing,merchant)
    total=capacity['spendable'];held={};selected={};used=probes=0;categories={}
    for u in existing:held[u['sku']]=held.get(u['sku'],0)+1
    slots=planning_slots(cfg,existing,candidates)
    def cost(c):return c['capital_commitment']/c['qty']
    def selected_qty(c):return len(selected.get(c['sku'],{}).get('deltas',[]))
    def delta(c):return marginal(c,selected_qty(c)+1,held.get(c['sku'],0))
    def progress(c,d):
        weight=c['evidence'].get('adaptive_category_weight',1)
        return max(0,c.get('rank_progress_surrogate') or 0)*d['book']/max(1,c['expected_delta']['book'])*weight
    def feasible(c):
        q=selected_qty(c)
        held_room=max(0,cfg['restock_max_qty']-held.get(c['sku'],0)) if stock_caps_enabled(cfg) else 20
        return (slots>0 and q<min(c['qty'],20,held_room) and used+cost(c)<=total+1e-6 and
                (c['sku'] in selected or len(selected)<min(10,cfg['restock_max_lines'])) and
                (not capital_caps(config) or categories.get(c['category'],0)+cost(c)<=total*.6+1e-6) and
                (not c['evidence']['exploration'] or probes+cost(c)<=total*capital_fraction(cfg,'exploration_fraction',.1)+1e-6) and
                delta(c)['book']>0)
    while True:
        available=[c for c in candidates if feasible(c)]
        if not available:break
        # Marginal holding includes inventory already present, preventing the
        # same paid arrivals from justifying unlimited depth across windows.
        def priority(c):
            d=delta(c)
            return (progress(c,d)/max(1/3,d['holding']),d['book']/max(1/3,d['holding']))
        c=max(available,key=priority);d=delta(c)
        row=selected.setdefault(c['sku'],{'source':c,'deltas':[]});row['deltas'].append(d)
        used+=cost(c);slots-=1;categories[c['category']]=categories.get(c['category'],0)+cost(c)
        if c['evidence']['exploration']:probes+=cost(c)
    absolute=[]
    for row in selected.values():
        c=row['source'];ds=row['deltas'];qty=len(ds);fraction=qty/c['qty']
        absolute.append(dict(c,qty=qty,max_cost=c['max_cost']*fraction,
            capital_commitment=c['capital_commitment']*fraction,score=sum(d['book'] for d in ds),
            expected_delta={key:sum(d[key] for d in ds) for key in ['book','profit','revenue']},
            allocation_book='absolute_progress',evidence=dict(c['evidence'],marginal_units=ds,portfolio_used=used)))
    # Score using original candidate totals rather than rescaled selected rows.
    originals={c['sku']:c for c in candidates}
    def objective(rows):
        rank=book=0
        for row in rows:
            c=originals[row['sku']]
            for d in row['evidence']['marginal_units']:
                rank+=progress(c,d)/max(1/3,d['holding'])
                book+=d['book']/max(1/3,d['holding'])
        return rank,book
    chosen=max([velocity,absolute],key=objective)
    evidence={'velocity':objective(velocity),'absolute':objective(absolute),
              'inventory_ceiling':cfg['inventory_target_units'],
              'note':'shared-economics forecast comparison, not measured lift'}
    return sorted([dict(c,evidence=dict(c['evidence'],portfolio_comparison=evidence)) for c in chosen],
                  key=lambda c:sum(d['book']/max(1/3,d['holding']) for d in c['evidence']['marginal_units']),reverse=True)
