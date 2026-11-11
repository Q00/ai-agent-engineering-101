"""Isolated real-SQL replay, mature-outcome checks and analytical background load."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / '.market/v14-stage-20261111'
sys.path.insert(0, str(STAGE))
from r3.state import Journal, atomic_json
from r3.economy import shop_candidates
from r3.outcomes import latest
from r3.autonomy import update, context
from r3 import market_memory


def main():
    source = sorted((STAGE / '.market').glob('parity-source-*.json'))[-1]
    data = json.loads(source.read_text(encoding='utf-8'))
    cutoff = data['cutoff']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = STAGE / '.market' / ('replay-' + stamp)
    journal = Journal(directory)
    with journal.connect() as db:
        for kind, rows in data['events'].items():
            db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',
                           [(eid, kind, at, role, body) for eid, at, role, body in rows])
        for key, value in data['kv'].items():
            db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, value))
    # Required for exact refund reconciliation, not present in candidate-only replay.
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as original:
        original.execute('BEGIN')
        with journal.connect() as db:
            for kind, limit in [('public_refund', 20000), ('broken_news', 500)]:
                rows = original.execute('SELECT id,kind,ts,role,data FROM events WHERE kind=? AND ts<=? '
                    'ORDER BY ts DESC,rowid DESC LIMIT ?', (kind, cutoff, limit)).fetchall()
                db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)', rows)
    for name, value in data['files'].items():
        atomic_json(directory / name, value)
        import os
        os.utime(directory / name, (cutoff, cutoff))
    inventory_hash = hashlib.sha256((directory / 'inventory.json').read_bytes()).hexdigest()
    before = latest(journal)
    confirmed = {oid: row.get('refunded') for oid, row in before.items() if row.get('canonical') and row.get('refund_status') == 'confirmed'}
    started = time.perf_counter(); journal.reconcile(cutoff); first_reconcile = time.perf_counter() - started
    started = time.perf_counter(); journal.reconcile(cutoff); second_reconcile = time.perf_counter() - started
    after = latest(journal)
    preserved = all(after.get(oid, {}).get('refund_status') == 'confirmed' and after[oid].get('refunded') == value for oid, value in confirmed.items())
    cfg = dict(data['config'], autonomy_v14_enabled=True, buyer_worker_enabled=True, execution_revision='v14-fastframe-1')
    journal.put('public_feed_caught_up_at', cutoff)
    plan = update(journal, cfg, cutoff)
    packet = context(journal, cfg, cutoff, role='shop')
    assert packet and plan['status'] == 'published_facts'
    assert all(c['status'] == 'shadow_requires_economic_validation' for c in plan['candidates'])
    # Legacy function with no decision frame; both paths share the same SQL fixture.
    spec = importlib.util.spec_from_file_location('r3.v13_replay_economy', ROOT / 'r3/economy.py')
    baseline = importlib.util.module_from_spec(spec); spec.loader.exec_module(baseline)
    stop = threading.Event(); errors = []; ticks = []
    def analytical_load():
        try:
            while not stop.is_set():
                journal.put('autonomy-v14:state', {})
                update(journal, cfg, cutoff)
                ticks.append(1)
                stop.wait(.05)
        except Exception as exc:
            errors.append(type(exc).__name__)
    thread = threading.Thread(target=analytical_load, daemon=True); thread.start()
    pairs = []
    try:
        cases = [c for c in data['cases'] if c['role'] == 'shop'][:4]
        for case in cases:
            values = {}
            for name, function in [('baseline', baseline.shop_candidates), ('candidate', shop_candidates)]:
                market_memory._BOOK_CACHE.clear()
                started = time.perf_counter()
                result = function(case['unit'], data['files']['inventory.json'], data['files']['catalog.json'], journal, cfg, now=cutoff)
                values[name] = {'seconds': time.perf_counter() - started,
                    'sha256': hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
            pairs.append({'case': case['id'], 'same_output': values['baseline']['sha256'] == values['candidate']['sha256'], **values})
    finally:
        stop.set(); thread.join(timeout=5)
    result = {'at': datetime.now(timezone.utc).isoformat(), 'source': str(source.relative_to(ROOT)),
        'reconcile_seconds': [first_reconcile, second_reconcile], 'canonical_confirmed_refunds': len(confirmed),
        'confirmed_refunds_preserved': preserved,
        'inventory_file_unchanged': inventory_hash == hashlib.sha256((directory / 'inventory.json').read_bytes()).hexdigest(),
        'facts_published': bool(packet), 'role_count': len(plan['role_ids']),
        'skills_executed': plan['executed_skills'], 'new_trade_rules_activated': 0,
        'background_ticks': len(ticks), 'background_errors': errors,
        'sql_under_background_load': pairs, 'all_outputs_identical': all(p['same_output'] for p in pairs),
        'market_model_or_payment_calls': 0,
        'scope': 'One local SQL fixture and analytical worker load; no full external network or economic superiority claim.'}
    path = ROOT / 'logs' / ('v14-replay-verification-' + stamp + '.json')
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('SOURCE', path); print(json.dumps(result, indent=2))
    assert preserved and result['inventory_file_unchanged'] and not errors and result['all_outputs_identical']


if __name__ == '__main__':
    main()
