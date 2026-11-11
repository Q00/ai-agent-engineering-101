"""Read-only live timing audit and isolated, nontrading hot-path profiles.

Uses a read-only SQLite transaction. Replayed writes stay in memory; no planner
execution, market calls, model calls, production journal writes or payments.
"""
from collections import Counter, defaultdict
import cProfile
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pstats
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def stats(values):
    values = sorted(v for v in values if type(v) in (int, float) and v >= 0)
    def percentile(p):
        if not values:
            return None
        pos = (len(values) - 1) * p
        a = int(pos)
        return values[a] + (values[min(a + 1, len(values) - 1)] - values[a]) * (pos - a)
    return {'n': len(values), 'p50': percentile(.5), 'p95': percentile(.95),
            'p99': percentile(.99), 'max': values[-1] if values else None}


class ShadowJournal:
    def __init__(self, connection, cutoff, files):
        self.c, self.cutoff, self.files = connection, cutoff, files
        self.md = ROOT / '.market'
        self.memory, self.calls = {}, Counter()
        self.rows, self.seconds = Counter(), Counter()

    def get(self, key, default=None):
        self.calls['get:' + key.split(':')[0]] += 1
        if key in self.memory:
            return self.memory[key]
        row = self.c.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.memory[key] = value
        self.calls['shadow_put'] += 1

    def events(self, kind, limit=500):
        started = time.perf_counter()
        rows = [dict(json.loads(d), _event_id=i, _ts=t, _role=r) for i, t, r, d in self.c.execute(
            'SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',
            (kind, self.cutoff, limit))]
        self.calls['events:' + kind] += 1
        self.rows[kind] += len(rows)
        self.seconds[kind] += time.perf_counter() - started
        return rows

    def snapshot(self, name, default=None):
        self.calls['snapshot:' + name] += 1
        return self.files.get(name, default)

    def add(self, *args, **kwargs):
        self.calls['shadow_add'] += 1

    def add_batch(self, rows, **kwargs):
        self.calls['shadow_batch_rows'] += len(rows)


def main():
    cutoff = time.time()
    since = cutoff - 21600
    cfg = json.loads((ROOT / 'policy.json').read_text(encoding='utf-8-sig'))
    files, metadata = {}, {}
    for name in ('catalog.json', 'board.json', 'inventory.json', 'registry.json', 'shops.json',
                 'order.json', 'forum.json', 'market_stock_counts.json', 'runtime.json', 'shop-health.json'):
        path = ROOT / '.market' / name
        raw = path.read_bytes()
        files[name] = json.loads(raw.decode('utf-8-sig'))
        metadata[name] = {'mtime': path.stat().st_mtime, 'bytes': len(raw),
                          'sha256': hashlib.sha256(raw).hexdigest()}
    kinds = ['buyer_order', 'buyer_preparation', 'buyer_saved_execution', 'shop_fast_execution',
             'attempt', 'buyer_quote_latency', 'buyer_quote_race', 'agreement', 'purchase',
             'market_result', 'shop_opportunity', 'shop_quote', 'model_usage', 'market_control']
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        events = {kind: [dict(json.loads(d), _ts=t) for t, d in db.execute(
            'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts',
            (kind, since, cutoff))] for kind in kinds}
        orders = {r['order']: r for r in events['buyer_order']}
        preparation = {}
        for kind in ('food', 'goods'):
            rows = [r for r in events['buyer_preparation'] if orders.get(r.get('order'), {}).get('kind') == kind]
            preparation[kind] = {'seconds': stats([r.get('seconds') for r in rows]),
                                 'candidate_counts': stats([r.get('candidates') for r in rows]),
                                 'over_one_second': sum(r.get('seconds', 0) >= 1 for r in rows)}
        observed = {'window_start_utc': datetime.fromtimestamp(since, timezone.utc).isoformat(),
            'window_end_utc': datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
            'event_counts': {k: len(v) for k, v in events.items()}, 'buyer_preparation': preparation,
            'buyer_chooser_seconds': stats([r.get('seconds') for r in events['buyer_saved_execution']]),
            'shop_chooser_seconds': stats([r.get('seconds') for r in events['shop_fast_execution']]),
            'shop_chooser_by_operation': {}, 'quote_rpc_seconds': stats([r.get('seconds') for r in events['buyer_quote_latency']]),
            'attempt_counts': dict(Counter(r.get('reason') or r.get('kind') for r in events['attempt'])),
            'attempt_seconds_by_kind': {},
            'quote_race': {'races': len(events['buyer_quote_race']),
                'strong_offers': sum(bool(r.get('strong_offer')) for r in events['buyer_quote_race']),
                'pending_discarded': sum(r.get('pending_discarded', 0) for r in events['buyer_quote_race'])}}
        for operation in {r.get('operation', 'quote') for r in events['shop_fast_execution']}:
            observed['shop_chooser_by_operation'][operation] = stats([r.get('seconds') for r in events['shop_fast_execution'] if r.get('operation', 'quote') == operation])
        for kind in {r.get('kind') for r in events['attempt']}:
            observed['attempt_seconds_by_kind'][str(kind)] = stats([r.get('seconds') for r in events['attempt'] if r.get('kind') == kind])
        first_agreement = {}
        for row in events['agreement']:
            first_agreement.setdefault(row['order'], row['_ts'])
        observed['agreement_to_recorded_purchase_seconds'] = stats([
            r.get('paid_at', r['_ts']) - first_agreement[r['order']] for r in events['purchase'] if r['order'] in first_agreement])
        first_result = {}
        for row in events['market_result']:
            first_result.setdefault(row['order'], row)
        observed['buyer_order_record_to_result_seconds'] = {
            kind: stats([first_result[oid]['_ts'] - row['_ts'] for oid, row in orders.items()
                         if row.get('kind') == kind and oid in first_result]) for kind in ('food', 'goods')}
        opportunities = {r['task']: r for r in events['shop_opportunity']}
        first_quote = {}
        for row in events['shop_quote']:
            first_quote.setdefault(row['task'], row['_ts'])
        observed['shop_opportunity_to_first_quote_seconds'] = stats([
            first_quote[task] - row['_ts'] for task, row in opportunities.items() if task in first_quote])
        observed['logging_bytes'] = {}
        for kind in ('prediction', 'customer_turn', 'decision', 'shop_fast_execution', 'buyer_saved_execution'):
            count, total, biggest = db.execute(
                'SELECT COUNT(*),SUM(length(CAST(data AS BLOB))),MAX(length(CAST(data AS BLOB))) '
                'FROM events WHERE kind=? AND ts>=? AND ts<=?', (kind, since, cutoff)).fetchone()
            observed['logging_bytes'][kind] = {'events': count, 'total_bytes': total or 0, 'largest_bytes': biggest or 0}

        from r3.state import Journal
        from r3.economy import buyer_candidates, shop_candidates
        from r3.customer_memory import summary
        from r3 import market_memory, contracts
        profiles = {}
        def profile(label, function):
            journal = ShadowJournal(db, cutoff, files)
            profiler = cProfile.Profile()
            started = time.perf_counter()
            value = profiler.runcall(function, journal)
            seconds = time.perf_counter() - started
            rows = []
            for (filename, line, name), (cc, nc, own, cumulative, _) in pstats.Stats(profiler).stats.items():
                if 'week-08' not in filename and filename != '~':
                    continue
                filename = filename.replace(str(ROOT), '.')
                rows.append({'function': f'{filename}:{line}:{name}', 'calls': nc,
                             'own_seconds': own, 'cumulative_seconds': cumulative})
            profiles[label] = {'instrumented_seconds': seconds, 'output_rows': len(value) if isinstance(value, list) else None,
                'journal_calls': dict(journal.calls), 'decoded_rows_by_kind': dict(journal.rows),
                'event_read_seconds': dict(journal.seconds),
                'top_cumulative_functions': sorted(rows, key=lambda r: r['cumulative_seconds'], reverse=True)[:14]}
            return value
        # Freeze time inside this audit process only, so replayed policy windows agree.
        original_time = time.time
        try:
            time.time = lambda: cutoff
            profile('reconcile_shadow_no_db_writes', lambda j: Journal.reconcile(j, cutoff))
            market_memory._BOOK_CACHE.clear(); contracts._HISTORY.clear(); contracts._IDENTITIES.clear()
            order = files['order.json']
            profiles['buyer_input'] = {'order': order.get('order'), 'kind': order.get('kind'),
                'acceptable_skus': len(order.get('acceptable', [])), 'shops': len(files['shops.json']),
                'shops_file_is_not_older_than_order': metadata['shops.json']['mtime'] >= metadata['order.json']['mtime']}
            if profiles['buyer_input']['shops_file_is_not_older_than_order']:
                for label in ('buyer_candidates_cold', 'buyer_candidates_warm'):
                    profile(label, lambda j: buyer_candidates(order, files['shops.json'], files['catalog.json'], j, cfg))
            for kind in ('food', 'goods'):
                unit = next((u for u in files['inventory.json'].get('units', []) if u.get('kind') == kind), None)
                if unit:
                    market_memory._BOOK_CACHE.clear()
                    memory = profile('shop_memory_anonymous_' + kind, lambda j: summary(j, 'latency-audit-shadow', unit))
                    for suffix in ('cold', 'warm'):
                        profile('shop_candidates_' + kind + '_' + suffix,
                                lambda j: shop_candidates(unit, files['inventory.json'], files['catalog.json'], j, cfg, customer_memory=memory, now=cutoff))
        finally:
            time.time = original_time
    import_seconds = {}
    for name, command in [('empty_child', 'pass'), ('buyer_module_child', 'import market_buyer')]:
        values = []
        for _ in range(3):
            started = time.perf_counter()
            result = subprocess.run([sys.executable, '-c', command], cwd=ROOT, capture_output=True, timeout=15)
            if result.returncode:
                raise RuntimeError('Nontrading import benchmark failed: ' + name)
            values.append(time.perf_counter() - started)
        import_seconds[name] = {'samples': values, 'median': sorted(values)[1]}
    result = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'observed_live_timings': observed, 'isolated_profiles': profiles, 'child_startup_seconds': import_seconds,
        'source_files': metadata, 'database_bytes': (ROOT / '.market/r3.sqlite3').stat().st_size,
        'limits': ['Existing timers do not cover the full parent order/network/payment path.',
            'Order record to result starts inside the Buyer child, after parent discovery and process startup.',
            'Agreement to recorded purchase includes payment plus reconciliation/recording; it is not pure network latency.',
            'Shop opportunity to quote excludes work before the opportunity event and response transmission.',
            'Profiles are instrumented single-input samples, not live p95 or economic improvement evidence.',
            'Replay uses one read-only connection, frozen file objects and shadow writes; production opens many connections and writes records.',
            'Cold/warm labels concern the sampled process cache; no speedup candidate is deployed.',
            'Outcome-shadow writes are counted but not committed, so reconciliation timing understates actual write cost.',
            'Snapshot files and the SQL transaction are close in time, not an atomic market snapshot.']}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = ROOT / 'logs' / ('main-latency-audit-' + stamp + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SOURCE', path)
    print(json.dumps({'observed': observed, 'profiles': {k: {f: v for f, v in x.items() if f != 'top_cumulative_functions'} for k, x in profiles.items()},
                      'startup': import_seconds}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
