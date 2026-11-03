"""Bounded strategy trials from own evidence and public market context.

This controller does not train models or edit policy.json. Official board flows
are observational comparisons, not causal estimates of a strategy's value.
"""
import json
import math
import statistics
import time
import uuid
from .state import policy, identity

INTERVAL = 3600
MATURITY = 21600
MIN_SAMPLES = 20
KEY = 'adaptive:'


def validate(role, patch):
    allowed = {'buyer_counter_ratio'} if role == 'buyer' else {'inventory_target_units', 'category_weights'}
    if role not in {'buyer', 'shop'} or not isinstance(patch, dict) or set(patch) - allowed:
        raise ValueError('Unsupported adaptive strategy field')
    for key, value in patch.items():
        if key == 'buyer_counter_ratio':
            if type(value) not in (int, float) or not .88 <= value <= .98:
                raise ValueError('Counter ratio outside bounds')
        elif key == 'inventory_target_units':
            if type(value) is not int or not 10 <= value <= 30:
                raise ValueError('Inventory target outside bounds')
        elif (not isinstance(value, dict) or len(value) > 200 or
              any(not isinstance(k, str) or type(v) not in (int, float) or not .75 <= v <= 1.5
                  for k, v in value.items())):
            raise ValueError('Category weight outside bounds')
    return dict(patch)


def strategy(journal, role):
    state = journal.get(KEY + role, {})
    try:
        patch = validate(role, state.get('patch', {}))
        return {'version': int(state.get('version', 0)), 'patch': patch}
    except (ValueError, TypeError):
        return {'version': 0, 'patch': {}}


def effective(journal, role, base=None, frozen=None):
    cfg = dict(base or policy())
    selected = frozen or strategy(journal, role)
    cfg.setdefault('buyer_counter_ratio', .92)
    cfg.setdefault('category_weights', {})
    cfg.update(validate(role, selected['patch']))
    cfg['strategy_version'] = selected['version']
    return cfg, selected


def begin_buyer(journal, order):
    key = 'buyer_strategy:' + str(order['order'])
    frozen = journal.get(key)
    cfg, selected = effective(journal, 'buyer', frozen=frozen)
    if frozen is None:
        journal.put(key, selected)
        journal.add('buyer_order', {'order': order['order'], 'budget': order['budget'],
                    'strategy_version': selected['version']}, eid='buyer-order:' + str(order['order']), role='buyer')
    return cfg, selected


def public_context(journal, now):
    catalog = journal.snapshot('catalog.json', {})
    own_urls = {url for url, row in journal.snapshot('registry.json', {}).items()
                if row.get('name') in {'seochanit', 'Seochanit Shop'}}
    counts = {}
    for event in journal.events('public_settle', 20000):
        if not now - MATURITY <= event['_ts'] <= now or event.get('shop') in own_urls:
            continue
        category = catalog.get(event.get('sku') or event.get('item'), {}).get('category')
        if category:
            counts[category] = counts.get(category, 0) + 1
    return counts


def board_snapshot(journal, now):
    board = journal.snapshot('board.json', {})
    if (board.get('round') != 3 or journal.get('board_round', 3) != 3 or
            now - journal.get('board_checked_at', 0) > 300):
        return None
    result = {'at': now}
    for role, table, field in [('buyer', 'buyers', 'surplus'), ('shop', 'shops', 'profit')]:
        rows = {row.get('name'): row.get(field) for row in board.get(table, [])
                if type(row.get(field)) in (int, float) and math.isfinite(row[field])}
        if 'seochanit' in rows:
            result[role] = {'own': rows.pop('seochanit'), 'peers': rows}
    return result


def flow(start, end, role):
    if role not in start or role not in end or end['at'] <= start['at']:
        return None
    hours = (end['at'] - start['at']) / 3600
    a, b = start[role], end[role]
    peers = [ (b['peers'][name] - value) / hours for name, value in a['peers'].items()
              if name in b['peers'] ]
    own = (b['own'] - a['own']) / hours
    benchmark = statistics.median(peers) if len(peers) >= 3 else 0
    return {'own_per_hour': own, 'peer_median_per_hour': benchmark,
            'relative_per_hour': own - benchmark, 'matched_peers': len(peers),
            'note': 'observational board flow; traffic and category mix can confound comparisons'}


def evidence(journal, role, version, now, since=0):
    kind = 'buyer_order' if role == 'buyer' else 'shop_opportunity'
    opportunities = [e for e in journal.events(kind, 10000)
                     if e.get('strategy_version') == version and since <= e['_ts'] <= now - MATURITY]
    if role == 'buyer':
        results = {e['order']: e for e in journal.events('market_result', 10000)}
        purchases = {e['order']: e for e in journal.events('purchase', 10000)}
        outcomes = {}
        for event in journal.events('outcome', 10000):
            if event.get('broken') or event['order'] not in outcomes:
                outcomes[event['order']] = event
        known, paid, bad = [], 0, 0
        for order in opportunities:
            oid = order['order']
            if oid in purchases:
                outcome = outcomes.get(oid, {})
                if not (outcome.get('matured') or outcome.get('broken')):
                    continue
                # A warranty expectation is not a confirmed refund or a final reward.
                if outcome.get('refund_status') == 'expected_by_warranty_rule':
                    continue
                paid += 1
                bad += bool(outcome.get('broken')) or purchases[oid].get('value', 0) <= 0
                known.append(order)
            elif results.get(oid, {}).get('verified') is False:
                known.append(order)
        ids = {e['order'] for e in known}
        counters = sum(e.get('order') in ids for e in journal.events('buyer_counter', 10000))
        price_failures = sum(e.get('order') in ids and e.get('kind') == 'no_agreement'
                             for e in journal.events('attempt', 10000))
        return {'samples': len(known), 'paid': paid, 'bad': bad, 'counter_moves': counters,
                'no_agreement': price_failures, 'success_rate': paid / max(1, len(known))}
    settlements = journal.events('shop_settled', 10000)
    pending = {e.get('task') for e in settlements if e.get('paid_at', e['_ts']) > now - MATURITY}
    ids = {e['task'] for e in opportunities} - pending
    paid = [e for e in settlements if e.get('task') in ids]
    own_categories = {}
    for event in paid:
        if event.get('category'):
            own_categories[event['category']] = own_categories.get(event['category'], 0) + 1
    return {'samples': len(ids), 'paid': len(paid), 'success_rate': len(paid) / max(1, len(ids)),
            'categories': own_categories,
            'note': 'unpaid opportunities are censored observations, not proven price rejections'}


def propose(journal, role, state, stats, market):
    current = state.get('patch', {})
    if role == 'buyer':
        ratio = current.get('buyer_counter_ratio', .92)
        if stats['counter_moves'] >= 5 and stats['no_agreement'] >= 5 and stats['success_rate'] < .5:
            new = min(.98, round(ratio + .02, 2))
            reason = 'Repeated failed price negotiations: test a smaller discount request'
        elif stats['success_rate'] >= .8 and not stats['bad']:
            new = max(.88, round(ratio - .02, 2))
            reason = 'High observed completion with no known bad outcomes: test a larger discount'
        else:
            return None
        if new != ratio:
            return dict(current, buyer_counter_ratio=new), reason
        return None
    inv = journal.snapshot('inventory.json', {})
    if inv.get('round') != 3:
        return None
    count = len(inv.get('units', []))
    target = current.get('inventory_target_units', policy()['inventory_target_units'])
    if stats['success_rate'] >= .5 and count < target / 2 and sum(market.values()) >= 5:
        new = min(30, target + 5)
    elif stats['success_rate'] < .2 and count >= target * .8:
        new = max(10, target - 5)
    else:
        new = target
    if new != target:
        return dict(current, inventory_target_units=new), 'Test inventory target against own conversion and current stock'
    weights = dict(current.get('category_weights', {}))
    choices = [(n * stats['categories'].get(cat, 0), cat) for cat, n in market.items()
               if n >= 3 and stats['categories'].get(cat, 0) >= 3 and weights.get(cat, 1) < 1.5 and
               (cat in weights or len(weights) < 200)]
    if choices:
        category = max(choices)[1]
        weights[category] = round(min(1.5, weights.get(category, 1) + .1), 2)
        return dict(current, category_weights=weights), 'Own mature sales and public category trades support a bounded allocation test'
    return None


def transition(journal, role, previous, updated, action, details, now):
    """Compare-and-swap plus audit event in one SQLite transaction."""
    with journal.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT value FROM kv WHERE key=?', (KEY + role,)).fetchone()
        actual = json.loads(row[0]) if row else {'version': 0, 'patch': {}, 'status': 'stable'}
        if actual != previous:
            return False
        connection.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (KEY + role, json.dumps(updated)))
        event = dict(action=action, before=previous, after=updated, **details)
        connection.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                           (uuid.uuid4().hex, 'strategy_change', now, role, json.dumps(event)))
    return True


def update(journal, now=None):
    now = time.time() if now is None else now
    snapshot = board_snapshot(journal, now)
    if snapshot is None:
        return {'status': 'waiting_for_fresh_round3_board'}
    # One controller evaluation per hour, shared by all processes.
    with journal.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT value FROM kv WHERE key=?', (KEY + 'last_tick',)).fetchone()
        if row and now - json.loads(row[0]) < INTERVAL:
            return {'status': 'cooldown'}
        connection.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (KEY + 'last_tick', json.dumps(now)))
    journal.add('strategy_market', snapshot, eid='strategy-market:' + str(int(now // INTERVAL)), ts=now)
    history = [e for e in journal.events('strategy_market', 500) if e['at'] <= now - MATURITY]
    market = public_context(journal, now)
    actions = []
    for role in ('buyer', 'shop'):
        state = journal.get(KEY + role, {'version': 0, 'patch': {}, 'status': 'stable'})
        validate(role, state['patch'])
        trial = state.get('trial')
        since = trial['started_at'] if trial else state.get('since', 0)
        stats = evidence(journal, role, state['version'], now, since)
        reason, action, next_state = 'insufficient_mature_evidence', 'hold', None
        if trial:
            observed = flow(trial['board_start'], snapshot, role)
            baseline = trial['baseline']
            # An absolute cash-loss guard can roll back before labels mature.
            shop_loss = (role == 'shop' and role in snapshot and role in trial['board_start'] and
                         snapshot[role]['own'] - trial['board_start'][role]['own'] < -500000)
            ready = now - trial['started_at'] >= MATURITY and stats['samples'] >= MIN_SAMPLES and observed
            degraded = ready and (observed['relative_per_hour'] < baseline['relative_per_hour'] -
                                  .15 * max(abs(baseline['relative_per_hour']), 1000) or
                                  observed['own_per_hour'] < min(0, baseline['own_per_hour']))
            previous_stats = trial['baseline_evidence']
            own_supported = (stats['paid'] >= 5 and
                             stats['success_rate'] >= previous_stats['success_rate'] - .1 and
                             (role != 'buyer' or stats['bad'] / max(1, stats['paid']) <=
                              previous_stats['bad'] / max(1, previous_stats['paid']) + .05))
            improved = ready and observed['relative_per_hour'] > baseline['relative_per_hour'] + \
                       .05 * max(abs(baseline['relative_per_hour']), 1000) and \
                       observed['own_per_hour'] >= baseline['own_per_hour'] and own_supported
            expired = now - trial['started_at'] >= 48 * 3600
            if shop_loss or degraded or expired and not improved:
                action, reason = 'rollback', 'Cash loss, degraded observed flow, or trial expired without evidence of improvement'
                next_state = dict(version=state['version'] + 1, patch=trial['previous_patch'], status='stable',
                                  since=now, cooldown_until=now + 24 * 3600,
                                  rejected=(state.get('rejected', []) +
                                            [{'patch': identity(state['patch']), 'until': now + 24 * 3600}])[-20:])
            elif improved:
                action, reason = 'promote', 'Own and market-relative observed flow improved; not causal proof'
                next_state = dict(state, status='stable', since=now, cooldown_until=now + MATURITY)
                next_state.pop('trial', None)
            if next_state:
                transition(journal, role, state, next_state, action,
                           {'reason': reason, 'evidence': stats, 'observed': observed}, now)
        elif now >= state.get('cooldown_until', 0) and stats['samples'] >= MIN_SAMPLES and history:
            baseline = flow(history[0], snapshot, role)
            proposal = propose(journal, role, state, stats, market) if baseline else None
            blocked = {entry['patch'] for entry in state.get('rejected', [])
                       if isinstance(entry, dict) and now < entry.get('until', 0)}
            if proposal and identity(proposal[0]) not in blocked:
                patch, reason = proposal
                validate(role, patch)
                next_state = dict(state, version=state['version'] + 1, patch=patch, status='trial',
                                  trial={'started_at': now, 'previous_patch': state['patch'],
                                         'board_start': snapshot, 'baseline': baseline,
                                         'baseline_evidence': stats})
                action = 'trial_started'
                transition(journal, role, state, next_state, action,
                           {'reason': reason, 'evidence': stats, 'public_categories': market}, now)
            else:
                reason = 'No supported untried bounded change'
        journal.add('strategy_evaluation', {'version': state['version'], 'action': action,
                    'reason': reason, 'evidence': stats, 'public_categories': market}, role=role, ts=now)
        actions.append({'role': role, 'action': action})
    return {'status': 'evaluated', 'actions': actions}
