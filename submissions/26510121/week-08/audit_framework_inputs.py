"""Read-only evidence for the market/strategy framework design."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time


def main():
    root = Path(__file__).resolve().parent
    now = time.time()
    with sqlite3.connect((root / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        state = {}
        for key in ['framework-arena', 'framework-plan', 'adaptive:buyer', 'adaptive:shop', 'market_control', 'market_rules']:
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            value = json.loads(row[0]) if row else None
            fields = {
                'framework-plan': ['at', 'issues', 'evaluation', 'next_actions'],
                'market_control': ['at', 'status', 'reasons', 'pause_restock', 'suggestions'],
            }.get(key)
            state[key] = {k: value.get(k) for k in fields} if fields and isinstance(value, dict) else value
        full = db.execute('SELECT count(*),min(ts),max(ts) FROM events WHERE kind=? AND ts>=? AND ts<=?',
                          ('public_settle', now - 21600, now)).fetchone()
        capped = db.execute('SELECT count(*),min(ts),max(ts) FROM '
                            '(SELECT ts FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC LIMIT 20000)',
                            ('public_settle', now)).fetchone()
        report = {
            'checked_at_utc': datetime.fromtimestamp(now, timezone.utc).isoformat(),
            'source': '.market/r3.sqlite3 read-only transaction',
            'state': state,
            'public_settles': {
                'six_hour_window_seconds': 21600,
                'full_six_hour_count': full[0], 'full_first_at': full[1], 'full_last_at': full[2],
                'market_memory_row_limit': 20000, 'selected_count': capped[0],
                'selected_first_at': capped[1], 'selected_last_at': capped[2],
                'selected_span_seconds': capped[2] - capped[1] if capped[1] is not None else None,
                'note': 'Capped query reproduces the event read bound; it is not a causal estimate of lost inventory or rank.',
            },
        }
    path = root / 'logs' / ('framework-input-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.open('x', encoding='utf-8').write(json.dumps(report, ensure_ascii=False, indent=2))
    print('SOURCE', path)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
