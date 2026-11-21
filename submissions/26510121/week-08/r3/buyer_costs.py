"""Background hidden-cost scenarios from trusted own sale costs, never peer claims.

Sales are a selected proxy for other shops' acquisition draws, not exact unit
costs. Small cohorts shrink to a public-range scenario grid (no uniform-law
claim). Main reads at most nine numeric samples; no scans or LLM in checkout.
"""
from collections import defaultdict
import math
import time
from .state import identity

KEY='buyer-cost-scenarios:v22'


def update(journal,config,now=None):
    now=time.time() if now is None else now
    catalog=journal.snapshot('catalog.json',{});groups=defaultdict(dict)
    for row in journal.events('shop_settled',10000):
        if not 0<=now-row['_ts']<=21600:continue
        product=catalog.get(row.get('sku'),{});L=product.get('list_price');cost=row.get('cost')
        grade=row.get('grade');kind=product.get('kind');unit=row.get('unit')
        if (grade not in ('A','B','C') or kind not in ('food','goods') or not unit or
                type(L) not in (int,float) or L<=0 or type(cost) not in (int,float) or
                not math.isfinite(cost) or not 0<=cost/L<=1.125):continue
        groups[kind+':'+grade].setdefault(unit,cost/L)
    rows={}
    for key,units in groups.items():
        values=sorted(units.values());valid=[v for v in values if v<1]
        rows[key]={'n':len(values),'positive_margin_n':len(valid),
            'quantile_ratios':[valid[round((len(valid)-1)*q/8)] for q in range(9)] if valid else [],
            'nonpositive_margin_n':len(values)-len(valid)}
    packet={'at':now,'expires_at':now+180,'rules_hash':journal.get('market_rules',{}).get('sha256'),
            'groups':rows,'source':'own_settled_costs_selected_proxy_not_other_sellers_cost',
            'selection_and_surge_transfer_uncertain':True}
    packet['id']=identity(packet);journal.put(KEY,packet)
    journal.add('buyer_cost_model_prepared',{'at':now,'id':packet['id'],
        'samples':{k:r['n'] for k,r in rows.items()},'source':packet['source']},role='buyer')
    return packet


def scenario(journal,product,grade,now=None):
    from .buyer_scoring import cost_scenario
    now=time.time() if now is None else now
    result=cost_scenario(product,grade);L=product['list_price']
    low=result['unit_cost']/L
    # Food may have degraded from a higher supply grade. With no grade,
    # retain a broad public-range scenario, never silently assume certified A.
    high=.75 if product.get('kind')=='food' else {'A':.75,'B':.60,'C':.45}.get(grade,.75)
    ratios=[low+(high-low)*q/8 for q in range(9)]
    packet=journal.get(KEY,{})
    group=packet.get('groups',{}).get(product.get('kind','')+':'+str(grade),{})
    usable=(0<=now-packet.get('at',-1000)<180 and now<packet.get('expires_at',0) and
            packet.get('rules_hash')==journal.get('market_rules',{}).get('sha256') and
            group.get('positive_margin_n',0)>=20 and len(group.get('quantile_ratios',[]))==9 and
            all(type(v) in (int,float) and math.isfinite(v) and low<=v<1 for v in group['quantile_ratios']))
    if usable:
        # Shrink sparse/selected proxy cohorts towards the public scenario.
        weight=min(.8,group['positive_margin_n']/(group['positive_margin_n']+50))
        ratios=[(1-weight)*a+weight*b for a,b in zip(ratios,group['quantile_ratios'])]
    result.update(cost_samples=[min(L-1,max(result['unit_cost'],round(L*v))) for v in ratios],
        expected_source='shrunk_own_cost_proxy' if usable else 'public_range_scenario_grid',
        samples=group.get('positive_margin_n',0) if usable else 0,
        model_id=packet.get('id') if usable else None,
        probabilities_are_scenarios=True,other_seller_cost_exact=False,
        nonpositive_margin_n=group.get('nonpositive_margin_n',0) if usable else None)
    return result
