"""Read-only live verification plus a clearly hypothetical next-window plan.

No trading, model requests, authentication changes or journal mutations.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time
import httpx
from r3.state import ROOT, read_json, policy
from r3.economy import cash_capacity, restock_candidates


class ReadJournal:
    def __init__(self, connection, cutoff):
        self.connection, self.cutoff = connection, cutoff

    def events(self, kind, limit=500):
        rows = self.connection.execute('SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? '
            'ORDER BY ts DESC,rowid DESC LIMIT ?', (kind, self.cutoff, limit)).fetchall()
        return [dict(json.loads(d), _event_id=i, _ts=t, _role=r) for i,t,r,d in rows]

    def get(self, key, default=None):
        row = self.connection.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def snapshot(self, name, default=None):
        return read_json(ROOT / '.market' / name, default)


def verify():
    now = time.time()
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get('https://market.ourolabs.ai/api/board')
        response.raise_for_status()
        board = response.json()
        health = read_json(ROOT / '.market/shop-health.json', {})
        try:
            card = client.get(health['url'].rstrip('/') + '/.well-known/agent-card.json')
            public_probe = {'status': card.status_code, 'name': card.json().get('name') if card.status_code == 200 else None}
        except Exception as exc:
            public_probe = {'error_type': type(exc).__name__}
    inventory = read_json(ROOT / '.market/inventory.json', {})
    runtime = read_json(ROOT / '.market/runtime.json', {})
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('BEGIN')
        j = ReadJournal(connection, now)
        epoch = j.get('objective_epoch', runtime.get('started_at', now))
        kinds = ['purchase', 'agreement', 'quote_comparison', 'shop_opportunity', 'shop_quote',
                 'shop_settled', 'restock_decision', 'forum_delivery', 'model_usage',
                 'strategy_error', 'attribution_error', 'forum_error']
        events = {kind: [e for e in j.events(kind, 10000) if e['_ts'] >= epoch] for kind in kinds}
        cfg = policy()
        capacity = cash_capacity(inventory, j, cfg, now)
        hypothetical = dict(inventory, restock={'open': True, 'opens_at': now-1, 'closes_at': now+180})
        candidates = restock_candidates(hypothetical, j.snapshot('catalog.json', {}), [], j, cfg, now)
        assert sum(c['capital_commitment'] for c in candidates) <= capacity['spendable'] + .01
        quotes_by_order = {}
        for e in events['quote_comparison']:
            quotes_by_order.setdefault(e['order'], []).append(e)
        paid = {e['order']: e for e in events['purchase']}
        comparison_paid = {o: {'quotes': rows, 'purchase': paid[o]} for o, rows in quotes_by_order.items()
                           if len({r['shop'] for r in rows}) >= 2 and o in paid}
        own = {table: next(r for r in board[table] if r['name'] == 'seochanit') for table in ('shops', 'buyers')}
        summary = {table: {k: row.get(k) for k in ('name', 'alive', 'all', 'deals', 'revenue', 'profit', 'surplus')}
                   for table, row in own.items()}
        deploys = sorted((ROOT / 'logs').glob('strategy-v4-deploy-*.json'))
        delta = {}
        if deploys:
            before = read_json(deploys[-1], {})
            for table in own:
                start = next(r for r in before['before_board'][table] if r['name'] == 'seochanit')
                delta[table] = {k: own[table][k]-start[k] for k in ('deals', 'all', 'revenue', 'profit', 'surplus')
                                if type(own[table].get(k)) in (int,float) and type(start.get(k)) in (int,float)}
        result = {'checked_at': datetime.fromtimestamp(now, timezone(timedelta(hours=9))).isoformat(),
            'policy': cfg, 'objective_revision': j.get('objective_revision'), 'epoch': epoch,
            'runtime': runtime, 'health': health, 'public_agent_card': public_probe,
            'board': board, 'inventory': inventory, 'capacity': capacity, 'own_summary': summary,
            'delta_since_restart_snapshot': delta,
            'event_counts_since_epoch': {k: len(v) for k,v in events.items()},
            'paid_after_two_quote_comparisons': comparison_paid, 'events_since_epoch': events,
            'hypothetical_next_open_window': {'NOT_AN_EXECUTED_PURCHASE': True, 'candidates': candidates},
            'limitations': 'Short observation; goods need 6h to mature. Historical prices/rates do not prove future demand. '
                          'Shadow supply window is artificial; all real purchases remain server-window constrained.'}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = ROOT / 'logs' / ('strategy-v4-live-' + stamp + '.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(path), 'checked_at': result['checked_at'], 'own': summary,
        'public_agent_card': public_probe, 'counts': result['event_counts_since_epoch'],
        'paid_two_quote_orders': len(comparison_paid), 'capacity': capacity,
        'shadow_plan': [(c['sku'],c['qty'],c['allocation_book']) for c in candidates]}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    verify()
