"""Risk-exception replenishment pilot (one-shot, expiring, hash-bound).

The 11 legacy UNKNOWN reservations are frozen into an immutable ledger: original rows, stable row ids, the source
pending SHA. They are never marked failed, released, matched or deleted, and their full reserve stays counted.
The pilot adds a small separate allowance on top: active non-legacy units, combined units, combined risk, a lifetime
list budget and lifetime unit count that sales/refusals never recycle. Any new unresolved request blocks further
pilot buys (never exempt). Everything fails closed: bad ledger, gate/config hash mismatch, missing counters,
duplicate unit ids, changed legacy rows, expiry."""
import hashlib
import json
import math
import os
from pathlib import Path

PILOT = 'risk-exception-v1'
LEDGER = 'risk-legacy-ledger.json'
CONFIG = 'risk-pilot.json'
COUNTERS = 'risk-pilot-counters.json'
CAPS = {'hours':24, 'max_active_units':3, 'max_combined_units':15, 'max_combined_risk':300000,
        'lifetime_budget':50000, 'lifetime_units':3, 'per_run_budget':50000, 'per_run_lines':3, 'cash_floor':.8}


def canonical(row):
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _rows_digest(rows):
    return sha(canonical(rows).encode('utf-8'))


def freeze(pending_bytes):
    """Ledger of the unmatched rows in the original pending bytes. Row id = sha256(canonical row + ordinal among
    identical rows), so identical rows get distinct, reproducible ids."""
    rows = json.loads(pending_bytes)
    seen, out = {}, []
    for r in rows:
        if r.get('matched_unit_id') is not None: continue
        c = canonical(r)
        n = seen.get(c, 0)
        seen[c] = n+1
        out.append({'id':sha(f'{c}\n{n}'.encode('utf-8')), 'row':r})
    return {'pilot':PILOT, 'source_pending_sha256':sha(pending_bytes), 'count':len(out),
            'reserve':sum(x['row']['reserve'] for x in out), 'rows':out, 'digest':_rows_digest(out)}


def _valid(led):
    if not isinstance(led, dict) or led.get('pilot') != PILOT or not isinstance(led.get('rows'), list): return False
    seen = {}
    for x in led['rows']:
        r = x.get('row') if isinstance(x, dict) else None
        if not isinstance(r, dict) or r.get('matched_unit_id') is not None or 'request' in r: return False
        if type(r.get('reserve')) not in (int, float) or not math.isfinite(r['reserve']) or r['reserve'] < 0: return False
        c = canonical(r)
        n = seen.get(c, 0)
        seen[c] = n+1
        if x.get('id') != sha(f'{c}\n{n}'.encode('utf-8')): return False
    return led.get('count') == len(led['rows']) and led.get('reserve') == sum(x['row']['reserve'] for x in led['rows']) \
        and led.get('digest') == _rows_digest(led['rows'])


def load_ledger(state):
    try:
        led = json.loads((Path(state)/LEDGER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return led if _valid(led) else None


def frozen(pending, led):
    """(ids of the pending row objects that are the frozen legacy rows, complete): complete only when every ledger
    row is present unchanged, with the same multiplicity."""
    if led is None: return set(), False
    want = {}
    for x in led['rows']: want[canonical(x['row'])] = want.get(canonical(x['row']), 0)+1
    got, ids = {}, set()
    for r in pending:
        c = canonical(r)
        if c in want:
            got[c] = got.get(c, 0)+1
            if got[c] <= want[c]: ids.add(id(r))
    return ids, got == want


def _atomic(path, obj):
    tmp = path.with_name(path.name+'.tmp')
    with open(tmp, 'w', encoding='utf-8') as s:
        json.dump(obj, s, indent=2, ensure_ascii=False)
        s.flush()
        os.fsync(s.fileno())
    os.replace(tmp, path)


def prepare(state, activated_at, backup_pending, caps=None, authorization=''):
    """One-shot arming: freeze the ledger from the original backup, check the live pending still holds every
    frozen row unchanged, write ledger/config/empty counters. Returns the hashes the gate must carry."""
    state = Path(state)
    if any((state/n).exists() for n in (CONFIG, LEDGER, COUNTERS)):
        raise ValueError('pilot already prepared: one-shot, the lifetime budget is never re-armed')
    raw = Path(backup_pending).read_bytes()
    led = freeze(raw)
    live = json.loads((state/'restock-pending.json').read_bytes())
    if not frozen(live, led)[1]: raise ValueError('live pending does not hold every frozen legacy row unchanged')
    c = dict(CAPS, **(caps or {}))
    _atomic(state/LEDGER, led)
    ledger_sha = sha((state/LEDGER).read_bytes())
    config = {'pilot':PILOT, 'activated_at':activated_at, 'expires_at':activated_at+c['hours']*3600, 'caps':c,
              'ledger_sha256':ledger_sha, 'source_pending_sha256':led['source_pending_sha256'],
              'backup_pending':str(backup_pending), 'authorization':authorization}
    _atomic(state/CONFIG, config)
    _atomic(state/COUNTERS, {'pilot':PILOT, 'planned':{}})
    return {'config_sha256':sha((state/CONFIG).read_bytes()), 'ledger_sha256':ledger_sha}


def armed(state):
    """True once any pilot artifact exists: from then on only an active, intact pilot may plan buys."""
    return any((Path(state)/n).exists() for n in (CONFIG, LEDGER, COUNTERS))


def request_id(req):
    return f"{req['invocation']}:{req['index']}:{req['unit']}"


def record(state, entries):
    """Idempotent lifetime counters keyed by request id: a replay never adds twice, nothing is ever removed."""
    path = Path(state)/COUNTERS
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('pilot') != PILOT or not isinstance(data.get('planned'), dict): raise ValueError('bad counters')
    for k, v in entries.items(): data['planned'].setdefault(k, v)
    _atomic(path, data)


def context(state, now, inv, pending, frozen_ids, complete, led):
    """Pilot room for this plan, or active=False with the first failing reason. Never raises."""
    state = Path(state)
    out = {'pilot':PILOT, 'active':False}
    try:
        try:
            config_raw = (state/CONFIG).read_bytes()
        except OSError:
            return dict(out, reason='no_pilot')
        gate = json.loads((state/'seller-v2-approved.json').read_text(encoding='utf-8'))
        config = json.loads(config_raw)
        bound = gate.get('risk_pilot') if isinstance(gate, dict) else None
        if gate.get('approved') is not True or gate.get('policy_version') != 'bounded-v2' or not isinstance(bound, dict):
            return dict(out, reason='gate_closed')
        if bound.get('config_sha256') != sha(config_raw): return dict(out, reason='config_hash_mismatch')
        if led is None: return dict(out, reason='ledger_invalid')
        ledger_sha = sha((state/LEDGER).read_bytes())
        if bound.get('ledger_sha256') != ledger_sha or config.get('ledger_sha256') != ledger_sha:
            return dict(out, reason='ledger_hash_mismatch')
        if not complete: return dict(out, reason='legacy_rows_changed')
        out['expires_at'] = config['expires_at']
        if not config['activated_at'] <= now < config['expires_at']: return dict(out, reason='expired')
        caps = config['caps']
        counters = json.loads((state/COUNTERS).read_text(encoding='utf-8'))
        if counters.get('pilot') != PILOT or not isinstance(counters.get('planned'), dict):
            return dict(out, reason='counters_invalid')
        units = inv.get('units', [])
        ids = [u.get('unit') for u in units]
        if not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids): return dict(out, reason='duplicate_unit')
        costs = [u.get('cost') for u in units]
        if not all(type(c) in (int, float) and math.isfinite(c) and c >= 0 for c in costs):
            return dict(out, reason='unit_cost_unknown')
        outstanding = [r for r in pending if r.get('matched_unit_id') is None]
        new = [r for r in outstanding if id(r) not in frozen_ids]
        used = dict(counters['planned'])
        for r in pending:
            if r.get('risk_pilot') == PILOT and isinstance(r.get('request'), dict):
                used.setdefault(request_id(r['request']), {'list':r['reserve']})
        active = len(units)+len(new)
        risk = led['reserve']+sum(costs)+sum(r['reserve'] for r in new)
        out.update(legacy_count=led['count'], legacy_reserve=led['reserve'], active_units=active, combined_risk=risk,
                   combined_units=led['count']+active, used_units=len(used),
                   used_budget=sum(v.get('list', 0) for v in used.values()), new_unresolved=len(new))
        if new: return dict(out, reason='new_unresolved_outstanding')  # fail closed, never exempt
        cash = inv['balance']-caps['cash_floor']*inv['start']-sum(r['reserve'] for r in outstanding)
        out['room_units'] = max(0, min(caps['max_active_units']-active, caps['max_combined_units']-led['count']-active,
                                       caps['lifetime_units']-out['used_units']))
        out['room_money'] = max(0, min(caps['max_combined_risk']-risk, caps['lifetime_budget']-out['used_budget'],
                                       caps['per_run_budget'], cash))
        out['per_run_lines'] = caps['per_run_lines']
        return dict(out, active=True, reason='active')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return dict(out, reason='error')


def last(state):
    """The pilot context recorded by the latest plan decision."""
    return json.loads((Path(state)/'restock-observation.json').read_text(encoding='utf-8'))['decision']['risk_pilot']
