"""Shadow-only procurement research. Not imported by any execution hook.

Only A-grade sales count as upside; unsold draws retain acquisition Book value.
Every draw pays storage. Supplier grades follow the published 70/20/10 mixture.
"""
import math
from .shop_growth import poisson_tail


def marginal_a_sale(draw,arrivals,held_a=0):
    if type(draw) is not int or not 1<=draw<=20 or arrivals<0 or held_a<0:
        raise ValueError('invalid_option_input')
    return .7*sum(math.comb(draw-1,k)*.7**k*.3**(draw-1-k)*poisson_tail(arrivals,held_a+k+1)
        for k in range(draw))


def option_rows(observed,catalog,held_a,risk,hours,remaining_days,*,cost_ratio=.75,use_mean=False,rate_scale=1):
    """Compare cost endpoints as scenarios; .75 is A's published cost upper bound.

    The risk bound is a pooled seller cohort, not a SKU-specific guarantee.
    This function returns research rows and never authorizes purchases.
    """
    q=risk.get('refund_upper')
    if use_mean:
        if not risk.get('mean_validated') or risk.get('mature_samples',0)<40:return []
        q=risk.get('refund_mean')
    if (type(q) not in (int,float) or not math.isfinite(q) or not 0<=q<=1
            or not .55<=cost_ratio<=.75 or not 0<rate_scale<=1 or hours<=0 or remaining_days<0):return []
    rows=[]
    for sku,o in observed.items():
        product=catalog.get(sku,{})
        if (product.get('kind')!='goods' or o['samples']<3 or o['buyers']<2
                or o['age_seconds']>1800):continue
        price=min(product['list_price'],o['price_p25'])
        factor=o['surge_factor']
        if not math.isfinite(factor) or not 1<=factor<=1.5:continue
        # Full absolute supplier bound remains reserved for each draw.
        capital=math.ceil(.75*product['list_price']*1.5)*(1+.01*remaining_days)
        conditional_cost=cost_ratio*product['list_price']*factor
        margin=price*(1-q)-conditional_cost
        if margin<=0:continue
        arrivals=o['samples']/6/max(1,o['competitors']+1)*hours*rate_scale
        storage=capital*.01*remaining_days
        deltas=[];revenues=[]
        for draw in range(1,21):
            prob=marginal_a_sale(draw,arrivals,held_a.get(sku,0))
            delta=prob*margin-storage
            if delta<=0:break
            deltas.append(delta);revenues.append(prob*price*(1-q))
        if not deltas:continue
        rows.append({'sku':sku,'name':product.get('name'),'qty':len(deltas),
            'forecast_book':sum(deltas),'forecast_revenue':sum(revenues),'capital':capital*len(deltas),
            'marginal_book':deltas,'a_sale_margin_scenario':margin,'held_a':held_a.get(sku,0),
            'samples':o['samples'],'buyers':o['buyers'],'contract_warranty_hours':6,
            'price_p25':price,'failure_scenario':q,'cost_ratio_scenario':cost_ratio,
            'arrival_rate_stress_scale':rate_scale})
    return sorted(rows,key=lambda r:r['forecast_book'],reverse=True)
