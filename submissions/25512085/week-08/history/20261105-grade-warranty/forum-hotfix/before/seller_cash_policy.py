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


def warranty_hours(grade, kind='goods'):
    if kind != 'goods':
        return 0
    return max(0,min(72,int(config().get('grade_warranty_hours',{}).get(grade,{'A':72,'B':6,'C':0}.get(grade,0)))))


def profit_floor(unit, hub, catalog, now, warranty=None):
    """Expected-profit floor, never a guarantee against a realized full refund."""
    p=config();cost=float(unit['cost']);grade=unit.get('live_grade') or unit.get('grade')
    hours=warranty_hours(grade,unit.get('kind','goods')) if warranty is None else warranty
    q=refund_probability(hub,unit['sku'],grade,catalog,now) if hours else 0
    if grade == 'B' and hours:
        q=max(PRIOR_BREAK['B'],q)
    # A flat5000 floor made low-list-price goods unnecessarily dearer than their catalog value.
    contribution=min(p.get('absolute_goods_contribution',5000),
                     max(p.get('minimum_goods_contribution',500),math.ceil(cost*.05)))
    if grade == 'B':
        contribution=max(contribution,math.ceil(cost*p.get('b_minimum_expected_margin_fraction',.05)))
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
                 and (kind != 'goods' or ((r.get('warranty_hours') or 0)==0 if hours==0 else
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
        q = refund_probability(hub,sku,grade,catalog,now) if warranty_hours(grade,info.get('kind','goods')) else 0
        if grade=='B' and q:
            q=max(PRIOR_BREAK['B'],q)
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
    request=re.compile(r'looking for|want to buy|stock request|availability|in stock\?|구매\s*(?:희망|원|요청)|재고\s*(?:있|문의)|찾(?:아요|습니다|고 있어)',re.I)
    for post in sorted(posts,key=lambda x:x.get('ts',0),reverse=True):
        pid=post.get('id');body=str(post.get('text') or '')
        if (post.get('role')!='buyer' or str(post.get('owner') or '').lower()=='mjforge'
                or not number(post.get('ts')) or not 0<=now-post['ts']<=1200
                or pid is None or str(pid) in replied or not request.search(body)):
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
