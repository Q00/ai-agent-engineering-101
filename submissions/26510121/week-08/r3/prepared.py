"""Versioned Buyer route priors prepared outside the order critical path.

Only observational priors are reused; budget, eligibility, stock, receipts and
payment are always checked by the existing order code.
"""
import time
from .state import atomic_json, identity

KINDS = ('attempt', 'purchase', 'agreement', 'buyer_quote_latency')


def version(journal):
    with journal.connect() as db:
        rows = [db.execute('SELECT rowid FROM events WHERE kind=? ORDER BY ts DESC,rowid DESC LIMIT 1',
                           (kind,)).fetchone() for kind in KINDS]
    return identity([row[0] if row else 0 for row in rows] + [journal.get('buyer_revision_epoch', 0),
                    journal.get('market_rules',{}).get('buyer_saved_formula'),
                    journal.get('buyer-cost-scenarios:v22',{}).get('id')])


def pack_execution(row):
    return dict(row, routes=[[shop, sku, values] for (shop, sku), values in row['routes'].items()])


def unpack_execution(row):
    return dict(row, routes={(shop, sku): values for shop, sku, values in row['routes']})


def update(journal, config, now=None):
    if not config.get('market_cycle_enabled'):
        return None
    now = time.time() if now is None else now
    token = version(journal)
    previous = journal.snapshot('buyer-prepared-v15.json', {})
    if previous.get('version') == token and 0 <= now - previous.get('at', 0) < 15:
        return previous
    from .buyer_routes import context
    from .buyer_execution import route_context
    from .decision_view import frame
    orders = journal.events('buyer_order', 100)
    hours = []
    for order in orders:
        value = order.get('use_within_hours', 0)
        if order.get('kind') == 'food' and type(value) in (int, float) and value not in hours:
            hours.append(value)
        if len(hours) == 3:
            break
    with frame(journal):
        routes = context(journal, now)
        executions = {'goods': pack_execution(route_context(journal, now, {'kind': 'goods'}))}
        for value in hours:
            executions['food:' + str(value)] = pack_execution(route_context(
                journal, now, {'kind': 'food', 'use_within_hours': value}))
    if version(journal) != token:
        return None  # An in-flight event changed the evidence during preparation.
    packet = {'schema': 1, 'at': now, 'expires_at': now + 20, 'version': token,
              'rules_hash': journal.get('market_rules', {}).get('sha256'),
              'routes': routes, 'executions': executions}
    if len(__import__('json').dumps(packet).encode()) > 1_000_000:
        return None
    atomic_json(journal.md / 'buyer-prepared-v15.json', packet)
    journal.add('buyer_prepared_published', {'version': token, 'segments': list(executions), 'at': now})
    return packet


def get(journal, order, config, now=None):
    if not config.get('market_cycle_enabled'):
        return None
    from .decision_view import fact
    now = time.time() if now is None else now
    packet = fact(journal, 'prepared-buyer-packet', lambda: journal.snapshot('buyer-prepared-v15.json', {}))
    if (packet.get('schema') != 1 or not 0 <= now - packet.get('at', 0) <= 20
            or now >= packet.get('expires_at', 0)
            or packet.get('rules_hash') != journal.get('market_rules', {}).get('sha256')
            or packet.get('version') != fact(journal, 'prepared-buyer-version', lambda: version(journal))):
        return None
    key = 'goods' if order.get('kind') == 'goods' else 'food:' + str(order.get('use_within_hours', 0))
    if key not in packet.get('executions', {}):
        return None
    return {'routes': packet['routes'], 'execution': unpack_execution(packet['executions'][key]),
            'version': packet['version']}
