"""Background service regions and explicit-contract completion evidence."""
from collections import Counter,defaultdict
import math
import time
from .state import identity
from .service_contracts import eligible

KEY='service-policy:v26'


def context(journal,now=None):
    from .decision_view import fact
    now=time.time() if now is None else now
    row=fact(journal,'service-policy-v26',lambda:journal.get(KEY,{}))
    if (not -2<=now-row.get('at',-1000)<180 or now>=row.get('expires_at',0)
            or row.get('rules_hash')!=journal.get('market_rules',{}).get('sha256')):return {}
    return row


def update(journal,config,now=None):
    if not config.get('service_contract_v26_enabled'):return None
    now=time.time() if now is None else now;old=journal.get(KEY,{})
    if 0<=now-old.get('at',0)<60:return old
    catalog=journal.snapshot('catalog.json',{});inv=journal.snapshot('inventory.json',{})
    sales=[r for r in journal.events('shop_settled',10000) if 0<=now-r['_ts']<21600]
    paid={r.get('task'):r for r in sales if r.get('price',0)>r.get('cost',math.inf)}
    paid_counts=Counter(r.get('sku') for r in paid.values());needs={}
    for r in journal.events('shop_service_need',10000):
        if 0<=now-r['_ts']<21600 and r.get('valid') and r.get('explicit_use'):
            needs.setdefault(r['task'],r)
    for r in journal.events('stockout',4000):
        if 0<=now-r['_ts']<21600 and r.get('reason')=='shelf_life' and r.get('required_use_hours',0)>0:
            needs.setdefault(r.get('task'),dict(r,minimum_hours=r['required_use_hours'],grades=None,warranty_hours=0))
    groups=defaultdict(lambda:defaultdict(dict))
    canonical={s.casefold():s for s in catalog}
    for task,r in needs.items():
        r=dict(r,sku=canonical.get(str(r.get('sku')).casefold(),r.get('sku')))
        p=catalog.get(r.get('sku'),{});h=r.get('minimum_hours',0)
        if p.get('kind')!='food' or not 0<h<=p.get('shelf_hours',0):continue
        key=(round(h,6),tuple(r.get('grades') or ()),r.get('warranty_hours',0))
        # Real paid tasks retain weight; anonymous requests are minute-deduped.
        dedup=('paid',task) if task in paid else ('request',int(r['_ts']//60))
        groups[r['sku']][key].setdefault(dedup,r)
    offered=defaultdict(set)
    for r in journal.events('shop_opportunity',10000):
        if 0<=now-r['_ts']<21600 and r.get('sku') and r.get('task'):offered[r['sku']].add(r['task'])
    profiles={}
    for sku,cells in groups.items():
        minutes={int(r['_ts']//60) for rows in cells.values() for r in rows.values()}
        if paid_counts[sku]<2 or len(minutes)<3:continue
        rows=[]
        for (h,grades,warranty),seen in cells.items():
            weight=sum(1 if key[0]=='paid' else .25 for key in seen)
            rows.append({'minimum_hours':h,'grades':list(grades) if grades else None,'warranty_hours':warranty,
                'weight':weight,'paid':sum(key[0]=='paid' for key in seen),'observed':len(seen)})
        explicit_weight=sum(r['weight'] for r in rows)
        # An explicit-use cohort is not the whole SKU's demand. Unknown requests
        # remain in the denominator; unmatched unpaid bursts get only .25 weight.
        unpaid={int(r['_ts']//60) for seen in cells.values() for key,r in seen.items() if key[0]=='request'}
        denominator=max(explicit_weight,len(offered[sku]|{t for t,r in paid.items() if r.get('sku')==sku})+.25*len(unpaid))
        profiles[sku]={'paid_sku_support':paid_counts[sku],'distinct_minutes':len(minutes),'buckets':rows,
            'need_fraction_scenario':explicit_weight/max(1,denominator),'opportunity_denominator':denominator}
    # Only arrivals seen promptly enter the supply eligibility cohort. Current
    # old stock is a survivor sample and must not masquerade as random supply.
    draws=[r for r in journal.events('shop_service_draw',10000) if 0<=now-r['_ts']<21600]
    seen={r['unit'] for r in draws};new=[]
    for u in inv.get('units',[]):
        p=catalog.get(u.get('sku'),{});shelf=p.get('shelf_hours')
        if (u.get('unit') in seen or u.get('kind')!='food' or not shelf or not u.get('expires_at')
                or not 0<=now-u.get('supplied_at',0)<=120):continue
        fraction=(u['expires_at']-u['supplied_at'])/3600/shelf
        if not 0<fraction<=1.01:continue
        row={'unit':u['unit'],'fraction':fraction,'source':'fresh_own_supply_observation'}
        new.append(('shop_service_draw',row,'service-draw:'+u['unit'],'shop'));draws.append(row)
    if new:journal.add_batch(new,ts=now)
    from .contracts import shop_identity
    contracts=defaultdict(dict)
    for q in reversed(journal.events('quote_comparison',6000)):
        if not 0<=now-q['_ts']<7200:continue
        a=q.get('assessment',{});state=a.get('saved_state',{})
        if state.get('score_formula')!=journal.get('market_rules',{}).get('buyer_saved_formula'):continue
        p=catalog.get(q.get('sku'),{});kind=p.get('kind')
        if not kind:continue
        key=shop_identity(journal,q['shop'])+'|'+kind
        complete=not a.get('conditional_terms') and a.get('required_grade') in ('A','B','C') if kind=='goods' else not a.get('conditional_terms')
        from .buyer_scoring import contribution,score_inputs
        try:value=contribution(q['ask'],p['list_price'],a['failure'],a['covered_failure'],**score_inputs(state))
        except (KeyError,ValueError):continue
        contracts[key][(q['order'],q['sku'])]={'complete':complete,'positive':complete and q['ask']<=a['cap'] and value>=state.get('saved',0)}
    summaries={key:{'n':len(rows),'complete':sum(v['complete'] for v in rows.values()),
        'positive':sum(v['positive'] for v in rows.values())} for key,rows in contracts.items()}
    result={'at':now,'expires_at':now+180,'rules_hash':journal.get('market_rules',{}).get('sha256'),
        'shop_needs':profiles,'buyer_contracts':summaries,'draw_fractions':[r['fraction'] for r in draws],
        'source':'explicit own requests; minute-deduped unpaid; paid support; observed quote terms',
        'selected_cohorts_not_causal':True}
    result['generation']=identity([result['rules_hash'],profiles,summaries,result['draw_fractions']])
    journal.put(KEY,result);journal.add('service_policy_prepared',{'at':now,'generation':result['generation'],
        'qualified_need_skus':len(profiles),'contract_groups':len(summaries),'supply_draws':len(draws)})
    return result


def need_plan(journal,config,sku,product,units,rate,desired,coverage,paid,now):
    from .shop_growth import target_stock
    from .risk import wilson
    packet=context(journal,now);profile=packet.get('shop_needs',{}).get(sku,{})
    rows=profile.get('buckets',[])
    if not rows or product.get('kind')!='food':return None
    hold=min(config.get('planning_horizon_hours',6),1/max(.01,rate))
    relevant=[u for u in units if u['sku']==sku];weight=sum(r['weight'] for r in rows);choices=[]
    for bucket in rows:
        fraction=(bucket['minimum_hours']+hold+config.get('checkout_buffer_seconds',30)/3600)/product['shelf_hours']
        if fraction>1:continue
        # Published lower bounds give no invented within-grade distribution.
        lower=0 if bucket.get('grades') else .75 if fraction<=.45 else .25 if fraction<=.8 else 0
        samples=packet.get('draw_fractions',[])
        if len(samples)>=20 and not bucket.get('grades'):
            lower=max(lower,wilson(sum(v>=fraction for v in samples),len(samples),False))
        if lower<=0:continue
        demand_share=sum(r['weight'] for r in rows if r['minimum_hours']>=bucket['minimum_hours'] and r.get('grades')==bucket.get('grades'))/weight
        rrate=rate*demand_share*profile.get('need_fraction_scenario',0)
        target=target_stock(rrate,paid,config,coverage_hours=coverage)
        spec=dict(bucket,sku=sku,valid=True)
        held=sum(eligible(u,{sku:product},spec,now+hold*3600,config.get('checkout_buffer_seconds',30)) for u in relevant)
        deficit=target-held
        if deficit<=0:continue
        needed=math.ceil(deficit/lower)
        choices.append({'needed':needed,'minimum_hours':bucket['minimum_hours'],'eligible_held':held,
            'need_rate':rrate,'eligible_draw_probability':lower,'target':target,'holding_scenario':hold,
            'profile_generation':packet['generation'],'kind':'food','planning_scenario_not_guarantee':True,
            'conditional_cost_ratio':.65 if fraction>.45 else .60})
    return max(choices,key=lambda r:r['needed']) if choices else None


def strict_sale_probability(row,quantity,horizon):
    from .shop_growth import poisson_tail
    p=row['eligible_draw_probability'];n=quantity-1;lam=row['need_rate']*horizon
    return p*sum(math.comb(n,k)*p**k*(1-p)**(n-k)*poisson_tail(lam,row['eligible_held']+k+1) for k in range(n+1))


def route_adjust(candidate,journal,now=None):
    from .contracts import shop_identity
    row=context(journal,now).get('buyer_contracts',{}).get(shop_identity(journal,candidate['shop'])+'|'+candidate['product'].get('kind',''),{})
    n=row.get('n',0)
    if n<8:return candidate
    chance=(row['positive']+2)/(n+4);factor=min(1,chance/.5)
    candidate['score']*=factor
    candidate['expected_delta']={k:v*factor for k,v in candidate['expected_delta'].items()}
    candidate.setdefault('evidence',{})['complete_contract_scenario']={'samples':n,'positive_quotes':row['positive'],
        'complete_quotes':row['complete'],'factor':factor,'not_a_seller_price_rejection':True}
    return candidate
