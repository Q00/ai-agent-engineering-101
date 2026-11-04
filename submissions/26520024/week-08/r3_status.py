"""Read-only local DB/ledger diagnostic; never creates state or contacts the market."""
import argparse
import json
from pathlib import Path
import sqlite3
import time

from r3_common import read_json, state_dir


def inspect_state(md=None):
    md = state_dir(md)
    result = {'state_directory': str(md), 'network': False,
              'note': 'Local evidence counts, not official leaderboard totals', 'databases': {}}
    for name in ('r3-memory.sqlite3', 'r3_seller.sqlite3', 'r3_seller_requests.sqlite3'):
        path = md / name
        item = {'exists': path.is_file()}
        result['databases'][name] = item
        if not path.is_file():
            continue
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'events' in tables:
                item['events_by_kind'] = dict(db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind'))
                item['public_ledger_max_seq'] = db.execute("SELECT MAX(CAST(event_key AS INTEGER)) FROM events "
                                                          "WHERE kind='market_settle'").fetchone()[0]
            if 'deals' in tables:
                item['reservations_by_status'] = dict(db.execute('SELECT status,COUNT(*) FROM deals GROUP BY status'))
                item['checkout_issued_or_uncertain'] = db.execute("SELECT COUNT(*) FROM deals "
                    "WHERE json_extract(data,'$.checkout_started')=1 AND status!='settled'").fetchone()[0]
        finally:
            db.close()
    result['public_cursor'] = read_json(md / 'public-cursor.json', {})
    result['supply_status'] = read_json(md / 'restock-status.json', {})
    result['model_circuit'] = read_json(md / 'model-circuit.json', {})
    inventory = md / 'inventory.json'
    result['inventory_age_seconds'] = round(time.time() - inventory.stat().st_mtime, 1) if inventory.exists() else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state')
    args = parser.parse_args()
    print(json.dumps(inspect_state(args.state), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
