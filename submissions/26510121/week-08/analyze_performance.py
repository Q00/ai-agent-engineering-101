"""Reproduce a frozen public-board/local-journal diagnostic; no model or market writes."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import statistics
import re
import sys
from r3.state import ROOT, Journal, read_json, atomic_json

KST = timezone(timedelta(hours=9))


def analyze(snapshot):
    source = read_json(snapshot)
    cutoff = datetime.fromisoformat(source['checked_at']).timestamp()
    j = Journal(ROOT / '.market')
    kinds = ['buyer_order', 'purchase', 'agreement', 'payment', 'market_result', 'attempt', 'public_settle',
             'shop_settled', 'shop_opportunity', 'restock_decision', 'buyer_counter', 'prediction', 'decision',
             'model_usage', 'strategy_change', 'forum_delivery']
    with j.connect() as connection:
        connection.execute('BEGIN')
        events = {kind: [dict(json.loads(d), _ts=t, _role=r) for t, r, d in connection.execute(
            'SELECT ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts,rowid', (kind, cutoff))]
            for kind in kinds}
    catalog = j.snapshot('catalog.json', {})
    board = source['board']
    own_shop = next(r for r in board['shops'] if r.get('name') == 'seochanit')
    own_buyer = next(r for r in board['buyers'] if r.get('name') == 'seochanit')
    result = {'source_snapshot': str(Path(snapshot).relative_to(ROOT)), 'checked_at': source['checked_at'],
              'scope': 'public Round 3 snapshot; journal events up to snapshot cutoff; no causal lift estimate'}

    def shop_metrics(row):
        n, revenue = row.get('deals', 0), row.get('revenue', 0)
        return {'name': row['name'], 'deals': n, 'revenue': revenue, 'profit': row.get('profit'),
                'revenue_per_deal': revenue / n if n else None,
                'profit_to_revenue': row['profit'] / revenue if revenue else None,
                'deals_per_visit': n / row['visits'] if row.get('visits') else None,
                'stars_rank': row.get('stars_rank'), 'all': row.get('all')}

    def buyer_metrics(row):
        n = row.get('deals', 0)
        return {'name': row['name'], 'orders': row.get('orders'), 'deals': n, 'surplus': row.get('surplus'),
                'surplus_per_deal': row['surplus'] / n if n else None,
                'deals_per_order': n / row['orders'] if row.get('orders') else None,
                'forum': row.get('forum'), 'posts': row.get('posts'), 'all': row.get('all')}

    result['shop_comparison'] = [shop_metrics(own_shop)] + [shop_metrics(r) for r in sorted(
        [r for r in board['shops'] if r.get('deals', 0) > 0], key=lambda r: r.get('all') or 0, reverse=True)[:5]]
    result['buyer_comparison'] = [buyer_metrics(own_buyer)] + [buyer_metrics(r) for r in sorted(
        [r for r in board['buyers'] if r.get('deals', 0) > 0], key=lambda r: r.get('all') or 0, reverse=True)[:5]]

    purchases = events['purchase']
    gaps = [(b['_ts'] - a['_ts'], a['_ts'], b['_ts']) for a, b in zip(purchases, purchases[1:])]
    gap, last_before, first_after = max(gaps) if gaps else (0, cutoff, cutoff)
    resumed_order = next((r['order'] for r in purchases if r['_ts'] == first_after), None)
    recovery_order_start = next((r['_ts'] for r in events['buyer_order'] if r['order'] == resumed_order), first_after)
    outage = datetime(2026, 11, 4, 1, 43, 21, tzinfo=KST).timestamp()
    result['purchase_gap'] = {'hours': gap / 3600, 'last_before': datetime.fromtimestamp(last_before, KST).isoformat(),
                              'first_after': datetime.fromtimestamp(first_after, KST).isoformat(),
                              'error_onset_source': 'logs/offline-incident-20261104T032618306392Z.json'}
    selected_orders = {p['context']['order']['order'] for p in events['prediction']
                       if p.get('role') == 'buyer' and 'order' in p.get('context', {})
                       and p.get('candidates') and 'shop' in p['candidates'][0]}
    agreed = {e['order'] for e in events['agreement']}
    paid = {e['order'] for e in purchases}
    terminal = {e['order'] for e in events['market_result']}
    phases = [('before_error', 0, outage), ('error_to_first_resumed_order', outage, recovery_order_start),
              ('from_first_resumed_order', recovery_order_start, cutoff + 1)]
    result['funnel_by_phase'] = {}
    for phase, start, end in phases:
        ids = {e['order'] for e in events['buyer_order'] if start <= e['_ts'] < end}
        result['funnel_by_phase'][phase] = {'received_orders': len(ids), 'terminal_orders': len(ids & terminal),
            'orders_with_shop_selection': len(ids & selected_orders), 'agreements': len(ids & agreed),
            'paid': len(ids & paid), 'paid_per_received': len(ids & paid) / len(ids) if ids else None}
    reasons = Counter()
    for row in events['payment']:
        reason = row.get('reason', '')
        group = ('paid' if row.get('paid') else 'food_shelf_life' if 'shelf life' in reason else
                 'certificate' if 'certificate' in reason else 'warranty' if 'warranty' in reason else
                 'other_or_unknown')
        reasons[group] += 1
    result['payment_attempts'] = dict(reasons)
    result['attempt_events'] = dict(Counter(e.get('reason') or e.get('kind') for e in events['attempt']))

    counters = defaultdict(list)
    for event in events['buyer_counter']:
        counters[(event['order'], event['shop'])].append(event)
    repeated = [rows for rows in counters.values() if len(rows) > 1 and
                any(a['price'] == b['price'] for a, b in zip(rows, rows[1:]))]
    result['repeated_counters'] = {'negotiations_with_counters': len(counters),
        'negotiations_with_adjacent_repeat': len(repeated),
        'samples': [{'order': rows[0]['order'], 'prices': [r['price'] for r in rows],
                     'asks': [r.get('ask') for r in rows]} for rows in repeated[-4:]]}

    def mix(rows, buyer=None):
        group = defaultdict(lambda: {'deals': 0, 'amount': 0, 'list_minus_paid_before_quality': 0})
        for e in rows:
            if buyer is not None and e.get('buyer') != buyer:
                continue
            product = catalog.get(e.get('sku') or e.get('item'), {})
            if not product:
                continue
            key = product['category']
            price = e.get('amount', e.get('price', 0))
            group[key]['deals'] += 1
            group[key]['amount'] += price
            group[key]['list_minus_paid_before_quality'] += product['list_price'] - price
        return sorted([dict(category=k, **v) for k, v in group.items()], key=lambda r: r['amount'], reverse=True)

    result['market_category_mix'] = mix(events['public_settle'])
    result['own_shop_category_mix'] = mix(events['shop_settled'])
    result['buyer_public_category_mix'] = {name: mix(events['public_settle'], name) for name in
                                         ['seochanit', 'inextro', 'myeonggi', 'mjforge']}
    result['buyer_public_coverage'] = {name: sum(r['deals'] for r in rows)
                                      for name, rows in result['buyer_public_category_mix'].items()}
    result['own_shop_kind_mix'] = dict(Counter(catalog.get(e.get('sku'), {}).get('kind', 'unknown')
                                             for e in events['shop_settled']))
    result['own_buyer_kind_mix'] = dict(Counter(e.get('kind') for e in purchases))
    # Buyer favourite categories are hidden and assigned by the market. Reconstruct
    # our offered orders from the preserved kit log rather than assuming peer mix.
    order_ids = {e['order'] for e in events['buyer_order']}
    order_time = {e['order']: e['_ts'] for e in events['buyer_order']}
    requested = {}
    with (ROOT / 'logs/market-kit.log').open(encoding='utf-8', errors='replace') as log:
        for line in log:
            match = re.search(r'\[order ([^\]]+)\] (.*), budget [\d.]+, round 3\b', line)
            if match and match[1] in order_ids:
                requested[match[1]] = match[2]
    order_mix = defaultdict(lambda: {'received': 0, 'paid': 0, 'before_error_received': 0, 'before_error_paid': 0})
    for oid, leaf in requested.items():
        row = order_mix[leaf]
        row['received'] += 1
        row['paid'] += oid in paid
        if order_time[oid] < outage:
            row['before_error_received'] += 1
            row['before_error_paid'] += oid in paid
    result['own_offered_order_categories'] = sorted([dict(category_leaf=k, **v) for k, v in order_mix.items()],
                                                    key=lambda r: r['received'], reverse=True)
    result['offered_order_log_coverage'] = {'matched': len(requested), 'received': len(order_ids)}
    sku_benchmarks = []
    own_skus = Counter(e['sku'] for e in purchases)
    for sku, n in own_skus.most_common(7):
        ours = [e['price'] for e in purchases if e['sku'] == sku]
        others = [e['amount'] for e in events['public_settle']
                  if (e.get('sku') or e.get('item')) == sku and e.get('buyer') != 'seochanit' and e.get('amount')]
        if others:
            sku_benchmarks.append({'sku': sku, 'name': catalog.get(sku, {}).get('name'), 'own_n': n,
                'own_median_price': statistics.median(ours), 'other_n': len(others),
                'other_median_price': statistics.median(others),
                'not_adjusted_for_grade_expiry_warranty': True})
    result['same_sku_price_benchmarks'] = sku_benchmarks
    result['last_shop_sale'] = datetime.fromtimestamp(events['shop_settled'][-1]['_ts'], KST).isoformat()
    result['last_restock_decision'] = datetime.fromtimestamp(events['restock_decision'][-1]['_ts'], KST).isoformat()

    predictions = {e['decision']: e for e in events['prediction']}
    stages = defaultdict(Counter)
    selected_portfolios = Counter()
    for event in events['decision']:
        prediction = predictions.get(event['decision'], {})
        cs = prediction.get('candidates') or [{}]
        stage = ('restock' if 'buy' in cs[0] else 'shop_quote' if prediction.get('role') == 'shop' else
                 'buyer_shop_choice' if 'shop' in cs[0] else 'buyer_negotiation')
        stages[stage][event['mode']] += 1
        if stage == 'restock': selected_portfolios[event['chosen']] += 1
    result['decision_modes_by_stage'] = {k: dict(v) for k, v in stages.items()}
    result['restock_portfolios'] = dict(selected_portfolios)
    result['model_fallback_reasons'] = dict(Counter(e['error'] for e in events['decision'] if e.get('error')))
    usage = defaultdict(lambda: {'calls': 0, 'estimated_usd': 0, 'roles': Counter()})
    for event in events['model_usage']:
        day = datetime.fromtimestamp(event['_ts'], timezone.utc).date().isoformat()
        usage[day]['calls'] += 1
        usage[day]['estimated_usd'] += event.get('estimated_usd', 0)
        usage[day]['roles'][event['role']] += 1
    result['model_usage_by_utc_day'] = {k: dict(v, roles=dict(v['roles'])) for k, v in usage.items()}
    result['strategy_changes'] = [{'at': datetime.fromtimestamp(e['_ts'], KST).isoformat(), 'role': e['_role'],
        'action': e['action'], 'before_patch': e['before'].get('patch'), 'after_patch': e['after'].get('patch'),
        'evidence': e.get('evidence')} for e in events['strategy_change']]

    posts = source['forum']
    result['forum_snapshot'] = {'posts': len(posts), 'roles': dict(Counter(p.get('role') for p in posts)),
        'buyer_wanted_count': sum(p.get('role') == 'buyer' and bool(re.search(
            r'\b(want|wanted|need|looking for)\b|구매 희망|구합니다|찾습니다|필요', p.get('text', ''), re.I)) for p in posts),
        'own_posts': [{k: p.get(k) for k in ['id','text','likes','dislikes','score']} for p in posts
                      if p.get('author') == 'seochanit'],
        'examples': [{k: p.get(k) for k in ['id','author','role','text','likes','dislikes','score']} for p in posts
                     if p.get('id') in [4095,4098,4101,4099]]}
    days = max(0, (datetime.fromisoformat('2026-11-17T23:59:00+09:00').timestamp() - cutoff) / 86400)
    food = max(.01, .85 - .60) / .75
    goods = max(.01, .85 * .85 - .69 * (1 + .01 * days)) / .75
    result['restock_score_factor'] = {'remaining_days': days, 'food': food, 'goods': goods,
        'food_to_goods': food / goods, 'meaning': 'equal other signals; code prior, not realized returns'}
    result['validation'] = {
        'order_cohorts_reconcile_to_board': sum(r['received_orders'] for r in result['funnel_by_phase'].values()) == own_buyer['orders'],
        'purchase_cohorts_reconcile_to_board': sum(r['paid'] for r in result['funnel_by_phase'].values()) == own_buyer['deals'],
        'shop_category_revenue_reconciles_to_board': sum(r['amount'] for r in result['own_shop_category_mix']) == own_shop['revenue'],
        'all_own_orders_have_preserved_log_category': len(requested) == len(order_ids),
        'own_public_settlement_count_reconciles': result['buyer_public_coverage']['seochanit'] == own_buyer['deals']}
    if not all(result['validation'].values()):
        raise ValueError('Source reconciliation changed; review incomplete/late records before reporting')
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.snapshot.resolve())
    path = ROOT / 'logs' / ('performance-analysis-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    atomic_json(path, report)
    print('OUTPUT', path)
    print(json.dumps({k: report[k] for k in ['funnel_by_phase','purchase_gap','payment_attempts',
        'repeated_counters','own_shop_kind_mix','own_buyer_kind_mix','restock_portfolios',
        'decision_modes_by_stage','restock_score_factor','last_shop_sale','last_restock_decision']},
        ensure_ascii=False, indent=2))
