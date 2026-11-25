"""Read-only recovery audit. Never deletes reservations or invalidates market tokens."""
import argparse
import json
import sqlite3
from contextlib import closing

from r3_common import read_json, state_dir
from r3_storage import database_dir
from r3_clock import clock as time


def inspect(md, now=None):
    md = state_dir(md)
    now = time.time() if now is None else now
    inventory = read_json(md / 'inventory.json', {})
    units = {u['unit']: u for u in inventory.get('units', [])}
    path = database_dir(md) / 'r3_seller.sqlite3'
    report = {'mode': 'read_only', 'policy': 'confirmed_cancel_with_live_inventory', 'items': [], 'blocked_cost': 0,
              'next_step': 'Confirmed canceled checkout plus current inventory can release via seller reconciliation; '
                           'unknown payment outcomes remain held. No server JWT revocation is asserted.'}
    if not path.exists():
        return report
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.execute('PRAGMA query_only=ON')
        for task, unit, status, expires, raw in db.execute(
                'SELECT task,unit,status,expires,data FROM deals WHERE unit IS NOT NULL'):
            data = json.loads(raw)
            session = data.get('checkout_session') or {}
            guarded = data.get('checkout_guarded') and not data.get('checkout_started') and not session
            if status == 'settled':
                action = 'retain_settled_tombstone'
            elif status in ('open', 'busy') and expires <= now and not data.get('receipt'):
                action = 'unsigned_quote_expires_via_reservation_gate'
            elif status in ('signed_pending', 'quarantined') and guarded and expires <= now:
                action = 'safe_revoke_via_next_seller_reconciliation'
            elif session.get('status') == 'canceled' and unit in units and data.get('completion_state') not in ('in_flight', 'uncertain', 'confirmed'):
                action = 'verify_canceled_checkout_then_release_via_seller'
            elif session:
                action = 'hold_unresolved_checkout'
            else:
                action = 'hold_pending_or_uncertain_promise'
            cost = units.get(unit, {}).get('cost', 0)
            if status != 'settled':
                report['blocked_cost'] += cost
            report['items'].append({'task': task, 'unit': unit, 'status': status,
                'in_inventory': unit in units, 'cost': cost, 'action': action,
                'checkout_id': session.get('id')})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state')
    args = parser.parse_args()
    print(json.dumps(inspect(args.state), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
