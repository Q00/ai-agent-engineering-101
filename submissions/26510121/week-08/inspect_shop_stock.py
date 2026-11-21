"""Read-only inventory reconciliation: actual supply console, paid sales and snapshots.

No model calls, market mutations, journal writes or strategy changes.
"""
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import re
import sqlite3
import time
from r3.state import ROOT, read_json, policy
from r3.economy import cash_capacity
from autonomous_status import ReadJournal

KST = timezone(timedelta(hours=9))


def main():
    now = time.time()
    inv_path = ROOT / '.market/inventory.json'
    inv = read_json(inv_path, {})
    cutoff = min(now, inv_path.stat().st_mtime)
    boundary = datetime(2026, 11, 5, 16, 5, tzinfo=KST).timestamp()
    logs = ['logs/auto-join-20261105T052404366665Z.txt',
            'logs/auto-join-20261105T074600934307Z.txt']
    supply = []
    for name in logs:
        # Numeric ASCII fields remain parseable independently of console encoding.
        for line in (ROOT / name).read_text(encoding='utf-8', errors='replace').splitlines():
            m = re.search(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x .* for (\d+), balance (\d+)', line)
            if m:
                at = datetime.fromisoformat('2026-11-05T' + m[1]).replace(tzinfo=KST).timestamp()
                if boundary <= at <= cutoff:
                    supply.append({'at': at, 'time_kst': m[1], 'qty': int(m[2]),
                                   'cost': int(m[3]), 'source': name})
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as c:
        c.execute('BEGIN')
        j = ReadJournal(c, cutoff)
        timeline = j.get('merchant_stock_timeline', [])
        baseline = max((r for r in timeline if r['at'] < boundary), key=lambda r: r['at'])
        sales = [e for e in j.events('shop_settled', 10000) if baseline['at'] < e['_ts'] <= cutoff]
        blocked = [e for e in j.events('restock_blocked', 100) if boundary < e['_ts'] <= cutoff]
        units = inv.get('units', [])
        counts = Counter(u['sku'] for u in units)
        initial = sum(baseline['counts'].values())
        added = sum(r['qty'] for r in supply)
        # Residual can include expiry, disposal, unobserved transfers, late records.
        # Do not call it waste without direct evidence.
        residual = initial + added - len(sales) - len(units)
        records = [{'time_kst': datetime.fromtimestamp(e['_ts'], KST).strftime('%H:%M:%S'),
                    'sku': e['sku'], 'price': e['price'], 'cost': e.get('cost'),
                    'strategy_version': e.get('strategy_version')} for e in sales]
        stock = [{'sku': sku, 'name': next(u['name'] for u in units if u['sku'] == sku),
                  'qty': qty, 'cost': sum(u['cost'] for u in units if u['sku'] == sku),
                  'age_hours': [round((cutoff-u['supplied_at'])/3600, 2) for u in units if u['sku'] == sku]}
                 for sku, qty in counts.items()]
        result = {'checked_kst': datetime.fromtimestamp(now, KST).isoformat(),
                  'inventory_cutoff_kst': datetime.fromtimestamp(cutoff, KST).isoformat(),
                  'reconciliation': {'baseline_kst': datetime.fromtimestamp(baseline['at'], KST).isoformat(),
                                     'initial_units': initial, 'actual_supplied': added,
                                     'paid_sales': len(sales), 'current_units': len(units),
                                     'unexplained_net_reduction': residual},
                  'actual_supply': supply, 'sales': records, 'stock': stock,
                  'stock_cost': sum(u['cost'] for u in units), 'cash': inv['balance'],
                  'cash_capacity': cash_capacity(inv, j, policy(), cutoff),
                  'supply_window': inv['restock'],
                  'blocked_restock': [{'time_kst': datetime.fromtimestamp(e['_ts'], KST).strftime('%H:%M:%S'),
                                       'reason': e['reason']} for e in blocked],
                  'control': {k: j.get('market_control', {}).get(k) for k in ['at', 'status', 'reasons']},
                  'health': read_json(ROOT/'.market/shop-health.json', {}),
                  'sources': {'inventory': str(inv_path), 'database': '.market/r3.sqlite3 (read-only snapshot)',
                              'console': logs},
                  'limitations': ['Inventory and market board are independently timed snapshots.',
                                  'Actual supply is successful console lines, not planned restock quantities.',
                                  'Zero net residual reconciles counts; offsetting unobserved movements are not ruled out.']}
    path = ROOT/'logs'/('shop-stock-review-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SOURCE', path)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
