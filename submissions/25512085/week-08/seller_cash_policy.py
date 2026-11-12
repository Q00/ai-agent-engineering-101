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
import sys as _sys

import _cash_demand
import _cash_forum
import _cash_pricing
import _cash_risk

# Keep the public module as the live dependency boundary, not a simple re-export.
# Existing callers patch config/functions/constants here; internal calls must see
# those patches too. Internal modules never import this facade (no import cycle).

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


_config_cache = None


def config():
    global _config_cache
    try:
        stat = CONFIG_PATH.stat()
        key = (str(CONFIG_PATH.resolve()), stat.st_mtime_ns, stat.st_size)
        if _config_cache is not None and _config_cache[0] == key:
            return dict(_config_cache[1])
        value = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        value = value if isinstance(value, dict) else {'enabled': False}
        _config_cache = (key, value)
        return dict(value)
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
    return _cash_risk.refund_probability(_sys.modules[__name__], hub, sku, grade, catalog, now)


def warranty_hours(grade, kind='goods'):
    return _cash_pricing.warranty_hours(_sys.modules[__name__], grade, kind)


def profit_floor(unit, hub, catalog, now, warranty=None):
    """Expected-profit floor, never a guarantee against a realized full refund."""
    return _cash_pricing.profit_floor(_sys.modules[__name__], unit, hub, catalog, now, warranty)


def reference_price(hub, sku, grade, now, kind='goods', warranty=None):
    """Never mix unknown/other grades into the first-quote comparable price."""
    return _cash_pricing.reference_price(_sys.modules[__name__], hub, sku, grade, now, kind, warranty)


def quote(unit, hub, catalog, now):
    """Return a cost-safe first quote only with comparable evidence. Keep clearance separate."""
    return _cash_pricing.quote(_sys.modules[__name__], unit, hub, catalog, now)


def note_stock(hub, held, now):
    observations = hub.setdefault('seller_cash', {}).setdefault('stock_observations', [])
    if not observations or now-observations[-1]['ts'] >= 30:
        observations.append({'ts':now,'skus':sorted(s for s,n in held.items() if n)})
    hub['seller_cash']['stock_observations'] = [r for r in observations if 0 <= now-r['ts'] <= 3600][-121:]


def forecast(hub, sku, info, catalog, now, inquiries=0):
    return _cash_demand.forecast(_sys.modules[__name__], hub, sku, info, catalog, now, inquiries)


def procurement(hub, sku, info, inv, qty, catalog, now):
    """Expected contribution for ranking; global legal cost ceiling for cash reservations."""
    return _cash_demand.procurement(_sys.modules[__name__], hub, sku, info, inv, qty, catalog, now)


def spending_budget(hub, inv, existing_floor, now):
    return _cash_demand.spending_budget(_sys.modules[__name__], hub, inv, existing_floor, now)


def allocate(groups, models, held, budget, cap, grade_spent, previous):
    """Restore proven stockouts first, then fund the highest total-cash forecasts."""
    return _cash_demand.allocate(_sys.modules[__name__], groups, models, held, budget, cap, grade_spent, previous)


def availability_plan(units, hub, catalog, now, forum=None):
    """Match real recent requests to certified stock; publish availability only."""
    return _cash_forum.availability_plan(_sys.modules[__name__], units, hub, catalog, now, forum)


def stock_post(units,hub,catalog,now):
    plan=availability_plan(units,hub,catalog,now)
    return plan['action']['post'] if plan else None
