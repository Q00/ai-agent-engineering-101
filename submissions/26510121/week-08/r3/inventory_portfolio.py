"""Whole-lot inventory competition with a preserved baseline and downside checks.

These are conditional six-hour planning scenarios, not observed future demand.
An extra unit must pay its own acquisition removal, refund, storage and expiry
costs. Legal cash and line bounds remain independent of forecast optimism.
"""
import copy
import math
import time
from .state import identity

KEY='inventory-portfolio:v27'


def brief(packet):
    """One baseline and one selected forecast; full search stays in the journal."""
    if not packet:return {'status':'no_fresh_portfolio'}
    alternatives=packet.get('alternatives',{})
    keys=('book','revenue','stress_book','rank_progress','units','lines','capital')
    result={k:packet.get(k) for k in ('at','id','selection','dominance_guard_passed','goods_units','candidate_counts')}
    result['baseline']={k:alternatives.get('baseline',{}).get(k) for k in keys}
    result['selected']={k:alternatives.get(packet.get('selection'),{}).get(k) for k in keys}
    result['forecast_not_realized']=True
    return result


def context(journal,now=None):
    now=time.time() if now is None else now;r=journal.get(KEY,{})
    return r if (0<=now-r.get('at',0)<180 and now<r.get('expires_at',0)
        and r.get('rules_hash')==journal.get('market_rules',{}).get('sha256')) else {}


def supported(growth,public,now):
    own=growth.get('positive_paid_while_available',0)
    buyers={name for name,n in public.get('external_buyers',[]) if name not in ('unknown','seochanit') and n>0}
    return own>=3 or (public.get('external_samples',0)>=20 and len(buyers)>=3 and
        0<=now-public.get('latest_at',0)<3600)


def scale(candidate,quantity):
    fraction=quantity/candidate['qty']
    return dict(candidate,qty=quantity,max_cost=candidate['max_cost']*fraction,
        capital_commitment=candidate['capital_commitment']*fraction,score=candidate['score']*fraction,
        expected_delta={k:v*fraction for k,v in candidate['expected_delta'].items()},
        evidence=copy.deepcopy(candidate['evidence']))


def downside(candidate):
    row=copy.deepcopy(candidate);e=row['evidence'];g=e.get('growth',{})
    n=max(0,g.get('positive_paid_while_available',0))
    # Explicit sensitivity, not a confidence interval or calibrated prediction.
    factor=1/(1+1/math.sqrt(n)) if n>=3 else .5
    e['arrival_rate_scenario']*=factor
    if e.get('exploration'):e['sale_probability_scenario']*=factor
    if e.get('service_eligibility'):
        e['service_eligibility']['need_rate']*=factor
    upper=(.60 if e['kind']=='food' else .69)*e['list_price']*e.get('supply_surge_factor',1)
    e['scenario_unit_cost']=max(e['scenario_unit_cost'],upper)
    row['evidence']['stress_rate_factor']=factor
    return row


def unit_scenarios(candidate,held):
    from .shop_growth import marginal
    adverse=downside(candidate)
    return [(marginal(candidate,q,held),marginal(adverse,q,held)) for q in range(1,candidate['qty']+1)]


def materialize(candidate,quantity,pairs,kind):
    row=scale(candidate,quantity);selected=pairs[:quantity]
    row.update(score=sum(a['book'] for a,b in selected),allocation_book=kind,
        expected_delta={k:sum(a[k] for a,b in selected) for k in ('book','profit','revenue')})
    row['evidence'].update(marginal_units=[a for a,b in selected],
        inventory_stress_units=[b for a,b in selected],
        downside_rate_factor=downside(candidate)['evidence']['stress_rate_factor'])
    return row


def forecast(rows,board):
    from .meta import rank_progress
    delta={k:sum(r['expected_delta'].get(k,0) for r in rows) for k in ('book','revenue')}
    stress=sum(m['book'] for r in rows for m in r['evidence']['inventory_stress_units'])
    return dict(delta,stress_book=stress,rank_progress=rank_progress(board,'shop','seochanit',delta),
        units=sum(r['qty'] for r in rows),lines=len(rows),
        capital=sum(r['capital_commitment'] for r in rows))


def dominates(after,before):
    eps=1e-6
    return (after['book']>=before['book']-eps and after['stress_book']>=before['stress_book']-eps
        and (before['rank_progress'] is None or after['rank_progress'] is not None and
            after['rank_progress']>=before['rank_progress']-1e-12))


def allocate(candidates,inv,capacity,config,existing,merchant,now=None,*,audit=None):
    from .portfolio import allocate as legacy_allocate
    from .meta import rank_progress
    now=time.time() if now is None else now
    if config.get('shop_inventory_caps_enabled',True) or config.get('shop_internal_capital_caps_enabled',True):
        return legacy_allocate(candidates,inv,capacity,dict(config,inventory_portfolio_v27_enabled=False),existing,merchant)
    held={}
    for u in existing:held[u['sku']]=held.get(u['sku'],0)+1
    originals={c['sku']:c for c in candidates};pairs={s:unit_scenarios(c,held.get(s,0)) for s,c in originals.items()}
    baseline_candidates=[scale(c,min(c['qty'],c['evidence'].get('v27_baseline_qty',c['qty'])))
        for c in candidates if c['evidence'].get('v27_baseline_qty',c['qty'])>0]
    board=merchant.get('_v27_board',{})
    for c in baseline_candidates:c['rank_progress_surrogate']=rank_progress(board,'shop','seochanit',c['expected_delta'])
    baseline=legacy_allocate(baseline_candidates,inv,capacity,dict(config,inventory_portfolio_v27_enabled=False),existing,merchant)
    baseline=[materialize(originals[r['sku']],r['qty'],pairs[r['sku']],r.get('allocation_book','v26_preserved_baseline')) for r in baseline]
    total=capacity['spendable'];line_limit=min(10,config['restock_max_lines'])

    book_scale=rank_progress(board,'shop','seochanit',{'book':100000})
    revenue_scale=rank_progress(board,'shop','seochanit',{'revenue':100000})
    book_scale=book_scale/100000 if book_scale is not None else 1
    revenue_scale=revenue_scale/100000 if revenue_scale is not None else 0

    def solve(seed,weight=0,strict=False):
        chosen={r['sku']:r['qty'] for r in seed};used=sum(r['capital_commitment'] for r in seed)
        processed=set()
        while True:
            offers=[]
            for sku,c in originals.items():
                if sku in processed:continue
                start=chosen.get(sku,0)
                if not c['evidence'].get('v27_depth_supported') and not start:continue
                if not start and len(chosen)>=line_limit:continue
                cost=c['capital_commitment']/c['qty'];affordable=min(c['qty'],start+int(max(0,total-used+1e-6)/cost))
                mean_book=mean_revenue=stress_book=stress_revenue=0;best=None
                for q in range(start+1,affordable+1):
                    normal,stress=pairs[sku][q-1]
                    if normal['book']<=0 or strict and stress['book']<=0:break
                    mean_book+=normal['book'];mean_revenue+=normal['revenue']
                    stress_book+=stress['book'];stress_revenue+=stress['revenue']
                    value=book_scale*(mean_book+weight*stress_book)+revenue_scale*mean_revenue
                    key=(value,mean_book,stress_book)
                    if value>0 and (best is None or key>best[0]):best=(key,sku,q,(q-start)*cost)
                if best:offers.append(best)
            if not offers:break
            value,sku,end,cost=max(offers);chosen[sku]=end;used+=cost;processed.add(sku)
        return [materialize(originals[s],q,pairs[s],'sku_lot_competition') for s,q in chosen.items()]

    proposals={'baseline':baseline,'baseline_plus_robust_depth':solve(baseline,strict=True)}
    for weight in (0,.25,1,4):proposals['lot_risk_'+str(weight)]=solve([],weight)
    forecasts={name:forecast(rows,board) for name,rows in proposals.items()}
    # Search the portfolio downside frontier rather than vetoing every SKU on
    # its own adverse draw. The prior portfolio defines the risk benchmark.
    low,high=4.,8.;bracket=None
    for _ in range(6):
        key='lot_risk_'+str(high);proposals[key]=solve([],high);forecasts[key]=forecast(proposals[key],board)
        if forecasts[key]['stress_book']>=forecasts['baseline']['stress_book']:
            bracket=(low,high);break
        low,high=high,high*2
    if bracket:
        low,high=bracket
        for _ in range(3):
            weight=(low+high)/2;key='lot_risk_'+str(weight);proposals[key]=solve([],weight);forecasts[key]=forecast(proposals[key],board)
            if forecasts[key]['stress_book']>=forecasts['baseline']['stress_book']:high=weight
            else:low=weight
    admissible=[name for name,f in forecasts.items() if dominates(f,forecasts['baseline'])]
    name=max(admissible,key=lambda n:(forecasts[n]['rank_progress'] or 0,forecasts[n]['book'],forecasts[n]['stress_book']))
    rows=proposals[name]
    assert len(rows)<=line_limit and sum(r['capital_commitment'] for r in rows)<=total+1e-5
    assert all(1<=r['qty']<=20 for r in rows)
    result={'at':now,'expires_at':now+180,'rules_hash':config.get('_v27_rules_hash'),
        'candidate_counts':{'all':len(candidates),'supported_goods':sum(c['evidence'].get('v27_depth_supported',False) and c['evidence']['kind']=='goods' for c in candidates),
            'supported_food':sum(c['evidence'].get('v27_depth_supported',False) and c['evidence']['kind']=='food' for c in candidates)},
        'selection':name,'dominance_guard_passed':dominates(forecasts[name],forecasts['baseline']),
        'alternatives':forecasts,'goods_units':sum(r['qty'] for r in rows if r['evidence']['kind']=='goods'),
        'selected':[{'sku':r['sku'],'qty':r['qty'],'held':held.get(r['sku'],0),
            'book':r['expected_delta']['book'],'stress_book':sum(m['book'] for m in r['evidence']['inventory_stress_units'])} for r in rows],
        'interpretation':'Shared-fact forecast dominance and explicit cost/demand sensitivity; not causal uplift or a competitor guarantee.'}
    # The caller persists this numerical packet; no runtime state in this allocator.
    result['id']=identity([result['rules_hash'],result['selection'],result['alternatives'],result['selected']])
    for row in rows:
        row['evidence']['inventory_portfolio_v27']={k:result[k] for k in ('id','selection','dominance_guard_passed')}
        rate=row['evidence'].get('arrival_rate_scenario',0)
        row['evidence']['economic_stock_cover_scenario_hours']=(held.get(row['sku'],0)+row['qty'])/rate if rate>0 else None
    if audit is not None:audit.update(result)
    return rows


def observe(journal,config,now=None):
    if not config.get('inventory_portfolio_v27_enabled'):return None
    now=time.time() if now is None else now
    packet=context(journal,now)
    from .demand_policy import context as demand_context
    demand=demand_context(journal,config,now) or {}
    row={'at':now,'rules_hash':journal.get('market_rules',{}).get('sha256'),
        'demand_policy_id':demand.get('id'),'demand_at':demand.get('at'),
        'portfolio_at':packet.get('at'),'selection':packet.get('selection'),
        'alternatives':packet.get('alternatives',{}),'goods_units':packet.get('goods_units'),
        'scope':'Background demand/stock/lot competition; Main uses numerical prepared plans, not additional LLMs.'}
    journal.put('inventory-observer:v27',row)
    return row
