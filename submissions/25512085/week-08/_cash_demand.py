"""Internal demand forecasts and bounded watchlist procurement; no I/O."""
import _cash_pricing


def watched(policy, sku, info):
    return info.get('kind') == 'goods' and sku in policy.config().get('procurement_watchlist', [])


def inventory_guard(policy, hub, sku, inv, now):
    """Eight-hour own settled velocity, never public demand or inquiry volume."""
    counts = policy.collections.Counter(
        u.get('live_grade') or u.get('grade') or 'unknown'
        for u in inv.get('units', []) if u.get('sku') == sku and u.get('grade') != 'expired')
    sales = policy.collections.Counter(
        r.get('grade') or 'unknown' for r in hub.get('seller_cash', {}).get('sales', {}).values()
        if r.get('own') is True and r.get('sku') == sku and policy.number(r.get('ts'))
        and 0 <= now-r['ts'] <= 8*3600)
    observed = hub.get('seller_cash', {}).get('coverage', {})
    complete = (policy.number(observed.get('since')) and observed['since'] <= now-8*3600
                and policy.number(observed.get('through')) and 0 <= now-observed['through'] <= 120)
    total, sold = sum(counts.values()), sum(sales.values())
    coverage = total*8/sold if sold else None
    grade_coverage = {g: counts[g]*8/sales[g] if sales[g] else None for g in ('A','B','C')}
    reasons = []
    if total and not sold:
        reasons.append('held_without_own_sales')
    if coverage is not None and coverage >= 4:
        reasons.append('total_coverage_4h')
    if total and not complete:
        reasons.append('held_with_incomplete_8h_observation')
    for g in ('B','C'):
        if counts[g] and ((grade_coverage[g] is not None and grade_coverage[g] >= 4)
                          or (not sales[g] and counts[g] >= 2)):
            reasons.append(g+'_accumulation')
    return {'max_additional_qty':0 if reasons else 1, 'reasons':reasons,
            'grade_counts':dict(counts), 'own_sales_8h':dict(sales), 'coverage_hours':coverage,
            'grade_coverage_hours':grade_coverage, 'complete_8h_observation':bool(complete),
            'horizon_hours':8, 'window_max_qty':1}


def forecast(policy, hub, sku, info, catalog, now, inquiries=0):
    p = policy.config()
    state = hub.get('seller_cash', {})
    rows = [r for r in state.get('sales', {}).values() if 0 <= now-r['ts'] <= 3600]
    market = [r for r in rows if r['sku'] == sku]
    own = [r for r in market if r.get('own')]
    category = [r for r in rows if catalog.get(r['sku'],{}).get('category') == info.get('category')]
    market_count, category_count = len(market), len(category)
    for sold_sku, events in state.get('market_sales', {}).items():
        same_sku = sold_sku == sku
        same_category = catalog.get(sold_sku, {}).get('category') == info.get('category')
        if same_sku or same_category:
            count = sum(0 <= now-ts <= 3600 for ts in events.values())
            if same_sku:
                market_count += count
            if same_category:
                category_count += count
    share = len(own)/market_count if market_count else 0
    cat_share = sum(bool(r.get('own')) for r in category)/category_count if category_count else 0
    target_share = min(1, max(share,cat_share,p.get('cold_start_share',.02))*p.get('share_growth_multiplier',1.5))
    own20 = sum(now-r['ts'] <= 1200 for r in own)
    # Correct only measured short gaps; a missing heartbeat never proves inventory availability.
    observations = hub.get('seller_cash',{}).get('stock_observations',[])
    available = sum(b['ts']-a['ts'] for a,b in zip(observations,observations[1:])
                    if 0 < b['ts']-a['ts'] <= 90 and sku in a['skus'])
    corrected = len(own)*1200/available if available >= 300 else len(own)/3
    prediction = max(own20,len(own)/3,corrected,market_count*target_share/3,inquiries/3)
    if market_count:
        prediction = min(prediction,max(market_count/3,own20,inquiries/3))
    minimum = p.get('minimum_proven_depth',4) if own or inquiries else p.get('minimum_new_depth',3)
    depth = min(p.get('maximum_depth',40),max(minimum,policy.math.ceil(prediction*p.get('forecast_minutes',30)/20*p.get('target_multiplier',1.75))))
    return {'own20':own20,'own60':len(own),'market60':market_count,'prediction20':prediction,'depth':depth,'target_share':target_share}


def procurement(policy, hub, sku, info, inv, qty, catalog, now):
    """Expected contribution for ranking; global legal cost ceiling for cash reservations."""
    guard = inventory_guard(policy,hub,sku,inv,now) if watched(policy,sku,info) else None
    if guard is not None:
        qty = min(qty,guard['max_additional_qty'])
    surge = next((r for r in inv.get('surge',[]) if r.get('sku') == sku),{})
    units = surge.get('units')
    current = surge.get('factor',1)
    if not policy.number(current) or not 1 <= current <= 1.5:
        return None
    factor = 1+min(.5,max(0,units+qty-50)*.002) if policy.number(units) else min(1.5,current+qty*.002)
    lp = info.get('list_price',0)
    if not policy.number(lp) or lp <= 0:
        return None
    weights = {'A':.7,'B':.2,'C':.1} if info.get('kind') == 'goods' else {'A':.25,'B':.5,'C':.25}
    expected = 0
    contributions, rates = {}, {}
    evidence, prices = {}, {}
    for grade,weight in weights.items():
        price = policy.reference_price(hub,sku,grade,now,info.get('kind','goods'))
        evidence[grade] = price is not None
        if price is None:
            # Prior is explicitly an estimate, never a current quote or a claimed executable price.
            price = lp*{'A':.80,'B':.72,'C':.65}[grade]
        q = policy.refund_probability(hub,sku,grade,catalog,now) if policy.warranty_hours(grade,info.get('kind','goods')) else 0
        if grade=='B' and q:
            q=max(policy.PRIOR_BREAK['B'],q)
        if guard is not None and grade == 'B' and policy.warranty_hours(grade,info.get('kind','goods')):
            q=max(.15,policy.PRIOR_BREAK['B'],q)
        cost = lp*{'A':.65,'B':.5,'C':.35}[grade]*factor
        contributions[grade] = price*(1-q)-cost
        rates[grade] = q
        prices[grade] = price
        expected += weight*contributions[grade]
    result = {'expected_unit_contribution':expected,'projected_surge':factor,
            'grade_contributions':contributions, 'break_rates':rates,
            'negative_grades':[g for g,v in contributions.items() if v < 0],
            'break_penalty':rates['A'] >= .08 and contributions['B'] < 0,
            # Other shops can buy between planning and supply. Reserve the published global maximum.
             'reservation_unit_cost':policy.math.ceil(lp*.75*1.5)}
    if guard is not None:
        scenarios = {}
        for name, fractions in (('average',{'A':.65,'B':.50,'C':.35}),
                                ('stress',{'A':.75,'B':.60,'C':.45})):
            grades = {}
            for g in weights:
                cost = lp*fractions[g]*factor
                storage = cost*.01*8/24
                minimum = _cash_pricing.minimum_contribution(policy,cost,g)
                raw = prices[g]*(1-rates[g])-cost-storage-minimum
                supported = evidence[g] and guard['own_sales_8h'].get(g,0) > 0
                contribution = raw if supported else min(0,raw)
                grades[g] = {'price':prices[g], 'price_basis':'reference' if evidence[g] else 'estimated_list_prior',
                             'refund_probability':rates[g], 'cost_list_fraction':fractions[g],
                             'own_sales_8h':guard['own_sales_8h'].get(g,0), 'cost':cost,
                             'future_storage':storage, 'minimum_contribution':minimum,
                             'raw_contribution':raw, 'contribution':contribution,
                             'unsupported_positive_capped':not supported and raw > 0}
            scenarios[name] = {'grades':grades, 'weighted_contribution':sum(weights[g]*grades[g]['contribution'] for g in weights)}
        for name, scenario in scenarios.items():
            if scenario['weighted_contribution'] <= 0:
                guard['reasons'].append(name+'_nonpositive_contribution')
                guard['max_additional_qty'] = 0
        guard['scenarios'] = scenarios
        guard['assumed_grade_weights'] = weights
        guard['list_price'] = lp
        guard['projected_surge'] = factor
        guard['decision'] = 'hold_additional' if guard['max_additional_qty'] == 0 else 'probe_one_per_window'
        result['procurement_guard'] = guard
        result['expected_unit_contribution'] = scenarios['average']['weighted_contribution']
        result['grade_contributions'] = {g:r['contribution'] for g,r in scenarios['average']['grades'].items()}
        result['negative_grades'] = [g for g,v in result['grade_contributions'].items() if v < 0]
        result['break_penalty'] = rates['A'] >= .08 and result['grade_contributions']['B'] < 0
    return result


def spending_budget(policy, hub, inv, existing_floor, now):
    p = policy.config()
    pending = sum(r['amount']*policy.PRIOR_BREAK.get(r.get('grade'),.35)
                  for r in hub.get('seller_cash', {}).get('sales', {}).values()
                  if r.get('own') and 0 <= now-r['ts'] <= 6*3600 and (r.get('warranty_hours') or 0) >= 6
                  and r.get('order') not in hub.get('seller_cash',{}).get('breaks',{}))
    reserve = max(existing_floor,p.get('minimum_cash_reserve',5000000),policy.math.ceil(pending*2))
    return int(max(0,inv['balance']-reserve)*p.get('deployable_fraction',.85)),reserve


def allocate(policy, groups, models, held, budget, cap, grade_spent, previous):
    """Restore proven stockouts first, then fund the highest total-cash forecasts."""
    funded = policy.collections.Counter()
    spent = 0
    priority = sorted(groups,key=lambda row:(models.get(row[1][0],{}).get('break_penalty',False),
                                            -models.get(row[1][0],{}).get('score20',0),row[1][0]))
    restoration = [(g,(s,min(3,n),c,m)) for g,(s,n,c,m) in priority
                   if not held[s] and models.get(s,{}).get('own60',0)>0
                   and not models.get(s,{}).get('break_penalty',False)]
    for grade,(sku,need,cost,margin) in restoration+priority:
        if sku in previous or cost <= 0:
            continue
        missing = need-funded[sku]
        model_guard = models.get(sku,{}).get('procurement_guard',{})
        if model_guard:
            missing = min(missing,model_guard.get('max_additional_qty',1)-funded[sku])
        allowance = budget-spent
        if grade == 'observe':
            allowance = min(allowance,max(0,cap//2-grade_spent[grade]))
        qty = min(missing,max(0,allowance)//cost)
        while qty > 0 and sum(policy.math.ceil((n+(qty if s==sku else 0))/20) for s,n in funded.items()) + (policy.math.ceil(qty/20) if sku not in funded else 0) > 10:
            qty -= 1
        if qty <= 0:
            continue
        funded[sku] += qty
        spent += qty*cost
        grade_spent[grade] += qty*cost
    orders = []
    for sku,qty in funded.items():
        previous[sku] = qty
        while qty:
            batch = min(20,qty)
            orders.append({'sku':sku,'qty':batch})
            qty -= batch
    return orders,spent
