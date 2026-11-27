"""Internal warranty and quote calculations, using live facade dependencies."""


def warranty_hours(policy, grade, kind='goods'):
    if kind != 'goods':
        return 0
    return max(0,min(72,int(policy.config().get('grade_warranty_hours',{}).get(grade,{'A':72,'B':6,'C':0}.get(grade,0)))))


def minimum_contribution(policy, cost, grade):
    """Same target for selling floors and watchlist procurement scenarios."""
    p = policy.config()
    contribution = min(p.get('absolute_goods_contribution',5000),
                       max(p.get('minimum_goods_contribution',500),policy.math.ceil(cost*.05)))
    if grade == 'B':
        contribution = max(contribution,policy.math.ceil(cost*p.get('b_minimum_expected_margin_fraction',.05)))
    return contribution


def profit_floor(policy, unit, hub, catalog, now, warranty=None):
    """Expected-profit floor, never a guarantee against a realized full refund."""
    p=policy.config();cost=float(unit['cost']);grade=unit.get('live_grade') or unit.get('grade')
    hours=policy.warranty_hours(grade,unit.get('kind','goods')) if warranty is None else warranty
    q=policy.refund_probability(hub,unit['sku'],grade,catalog,now) if hours else 0
    if grade == 'B' and hours:
        q=max(policy.PRIOR_BREAK['B'],q)
    # A flat5000 floor made low-list-price goods unnecessarily dearer than their catalog value.
    contribution=minimum_contribution(policy,cost,grade)
    age=max(0,now-float(unit.get('supplied_at') or now))
    storage=cost*.01*age/86400
    return policy.math.ceil((cost+storage+contribution)/max(1-q,1e-9))


def reference_price(policy, hub, sku, grade, now, kind='goods', warranty=None):
    """Never mix unknown/other grades into the first-quote comparable price."""
    observations = hub.get('demand_radar', {}).get('sku_comparables', {}).get(sku, [])
    hours=policy.warranty_hours(grade,kind) if warranty is None else warranty
    for lookback in ([3600,10800] if kind=='goods' and hours==0 else [3600]):
        valid = [r for r in observations if r.get('grade') == grade and policy.number(r.get('ts'))
                 and 0 <= now-r['ts'] <= lookback and policy.number(r.get('amount')) and r['amount'] > 0
                 and (kind != 'goods' or (policy.number(r.get('warranty_hours')) and r['warranty_hours']==0 if hours==0 else
                      policy.number(r.get('warranty_hours')) and r['warranty_hours']>=6))]
        if len(valid)>=5:
            return int(round(policy.statistics.median(r['amount'] for r in valid)))
    return None


def quote(policy, unit, hub, catalog, now):
    """Return a cost-safe first quote only with comparable evidence. Keep clearance separate."""
    if not policy.config().get('enabled'):
        return None
    sku = unit.get('sku')
    grade = str(unit.get('live_grade') or unit.get('grade') or '')
    kind = unit.get('kind', 'goods')
    if kind == 'goods' and now-float(unit.get('supplied_at') or now) >= 8*3600:
        return None
    if kind == 'food' and float(unit.get('hours_left') or 0) < 72:
        return None
    price = policy.reference_price(hub, sku, grade, now, kind)
    if price is None or not policy.number(unit.get('cost')):
        return None
    p = policy.config()
    hours=policy.warranty_hours(grade,kind)
    q = policy.refund_probability(hub, sku, grade, catalog, now) if hours else 0
    if grade=='B' and hours:
        q=max(policy.PRIOR_BREAK['B'],q)
    contribution = p.get('absolute_goods_contribution',5000) if kind == 'goods' else p.get('absolute_food_contribution',1000)
    floor = policy.profit_floor(unit,hub,catalog,now) if kind=='goods' else policy.math.ceil(unit['cost']+contribution)
    ask = max(floor, price)
    last = max(floor, policy.math.ceil(ask*(1-p.get('maximum_concession_fraction',.01))))
    return {'open_ask':ask, 'r1_target':last, 'r2_target':last, 'hard_floor':floor,
            'break_rate':q, 'cash_first_quote':True, 'warranty_hours':hours,
            'quote_basis':'same-SKU/grade/warranty-class-settles'}
