"""Compare baseline and staged decision outputs on the same bounded real facts.

All original SQL is read-only. Worker processes make no market/model calls and
keep derived writes in memory. Synthetic stock stress is explicitly labelled.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / '.market/v16-stage-20261111'


def worker(code, source, output):
    sys.path.insert(0, str(code))
    from r3.economy import shop_candidates, buyer_candidates
    from r3 import market_memory, contracts, long_memory, buyer_quality
    data = json.loads(source.read_text(encoding='utf-8'))
    class Facts:
        md = ROOT / '.market'
        def __init__(self, db):
            self.c, self.memory, self.decoded = db, {}, 0
        def get(self, key, default=None):
            if key in self.memory:
                return self.memory[key]
            raw = data['kv'].get(key)
            return json.loads(raw) if raw is not None else default
        def put(self, key, value): self.memory[key] = value
        def add(self, *a, **kw): pass
        def snapshot(self, name, default=None): return data['files'].get(name, default)
        def events(self, kind, limit=500):
            rows = data['events'].get(kind, [])[:limit]
            self.decoded += len(rows)
            return [dict(json.loads(d), _event_id=i, _ts=t, _role=r) for i, t, r, d in rows]
    rows = []
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        original_time = time.time
        try:
            time.time = lambda: data['cutoff']
            for case in data['cases']:
                market_memory._BOOK_CACHE.clear(); contracts._HISTORY.clear(); contracts._IDENTITIES.clear()
                long_memory._FACT_CACHE.clear(); buyer_quality._CACHE.clear()
                journal = Facts(db)
                started = time.perf_counter()
                if case['role'] == 'shop':
                    result = shop_candidates(case['unit'], data['files']['inventory.json'],
                        data['files']['catalog.json'], journal, data['config'], now=data['cutoff'])
                else:
                    result = buyer_candidates(case['order'], case['shops'], data['files']['catalog.json'], journal, data['config'])
                seconds = time.perf_counter() - started
                encoded = json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
                rows.append({'case': case['id'], 'kind': case['kind'], 'seconds': seconds,
                             'rows': len(result), 'decoded_rows': journal.decoded,
                             'sha256': hashlib.sha256(encoded).hexdigest()})
        finally:
            time.time = original_time
    output.write_text(json.dumps(rows, indent=2), encoding='utf-8')


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    source = STAGE / '.market' / ('parity-source-' + stamp + '.json')
    files = {name: json.loads((ROOT / '.market' / name).read_text(encoding='utf-8-sig'))
             for name in ('inventory.json', 'catalog.json', 'registry.json', 'board.json', 'shops.json', 'order.json')}
    cutoff = time.time()
    kinds = {'purchase': 10000, 'attempt': 2000, 'agreement': 3000, 'buyer_quote_latency': 1000,
             'buyer_contract_result': 6000, 'forum_observation': 1500, 'shop_opportunity': 10000,
             'shop_settled': 10000, 'shop_quote': 10000, 'public_settle': 20000, 'public_broken': 20000,
             'outcome': 10000, 'ambiguous_break': 2000}
    keys = ['market_rules', 'public_ledger_seq', 'public_feed_caught_up_at', 'market_control',
            'buyer_revision_epoch', 'buyer_offer_blocks', 'market-shop-identities', 'quality_risk_v4',
            'shop_quality_cohorts_v9', 'buyer_public_quality_v11', 'memory-refreshed-at',
            'memory-contract-facts:v2', 'shop-rating-shadow']
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        events = {kind: db.execute('SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? '
                  'ORDER BY ts DESC,rowid DESC LIMIT ?', (kind, cutoff, limit)).fetchall() for kind, limit in kinds.items()}
        kv = {}
        for key in keys:
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            if row: kv[key] = row[0]
    cases, groups = [], {}
    for unit in files['inventory.json'].get('units', []):
        if unit.get('expires_at') and unit['expires_at'] <= cutoff:
            continue
        group = (unit.get('kind'), unit.get('grade'))
        if groups.get(group, 0) >= 2:
            continue
        groups[group] = groups.get(group, 0) + 1
        cases.append({'id': 'unit-' + unit['unit'], 'role': 'shop', 'kind': unit['kind'], 'unit': unit})
    order_path, shops_path = ROOT / '.market/order.json', ROOT / '.market/shops.json'
    if shops_path.stat().st_mtime >= order_path.stat().st_mtime and files['order.json'].get('acceptable'):
        cases.append({'id': 'current-buyer', 'role': 'buyer', 'kind': files['order.json'].get('kind'),
                      'order': files['order.json'], 'shops': files['shops.json']})
    # Stress data uses real product/registry fields but does not assert live stock.
    skus = [s for s, p in files['catalog.json'].items() if p.get('kind') == 'goods'][:20]
    peers = list(files['registry.json'].items())[:15]
    shops = [{'url': url, 'name': row['name'], 'offers': [{'sku': sku, 'qty': 1} for sku in skus]} for url, row in peers]
    cases.append({'id': 'synthetic-goods-stock-stress', 'role': 'buyer', 'kind': 'goods',
        'order': {'order': 'nontrading-stress', 'kind': 'goods', 'acceptable': skus,
                  'budget': 10000000, 'expires': cutoff + 180}, 'shops': shops})
    data = {'cutoff': cutoff, 'files': files, 'events': events, 'kv': kv, 'cases': cases,
            'config': json.loads((ROOT / 'policy.json').read_text(encoding='utf-8-sig'))}
    source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    paths = {}
    for name, code in [('baseline', ROOT), ('candidate', STAGE)]:
        path = ROOT / 'logs' / ('v16-parity-' + name + '-' + stamp + '.json')
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', str(code),
                                 str(source), str(path)], cwd=ROOT, capture_output=True, timeout=180)
        if result.returncode:
            failure = ROOT / 'logs' / ('v16-parity-failure-' + name + '-' + stamp + '.txt')
            failure.write_bytes(result.stdout + result.stderr)
            raise RuntimeError('parity worker failed; see ' + str(failure))
        paths[name] = path
    baseline = json.loads(paths['baseline'].read_text())
    candidate = json.loads(paths['candidate'].read_text())
    pairs = [dict(case=a['case'], kind=a['kind'], same_output=a['sha256'] == b['sha256'],
                  baseline_seconds=a['seconds'], candidate_seconds=b['seconds'],
                  baseline_rows=a['decoded_rows'], candidate_rows=b['decoded_rows']) for a, b in zip(baseline, candidate)]
    result = {'source': str(source.relative_to(ROOT)), 'pairs': pairs,
        'all_outputs_identical': len(baseline) == len(candidate) and all(p['same_output'] for p in pairs),
        'total_baseline_seconds': sum(p['baseline_seconds'] for p in pairs),
        'total_candidate_seconds': sum(p['candidate_seconds'] for p in pairs),
        'network_or_model_calls': 0, 'deployed': False,
        'scope': 'Same frozen bounded evidence, one pass per input; no economic superiority or full network latency claim.'}
    path = ROOT / 'logs' / ('v16-parity-summary-' + stamp + '.json')
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('SOURCE', path)
    print(json.dumps(result, indent=2))
    if not result['all_outputs_identical']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', nargs=3, type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(*args.worker)
    else:
        main()
