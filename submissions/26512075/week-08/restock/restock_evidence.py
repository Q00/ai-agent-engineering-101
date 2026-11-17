"""Read-only grade evidence and persistent pilot state shared by planner and guard."""
import copy
import hashlib
import json
import math
from pathlib import Path
from collections import defaultdict
import statistics


def enrich_grades(state, history):
    """Join immutable own-settlement evidence; never rewrite historical logs."""
    if history is None:
        return None
    result = copy.deepcopy(history)
    run = result['market_run']
    prefix = hashlib.sha256(run.encode()).hexdigest()[:16]
    for event in result['events']:
        if event.get('grade') in ('A', 'B', 'C'):
            continue
        event_id = event.get('id', '')
        suffix = event_id.removeprefix(f'{run}:seller:')
        if not suffix.isdigit() or event_id != f'{run}:seller:{suffix}':
            continue
        path = Path(state)/'history/settlements'/f'{prefix}-{suffix}.json'
        if not path.exists():
            continue
        evidence = json.loads(path.read_text())
        if (evidence.get('market_run') == run
                and evidence.get('ledger') == int(suffix)
                and all(evidence.get(k) == event.get(k) for k in ('sku', 'unit', 'price'))
                and evidence.get('actual_cost') == event.get('cost')
                and evidence.get('settled_grade') in ('A', 'B', 'C')):
            event['grade'] = evidence['settled_grade']
    return result


def attach_pilot_state(inv, control, history, cfg):
    """A missing unit is not a sale; only exact-unit settlement frees the pilot slot."""
    probe = control.get('exploration') or {}
    pending = bool(control.get('pending')) or bool(probe and probe.get('status') != 'reconciled')
    unit = (probe.get('unit') or {}).get('unit')
    sold = bool(unit and any(e.get('settled') is True and e.get('role') == 'seller'
                            and e.get('round') == 3 and e.get('unit') == unit
                            and e.get('market_run') == control.get('market_run')
                            and e.get('sku') == probe.get('sku')
                            for e in (history or {}).get('events', [])))
    still_present = bool(unit and any(u.get('unit') == unit for u in inv['units']))
    occupied = bool(probe) and (not sold or still_present)
    used = control.get('exploration_commitment_used', 0)
    if type(used) not in (int, float) or not math.isfinite(used) or used < 0:
        raise ValueError('invalid persistent exploration budget')
    inv['_exploration_pending'] = pending
    inv['_exploration_occupied'] = occupied
    if cfg.purchase_budget is not None:
        committed = control.get('purchase_commitment_used', 0)
        if type(committed) is not int or committed < 0:
            raise ValueError('invalid persistent purchase budget')
        inv['_purchase_remaining'] = max(0, cfg.purchase_budget - committed)
    if cfg.exploration_budget is not None:
        inv['_exploration_remaining'] = max(0, cfg.exploration_budget-used)
    return inv


def grade_samples(history, sku, now, lookback):
    """Called after sale_stats validates the history; unknown grades remain explicit."""
    samples = defaultdict(list)
    unknown = False
    seen = set()
    for e in (history or {}).get('events', []):
        if (e.get('id') in seen or e.get('sku') != sku or e.get('settled') is not True
                or e.get('role') != 'seller' or e.get('round') != 3
                or e.get('market_run') != history.get('market_run')
                or not now-lookback*3600 <= e['ts'] <= history['as_of']):
            continue
        seen.add(e['id'])
        grade, cost = e.get('grade'), e.get('cost')
        if (grade not in ('A', 'B', 'C') or type(cost) not in (int, float)
                or not math.isfinite(cost) or cost <= 0):
            unknown = True
            continue
        samples[grade].append((cost, e['price']-e.get('refund', 0)))
    return samples, unknown


def grade_economics(history, sku, now, cfg, factor, hold_hours, kind):
    samples, unknown = grade_samples(history, sku, now, cfg.lookback_hours)
    scenarios = {}
    for grade, rows in sorted(samples.items()):
        # Historical actual costs are estimates, never supplier quotes. Applying the
        # full current surge (historical surge >= 1) avoids understating a surge change.
        cost = max(c for c, _ in rows) * factor
        price = statistics.median(p for _, p in rows)
        if grade in ('B', 'C'):
            # Our current seller opens these units at exactly cost * 1.03.
            price = min(price, math.ceil(cost * 103 / 100))
        storage = cost * .01 * hold_hours / 24 if kind == 'goods' else 0
        risk = price * cfg.refund_risk_fraction if kind == 'goods' else 0
        scenarios[grade] = {'grade': grade, 'samples': len(rows),
            'estimated_acquisition_cost': cost, 'sale_price': price,
            'estimated_storage': storage, 'risk_buffer': risk,
            'estimated_margin': price-cost-storage-risk,
            'cost_source': 'same_grade_max_observed_cost_times_current_surge',
            'sale_price_source': 'same_grade_settlements_capped_by_bc_3pct_policy' if grade in ('B', 'C') else 'same_grade_settlements'}
    return scenarios, unknown
