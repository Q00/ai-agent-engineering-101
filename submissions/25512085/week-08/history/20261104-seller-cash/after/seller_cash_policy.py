"""Seller-only cash-growth policy. Pure calculations, bounded state, no network.

Public settled volume is a demand signal, never our promised sales. All rates and
refund probabilities below are estimates; no statistic is a guaranteed margin.
"""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path
import statistics
from adaptive_strategy import note_ledger_coverage

CONFIG_PATH = Path(__file__).with_name('seller_cash_policy.json')
PRIOR_BREAK = {'A': .03, 'B': .15, 'C': .35}


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
    state = hub.setdefault('seller_cash', {})
    # An older shared cursor does not prove that this new observer saw the same history.
    note_ledger_coverage({'adaptive_ledger_coverage':state.setdefault('coverage',{})},rows,now)
    sales = state.setdefault('sales', {})
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
        sales[str(int(seq))] = {'ts': ts, 'sku': sku, 'amount': d['amount'], 'grade': d.get('grade'),
                                'warranty_hours': d.get('warranty_hours'), 'order': d.get('order'),
                                'own': str(d.get('shop') or '').rstrip('/') in own_urls}
    state['sales'] = dict(sorted(((s,d) for s,d in sales.items() if now-8*3600 <= d['ts'] <= now),
                                key=lambda pair: pair[1]['ts'])[-40000:])
    state['breaks'] = {k:d for k,d in breaks.items() if now-8*3600 <= d['ts'] <= now}


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
    return list(hub.get('seller_cash', {}).get('sales', {}).values())


def refund_probability(hub, sku, grade, catalog, now):
    prior = PRIOR_BREAK.get(grade, .35)
    old = [r for r in sale_rows(hub) if now-8*3600 <= r['ts'] <= now-6*3600 and r.get('grade') == grade]
    own_sku = [r for r in old if r['sku'] == sku]
    if len(own_sku) >= 30:
        old = own_sku
    else:
        cat = catalog.get(sku, {}).get('category')
        old = [r for r in old if catalog.get(r['sku'], {}).get('category') == cat]
    broken = hub.get('seller_cash', {}).get('breaks', {})
    return (sum(r.get('order') in broken for r in old) + 20*prior)/(len(old)+20)


def reference_price(hub, sku, grade, now, kind='goods'):
    """Never mix unknown/other grades into the first-quote comparable price."""
    observations = hub.get('demand_radar', {}).get('sku_comparables', {}).get(sku, [])
    valid = [r for r in observations if r.get('grade') == grade and number(r.get('ts'))
             and 0 <= now-r['ts'] <= 3600 and number(r.get('amount')) and r['amount'] > 0
             and (kind != 'goods' or number(r.get('warranty_hours')) and r['warranty_hours'] >= 6)]
    return int(round(statistics.median(r['amount'] for r in valid))) if len(valid) >= 5 else None


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
    q = refund_probability(hub, sku, grade, catalog, now) if kind == 'goods' else 0
    contribution = p.get('absolute_goods_contribution',5000) if kind == 'goods' else p.get('absolute_food_contribution',1000)
    floor = math.ceil((unit['cost']+contribution)/(1-q))
    ask = max(floor, price)
    last = max(floor, math.ceil(ask*(1-p.get('maximum_concession_fraction',.01))))
    return {'open_ask':ask, 'r1_target':last, 'r2_target':last, 'hard_floor':floor,
            'break_rate':q, 'cash_first_quote':True, 'quote_basis':'same-SKU/grade/6h-settles-1h'}


def note_stock(hub, held, now):
    observations = hub.setdefault('seller_cash', {}).setdefault('stock_observations', [])
    if not observations or now-observations[-1]['ts'] >= 30:
        observations.append({'ts':now,'skus':sorted(s for s,n in held.items() if n)})
    hub['seller_cash']['stock_observations'] = [r for r in observations if 0 <= now-r['ts'] <= 3600][-121:]


def forecast(hub, sku, info, catalog, now, inquiries=0):
    p = config()
    rows = [r for r in sale_rows(hub) if 0 <= now-r['ts'] <= 3600]
    market = [r for r in rows if r['sku'] == sku]
    own = [r for r in market if r.get('own')]
    category = [r for r in rows if catalog.get(r['sku'],{}).get('category') == info.get('category')]
    share = len(own)/len(market) if market else 0
    cat_share = sum(bool(r.get('own')) for r in category)/len(category) if category else 0
    target_share = min(1, max(share,cat_share,p.get('cold_start_share',.02))*p.get('share_growth_multiplier',1.5))
    own20 = sum(now-r['ts'] <= 1200 for r in own)
    # Correct only measured short gaps; a missing heartbeat never proves inventory availability.
    observations = hub.get('seller_cash',{}).get('stock_observations',[])
    available = sum(b['ts']-a['ts'] for a,b in zip(observations,observations[1:])
                    if 0 < b['ts']-a['ts'] <= 90 and sku in a['skus'])
    corrected = len(own)*1200/available if available >= 300 else len(own)/3
    prediction = max(own20,len(own)/3,corrected,len(market)*target_share/3,inquiries/3)
    if market:
        prediction = min(prediction,max(len(market)/3,own20,inquiries/3))
    minimum = p.get('minimum_proven_depth',4) if own or inquiries else p.get('minimum_new_depth',3)
    depth = min(p.get('maximum_depth',40),max(minimum,math.ceil(prediction*p.get('forecast_minutes',30)/20*p.get('target_multiplier',1.75))))
    return {'own20':own20,'own60':len(own),'market60':len(market),'prediction20':prediction,'depth':depth,'target_share':target_share}


def procurement(hub, sku, info, inv, qty, catalog, now):
    """Expected contribution for ranking; global legal cost ceiling for cash reservations."""
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
    for grade,weight in weights.items():
        price = reference_price(hub,sku,grade,now,info.get('kind','goods'))
        if price is None:
            # Prior is explicitly an estimate, never a current quote or a claimed executable price.
            price = lp*{'A':.80,'B':.72,'C':.65}[grade]
        q = refund_probability(hub,sku,grade,catalog,now) if info.get('kind') == 'goods' else 0
        cost = lp*{'A':.65,'B':.5,'C':.35}[grade]*factor
        expected += weight*(price*(1-q)-cost)
    return {'expected_unit_contribution':expected,'projected_surge':factor,
            # Other shops can buy between planning and supply. Reserve the published global maximum.
            'reservation_unit_cost':math.ceil(lp*.75*1.5)}


def spending_budget(hub, inv, existing_floor, now):
    p = config()
    pending = sum(r['amount']*PRIOR_BREAK.get(r.get('grade'),.35) for r in sale_rows(hub)
                  if r.get('own') and 0 <= now-r['ts'] <= 6*3600 and (r.get('warranty_hours') or 0) >= 6
                  and r.get('order') not in hub.get('seller_cash',{}).get('breaks',{}))
    reserve = max(existing_floor,p.get('minimum_cash_reserve',5000000),math.ceil(pending*2))
    return int(max(0,inv['balance']-reserve)*p.get('deployable_fraction',.85)),reserve


def allocate(groups, models, held, budget, cap, grade_spent, previous):
    """Restore proven stockouts first, then fund the highest total-cash forecasts."""
    funded = collections.Counter()
    spent = 0
    priority = sorted(groups,key=lambda row:(-models.get(row[1][0],{}).get('score20',0),row[1][0]))
    restoration = [(g,(s,min(3,n),c,m)) for g,(s,n,c,m) in priority
                   if not held[s] and models.get(s,{}).get('own60',0)>0]
    for grade,(sku,need,cost,margin) in restoration+priority:
        if sku in previous or cost <= 0:
            continue
        missing = need-funded[sku]
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


def stock_post(units, hub, catalog, now):
    """Only advertise verified held goods, at the same quote used by the seller."""
    ranked = []
    for u in units:
        if u.get('kind') != 'goods' or u.get('grade') not in PRIOR_BREAK or not u.get('cert'):
            continue
        value = quote(u,hub,catalog,now)
        if value:
            ranked.append(((value['open_ask']*(1-value['break_rate'])-u['cost']),u,value))
    lines, seen = [],set()
    for _,u,value in sorted(ranked,key=lambda r:-r[0]):
        key = u['sku'],u['grade'],value['open_ask']
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"SKU={u['sku']} grade={u['grade']} qty=1 first_quote={value['open_ask']} cert=true warranty_hours=6")
        if len(lines)==3:
            break
    if not lines:
        return None
    return '[mjforge verified stock]\n'+'\n'.join(lines)+f'\nChecked_at={int(now)}. Each quote is for one verified unit; stock is rechecked at checkout. Send the SKU for an immediate quote.'
