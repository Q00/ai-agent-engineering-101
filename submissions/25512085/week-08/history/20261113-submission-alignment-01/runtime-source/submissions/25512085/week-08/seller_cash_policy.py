"""Seller-only cash-growth policy. Pure calculations, bounded state, no network.

Public settled volume is a demand signal, never our promised sales. All rates and
refund probabilities below are estimates; no statistic is a guaranteed margin.
"""
from __future__ import annotations

import collections
import json
import math
import re
from pathlib import Path
import statistics
from adaptive_strategy import note_ledger_coverage

CONFIG_PATH = Path(__file__).with_name('seller_cash_policy.json')
PRIOR_BREAK = {'A': .03, 'B': .15, 'C': .35}
MAX_BREAK_SKUS = 5000
MAX_BREAK_PENDING = 20000
BREAK_MATURITY_S = 6*3600
BREAK_RETENTION_S = 8*3600


def compact_cash_sales(hub, now):
    """Own warranties need eight hours; other sales only feed one-hour counts.

    Keep seq identities (rather than rounded minute buckets) for exact replay
    deduplication and inclusive rolling-window boundaries.
    """
    state = hub.get('seller_cash', {})
    market = state.setdefault('market_sales', {})
    own = {}
    for seq, row in state.get('sales', {}).items():
        if row.get('own'):
            if now-8*3600 <= row['ts'] <= now:
                own[seq] = row
                market.get(row['sku'], {}).pop(seq, None)
        elif now-3600 <= row['ts'] <= now:
            market.setdefault(row['sku'], {})[seq] = row['ts']
    state['sales'] = own
    state['market_sales'] = {sku: recent for sku, events in market.items()
                             if (recent := {seq:ts for seq,ts in events.items()
                                            if now-3600 <= ts <= now})}


def compact_break_orders(hub, now, *, mature=False):
    """Market rows are [sku, grade, ts, flags]; flags: sold=1, broken=2.

    Only own sales keep named order records. Mature market sales contribute to
    counters once, then retain only the small late-break/replay identity.
    """
    state = hub.get('break_observer', {})
    market = state.setdefault('market_orders', {})
    own = {}
    for order, row in state.get('orders', {}).items():
        if row.get('own'):
            own[order] = row
        else:
            market[order] = [row['sku'], row['grade'], row['ts'],
                             int(row['sold']) | (int(row['broken']) << 1)]
    evidence = state.get('forum_evidence', {})
    for order, row in (list(own.items()) + list(market.items()) if mature else ()):
        is_own = isinstance(row, dict)
        sku, grade, ts, sold = ((row['sku'], row['grade'], row['ts'], row['sold'])
                                if is_own else (row[0], row[1], row[2], row[3] & 1))
        if not sold and now-ts >= BREAK_MATURITY_S:
            stats = hub.setdefault('break_stats' if is_own else 'market_break_stats', {})
            stat = stats.setdefault(sku, {}).setdefault(grade, {'sold':0, 'broken':0, 'last_ts':ts})
            stat['sold'] += 1
            stat['last_ts'] = max(stat['last_ts'], ts)
            # Proof is created only for a newly accepted ledger sale, and marked
            # here at the actual counter increment (not inferred from age).
            if order in evidence:
                evidence[order]['mature'] = True
            if is_own:
                row['sold'] = True
            else:
                row[3] |= 1
    def bounded(rows, timestamp):
        if len(rows) > MAX_BREAK_PENDING:
            return dict(sorted(rows.items(), key=lambda p:timestamp(p[1]))[-MAX_BREAK_PENDING:])
        return rows
    state['orders'] = bounded({k:r for k,r in own.items()
                               if not r['sold'] or now-r['ts'] <= BREAK_RETENTION_S}, lambda r:r['ts'])
    state['market_orders'] = bounded({k:r for k,r in market.items()
                                      if not r[3] & 1 or now-r[2] <= BREAK_RETENTION_S}, lambda r:r[2])
    state['unmatched'] = bounded({k:t for k,t in state.get('unmatched', {}).items()
                                  if now-t <= BREAK_RETENTION_S}, lambda t:t)
    if 'forum_evidence' in state:
        state['forum_evidence'] = bounded(evidence, lambda r:r['ts'])


# Pre-deployment own-shop observations, only sales aged at least six hours.
# G07ec17d5 C and G8ec51935 C were not measured; do not invent samples.
seed_break_stats = {
    'G1515d951': {'A': (110,9), 'B': (32,8), 'C': (21,5)},
    'G8ec51935': {'A': (40,4), 'B': (8,3)},
    'G07ec17d5': {'A': (96,2), 'B': (27,2)},
}


def observe_break_stats(hub, rows, catalog, now, own_urls, own_name):
    """Persistent disjoint own/other-shop counts, with a separate replay cursor.

    Keep order identities for eight hours (six-hour break window plus margin), bounded even
    if the ledger is hostile. The cursor prevents replay after order eviction.
    """
    own = hub.setdefault('break_stats', {})
    market = hub.setdefault('market_break_stats', {})
    state = hub.setdefault('break_observer', {})
    if not state.get('seeded'):
        for sku, grades in seed_break_stats.items():
            if sku not in own:
                own[sku] = {g: {'sold':s, 'broken':b, 'last_ts':now}
                            for g,(s,b) in grades.items()}
        state['seeded'] = True
    if 'market_orders' not in state:
        compact_break_orders(hub, now)
    orders = state.setdefault('orders', {})
    market_orders = state.setdefault('market_orders', {})
    unmatched = state.setdefault('unmatched', {})
    # Never backfill proof from old counters/orders or unvalidated forum refs.
    evidence = state.setdefault('forum_evidence', {})
    cursor = state.get('seq', -1)
    lookup = {s.lower():s for s in catalog}
    urls = {str(u).rstrip('/') for u in own_urls}

    def counter(row):
        stats = own if row['own'] else market
        return stats.setdefault(row['sku'], {}).setdefault(
            row['grade'], {'sold':0, 'broken':0, 'last_ts':row['ts']})

    for e in sorted((r for r in rows if number(r.get('seq'))), key=lambda r:r['seq']):
        seq, ts, d = e['seq'], e.get('ts'), e.get('data') or {}
        if seq <= cursor or not number(ts) or ts > now:
            continue
        cursor = seq
        if e.get('round') != 3 or not d.get('order'):
            continue
        order = str(d['order'])
        if e.get('kind') == 'settle':
            sku = lookup.get(str(d.get('sku') or d.get('item') or '').lower())
            if (order in orders or order in market_orders or sku is None or d.get('grade') not in PRIOR_BREAK
                    or d.get('buyer') == own_name or not number(d.get('amount')) or d['amount'] <= 0):
                continue
            row = {'sku':sku, 'grade':d['grade'], 'ts':ts, 'sold':False,
                   'broken':False, 'own':str(d.get('shop') or d.get('seller') or '').rstrip('/') in urls}
            evidence[order] = {'sku':sku, 'grade':d['grade'], 'ts':ts,
                               'sale_seq':seq, 'mature':False, 'break_seq':None,
                               'own':row['own']}
            if row['own']:
                orders[order] = row
            if order in unmatched:
                row['broken'] = True
                counter(row)['broken'] += 1
                unmatched.pop(order)
            if not row['own']:
                market_orders[order] = [sku, row['grade'], ts, int(row['broken']) << 1]
        elif e.get('kind') == 'broken':
            row = orders.get(order)
            market_row = market_orders.get(order)
            if market_row is not None:
                if not market_row[3] & 2:
                    market_row[3] |= 2
                    if order in evidence:
                        evidence[order]['break_seq'] = seq
                    stat = market.setdefault(market_row[0], {}).setdefault(
                        market_row[1], {'sold':0, 'broken':0, 'last_ts':market_row[2]})
                    stat['broken'] += 1
                    stat['last_ts'] = max(stat['last_ts'], ts)
            elif row is None:
                unmatched[order] = ts
            elif not row['broken']:
                row['broken'] = True
                if order in evidence:
                    evidence[order]['break_seq'] = seq
                stat = counter(row)
                stat['broken'] += 1
                stat['last_ts'] = max(stat['last_ts'], ts)
    state['seq'] = cursor
    compact_break_orders(hub, now, mature=True)
    for stats in (own, market):
        keep = sorted(stats, key=lambda s:max((v['last_ts'] for v in stats[s].values()), default=0))[-MAX_BREAK_SKUS:]
        for sku in set(stats)-set(keep):
            del stats[sku]
            # Counter eviction starts a new cohort if this SKU returns later.
            state['forum_evidence'] = {order:r for order,r in state['forum_evidence'].items()
                                       if not (r['sku'] == sku and r['own'] == (stats is own))}


def config():
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {'enabled': False}
    except (OSError, ValueError):
        return {'enabled': False}


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def observe(hub, rows, catalog, now, own_urls, own_name):
    """Idempotent eight-hour sale/break observations shared under the existing lock."""
    observe_break_stats(hub, rows, catalog, now, own_urls, own_name)
    state = hub.setdefault('seller_cash', {})
    # An older shared cursor does not prove that this new observer saw the same history.
    note_ledger_coverage({'adaptive_ledger_coverage':state.setdefault('coverage',{})},rows,now)
    sales = state.setdefault('sales', {})
    market_sales = state.setdefault('market_sales', {})
    breaks = state.setdefault('breaks', {})
    lookup = {s.lower(): s for s in catalog}
    excluded = set(config().get('excluded_buyers', [])) | {own_name}
    for e in rows:
        d, ts, seq = e.get('data') or {}, e.get('ts'), e.get('seq')
        if e.get('round') != 3 or not number(ts) or not now-8*3600 <= ts <= now or not number(seq):
            continue
        if e.get('kind') == 'broken' and d.get('order'):
            breaks[d['order']] = {'ts': ts, 'refunded': d.get('refunded') or 0}
        if e.get('kind') != 'settle' or d.get('buyer') in excluded or not number(d.get('amount')) or d['amount'] <= 0:
            continue
        sku = lookup.get(str(d.get('sku') or d.get('item') or '').lower())
        if sku is None:
            continue
        is_own = str(d.get('shop') or '').rstrip('/') in own_urls
        if not is_own:
            if ts >= now-3600:
                market_sales.setdefault(sku, {})[str(int(seq))] = ts
            continue
        sales[str(int(seq))] = {'ts': ts, 'sku': sku, 'amount': d['amount'], 'grade': d.get('grade'),
                                'warranty_hours': d.get('warranty_hours'), 'order': d.get('order'),
                                'own': True}
    state['sales'] = dict(sorted(((s,d) for s,d in sales.items() if now-8*3600 <= d['ts'] <= now),
                                key=lambda pair: pair[1]['ts'])[-40000:])
    state['breaks'] = {k:d for k,d in breaks.items() if now-8*3600 <= d['ts'] <= now}
    compact_cash_sales(hub, now)


def ready(hub, inv, now, inventory_fresh, phase):
    p, coverage = config(), hub.get('adaptive_ledger_coverage', {})
    observed = hub.get('seller_cash',{}).get('coverage',{})
    return bool(p.get('enabled') and phase == 'normal' and inventory_fresh and inv.get('round', 0) >= 3
                and number(inv.get('balance')) and inv['balance'] >= p.get('minimum_growth_balance', 6200000)
                and number(coverage.get('since')) and coverage['since'] <= now-1200
                and number(coverage.get('through')) and 0 <= now-coverage['through'] <= 120
                and number(observed.get('since')) and observed['since'] <= now-1200
                and number(observed.get('through')) and 0 <= now-observed['through'] <= 120)


def sale_rows(hub):
    state = hub.get('seller_cash', {})
    return (list(state.get('sales', {}).values()) +
            [{'sku':sku, 'ts':ts} for sku, events in state.get('market_sales', {}).items()
             for ts in events.values()])


def refund_probability(hub, sku, grade, catalog, now):
    if not warranty_hours(grade, catalog.get(sku, {}).get('kind','goods')):
        return 0
    prior = PRIOR_BREAK.get(grade, .35)
    own = hub.get('break_stats', {}).get(sku, {}).get(grade, {})
    market = hub.get('market_break_stats', {}).get(sku, {}).get(grade, {})
    q = (own.get('broken',0)+.5*market.get('broken',0)+20*prior)/(own.get('sold',0)+.5*market.get('sold',0)+20)
    return min(1, max(prior if grade == 'B' else 0, q))


def warranty_hours(grade, kind='goods'):
    if kind != 'goods':
        return 0
    return max(0,min(72,int(config().get('grade_warranty_hours',{}).get(grade,{'A':72,'B':6,'C':0}.get(grade,0)))))


def minimum_contribution(cost, grade):
    """Same target for selling floors and watchlist procurement scenarios."""
    p = config()
    contribution = min(p.get('absolute_goods_contribution',5000),
                       max(p.get('minimum_goods_contribution',500),math.ceil(cost*.05)))
    if grade == 'B':
        contribution = max(contribution,math.ceil(cost*p.get('b_minimum_expected_margin_fraction',.05)))
    return contribution


def watched(sku, info):
    return info.get('kind') == 'goods' and sku in config().get('procurement_watchlist', [])


def inventory_guard(hub, sku, inv, now):
    """Eight-hour own settled velocity, never public demand or inquiry volume."""
    counts = collections.Counter(
        u.get('live_grade') or u.get('grade') or 'unknown'
        for u in inv.get('units', []) if u.get('sku') == sku and u.get('grade') != 'expired')
    sales = collections.Counter(
        r.get('grade') or 'unknown' for r in hub.get('seller_cash', {}).get('sales', {}).values()
        if r.get('own') is True and r.get('sku') == sku and number(r.get('ts'))
        and 0 <= now-r['ts'] <= 8*3600)
    observed = hub.get('seller_cash', {}).get('coverage', {})
    complete = (number(observed.get('since')) and observed['since'] <= now-8*3600
                and number(observed.get('through')) and 0 <= now-observed['through'] <= 120)
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



def profit_floor(unit, hub, catalog, now, warranty=None):
    """Expected-profit floor, never a guarantee against a realized full refund."""
    p=config();cost=float(unit['cost']);grade=unit.get('live_grade') or unit.get('grade')
    hours=warranty_hours(grade,unit.get('kind','goods')) if warranty is None else warranty
    q=refund_probability(hub,unit['sku'],grade,catalog,now) if hours else 0
    if grade == 'B' and hours:
        q=max(PRIOR_BREAK['B'],q)
    # A flat5000 floor made low-list-price goods unnecessarily dearer than their catalog value.
    contribution=minimum_contribution(cost,grade)
    age=max(0,now-float(unit.get('supplied_at') or now))
    storage=cost*.01*age/86400
    return math.ceil((cost+storage+contribution)/max(1-q,1e-9))


def reference_price(hub, sku, grade, now, kind='goods', warranty=None):
    """Never mix unknown/other grades into the first-quote comparable price."""
    observations = hub.get('demand_radar', {}).get('sku_comparables', {}).get(sku, [])
    hours=warranty_hours(grade,kind) if warranty is None else warranty
    for lookback in ([3600,10800] if kind=='goods' and hours==0 else [3600]):
        valid = [r for r in observations if r.get('grade') == grade and number(r.get('ts'))
                 and 0 <= now-r['ts'] <= lookback and number(r.get('amount')) and r['amount'] > 0
                 and (kind != 'goods' or (number(r.get('warranty_hours')) and r['warranty_hours']==0 if hours==0 else
                      number(r.get('warranty_hours')) and r['warranty_hours']>=6))]
        if len(valid)>=5:
            return int(round(statistics.median(r['amount'] for r in valid)))
    return None


def quote(unit, hub, catalog, now):
    """Return a cost-safe first quote only with comparable evidence. Keep clearance separate."""
    if not config().get('enabled'):
        return None
    sku = unit.get('sku')
    grade = str(unit.get('live_grade') or unit.get('grade') or '')
    kind = unit.get('kind', 'goods')
    if kind == 'goods' and now-float(unit.get('supplied_at') or now) >= 8*3600:
        return None
    if kind == 'food' and float(unit.get('hours_left') or 0) < 72:
        return None
    price = reference_price(hub, sku, grade, now, kind)
    if price is None or not number(unit.get('cost')):
        return None
    p = config()
    hours=warranty_hours(grade,kind)
    q = refund_probability(hub, sku, grade, catalog, now) if hours else 0
    if grade=='B' and hours:
        q=max(PRIOR_BREAK['B'],q)
    contribution = p.get('absolute_goods_contribution',5000) if kind == 'goods' else p.get('absolute_food_contribution',1000)
    floor = profit_floor(unit,hub,catalog,now) if kind=='goods' else math.ceil(unit['cost']+contribution)
    ask = max(floor, price)
    last = max(floor, math.ceil(ask*(1-p.get('maximum_concession_fraction',.01))))
    return {'open_ask':ask, 'r1_target':last, 'r2_target':last, 'hard_floor':floor,
            'break_rate':q, 'cash_first_quote':True, 'warranty_hours':hours,
            'quote_basis':'same-SKU/grade/warranty-class-settles'}


def note_stock(hub, held, now):
    observations = hub.setdefault('seller_cash', {}).setdefault('stock_observations', [])
    if not observations or now-observations[-1]['ts'] >= 30:
        observations.append({'ts':now,'skus':sorted(s for s,n in held.items() if n)})
    hub['seller_cash']['stock_observations'] = [r for r in observations if 0 <= now-r['ts'] <= 3600][-121:]


def forecast(hub, sku, info, catalog, now, inquiries=0):
    p = config()
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
    depth = min(p.get('maximum_depth',40),max(minimum,math.ceil(prediction*p.get('forecast_minutes',30)/20*p.get('target_multiplier',1.75))))
    return {'own20':own20,'own60':len(own),'market60':market_count,'prediction20':prediction,'depth':depth,'target_share':target_share}


def procurement(hub, sku, info, inv, qty, catalog, now):
    """Expected contribution for ranking; global legal cost ceiling for cash reservations."""
    guard = inventory_guard(hub,sku,inv,now) if watched(sku,info) else None
    if guard is not None:
        qty = min(qty,guard['max_additional_qty'])
    surge = next((r for r in inv.get('surge',[]) if r.get('sku') == sku),{})
    units = surge.get('units')
    current = surge.get('factor',1)
    if not number(current) or not 1 <= current <= 1.5:
        return None
    factor = 1+min(.5,max(0,units+qty-50)*.002) if number(units) else min(1.5,current+qty*.002)
    lp = info.get('list_price',0)
    if not number(lp) or lp <= 0:
        return None
    weights = {'A':.7,'B':.2,'C':.1} if info.get('kind') == 'goods' else {'A':.25,'B':.5,'C':.25}
    expected = 0
    contributions, rates = {}, {}
    evidence, prices = {}, {}
    for grade,weight in weights.items():
        price = reference_price(hub,sku,grade,now,info.get('kind','goods'))
        evidence[grade] = price is not None
        if price is None:
            # Prior is explicitly an estimate, never a current quote or a claimed executable price.
            price = lp*{'A':.80,'B':.72,'C':.65}[grade]
        q = refund_probability(hub,sku,grade,catalog,now) if warranty_hours(grade,info.get('kind','goods')) else 0
        if grade=='B' and q:
            q=max(PRIOR_BREAK['B'],q)
        if guard is not None and grade == 'B' and warranty_hours(grade,info.get('kind','goods')):
            q=max(.15,PRIOR_BREAK['B'],q)
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
             'reservation_unit_cost':math.ceil(lp*.75*1.5)}
    if guard is not None:
        scenarios = {}
        for name, fractions in (('average',{'A':.65,'B':.50,'C':.35}),
                                ('stress',{'A':.75,'B':.60,'C':.45})):
            grades = {}
            for g in weights:
                cost = lp*fractions[g]*factor
                storage = cost*.01*8/24
                minimum = minimum_contribution(cost,g)
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


def spending_budget(hub, inv, existing_floor, now):
    p = config()
    pending = sum(r['amount']*PRIOR_BREAK.get(r.get('grade'),.35)
                  for r in hub.get('seller_cash', {}).get('sales', {}).values()
                  if r.get('own') and 0 <= now-r['ts'] <= 6*3600 and (r.get('warranty_hours') or 0) >= 6
                  and r.get('order') not in hub.get('seller_cash',{}).get('breaks',{}))
    reserve = max(existing_floor,p.get('minimum_cash_reserve',5000000),math.ceil(pending*2))
    return int(max(0,inv['balance']-reserve)*p.get('deployable_fraction',.85)),reserve


def allocate(groups, models, held, budget, cap, grade_spent, previous):
    """Restore proven stockouts first, then fund the highest total-cash forecasts."""
    funded = collections.Counter()
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
        while qty > 0 and sum(math.ceil((n+(qty if s==sku else 0))/20) for s,n in funded.items()) + (math.ceil(qty/20) if sku not in funded else 0) > 10:
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


def availability_plan(units, hub, catalog, now, forum=None):
    """Match real recent requests to certified stock; publish availability only."""
    state=hub.get('forum_state',{})
    if now-state.get('last_shop_post_ts',0)<300:
        return None
    held={u['sku']:u for u in units if u.get('sku') in catalog and u.get('cert')
          and u.get('grade')!='expired' and (u.get('kind')!='food' or (u.get('expires_at') or 0)>now+600)}
    if not held:
        return None
    lookup={s.lower():s for s in held}
    def text_for(skus):
        lines=['[mjforge 재고 안내]']
        for sku in skus[:3]:
            name=str(catalog[sku].get('name') or held[sku].get('name') or sku).replace('\n',' ').replace('\r',' ')[:100]
            lines.append(f'{sku} {name}: 재고 있음')
        return '\n'.join(lines)
    posts=forum.get('posts',[]) if isinstance(forum,dict) else forum if isinstance(forum,list) else []
    replied=set(state.get('availability_replied_threads',[]))
    request=re.compile(r'looking for|want to buy|stock request|(?:check\s+)?availability\s*(?:of|\?)|in stock\?|구매\s*(?:희망|원|요청)|재고\s*(?:있|문의)|찾(?:아요|습니다|고 있어)',re.I)
    report=re.compile(r'\bsettled\b|\bpaid\b|\bpurchased\b|\bbought\b|not a guarantee|정산|구매\s*(?:완료|후기)|거래\s*(?:완료|후기)|리포트',re.I)
    for post in sorted(posts,key=lambda x:x.get('ts',0),reverse=True):
        pid=post.get('id');body=str(post.get('text') or '')
        if (post.get('role')!='buyer' or str(post.get('owner') or '').lower()=='mjforge'
                or not number(post.get('ts')) or not 0<=now-post['ts']<=1200
                or pid is None or str(pid) in replied or report.search(body) or not request.search(body)):
            continue
        skus=sorted({lookup[k.lower()] for k in re.findall(r'\b[GgFf][0-9a-fA-F]{7,10}\b',body) if k.lower() in lookup})
        if skus:
            return {'action':{'post':text_for(skus),'refs':[],'as':'shop','thread':pid},
                    'skus':skus[:3],'demand_ts':post['ts'],'thread':str(pid)}
    if now-state.get('last_shop_post_ts',0)<config().get('forum_interval_seconds',1200):
        return None
    announced=state.get('availability_announced',{})
    wants=hub.get('demand_radar',{}).get('sku_inquiries',{})
    ranked=[]
    for sku in held:
        events=[e['ts'] for e in wants.get(sku.lower(),[]) if number(e.get('ts')) and 0<=now-e['ts']<=1200
                and e['ts']>announced.get(sku,0)]
        if events:ranked.append((len(events),max(events),sku))
    if not ranked:
        return None
    chosen=sorted(ranked,reverse=True)[:3];skus=[v[2] for v in chosen]
    return {'action':{'post':text_for(skus),'refs':[],'as':'shop'},'skus':skus,
            'demand_ts':max(v[1] for v in chosen),'thread':None}


def stock_post(units,hub,catalog,now):
    plan=availability_plan(units,hub,catalog,now)
    return plan['action']['post'] if plan else None
