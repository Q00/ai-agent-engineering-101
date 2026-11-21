"""Public GETs and read-only SQL: prove v14 workers and role packets are used."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time

import httpx
from profile_main_latency_20261111 import stats
from r3.state import ROOT, read_json, policy
from r3.autonomy import context
from autonomous_status import ReadJournal


def main():
    now = time.time()
    runtime = read_json(ROOT / '.market/runtime.json', {})
    start = runtime.get('started_at', now)
    cfg = policy()
    health = read_json(ROOT / '.market/shop-health.json', {})
    url = health.get('url', '').rstrip('/')
    def get(path):
        try:
            response = httpx.get(path, timeout=8, trust_env=False, headers={'Cache-Control': 'no-cache'})
            return {'status': response.status_code, 'data': response.json() if response.status_code == 200 else {}}
        except Exception as exc:
            return {'error_type': type(exc).__name__, 'data': {}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(get, [url + '/.well-known/agent-card.json', url + '/.well-known/acp.json',
            'https://market.ourolabs.ai/api/board', 'https://market.ourolabs.ai/api/shops']))
    board = responses[2]['data']
    own = {table: next((r for r in board.get(table, []) if r.get('name') == 'seochanit'), {}) for table in ('shops', 'buyers')}
    listed = any(r.get('name') == 'seochanit' and r.get('url', '').rstrip('/') == url for r in responses[3]['data'].get('shops', []))
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN'); journal = ReadJournal(db, now)
        def events(kind):
            return [dict(json.loads(d), _ts=t) for t, d in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts', (kind, start, now))]
        kinds = ['buyer_worker_execution', 'buyer_worker_error', 'autonomy_v14_evaluation', 'autonomy_v14_skill_execution',
                 'autonomy_v14_error', 'market_control_error', 'strategy_error', 'merchant_context_error',
                 'forum_error', 'feed_probe_error', 'purchase', 'shop_settled', 'model_usage']
        counts = {kind: len(events(kind)) for kind in kinds}
        consumed = db.execute("SELECT COUNT(*) FROM events WHERE kind='prediction' AND ts>=? AND ts<=? "
            "AND json_type(data,'$.context.observed_role_facts')='object'", (start, now)).fetchone()[0]
        state = journal.get('autonomy-v14:state', {})
        packets = {role: context(journal, cfg, now, role=role) for role in ('shop', 'buyer')}
        orders = {r['order']: r for r in events('buyer_order')}
        preparation = events('buyer_preparation')
        metrics = {kind: stats([r.get('seconds') for r in preparation if orders.get(r.get('order'), {}).get('kind') == kind]) for kind in ('food', 'goods')}
        control = journal.get('market_control', {})
        feed_at = journal.get('public_feed_caught_up_at', 0)
        results = events('market_result')
        recent_feed_errors = events('feed_probe_error')[-3:]
    promotion = sorted((ROOT / 'logs').glob('v14-source-promotion-*.json'))[-1]
    manifest = read_json(promotion, {})
    source_matches = all(hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest() == row['candidate_sha256'] for row in manifest.get('files', []))
    online = (all(r.get('status') == 200 for r in responses) and listed and
              all(own[t].get('alive') is True for t in own) and
              (own['shops'].get('card') or {}).get('url', '').rstrip('/') == url)
    active = (source_matches and counts['buyer_worker_execution'] > 0 and
              state.get('at', 0) >= start and state.get('status') == 'published_facts' and consumed > 0 and
              all(packets.values()) and len(state.get('role_ids', [])) == 12)
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'runtime': runtime, 'url': url,
        'public_statuses': [r.get('status') for r in responses], 'public_online': online,
        'public_errors': [r.get('error_type') for r in responses],
        'listed': listed, 'own': {table: {k: row.get(k) for k in ('alive', 'all', 'book', 'deals', 'saved', 'stars_rank')}
                                 for table, row in own.items()},
        'source_matches_promoted_manifest': source_matches, 'runtime_features_activated': active,
        'counts_since_restart': counts, 'role_packet_consuming_decisions': consumed,
        'autonomy': {k: state.get(k) for k in ('at', 'status', 'revision', 'issues', 'role_ids', 'executed_skills', 'paid_model_calls')},
        'packets': packets, 'preparation_seconds': metrics, 'control': {k: control.get(k) for k in ('at', 'status', 'reasons')},
        'public_feed_age_seconds': now - feed_at,
        'verified_result_count': sum(r.get('verified') is True for r in results), 'recent_feed_errors': recent_feed_errors,
        'limits': ['Short post-release observations are not causal economic/rank evidence.',
                   'Public registry/board requests are separate snapshots and can change.',
                   'New rule proposals remain shadow; no first-place or zero-error guarantee.']}
    path = ROOT / 'logs' / ('v14-live-verification-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SOURCE', path)
    print(json.dumps({k: v for k, v in report.items() if k not in ('packets', 'limits')}, ensure_ascii=True, indent=2))
    return active and online


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
