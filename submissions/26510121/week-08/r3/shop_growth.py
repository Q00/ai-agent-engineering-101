"""Capital/time-aware shop planning using documented supply and observed sales.

Quantiles and marginal sale probabilities are explicit Poisson scenarios, not
identified demand distributions. Cash affordability remains worst-case bounded.
"""
import math


def poisson_tail(arrivals, quantity):
    if quantity<=0:return 1.0
    if arrivals<=0:return 0.0
    term=math.exp(-arrivals);cdf=term
    for index in range(1,quantity):
        term*=arrivals/index;cdf+=term
    return max(0.0,min(1.0,1-cdf))


def target_stock(rate, paid, config):
    maximum=min(20,config['restock_max_qty'])
    arrivals=max(0,rate)*config.get('supply_cycle_seconds',1200)/3600
    # Proven repeat demand can use the legal 20-unit lot capacity. A raw
    # request or peer announcement alone never qualifies as paid evidence.
    service=config.get('shop_stock_service_level',.9) if paid>=3 else .65
    target=1
    while target<maximum and poisson_tail(arrivals,target+1)>1-service:
        target+=1
    return min(maximum,target if paid>=3 else min(2,target))


def resale_terms(product,public,journal,config,now):
    """Match grade and warranty instead of taxing every goods sale as insured."""
    base=min(product['list_price'],public.get('median',.85*product['list_price']))
    if product.get('kind')!='goods':return {'price':base,'refund_stress':0,'terms':[]}
    from .risk import goods_risk
    rows=[]
    for grade,weight in [('A',.7),('B',.2),('C',.1)]:
        choices=[]
        for term in public.get('term_prices',[]):
            if term.get('grade')!=grade or term.get('n',0)<3 or term.get('warranty_hours') not in (0,3,6):continue
            warranty=term['warranty_hours'];price=min(product['list_price'],term['median'])
            refund=goods_risk(journal,grade,warranty,config,now)['failure_upper'] if warranty else 0
            # This is an opportunity-cost scenario, not a market fee.
            liquidity=price*(warranty/6)*config.get('shop_warranty_liquidity_penalty',.02)
            choices.append({'grade':grade,'weight':weight,'price':price,'refund_stress':refund,
                'warranty_hours':warranty,'observations':term['n'],
                'selection_value':price*(1-refund)-liquidity})
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
    probability=(evidence.get('sale_probability_scenario',.25) if evidence.get('exploration') else
                 poisson_tail(max(0,rate)*horizon,held+quantity))
    holding=min(horizon,max(1/3,(held+quantity)/max(.01,rate)))
    price=evidence['resale_price_scenario'];cost=evidence['scenario_unit_cost']
    revenue=probability*price*(1-evidence['refund_stress'])
    storage=cost*.01*holding/24 if evidence['kind']=='goods' else 0
    expiry=(1-probability)*cost*.25 if evidence['kind']=='food' else 0
    return {'book':revenue-probability*cost-storage-expiry,'revenue':revenue,
            'profit':revenue-storage-expiry,'probability':probability,'holding':holding}


def allocate_velocity(candidates,inv,capacity,config,existing,merchant):
    total=capacity['spendable'];slots=max(0,config['inventory_target_units']-len(existing))
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
            categories.get(c['category'],0)+cost<=total*.6+1e-6 and
            (not c['evidence']['exploration'] or exploration+cost<=total*config['exploration_fraction']+1e-6) and
            marginal_row(c)['book']>0)
    def add(c):
        nonlocal used,exploration,slots
        delta=marginal_row(c);cost=unit_cost(c)
        row=selected.setdefault(c['sku'],{'source':c,'qty':0,'deltas':[]})
        row['qty']+=1;row['deltas'].append(delta)
        used+=cost;slots-=1;categories[c['category']]=categories.get(c['category'],0)+cost
        if c['evidence']['exploration']:exploration+=cost
        coverage.update(c['evidence'].get('growth',{}).get('coverage_keys',[]))
    breadth=total*config.get('assortment_coverage_fraction',.35)
    while True:
        available=[c for c in candidates if feasible(c) and c['sku'] not in selected and gain(c)>0 and
                   len(selected)<max(1,config['restock_max_lines']//3) and used+unit_cost(c)<=breadth]
        if not available:break
        add(max(available,key=lambda c:(gain(c)/max(1,unit_cost(c))**.5,marginal_row(c)['book'])))
    def priority(c):
        delta=marginal_row(c)
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
                marginal_units=deltas,portfolio_used=used,
                capital_velocity_scenario=sum(d['book']/d['holding'] for d in deltas)/max(1,unit_cost(c)*qty))))
    return sorted(result,key=lambda c:(c['expected_delta']['book']/
        max(1/3,c['evidence']['expected_holding_hours']),c['expected_delta']['book']),reverse=True)
