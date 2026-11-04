"""Two-hour, local-only strategy review. No model, shell, network, or source edits."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
import math
import signal
import sqlite3
import statistics

from r3_clock import clock as time
from r3_common import (DEFAULT_POLICY, atomic_json, load_catalog, load_inventory,
                       load_policy, read_json, require_live, state_dir)
from r3_storage import database_dir, exclusive_lock

INTERVAL = 7200
ROW_LIMIT = 20000
TUNABLE = {'trend_half_life_seconds', 'purchase_budget_fraction',
           'opening_price_fraction', 'min_turnover_hours'}


def summarize(events, catalog, inventory, cursor, now, started_at, capped=False):
    counts = [Counter(), Counter()]
    skus = Counter()
    ratios, shops = defaultdict(list), defaultdict(set)
    buckets, seen, refs = set(), set(), []
    own = Counter()
    for event in events:
        age = now - event['ts']
        if not 0 <= age < 2 * INTERVAL:
            continue
        data, kind = event['data'], event['kind']
        if kind == 'market_settle':
            sku, seq = data.get('item'), data.get('seq')
            product = catalog.get(sku)
            if not product or type(seq) is not int or seq in seen or data.get('source') != 'public_ledger':
                continue
            seen.add(seq)
            window = int(age >= INTERVAL)
            category = product['category']
            counts[window][category] += 1
            buckets.add(int(age // 900))
            if not window:
                skus[sku] += 1
                refs.append(seq)
                price, listed = data.get('amount', data.get('price')), product['list_price']
                if (type(price) in (int, float) and math.isfinite(price)
                        and 0 < price / listed <= 1.5 and isinstance(data.get('shop'), str)):
                    ratios[category].append(price / listed)
                    shops[category].add(data['shop'])
        elif age < INTERVAL:
            if kind == 'payment':
                own['paid' if data.get('paid') is True else 'payment_rejected'] += 1
            elif kind == 'negotiation':
                own['negotiations'] += 1
                if data.get('outcome') == 'receipt_pending':
                    own['signed_receipts_not_payment_proof'] += 1
            elif kind == 'policy_fallback':
                own['policy_fallbacks'] += 1
            elif kind == 'shop_review':
                own['customer_review_claims'] += 1
    totals = [sum(c.values()) for c in counts]
    coverage = (not capped and now - started_at >= 2 * INTERVAL
                and 0 <= now - cursor.get('polled_at', 0) <= 180
                and buckets == set(range(16)) and min(totals) >= 20)
    categories = set(counts[0]) | set(counts[1])
    shift = (sum(abs(counts[0][c] / totals[0] - counts[1][c] / totals[1])
                 for c in categories) / 2 if min(totals) else None)
    rows = []
    for category in sorted(categories, key=lambda c: (-counts[0][c], c))[:15]:
        n, before = counts[0][category], counts[1][category]
        rows.append({'category': category, 'recent_sales': n, 'previous_sales': before,
                     'growth': round(n / before - 1, 3) if coverage and min(n, before) >= 5 else None})
    cost = sum(u['cost'] for u in inventory['units'])
    aging = sum(u['cost'] for u in inventory['units'] if now - u['supplied_at'] >= 6 * 3600)
    expiring = sum(u['cost'] for u in inventory['units']
                   if u['kind'] == 'food' and u['expires_at'] - now <= 3 * 3600)
    held_ratios = []
    for unit in inventory['units']:
        cat = catalog.get(unit['sku'], {}).get('category')
        if now - unit['supplied_at'] >= INTERVAL and len(ratios[cat]) >= 10 and len(shops[cat]) >= 3:
            held_ratios.append(statistics.median(ratios[cat]))
    related = []
    for sku, sales in skus.most_common(5):
        product = catalog[sku]
        candidates = [(abs(p['list_price'] / product['list_price'] - 1), other)
                      for other, p in catalog.items() if other != sku and not skus[other]
                      and p['category'] == product['category']
                      and .5 <= p['list_price'] / product['list_price'] <= 1.5]
        for _, other in sorted(candidates)[:2]:
            related.append({'sku': other, 'related_to': sku, 'observed_anchor_sales': sales,
                            'status': 'unseen_related_hypothesis_not_a_purchase_instruction'})
    return {'window_seconds': INTERVAL, 'recent_sales': totals[0], 'previous_sales': totals[1],
            'coverage_sufficient': coverage, 'rows_capped': capped,
            'covered_quarter_hours': len(buckets), 'share_shift': round(shift, 4) if shift is not None else None,
            'categories': rows, 'top_skus': skus.most_common(10), 'related_hypotheses': related,
            'evidence_refs': refs[:8], 'own_recent_counts': dict(own),
            'inventory': {'balance': inventory['balance'], 'units': len(inventory['units']),
                          'stock_cost': cost, 'aging_cost': aging, 'expiring_food_cost': expiring},
            'held_category_price_ratio': statistics.median(held_ratios) if held_ratios else None,
            'limits': ['Observed ledger sample, not complete market demand or causal evidence.',
                       'No individual shop is blamed using unsigned reviews.',
                       'No Players percentile or final surplus is inferred from local counts.']}


def propose(summary, baseline):
    if not summary['coverage_sufficient']:
        return {}, ['Insufficient fresh two-window coverage: hold current settings.']
    desired = {key: baseline[key] for key in TUNABLE}
    reasons = []
    if summary['share_shift'] >= .25:
        desired['trend_half_life_seconds'] = min(baseline['trend_half_life_seconds'], 10800)
        reasons.append('Category sales shares shifted >=25%; shorten evidence half-life to at most 3h.')
    stock = summary['inventory']
    if stock['stock_cost'] and (stock['aging_cost'] >= .5 * stock['stock_cost']
                               or stock['expiring_food_cost'] >= .25 * stock['stock_cost']):
        desired['purchase_budget_fraction'] = min(baseline['purchase_budget_fraction'], .05)
        reasons.append('Aging/expiring capital: limit each new purchase plan to at most 5% of cash.')
    if stock['expiring_food_cost']:
        desired['min_turnover_hours'] = max(baseline['min_turnover_hours'], 12)
        reasons.append('Food expiry exposure: require at least 12h estimated turnover margin for new stock.')
    ratio = summary['held_category_price_ratio']
    base_price = baseline['opening_price_fraction']
    if ratio is not None and ratio + .03 < base_price:
        desired['opening_price_fraction'] = min(base_price, max(.75, base_price - .05, ratio + .03))
        reasons.append('Aged held categories clear below baseline: lower new quote baseline by at most 5 points.')
    return desired, reasons or ['Fresh evidence does not justify a deviation; use saved baseline.']


def read_events(md, now):
    path = database_dir(md) / 'r3-memory.sqlite3'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute("SELECT kind,ts,data FROM events WHERE ts>=? AND ts<=? AND kind IN "
                          "('market_settle','payment','negotiation','policy_fallback','shop_review') "
                          "ORDER BY ts DESC LIMIT ?", (now - 2 * INTERVAL, now, ROW_LIMIT + 1)).fetchall()
    return [{'kind': k, 'ts': ts, 'data': json.loads(data)} for k, ts, data in rows[:ROW_LIMIT]], len(rows) > ROW_LIMIT


def apply_policy(md, before, baseline_raw, desired):
    if set(desired) - TUNABLE:
        raise ValueError('Only whitelisted strategy parameters may change')
    baseline = dict(DEFAULT_POLICY, **baseline_raw)
    for key, value in desired.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('finite strategy value required')
        if key in ('purchase_budget_fraction', 'opening_price_fraction') and not 0 < value <= baseline[key]:
            raise ValueError('Cannot increase purchase exposure or opening quote above baseline')
        if key == 'min_turnover_hours' and value < baseline[key]:
            raise ValueError('Cannot weaken food turnover protection')
        if key == 'trend_half_life_seconds' and not 60 <= value <= 7 * 86400:
            raise ValueError('Invalid evidence half-life')
    after = dict(before)
    for key, value in desired.items():
        if value == baseline[key] and key not in baseline_raw:
            after.pop(key, None)
        else:
            after[key] = value
    if read_json(md / 'policy.json', {}) != before:
        raise RuntimeError('Policy edited during review; refusing to overwrite it')
    if after != before:
        atomic_json(md / 'trend-policy-pending.json', {'before': before, 'after': after,
                                                     'baseline': baseline_raw, 'at': time.time()})
        atomic_json(md / 'policy.json', after)
    return after


def review_once(md=None, now=None, apply=False):
    md = state_dir(md)
    now = time.time() if now is None else now
    if apply:
        require_live()
    current = read_json(md / 'policy.json', {})
    load_policy(md)
    previous = read_json(md / 'trend-watch-state.json', {})
    baseline_raw = previous.get('baseline_policy', current)
    baseline = dict(DEFAULT_POLICY, **baseline_raw)
    inventory = load_inventory(md, now=now)
    events, capped = read_events(md, now)
    started = previous.get('started_at', now)
    summary = summarize(events, load_catalog(md), inventory,
                        read_json(md / 'public-cursor.json', {}), now, started, capped)
    desired, reasons = propose(summary, baseline)
    manual_edit = bool(previous) and current != previous.get('last_applied_policy')
    status = 'paused_manual_policy_edit' if manual_edit else ('ready' if desired else 'insufficient_data')
    after = current
    if apply and not manual_edit:
        after = apply_policy(md, current, baseline_raw, desired)
    before_effective, after_effective = dict(DEFAULT_POLICY, **current), dict(DEFAULT_POLICY, **after)
    changes = {key: {'before': before_effective[key], 'after': after_effective[key]}
               for key in TUNABLE if before_effective[key] != after_effective[key]}
    report = {'generated_at': now, 'next_due_at': now + INTERVAL, 'status': status,
              'mode': 'applied' if apply else 'read_only', 'summary': summary,
              'proposed': desired, 'changes': changes, 'reasons': reasons,
              'source_edits': False, 'network_requests': 0, 'model_calls': 0}
    if apply:
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        atomic_json(md.parent / 'logs' / 'trend-watch' / (stamp + '.json'), report)
        state = dict(previous, started_at=started, next_due_at=now + INTERVAL,
                     baseline_policy=baseline_raw, last_status=status)
        if not manual_edit:
            state['last_applied_policy'] = after
        atomic_json(md / 'trend-watch-state.json', state)
        atomic_json(md / 'trend-watch-latest.json', report)
    return report


def restore(md):
    require_live()
    saved = read_json(md / 'trend-watch-state.json', {})
    if not saved or read_json(md / 'policy.json', {}) != saved.get('last_applied_policy'):
        raise RuntimeError('No owned policy state, or manual edits detected; no restoration performed')
    atomic_json(md / 'policy.json', saved['baseline_policy'])
    atomic_json(md / 'trend-watch-state.json', dict(saved, last_applied_policy=saved['baseline_policy'],
                                                 last_status='restored'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--loop', action='store_true')
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--restore', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    md = state_dir(args.state)
    if not args.apply:
        if not args.once:
            parser.error('--loop and --restore require --apply')
        print(json.dumps(review_once(md), ensure_ascii=False, indent=2))
        return
    require_live()
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with exclusive_lock(database_dir(md) / 'trend-watch.lock'):
        if args.restore:
            restore(md)
            print('Saved baseline restored. No source or server settings changed.', flush=True)
            return
        while not stopped:
            now = time.time()
            deadline = load_policy(md)['trading_deadline']
            if deadline is not None and now >= deadline:
                print('Trading deadline reached; trend watcher stopped.', flush=True)
                break
            due = read_json(md / 'trend-watch-state.json', {}).get('next_due_at', 0)
            if args.once or now >= due:
                try:
                    report = review_once(md, now=now, apply=True)
                    print(json.dumps({key: report[key] for key in ('generated_at', 'next_due_at', 'status', 'changes', 'reasons')},
                                     ensure_ascii=False), flush=True)
                except Exception as exc:
                    # Failed/stale observations cannot authorize strategy changes.
                    print(json.dumps({'status': 'error', 'error': type(exc).__name__}), flush=True)
                    if args.once:
                        raise
                    for _ in range(INTERVAL // 60):
                        if stopped:
                            break
                        time.sleep(60)
                if args.once:
                    break
            else:
                time.sleep(min(60, due - now))


if __name__ == '__main__':
    main()
