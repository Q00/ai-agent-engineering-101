"""Read-only inventory/control snapshot; never invokes planners or market APIs."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

from r3.shop_growth import inventory_limit, target_stock

ROOT = Path(__file__).resolve().parent


def main():
    now = time.time()
    def snapshot(name):
        path = ROOT / '.market' / name
        return json.loads(path.read_text(encoding='utf-8-sig')), path.stat().st_mtime
    inv, inv_at = snapshot('inventory.json')
    board, board_at = snapshot('board.json')
    cfg = json.loads((ROOT / 'policy.json').read_text(encoding='utf-8-sig'))
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        def kv(key):
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else {}
        control = kv('market_control')
        strategy = kv('adaptive:shop')
        effective_cfg = dict(cfg, **strategy.get('patch', {}))
        plans = [dict(json.loads(data), at=at) for at, data in db.execute(
            'SELECT ts,data FROM events WHERE kind=? ORDER BY ts DESC LIMIT 12',
            ('restock_decision',))]
        blocked = Counter()
        for kind in ('restock_blocked', 'supply_blocked'):
            for data, in db.execute('SELECT data FROM events WHERE kind=? AND ts>=? AND ts<=?',
                                   (kind, now - 21600, now)):
                blocked[kind + ':' + json.loads(data).get('reason', 'unknown')] += 1
        sales = [json.loads(data) for data, in db.execute(
            'SELECT data FROM events WHERE kind=? AND ts>=? AND ts<=?',
            ('shop_settled', now - 21600, now))]
        checked_at = kv('board_checked_at')
    units = inv.get('units', [])
    own = next((r for r in board.get('shops', []) if r.get('name') == 'seochanit'), {})
    result = {
        'checked_at_utc': datetime.fromtimestamp(now, timezone.utc).isoformat(),
        'mode': 'local_read_only_no_market_or_model_calls',
        'inventory': {
            'file_mtime_utc': datetime.fromtimestamp(inv_at, timezone.utc).isoformat(),
            'snapshot_age_seconds': now - inv_at, 'cash': inv.get('balance'),
            'units': len(units), 'skus': len({u['sku'] for u in units}),
            'stock_cost': sum(u.get('cost', 0) for u in units),
            'sku_counts': dict(Counter(u['sku'] for u in units)),
        },
        'board': {'file_mtime': board_at, 'last_checked_at': checked_at,
                  'own': {k: own.get(k) for k in ('book', 'profit', 'all', 'revenue', 'net_revenue', 'deals', 'stars_rank', 'alive')}},
        'control': {k: control.get(k) for k in ('at', 'status', 'pause_restock', 'reasons', 'cash_capacity')},
        'shop_adaptive': strategy,
        'effective_target': effective_cfg['inventory_target_units'],
        'effective_ceiling': inventory_limit(effective_cfg),
        'target_parameter_effect': [
            {'inventory_target_units': n, 'active_ceiling': inventory_limit(dict(effective_cfg, inventory_target_units=n))}
            for n in (60, 65, 90)
        ],
        'sku_target_examples_not_demand_estimates': [
            {'arrivals_per_hour': rate, 'positive_paid_count': paid,
             'desired_units': target_stock(rate, paid, effective_cfg)}
            for rate in (1, 5, 10, 20) for paid in (0, 3)
        ],
        'recent_restock_plans_not_actual_purchases': plans,
        'blocked_event_counts_last_six_hours_not_outage_duration': dict(blocked),
        'sales_last_six_hours': {'count': len(sales),
            'revenue': sum(e.get('price', 0) for e in sales),
            'known_cost_sales': sum(type(e.get('cost')) in (int, float) for e in sales),
            'gross_margin_before_refunds_storage_expiry': sum(e['price'] - e['cost'] for e in sales if type(e.get('cost')) in (int, float))},
        'limits': ['Snapshots may have different timestamps.',
                   'No full candidate rejection trace was reconstructed; ranking the causes requires it.',
                   'Six-hour realized sales are not a controlled inventory-policy comparison.',
                   'Changing an inventory ceiling does not itself prove more sales or higher Book.'],
    }
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = ROOT / 'logs' / f'inventory-controls-{stamp}.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
