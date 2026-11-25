"""Read-only diagnosis: historical outage debt versus post-recovery performance.

Uses frozen board snapshots for matched-time peer comparisons, and a read-only
SQLite transaction for own settlement/refund reconciliation. No model calls,
trading, policy changes or operational journal writes.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import statistics
import sys

ROOT = Path(__file__).resolve().parent
OWNER = 'seochanit'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rank(rows, metric, own):
    valid = [r for r in rows if type(r.get(metric)) in (int, float)]
    return {'rank': 1 + sum(r[metric] > own[metric] for r in valid), 'n': len(valid)}


def timestamp(source):
    return datetime.fromisoformat(source['checked_at']).timestamp()


def compare(start, end, table):
    hours = (timestamp(end) - timestamp(start)) / 3600
    a = {r['name']: r for r in start['board'][table]}
    b = {r['name']: r for r in end['board'][table]}
    money = ['profit', 'revenue'] if table == 'shops' else ['surplus']
    activity = 'visits' if table == 'shops' else 'orders'

    def row(name):
        x, y = a[name], b[name]
        delta = {k: y.get(k, 0) - x.get(k, 0) for k in ['deals', activity] + money}
        return {'name': name, 'delta': delta, 'per_hour': {k: v / hours for k, v in delta.items()},
                'all_start': x['all'], 'all_end': y['all'], 'all_delta': y['all'] - x['all']}

    # Endpoint-online is only an observable proxy, not full peer uptime.
    peers = [n for n in a.keys() & b.keys() if n != OWNER and
             not a[n].get('house') and not b[n].get('house') and
             a[n].get('alive') and b[n].get('alive') and a[n].get('deals', 0) >= 20 and
             b[n].get(activity, 0) > a[n].get(activity, 0)]
    rows = [row(n) for n in peers]
    return {'hours': hours, 'own': row(OWNER),
            'leaders': [row(n) for n in ['inextro', 'yms979', 'dae-yangkim', 'psyoung82'] if n in a and n in b],
            'matched_active_peers': len(rows),
            'median_peer_per_hour': {k: statistics.median(r['per_hour'][k] for r in rows)
                                     for k in ['deals', activity] + money} if rows else {},
            'cohort_note': '>=20 initial deals, online at both endpoints, positive visit/order growth; continuous peer uptime unknown'}


def analyze(source_path):
    latest = read(source_path)
    cutoff = timestamp(latest)
    recovered = read(ROOT / 'logs/market-analysis-source-20261104T033721293560Z.json')
    recent = read(ROOT / 'logs/luna-final-progress-source-20261104T082818563031Z.json')
    since = timestamp(recent)
    result = {'source': str(Path(source_path).resolve().relative_to(ROOT)),
              'checked_at': latest['checked_at'],
              'scope': 'observed post-recovery performance and accounting; no causal outage-loss estimate',
              'windows': {}}
    for label, start in [('since_recovery_snapshot', recovered), ('recent_normal_operation', recent)]:
        result['windows'][label] = {'start': start['checked_at'], 'end': latest['checked_at'],
                                   'shops': compare(start, latest, 'shops'),
                                   'buyers': compare(start, latest, 'buyers')}
    result['current'] = {}
    for table, metrics in [('shops', ['all', 'profit', 'revenue', 'stars_rank', 'deals']),
                           ('buyers', ['all', 'surplus', 'deals', 'forum', 'posts'])]:
        rows = [r for r in latest['board'][table] if r.get('deals', 0) > 0]
        own = next(r for r in rows if r['name'] == OWNER)
        result['current'][table] = {'own': {k: own.get(k) for k in ['name', 'alive'] + metrics},
            'ranks': {k: rank(rows, k, own) for k in metrics},
            'median': {k: statistics.median(r[k] for r in rows if type(r.get(k)) in (int, float)) for k in metrics}}
    players = [r for r in latest['board']['players'] if not r.get('house') and type(r.get('total')) in (int, float)]
    own = next(r for r in players if r.get('player', '').casefold() == OWNER)
    result['current']['players'] = dict(own, rank=rank(players, 'total', own))

    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as c:
        c.execute('BEGIN')
        kinds = ['shop_settled', 'public_broken', 'purchase', 'buyer_order', 'buyer_execution',
                 'market_result', 'attempt', 'shop_opportunity', 'stockout', 'restock_decision', 'public_settle']
        events = {k: [dict(json.loads(d), _id=i, _ts=t) for i, t, d in c.execute(
            'SELECT id,ts,data FROM events WHERE kind=? AND ts<=? ORDER BY ts,rowid', (k, cutoff))] for k in kinds}
    catalog = read(ROOT / '.market/catalog.json')
    paid = events['shop_settled']
    by_unit = {r['unit']: r for r in paid}
    own_broken = [r for r in events['public_broken'] if r.get('unit') in by_unit]
    new_sales = [r for r in paid if since < r['_ts'] <= cutoff]
    new_refunds = [r for r in own_broken if since < r['_ts'] <= cutoff and r.get('refunded', 0) > 0]
    gross = sum(r['price'] for r in new_sales)
    refunds = sum(r['refunded'] for r in new_refunds)
    net = result['windows']['recent_normal_operation']['shops']['own']['delta']
    result['shop_recent_accounting'] = {'new_settled_sales': len(new_sales), 'gross_sales': gross,
        'refunds_count': len(new_refunds), 'refunds': refunds,
        'observed_net_revenue_delta': net['revenue'], 'observed_profit_delta': net['profit'],
        'revenue_residual': net['revenue'] - (gross - refunds),
        'profit_residual': net['profit'] - (gross - refunds),
        'new_sale_kinds': dict(Counter(catalog.get(r['sku'], {}).get('kind', 'unknown') for r in new_sales)),
        'refund_records': [dict(r, original_sale=by_unit[r['unit']]) for r in new_refunds]}
    assert result['shop_recent_accounting']['revenue_residual'] == 0, 'Board versus journal revenue mismatch'
    assert len(new_sales) == net['deals'], 'Board versus journal settlement mismatch'

    inv = latest['inventory']
    policy = read(ROOT / 'policy.json')
    liabilities = [r for r in paid if cutoff - r.get('paid_at', 0) < (r.get('warranty_hours') or 0) * 3600]
    liability = sum(r['price'] for r in liabilities)
    reserved_units = {r['unit'] for r in liabilities}
    refunded_units = {r['unit']: r for r in own_broken if r.get('refunded', 0) > 0}
    duplicate = sum(by_unit[u]['price'] for u in reserved_units & refunded_units.keys())
    balance = inv['balance']
    floor = max(policy['cash_floor'], balance * policy['cash_reserve_fraction'])
    from r3.finance import reserve_days
    days = reserve_days(policy, cutoff)
    storage = sum(u.get('cost', 0) for u in inv.get('units', []) if u.get('kind') == 'goods') * .01 * days
    result['cash_and_inventory'] = {'balance': balance, 'floor': floor, 'reserved_gross_warranty': liability,
        'already_refunded_still_reserved': duplicate, 'storage_to_end': storage,
        'current_spendable': max(0, balance - floor - liability - storage),
        'spendable_if_confirmed_refunds_are_released': max(0, balance - floor - (liability - duplicate) - storage),
        'inventory_units': len(inv.get('units', [])),
        'inventory_kinds': dict(Counter(u['kind'] for u in inv.get('units', []))),
        'inventory_acquisition_cost': sum(u['cost'] for u in inv.get('units', [])),
        'last_restock_plan_at': datetime.fromtimestamp(events['restock_decision'][-1]['_ts'], timezone(timedelta(hours=9))).isoformat(),
        'recent_restock_plans': sum(r['_ts'] > since for r in events['restock_decision'])}
    orders = {r['order'] for r in events['buyer_order'] if since <= r['_ts'] <= cutoff - 120}
    purchases = {r['order'] for r in events['purchase']}
    terminal = {r['order'] for r in events['market_result']}
    result['recent_buyer_funnel'] = {'orders_older_than_120_seconds': len(orders),
        'terminal': len(orders & terminal), 'paid': len(orders & purchases),
        'paid_per_received': len(orders & purchases) / len(orders) if orders else None,
        'attempt_events': dict(Counter(r.get('reason') or r.get('kind') for r in events['attempt'] if r.get('order') in orders))}
    post = [r for r in events['purchase'] if r['_ts'] >= timestamp(recovered)]
    gaps = [(b['_ts'] - a['_ts']) / 60 for a, b in zip(post, post[1:])]
    result['post_recovery_activity'] = {'purchases': len(post), 'max_inter_purchase_gap_minutes': max(gaps) if gaps else None,
                                       'health_at_cutoff': latest['health']}
    result['historic_outage'] = read(ROOT / 'logs/performance-analysis-20261104T040052537258Z.json')['purchase_gap']
    # A deliberately simple illustration, not an identified counterfactual:
    # no compounding, market-time/category changes, or assignment effects.
    lost_hours = result['historic_outage']['hours']
    result['same_recent_pace_outage_scenario'] = {
        'lost_hours': lost_hours,
        'buyer_surplus_at_recent_rate': lost_hours * result['windows']['recent_normal_operation']['buyers']['own']['per_hour']['surplus'],
        'warning': 'not a causal outage loss estimate; rates, categories, reinvestment and peers would differ'}
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    result = analyze(args.snapshot)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = ROOT / 'logs' / ('recovery-gap-analysis-' + stamp + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('ANALYSIS', path)
