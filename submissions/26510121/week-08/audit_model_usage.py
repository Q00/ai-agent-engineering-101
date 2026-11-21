"""Read-only audit of model usage and deterministic decision routes."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time
from collections import Counter


def main():
    root = Path(__file__).resolve().parent
    now = time.time()
    cutoff = now - 72 * 3600
    c = sqlite3.connect((root / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True)
    modes, errors = Counter(), Counter()
    for role, raw in c.execute('SELECT role,data FROM events WHERE kind=? AND ts>=?', ('decision', cutoff)):
        row = json.loads(raw)
        modes[f'{role}:{row.get("mode")}'] += 1
        if row.get('error'):
            errors[row['error']] += 1
    recent = []
    for at, raw in c.execute('SELECT ts,data FROM events WHERE kind=? ORDER BY ts DESC LIMIT 3', ('model_usage',)):
        row = json.loads(raw)
        recent.append(dict(at_utc=dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(),
                           **{k: row.get(k) for k in ['role', 'model', 'usage', 'estimated_usd']}))
    report = {
        'checked_at_utc': dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
        'window_hours': 72,
        'usage_events_all_time': c.execute('SELECT count(*) FROM events WHERE kind=?', ('model_usage',)).fetchone()[0],
        'usage_events_window': c.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=?', ('model_usage', cutoff)).fetchone()[0],
        'decision_modes': dict(modes), 'fallback_errors': dict(errors), 'last_usage': recent,
        'spending_since_cutoff_date': c.execute(
            'SELECT day,status,count(*),sum(amount) FROM spending WHERE day>=? GROUP BY day,status',
            (dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).date().isoformat(),)).fetchall(),
    }
    c.close()
    path = root / 'logs' / ('model-usage-audit-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('LOG', path)


if __name__ == '__main__':
    main()
