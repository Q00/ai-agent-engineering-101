"""First-hand inventory evidence, with explicit gaps and censored demand.

Visits are a selected sample, not all buyer requests. A catalog category is not a
demand count. Missing snapshots never imply zero demand or continuous exposure.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from pathlib import Path
import re

from market_memory import record

MAX_SNAPSHOT_GAP = 120.0
MAX_SNAPSHOT_AGE = 30.0
CONVERSION_WINDOW_HOURS = 12.
CONVERSION_MATURITY_SECONDS = 900
CONVERSION_PRIOR_SUCCESSES = 2.
CONVERSION_PRIOR_FAILURES = 2.
SKU_CONVERSION_PRIOR_ATTEMPTS = 4.
EXPIRY_SUPPLY_BUFFER_HOURS = 2.
EXPIRY_MIN_TASKS = 2


def demand_outcomes(history: list[dict], now: float) -> dict:
    """Link served attempts once per forecast batch; pending is not failure.

    A task is the request identity. Legacy settlements without a task can only
    use a unique signed-receipt unit/price/SKU match. No name-based joins.
    """
    start = now-CONVERSION_WINDOW_HOURS*3600
    openings, receipts, settlements, failures = {}, defaultdict(dict), {}, defaultdict(list)
    for row in history:
        at=timestamp(row.get('at'))
        if at is None or not start <= at <= now:continue
        event,task,sku=row.get('event'),row.get('task_id'),row.get('sku')
        if event=='opening' and task and sku:
            key=(task,sku);openings[key]=min(at,openings.get(key,at))
        elif event=='receipt_signed' and task and sku and row.get('unit') and type(row.get('price')) is int:
            receipts[(sku,row['unit'],row['price'])][task]=at
        elif event=='settled' and row.get('ledger') is not None and sku:
            settlements.setdefault(row['ledger'],(row,at))
        elif event in ('rejected','cancelled','checkout_unpaid') and task:
            failures[task].append(at)
    settled_tasks=defaultdict(set);settled_at={}
    for row,at in settlements.values():
        task=row.get('task_id');sku=row['sku']
        if not task:
            matches={task for task,t in receipts.get((sku,row.get('unit'),row.get('price')),{}).items() if t<=at}
            if len(matches)==1:task=next(iter(matches))
        if task:
            settled_tasks[sku].add(task)
            key=(task,sku);settled_at[key]=min(at,settled_at.get(key,at))
    counts=defaultdict(lambda:{'settled':0,'not_settled':0,'pending_or_immature':0})
    for (task,sku),opened in openings.items():
        target=counts[sku]
        if now-opened<CONVERSION_MATURITY_SECONDS:
            target['pending_or_immature']+=1
        elif settled_at.get((task,sku),-1)>=opened:
            target['settled']+=1
        elif any(at>=opened for at in failures.get(task,[])):
            target['not_settled']+=1
        else:
            target['pending_or_immature']+=1
    return {'settled_tasks':settled_tasks,'served_tasks':{key for key in openings},
            'conversion_counts':dict(counts)}


def conversion_estimate(sku: str, outcomes: dict) -> dict:
    counts=outcomes['conversion_counts']
    shop_success=sum(r['settled'] for r in counts.values())
    shop_failure=sum(r['not_settled'] for r in counts.values())
    shop_rate=(shop_success+CONVERSION_PRIOR_SUCCESSES)/(shop_success+shop_failure+
                CONVERSION_PRIOR_SUCCESSES+CONVERSION_PRIOR_FAILURES)
    local=counts.get(sku,{'settled':0,'not_settled':0,'pending_or_immature':0})
    completed=local['settled']+local['not_settled']
    rate=(local['settled']+SKU_CONVERSION_PRIOR_ATTEMPTS*shop_rate)/(completed+SKU_CONVERSION_PRIOR_ATTEMPTS)
    return {'probability':round(rate,6),'sku_outcomes':local,'shop_settled':shop_success,
            'shop_not_settled':shop_failure,'window_hours':CONVERSION_WINDOW_HOURS,
            'maturity_seconds':CONVERSION_MATURITY_SECONDS,
            'shop_beta_prior':[CONVERSION_PRIOR_SUCCESSES,CONVERSION_PRIOR_FAILURES],
            'sku_prior_attempts':SKU_CONVERSION_PRIOR_ATTEMPTS,
            'source':'SKU completed served attempts shrunk toward shop rate; shop Beta(2,2) prior',
            'caution':'Unvalidated estimate from selected completed quotes, not guaranteed stockout conversion. '
                      'Pending, expiry-unknown and immature attempts are excluded, not labeled failures.'}


def timestamp(value) -> float | None:
    if type(value) in (int, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    return None


def unserved_exact_stockouts(history: list[dict], now: float) -> dict[str, int]:
    """Count distinct recent tasks for an exact SKU that we did not later serve."""
    outcomes = demand_outcomes(history, now)
    tasks = defaultdict(set)
    for row in history:
        at = timestamp(row.get('at'))
        task = row.get('task_id')
        skus = row.get('requested_skus') or []
        if (row.get('event') != 'no_stock' or not task or len(skus) != 1
                or at is None or not now - CONVERSION_WINDOW_HOURS * 3600 <= at <= now):
            continue
        sku = skus[0]
        if (task, sku) not in outcomes['served_tasks'] and task not in outcomes['settled_tasks'].get(sku, set()):
            tasks[sku].add(task)
    return {sku: len(requests) for sku, requests in tasks.items()}


def expiry_shortfalls(history, catalog, units, now, *, outcomes=None):
    """Distinct unserved exact food requests with a plausible fresh-stock option."""
    start = now - CONVERSION_WINDOW_HOURS * 3600
    exact = {}
    gaps = {}
    for row in history:
        event, task = row.get('event'), row.get('task_id')
        if event not in ('request_seen', 'requirements_unmet') or not task:
            continue
        at = timestamp(row.get('at'))
        if at is None or not start <= at <= now:
            continue
        if event == 'request_seen':
            skus = row.get('requested_skus') or []
            if len(skus) == 1:
                exact[task] = skus[0]
            continue
        gap = row.get('expiry_shortfall')
        if isinstance(gap, dict):
            sku, hours, verified = gap.get('sku'), gap.get('required_hours'), True
            if (row.get('requested_skus') or []) != [sku]:
                continue
        elif 'expiry_shortfall' not in row:
            # Legacy initial failures have no per-unit reason. Only recover
            # exact food requests with no other restrictive purchase condition.
            conditions = row.get('requirements') or {}
            grades = conditions.get('grades')
            if (conditions.get('warranty_hours') or conditions.get('cert_required')
                    or (grades and set(grades) != {'A', 'B', 'C'})):
                continue
            sku, hours, verified = exact.get(task), conditions.get('min_hours_left'), False
        else:
            continue
        product = catalog.get(sku, {})
        shelf = product.get('shelf_hours')
        if (product.get('kind') != 'food' or type(hours) not in (float, int)
                or type(shelf) not in (float, int) or not 0 < hours <= shelf - EXPIRY_SUPPLY_BUFFER_HOURS):
            continue
        previous = gaps.get(task)
        if previous is None or (verified, hours) > (previous[2], previous[1]):
            gaps[task] = (sku, hours, verified)

    if not gaps:
        return {}
    outcomes = demand_outcomes(history, now) if outcomes is None else outcomes
    available = defaultdict(float)
    for unit in units:
        expires = unit.get('expires_at')
        if unit.get('kind') == 'food' and type(expires) in (float, int):
            sku = unit['sku']
            available[sku] = max(available[sku], (expires - now) / 3600)
    by_sku = defaultdict(list)
    for task, (sku, hours, verified) in gaps.items():
        if ((task, sku) in outcomes['served_tasks'] or task in outcomes['settled_tasks'].get(sku, ())
                or available[sku] >= hours):
            continue
        by_sku[sku].append((hours, verified))
    result = {}
    for sku, rows in by_sku.items():
        if len(rows) < EXPIRY_MIN_TASKS:
            continue
        target = sorted((hours for hours, _ in rows), reverse=True)[EXPIRY_MIN_TASKS - 1]
        result[sku] = {'unserved_tasks_12h': len(rows),
                       'confirmed_expiry_shortfalls': sum(verified for _, verified in rows),
                       'target_remaining_hours': target,
                       'best_stock_remaining_hours': round(available[sku], 2),
                       'catalog_shelf_hours': catalog[sku]['shelf_hours']}
    return result


def requested_skus(text: str, catalog: dict) -> list[str]:
    """Attribute only exact SKU tokens or an unambiguous full product name."""
    text = text.casefold()
    exact = [sku for sku in catalog if sku.casefold() in text and
             re.search(r'(?<![\w-])' + re.escape(sku.casefold()) + r'(?![\w-])', text)]
    if exact:
        return exact[:10]
    names = [sku for sku, product in catalog.items() if isinstance(product, dict)
             and isinstance(product.get('name'), str) and len(product['name']) >= 6
             and product['name'].casefold() in text]
    return names if len(names) == 1 else []


def snapshot(inventory: dict, source: Path, history: list[dict], now: float, *, source_time=None) -> bool:
    if source_time is None:
        try:
            source_time = source.stat().st_mtime
        except OSError:
            return False
    if not 0 <= now - source_time <= MAX_SNAPSHOT_AGE:
        return False
    previous = next((e for e in reversed(history) if e['event'] == 'inventory_snapshot'), {})
    if previous.get('source_time') == source_time:
        return False
    stock = defaultdict(lambda: {'qty': 0, 'grades': {}})
    for u in inventory.get('units', []):
        if not u.get('sku') or u.get('grade') not in ('A', 'B', 'C'):
            continue
        if u.get('expires_at') is not None and u['expires_at'] <= now:
            continue
        row = stock[u['sku']]
        row['qty'] += 1
        row['grades'][u['grade']] = row['grades'].get(u['grade'], 0) + 1
    record('inventory_snapshot', source_time=source_time, round=inventory.get('round'), stock=dict(stock))
    return True


def evidence(sku: str, history: list[dict], now: float, hours: float = 12., *, outcomes=None) -> dict:
    start = now - hours * 3600
    visits, settled, no_stock = {}, {}, {}
    snapshots = {}
    unattributed = set()
    eligible_stockouts=set();ambiguous_stockouts=set()
    outcomes=demand_outcomes(history,now) if outcomes is None else outcomes
    for row in history:
        at = timestamp(row.get('at'))
        if row['event'] == 'inventory_snapshot' and row.get('round') == 3:
            t = timestamp(row.get('source_time'))
            if t is not None and start - MAX_SNAPSHOT_GAP <= t <= now:
                snapshots[t] = row.get('stock', {})
        if at is None or not start <= at <= now:
            continue
        task = row.get('task_id')
        if row['event'] == 'request_seen' and task and sku in row.get('requested_skus', []):
            visits.setdefault(task, at)
        elif row['event'] == 'opening' and task and row.get('sku') == sku:
            visits.setdefault(task, at)
        elif row['event'] == 'no_stock' and task:
            if sku in row.get('requested_skus', []):
                no_stock.setdefault(task, at); visits.setdefault(task, at)
                if set(row['requested_skus'])=={sku}:eligible_stockouts.add(task)
                else:ambiguous_stockouts.add(task)
            elif not row.get('requested_skus'):
                unattributed.add(task)
        elif row['event'] == 'settled' and row.get('sku') == sku and row.get('ledger') is not None:
            settled.setdefault(row['ledger'], at)
    coverage, exposure, stocked_intervals = 0., 0., []
    times = sorted(snapshots)
    for a, b in zip(times, times[1:]):
        if b - a > MAX_SNAPSHOT_GAP:
            continue
        duration = max(0., min(now, b) - max(start, a))
        coverage += duration
        if snapshots[a].get(sku, {}).get('qty', 0) > 0:
            exposure += duration
            stocked_intervals.append((max(start, a), min(now, b)))
    in_stock_visits = 0
    for at in visits.values():
        if any(a <= at < b for a, b in stocked_intervals):
            in_stock_visits += 1
    in_stock_settlements = sum(any(a <= at < b for a,b in stocked_intervals) for at in settled.values())
    # Never add a request again after it was served or actually settled.
    already_served={task for task in eligible_stockouts if (task,sku) in outcomes['served_tasks']
                    or task in outcomes['settled_tasks'].get(sku,set())}
    eligible_stockouts-=already_served|ambiguous_stockouts
    return {'window_hours': hours, 'observed_visits': len(visits),
            'observed_no_stock_visits': len(no_stock), 'confirmed_settlements': len(settled),
            'eligible_lost_stockout_visits':len(eligible_stockouts),
            'ambiguous_stockout_visits':len(ambiguous_stockouts),
            'already_served_stockout_visits':len(already_served),
            'recent_stockout_times': sorted(no_stock.values())[-10:],
            'snapshot_coverage_hours': round(coverage / 3600, 4),
            'in_stock_exposure_hours': round(exposure / 3600, 4),
            'visits_during_observed_stock': in_stock_visits,
            'settlements_during_observed_stock': in_stock_settlements,
            'observed_visit_rate_while_stocked': round(in_stock_visits / (exposure / 3600), 3) if exposure >= 300 else None,
            'unattributed_no_stock_visits': len(unattributed),
            'total_demand': None, 'demand_is_censored': True,
            'caution': 'Selected shop visits only; stock snapshots do not prove listing visibility. '
                       'Unobserved requests and time gaps are unknown, not zero. '
                       'Public demand.json is a category catalog, not demand frequency.'}
