"""One-day inventory pilot (one-day-v1): a NEW user-authorized 24 h acquisition mandate, not an extension of the
expired risk-exception-v1 pilot.

Caps (user-approved, 2026-11-10): lifetime new acquisition spend 1,000,000 (confirmed actual totals plus the upper
bound of every unresolved request, never reset by sales; only a request the kit log proves refused/not attempted is
released); concurrent on-hand cost + all non-legacy pending reserves <= 100,000, then 350,000 only after a real
settled sale of a pilot unit; gross attributable losses < 120,000 (no profitable
sale offsets); risk-adjusted cash = balance - legacy reserve - new reserves - plan >= 5,000,000.

Every new unit is reserved at the supply contract's upper bound: grade A cost is at most 75% of list and surge adds
at most +50% (agent.md Round 3), so ceil(list x 1.125). Real cost replaces it once the kit logs the supply total.

The 11 legacy UNKNOWN rows (risk_pilot ledger, 245,080) stay frozen at full reserve, bound by their ledger hash.
Missing/corrupt config, ledger, gate binding, legacy rows or inventory fail NEW acquisition closed; observation of
already owned units (arrivals, sales, expiry, refunds, losses) continues after expiry."""
import hashlib
import json
import math
import os
import time
from pathlib import Path

PILOT = 'one-day-v1'
CONFIG = 'one-day-pilot.json'
LEDGER = 'one-day-pilot-ledger.json'
STATUS = 'one-day-pilot-status.json'
LEGACY = 'risk-legacy-ledger.json'
CAPS = {'hours':24, 'lifetime_spend':1000000, 'stage1':100000, 'stage2':350000, 'gross_loss':120000,
        'cash_floor':5000000, 'per_run_lines':3}
# slot-recovery-v1 (user1570233935940554783): the per-SKU held_max (HELD {1:2, 2:4}) and the SKU-share concentration cap
# (sku_share .5) are retired for every eligible candidate. A sku_share left in an older config is historical only and
# never enforced; quantity is bounded by the money room and restock_r3.PER_CALL (a request guard, not an inventory cap).
RETIRED_CAPS = ('sku_share',)
# risk-ceiling-disable-v1 (user '왜지럴 위험한도 풀어', amendment #7): config['loss_ceiling'] is a finite state, never a
# number. Absent (every config before #7) = 'enforced': gross loss + warranty liability >= caps.gross_loss blocks new
# acquisition. 'disabled' = that combined ceiling is not applied (caps.gross_loss stays in the config as a historical
# value only); losses and the warranty liability are still counted and reported, and the warranty liability is still
# subtracted from cash/bank room. Any other value is unknown/corrupt: no acquisition (fail closed).
LOSS_CEILING_MODES = ('enforced', 'disabled')
# unrestricted-rank-v1 (Discord 1571684354835484704 '모든 제한 해제하고 ... 파산해도 괜찮아', amendment #8):
# config['financial_limits'] is a finite state, never a number. Absent = 'enforced' (every cap as before). 'disabled' =
# no lifetime spend, stage/concurrent, cash floor, bankrupt-line hold, combined loss+warranty ceiling or pilot per-run
# line cap; the room is the observed balance minus genuine in-flight (held, unconfirmed) reserves only, so one plan can
# never double spend. Caps stay in the config as history and every old counter/liability is still computed and
# reported. Expiry, gate/hash binding and evidence-integrity guards are unchanged. Any other value fails closed.
FINANCIAL_LIMIT_MODES = ('enforced', 'disabled')
FINANCIAL_LIMITS_OFF = ('lifetime_spend', 'stage', 'cash_floor', 'bankrupt_line_hold', 'loss_ceiling', 'per_run_lines')
KIT_MAX_LINES = 10  # market kit protocol: at most 10 buy lines per restock run (supplier batch, not a risk cap)
UB_PER_MILLE = 1125  # contract max: grade A cost <= 75% list, surge <= +50%  -> 1.125 x list
EXPECTED_COST = {'food':.5, 'goods':.59}  # published grade mix x cost band midpoints (agent.md Round 3)
ROTATION_H = 72  # fallback expected rotation: food shelf life must be GREATER than this without own rotation evidence
SKEW_S = 300  # server supplied_at vs local activation clock
RELEASED = ('refused', 'not_attempted')
KST = 9*3600


def sha(data):
    return hashlib.sha256(data).hexdigest()


def num(v):
    return type(v) in (int, float) and math.isfinite(v)


def loss_ceiling_mode(config):
    """'enforced' | 'disabled', or None for an unknown/corrupt mode."""
    if not isinstance(config, dict): return None
    m = config.get('loss_ceiling', 'enforced')
    return m if type(m) is str and m in LOSS_CEILING_MODES else None


def financial_limits_mode(config):
    """'enforced' | 'disabled', or None for an unknown/corrupt value (numbers, sentinels, other strings)."""
    if not isinstance(config, dict): return None
    m = config.get('financial_limits', 'enforced')
    return m if type(m) is str and m in FINANCIAL_LIMIT_MODES else None


def upper_bound(list_price):
    return -(-int(math.ceil(list_price))*UB_PER_MILLE//1000)


def kst(t):
    return time.strftime('%Y-%m-%d %H:%M:%S KST', time.gmtime(t+KST)) if num(t) else None


def _atomic(path, obj):
    tmp = path.with_name(path.name+'.tmp')
    with open(tmp, 'w', encoding='utf-8') as s:
        json.dump(obj, s, indent=2, ensure_ascii=False, sort_keys=True)
        s.flush()
        os.fsync(s.fileno())
    os.replace(tmp, path)


def configured(state):
    return (Path(state)/CONFIG).exists() or (Path(state)/LEDGER).exists()


def activate(state, now, authorization='', sources=None, caps=None):
    """One-shot arming from the live state: frozen legacy ledger intact in pending, valid inventory. Writes the empty
    ledger, then the immutable config. Returns the binding the gate must carry."""
    import risk_pilot
    state = Path(state)
    if configured(state): raise ValueError('one-day pilot already activated: never re-armed or reset')
    inv = json.loads((state/'inventory.json').read_text(encoding='utf-8'))
    if not _inventory_ok(inv): raise ValueError('inventory invalid')
    led = risk_pilot.load_ledger(state)
    pending = json.loads((state/'restock-pending.json').read_text(encoding='utf-8'))
    if led is None or not risk_pilot.frozen(pending, led)[1]: raise ValueError('legacy ledger invalid or rows changed')
    c = dict(CAPS, **(caps or {}))
    config = {'pilot':PILOT, 'activated_at':now, 'expires_at':now+c['hours']*3600, 'caps':c,
              'activated_kst':kst(now), 'expires_kst':kst(now+c['hours']*3600),
              'legacy':{'ledger_sha256':sha((state/LEGACY).read_bytes()), 'count':led['count'],
                        'reserve':led['reserve'], 'source_pending_sha256':led['source_pending_sha256']},
              'baseline':{'balance':inv['balance'], 'start':inv['start'], 'round':inv.get('round'),
                          'units':sorted(u['unit'] for u in inv['units']),
                          'stock_cost':sum(u['cost'] for u in inv['units']),
                          'inventory_sha256':sha((state/'inventory.json').read_bytes())},
              'sources':sources or {}, 'upper_bound':'ceil(list x 0.75 x 1.5): contract max cost share x max surge',
              'authorization':authorization}
    _atomic(state/LEDGER, {'pilot':PILOT, 'requests':{}, 'units':{}, 'events':{}})
    _atomic(state/CONFIG, config)
    return {'config_sha256':sha((state/CONFIG).read_bytes())}


def _inventory_ok(inv):
    if not isinstance(inv, dict) or not num(inv.get('balance')) or not num(inv.get('start')) or inv['start'] <= 0:
        return False
    units = inv.get('units')
    if not isinstance(units, list): return False
    ids = [u.get('unit') if isinstance(u, dict) else None for u in units]
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids): return False
    return all(isinstance(u.get('sku'), str) and num(u.get('cost')) and u['cost'] >= 0 for u in units)


def _ledger_ok(led):
    if not isinstance(led, dict) or led.get('pilot') != PILOT: return False
    if not all(isinstance(led.get(k), dict) for k in ('requests', 'units', 'events')): return False
    for q in led['requests'].values():
        if not isinstance(q, dict) or not isinstance(q.get('sku'), str) or type(q.get('qty')) is not int or q['qty'] < 1:
            return False
        if not num(q.get('ub')) or q['ub'] <= 0 or q.get('status') not in (None, 'supplied')+RELEASED: return False
        if q.get('total') is not None and not (num(q['total']) and q['total'] >= 0): return False
    for u in led['units'].values():
        if not isinstance(u, dict) or not isinstance(u.get('sku'), str) or not num(u.get('cost')) or u['cost'] < 0:
            return False
    for e in led['events'].values():
        if not isinstance(e, dict) or e.get('kind') not in ('settle', 'broken', 'expire'): return False
    return True


def load_ledger(state):
    try:
        led = json.loads((Path(state)/LEDGER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return led if _ledger_ok(led) else None


def request_key(req):
    return f"{req['invocation']}:{req['index']}"


def record_requests(state, entries):
    """Durable BEFORE the pending rows/buy line: a crash can only over-count, never recycle lifetime room."""
    path = Path(state)/LEDGER
    led = load_ledger(state)
    if led is None: raise ValueError('ledger invalid')
    for k, v in entries.items(): led['requests'].setdefault(k, v)
    _atomic(path, led)


def lifetime(led):
    """Confirmed actual totals + upper bound of every unresolved request; released only on proven refusal. Never
    below the actual cost of the pilot units seen on hand."""
    by_req = 0
    for q in led['requests'].values():
        if q.get('status') in RELEASED: continue
        by_req += q['total'] if q.get('status') == 'supplied' and num(q.get('total')) else q['ub']
    return max(by_req, sum(u['cost'] for u in led['units'].values()))


def _supplied(led):
    """Per SKU: units and exact cost the kit log proves supplied (upper bound where the total was not logged)."""
    sup = {}
    for q in led['requests'].values():
        if q.get('status') != 'supplied': continue
        s = sup.setdefault(q['sku'], {'qty':0, 'cost':0, 'exact':True, 'first_at':q.get('at')})
        s['qty'] += q['qty']
        s['cost'] += q['total'] if num(q.get('total')) else q['ub']
        s['exact'] = s['exact'] and num(q.get('total'))
        if num(q.get('at')) and not (num(s['first_at']) and s['first_at'] <= q['at']): s['first_at'] = q['at']
    return sup


def unattributed(led):
    """Supplied pilot units never durably observed (arrived and left between inventory snapshots), per SKU: count
    and the supplied cost not represented by observed unit costs. No per-unit allocation is invented."""
    out = {}
    for sku, s in _supplied(led).items():
        obs = [u['cost'] for u in led['units'].values() if u['sku'] == sku]
        if s['qty'] > len(obs): out[sku] = {'missing':s['qty']-len(obs), 'cost':max(0, s['cost']-sum(obs))}
    return out


def financial(led, new):
    """Financial reservation reconciliation, separate from physical row->unit association (none is inferred).
    A request is financially confirmed only when the durable ledger says 'supplied' with an exact total; its pending rows
    then stop carrying the upper bound (counted once per request, duplicates collapse). Per SKU, that exact confirmed
    cost is discharged only by exact costs of pilot units the ledger observed (on hand or gone) supplied no earlier than
    the SKU's first confirmed request - SKEW_S; the remainder stays held as unmatched confirmed capital risk (no sale,
    missing snapshot or age releases it). Rows whose ledger result is unknown/inexact (incl. a pending-before-ledger
    crash) keep their full reserve. A row whose exact result disagrees with the ledger is a conflict (fail closed).
    Aggregate bound per SKU: held UB >= actual unknown cost, so held + max(0, confirmed - observed) >= unobserved cost."""
    reqs, held, conflicts, confirmed = led['requests'], [], [], {}
    for k, q in reqs.items():
        if q.get('status') == 'supplied' and num(q.get('total')): confirmed[k] = q
    for r in new:
        req = r.get('request')
        key = request_key(req) if r.get('one_day_pilot') == PILOT and isinstance(req, dict)             and isinstance(req.get('invocation'), str) and 'index' in req else None
        q, st = reqs.get(key) if key else None, r.get('supply_result')
        total = (r.get('supply_evidence') or {}).get('total') if isinstance(r.get('supply_evidence'), dict) else None
        if q is not None and st is not None and q.get('status') is not None and (
                st != q['status'] or q['sku'] != r.get('sku') or st == 'supplied' and total != q.get('total')):
            conflicts.append(key)
            held.append(r)
        elif key in confirmed and st == 'supplied':
            continue
        else:
            held.append(r)
    by_sku = {}
    for k, q in confirmed.items():
        s = by_sku.setdefault(q['sku'], {'confirmed':0, 'requests':[], 'since':q.get('at')})
        s['confirmed'] += q['total']
        s['requests'].append(k)
        if num(q.get('at')) and not (num(s['since']) and s['since'] <= q['at']): s['since'] = q['at']
    unmatched = {}
    for sku, s in by_sku.items():
        cover = sum(u['cost'] for u in led['units'].values() if u['sku'] == sku and num(s['since']) and (
            u.get('supplied_at_unknown') and u.get('attributed') or
            num(u.get('supplied_at')) and u['supplied_at'] >= s['since']-SKEW_S))
        unmatched[sku] = {'cost':max(0, s['confirmed']-cover), 'confirmed':s['confirmed'], 'observed_cover':cover,
                          'requests':sorted(s['requests'])}
    return {'held_rows':held, 'conflicts':sorted(set(conflicts)), 'unmatched_confirmed':unmatched,
            'unmatched_total':sum(v['cost'] for v in unmatched.values()),
            'recovery':'partial_conservative' if any(v['cost'] for v in unmatched.values()) else 'exact_cover',
            'note':'exact confirmed acquisition cost replaces upper-bound reserves; unmatched confirmed cost is held '
                   'as capital risk (concurrent only, never subtracted from cash again) until exact observed unit cost '
                   'covers it; no physical unit mapping inferred'}


def losses(led, inv, now):
    """Gross attributable losses per pilot unit, never netted across units or against a unit's own sale profit:
    sold -> margin max(0, cost - price) + refund outflow (sum of observed refunds minus independently evidenced
    recovered value: none is evidenced, so 0); expired -> cost; gone without sale/expiry evidence -> cost
    (provisional, replaced when the sale shows up); goods also accrue storage (cost x storage_per_day x days held).
    The acquisition cost is counted at most once per unit. An observed refund with an unknown amount sets
    unknown=True (acquisition fails closed), never 0. Supplied but never observed units: their unrepresented supplied
    cost is a provisional loss (unattributed_acquisition_cost), never offset by a presumed sale.
    Components (corrected after parent review; total semantics unchanged except that known refunds now always count):
      confirmed (immutable exact events only, can only grow): exact settled negative margins max(0, cost - price) +
        exact observed refund amounts (in ANY unit state) + cost of units named by an explicit market expire event;
      provisional_acquisition: cost of gone-without-settle units, inferred expiry (gone/on hand past expires_at with no
        expire row) and unattributed supplied cost; replaced when an exact settle/expire row appears;
      estimated_storage: ALL goods storage accrual (incl. activation-bound backfilled arrivals). It is an estimate that
        an exact sale ts may correct downward; that is never a confirmed-loss reset.
    total = confirmed + provisional_acquisition + estimated_storage; the cap uses total. A unit with both a settle and an
    expire row is a conflict: unknown=True (acquisition closed) and the expire cost stays confirmed (never erased)."""
    onhand = {u['unit'] for u in inv.get('units', []) if isinstance(u, dict)}
    rate = inv.get('storage_per_day') if num(inv.get('storage_per_day')) else .01
    ev = {}
    for e in led['events'].values():
        for uid in e.get('units') or [e.get('unit')]: ev.setdefault(uid, []).append(e)
    out = {'total':0, 'margin':0, 'expired':0, 'refund':0, 'storage':0, 'provisional':0, 'unknown':False,
           'unknown_refund_events':[], 'conflicting_events':[], 'units':{}}
    conf = {'margin':0, 'refund':0, 'expired_event':0}
    prov = {'unaccounted':0, 'inferred_expiry':0, 'unattributed':0}
    prov_units = []
    for uid, u in sorted(led['units'].items()):
        es = ev.get(uid, [])
        sale = next((e for e in es if e['kind'] == 'settle'), None)
        expire_event = any(e['kind'] == 'expire' for e in es)
        broken = [e for e in es if e['kind'] == 'broken']
        refund = sum(e['refunded'] for e in broken if num(e.get('refunded')))
        bad = [e.get('seq') for e in broken if not num(e.get('refunded'))]
        if bad:
            out['unknown'] = True
            out['unknown_refund_events'] += bad
        end = sale['ts'] if sale and num(sale.get('ts')) else u.get('gone_at') or now
        # arrival unknown (backfilled unit): storage from activation, a labelled conservative assumption
        start = u['supplied_at'] if num(u.get('supplied_at')) else u['storage_from'] if num(u.get('storage_from')) else end
        store = u['cost']*rate*max(0, end-start)/86400 if u.get('kind') == 'goods' else 0
        out['refund'] += refund
        conf['refund'] += refund  # an exact observed refund is an outflow whatever else is (not yet) known
        provisional = False
        if sale and expire_event:
            state_, loss = 'conflict', u['cost']  # conservative: the expiry write-off is kept, the sale is not used
            out['unknown'] = True
            out['conflicting_events'].append(uid)
            out['expired'] += loss
            conf['expired_event'] += loss
        elif sale:
            loss = max(0, u['cost']-sale['price'])
            state_ = 'sold'
            out['margin'] += loss
            conf['margin'] += loss
        elif expire_event:
            state_, loss = 'expired', u['cost']
            out['expired'] += loss
            conf['expired_event'] += loss
        elif u.get('kind') == 'food' and num(u.get('expires_at')) and (
                uid in onhand and u['expires_at'] <= now or
                uid not in onhand and u['expires_at'] <= (u.get('gone_at') or now)+60):
            state_, loss, provisional = 'expired', u['cost'], True  # inferred: no expire row yet
            out['expired'] += loss
            prov['inferred_expiry'] += loss
        elif uid in onhand:
            state_, loss = 'held', 0
        else:
            state_, loss, provisional = 'unaccounted', u['cost'], True
            out['provisional'] += loss
            prov['unaccounted'] += loss
        out['storage'] += store
        out['units'][uid] = {'state':state_, 'loss':loss+refund+store, 'provisional':provisional,
                             'estimated_storage':store}
        out['total'] += loss+refund+store
        if provisional:
            prov_units.append({'unit':uid, 'sku':u['sku'], 'state':state_, 'cost':u['cost'],
                               'gone_at':u.get('gone_at'), 'gone_kst':kst(u.get('gone_at'))})
    out['unattributed'] = unattributed(led)
    out['unattributed_acquisition_cost'] = sum(v['cost'] for v in out['unattributed'].values())
    prov['unattributed'] = out['unattributed_acquisition_cost']
    out['provisional'] += out['unattributed_acquisition_cost']
    out['total'] += out['unattributed_acquisition_cost']
    out['confirmed'] = dict(conf, total=sum(conf.values()))
    out['confirmed_total'] = out['confirmed']['total']
    out['provisional_acquisition'] = dict(prov, total=sum(prov.values()))
    out['provisional_acquisition_total'] = out['provisional_acquisition']['total']
    out['estimated_storage'] = out['storage']
    out['provisional_units'] = prov_units
    return out


def preflight(state):
    """Integrity before any pending mutation (supply resolution, reconciliation, pruning): None when the config is
    readable and bound by the gate, the ledger is valid and the legacy ledger matches the config; else the reason.
    Expiry is NOT checked here: post-expiry observation/reconciliation of owned stock must continue."""
    state = Path(state)
    try:
        raw = (state/CONFIG).read_bytes()
        config = json.loads(raw)
        gate = _gate(state)
        bound = gate.get('one_day_pilot') if isinstance(gate, dict) else None
        if not isinstance(bound, dict): return 'gate_closed'
        if bound.get('config_sha256') != sha(raw): return 'config_hash_mismatch'
        if load_ledger(state) is None: return 'ledger_invalid'
        if config['legacy']['ledger_sha256'] != sha((state/LEGACY).read_bytes()): return 'legacy_ledger_invalid'
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 'config_invalid'
    return None


def _gate(state):
    try:
        return json.loads((Path(state)/'seller-v2-approved.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def context(state, now, inv, pending, frozen_ids, complete, legacy):
    """Room for this plan, or active=False with the first failing reason. Never raises."""
    state = Path(state)
    out = {'pilot':PILOT, 'active':False}
    try:
        try:
            raw = (state/CONFIG).read_bytes()
            config = json.loads(raw)
        except (OSError, ValueError):
            return dict(out, reason='config_invalid')
        gate = _gate(state)
        bound = gate.get('one_day_pilot') if isinstance(gate, dict) else None
        if not isinstance(gate, dict) or gate.get('approved') is not True or gate.get('policy_version') != 'bounded-v2' \
                or not isinstance(bound, dict):
            return dict(out, reason='gate_closed')
        if bound.get('config_sha256') != sha(raw): return dict(out, reason='config_hash_mismatch')
        caps = config['caps']
        mode = loss_ceiling_mode(config)
        fmode = financial_limits_mode(config)
        out.update(activated_at=config['activated_at'], expires_at=config['expires_at'], caps=caps,
                   loss_ceiling_mode=mode, financial_limits_mode=fmode)
        if mode is None: return dict(out, reason='loss_ceiling_mode_invalid')
        if fmode is None: return dict(out, reason='financial_limits_mode_invalid')
        if legacy is None or config['legacy']['ledger_sha256'] != sha((state/LEGACY).read_bytes()) \
                or legacy['reserve'] != config['legacy']['reserve'] or legacy['count'] != config['legacy']['count']:
            return dict(out, reason='legacy_ledger_invalid')
        if not complete: return dict(out, reason='legacy_rows_changed')
        led = load_ledger(state)
        if led is None: return dict(out, reason='ledger_invalid')
        if not _inventory_ok(inv): return dict(out, reason='inventory_invalid')
        units = inv['units']
        sold = sorted(k for k, e in led['events'].items() if e['kind'] == 'settle')
        stage = 2 if sold else 1
        cap = caps['stage2'] if stage == 2 else caps['stage1']
        new = [r for r in pending if r.get('matched_unit_id') is None and id(r) not in frozen_ids]
        if not all(num(r.get('reserve')) and r['reserve'] >= 0 for r in new): return dict(out, reason='pending_invalid')
        fin = financial(led, new)
        by_sku = {}
        for u in units: by_sku[u['sku']] = by_sku.get(u['sku'], 0)+u['cost']
        for r in fin['held_rows']: by_sku[r['sku']] = by_sku.get(r['sku'], 0)+r['reserve']
        for sku, v in fin['unmatched_confirmed'].items():
            if v['cost']: by_sku[sku] = by_sku.get(sku, 0)+v['cost']
        concurrent = sum(by_sku.values())
        spent = lifetime(led)
        loss = losses(led, inv, now)
        new_reserve = sum(r['reserve'] for r in fin['held_rows'])  # confirmed costs are already debited from balance
        import warranty  # lazy: warranty imports this module
        # warranty-72h-v1: every quoted intent + issued authority at full sell price, retained (unknown) until an exact
        # terminal mapping exists; a known refund may also appear in loss -> conservative double count, documented.
        wl = warranty.pilot_liability(state)
        if wl is None: return dict(out, reason='warranty_state_invalid')
        cash = inv['balance']-legacy['reserve']-new_reserve-wl-caps['cash_floor']
        bank = inv['balance']-new_reserve-wl-(inv.get('bankrupt_line') if num(inv.get('bankrupt_line')) else 500000)
        out.update(stage=stage, stage_cap=cap, sku_cap=None, settled_sale_events=sold,
                   legacy_count=legacy['count'], legacy_reserve=legacy['reserve'], new_pending=len(new),
                   new_reserve=new_reserve, concurrent=concurrent, by_sku=by_sku, lifetime_committed=spent,
                   gross_loss=loss['total'], loss=loss, cash_room=cash, bank_room=bank, warranty_liability=wl,
                   per_run_lines=caps['per_run_lines'],
                   financial={k:v for k, v in fin.items() if k != 'held_rows'})
        if fin['conflicts']: return dict(out, reason='financial_evidence_conflict')
        if not config['activated_at'] <= now < config['expires_at']: return dict(out, reason='expired')
        if loss['unknown']: return dict(out, reason='loss_unknown')
        if led.get('conflicts'): return dict(out, reason='evidence_conflict')
        # a supplied unit whose identity is unknown: its refunds/expiry would be unobservable -> no new acquisition
        if loss['unattributed']: return dict(out, reason='unattributed_acquisition')
        if fmode == 'disabled':
            avail = inv['balance']-new_reserve  # genuine in-flight reserves only: operational, not a risk ceiling
            out['risk_ceiling'] = {'mode':'disabled', 'enforced':False, 'gross_loss_plus_warranty':loss['total']+wl,
                                   'cap':None, 'historical_gross_loss_cap_ignored':caps.get('gross_loss')}
            out['financial_limits'] = {'mode':'disabled', 'enforced':False, 'available_cash':avail,
                                       'in_flight_reserve':new_reserve, 'not_enforced':list(FINANCIAL_LIMITS_OFF),
                                       'historical_caps':dict(caps),
                                       'retained_accounting':{'legacy_UNKNOWN_reserve':legacy['reserve'],
                                                              'warranty_liability':wl, 'lifetime_committed':spent,
                                                              'concurrent':concurrent, 'gross_loss':loss['total']}}
            out.update(per_run_lines=KIT_MAX_LINES, room=max(0, avail))
            if avail <= 0: return dict(out, reason='cash_insufficient')
            return dict(out, active=True, reason='active')
        out['financial_limits'] = {'mode':'enforced', 'enforced':True}
        out['risk_ceiling'] = {'mode':mode, 'enforced':mode == 'enforced', 'gross_loss_plus_warranty':loss['total']+wl,
                               'cap':caps['gross_loss'] if mode == 'enforced' else None,
                               'historical_gross_loss_cap_ignored':caps.get('gross_loss') if mode == 'disabled' else None}
        if mode == 'enforced' and loss['total']+wl >= caps['gross_loss']: return dict(out, reason='loss_cap')
        if cash <= 0 or bank <= 0: return dict(out, reason='cash_insufficient')
        room = min(cap-concurrent, caps['lifetime_spend']-spent, cash, bank)
        out['room'] = max(0, room)
        if room <= 0: return dict(out, reason='capacity_full' if cap-concurrent <= 0 else
                                  'lifetime_cap' if caps['lifetime_spend']-spent <= 0 else 'cash_insufficient')
        return dict(out, active=True, reason='active')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return dict(out, reason='error')


def candidates(catalog, evidence, stage, rotation=None):
    """Proven own SKUs (two distinct exact own settle ids), ranked by median own realized price over the expected
    contract cost share; food only when shelf life is greater than the expected rotation. rotation (from
    microeconomics.food_rotation) replaces the fixed 72 h only for a SKU with qualified own rotation evidence:
    actual shelf life (min of catalog and observed own units) > empirical rotation + buffer."""
    import statistics
    out = []
    for sku, ids in evidence['own_sku_ids'].items():
        c = catalog.get(sku)
        if len(set(ids)) < 2 or not isinstance(c, dict) or c.get('kind') not in EXPECTED_COST: continue
        price = c.get('list_price')
        if not num(price) or price <= 0: continue
        r = (rotation or {}).get(sku)
        if c['kind'] == 'food' and isinstance(r, dict) and r.get('qualified') is True:
            if not (num(r.get('actual_shelf_hours')) and num(r.get('threshold_hours'))
                    and r['actual_shelf_hours'] > r['threshold_hours']): continue
        elif c['kind'] == 'food' and not (num(c.get('shelf_hours')) and c['shelf_hours'] > ROTATION_H): continue
        paid = [v['price'] for k, v in evidence['own_ledger'].items() if v.get('sku') == sku and num(v.get('price'))]
        if len(paid) < 2: continue
        ratio = statistics.median(paid)/price/EXPECTED_COST[c['kind']]
        if ratio <= 1: continue
        out.append((-ratio, sku, upper_bound(price), None))  # 3rd field: retired held_max, kept for tuple shape
    return [(sku, ub, held, -r) for r, sku, ub, held in sorted(out)]


def write_status(state, ctx, now, extra=None):
    """Reporter-readable status: activation/expiry, caps, stage, counters, losses, legacy, baseline, blockers."""
    state = Path(state)
    try:
        config = json.loads((state/CONFIG).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        config = None
    led = load_ledger(state)
    keep = ('stage', 'stage_cap', 'sku_cap', 'room', 'concurrent', 'by_sku', 'new_pending', 'new_reserve',
            'lifetime_committed', 'gross_loss', 'cash_room', 'bank_room', 'legacy_count', 'legacy_reserve',
            'settled_sale_events', 'warranty_liability', 'loss_ceiling_mode', 'risk_ceiling', 'financial_limits_mode',
            'financial_limits', 'per_run_lines')
    status = {'pilot':PILOT, 'updated_at':now, 'updated_kst':kst(now), 'active':ctx.get('active'),
              'reason':ctx.get('reason'), 'blockers':[] if ctx.get('active') else [ctx.get('reason')],
              **{k:ctx[k] for k in keep if k in ctx}}
    if isinstance(ctx.get('loss'), dict):
        status['loss'] = ctx['loss']
        status['unattributed_acquisition_cost'] = ctx['loss'].get('unattributed_acquisition_cost')
        status['unattributed'] = ctx['loss'].get('unattributed')
    if config:
        status.update(activated_at=config.get('activated_at'), expires_at=config.get('expires_at'),
                      activated_kst=config.get('activated_kst'), expires_kst=config.get('expires_kst'),
                      caps=config.get('caps'), legacy=config.get('legacy'), baseline=config.get('baseline'),
                      sources=config.get('sources'), authorization=config.get('authorization'))
        mode = loss_ceiling_mode(config)
        status['loss_ceiling_mode'] = mode if mode else 'invalid'
        if mode == 'disabled':
            status['risk_ceiling'] = dict(status.get('risk_ceiling') or {}, mode='disabled', enforced=False,
                                          cap=None, historical_gross_loss_cap_ignored=(config.get('caps') or {}).get('gross_loss'),
                                          note='combined gross loss + warranty ceiling DISABLED (amendment #7); '
                                               'caps.gross_loss is historical only and not enforced; losses and warranty '
                                               'liability still counted; warranty liability still reduces cash/bank room')
        fmode = financial_limits_mode(config)
        status['financial_limits_mode'] = fmode if fmode else 'invalid'
        if fmode == 'disabled':
            status['financial_limits'] = dict(status.get('financial_limits') or {}, mode='disabled', enforced=False,
                                              note='ALL simulated financial limits DISABLED (amendment #8): room = '
                                                   'balance - in-flight reserves; caps historical only; legacy reserve, '
                                                   'warranty liability, lifetime/concurrent/loss still counted and '
                                                   'reported, not held back')
            if isinstance(status.get('risk_ceiling'), dict):
                status['risk_ceiling']['note'] = 'combined ceiling disabled (#7) and all financial limits disabled (#8)'
    if led:
        status['requests'] = led['requests']
        status['units'] = led['units']
        status['events'] = led['events']
    status['loss_note'] = ('gross per pilot unit: max(0, cost-price) on sale + refund outflow (observed refunds minus '
                           'independently evidenced recovered value; none evidenced so 0) + expiry/write-off at cost + '
                           'gone-without-evidence at cost (provisional) + goods storage accrual; acquisition cost counted '
                           'once per unit; no sale profit (same or other unit) offsets; unknown refund amount -> '
                           'loss_unknown, acquisition closed; legacy losses are not observed and stay covered by the '
                           'full frozen legacy reserve. total = confirmed (exact negative settle margins + exact refunds + '
                           'explicit expire events; only grows) + provisional_acquisition (gone-without-settle / inferred '
                           'expiry / unattributed; replaced by exact rows, never by profit offset) + estimated_storage '
                           '(an accrual estimate an exact sale ts may correct; never confirmed). The cap uses total')
    gate = _gate(state)
    # 'sources' = frozen activation-time hashes; installed = what the gate approves now (post-correction installs)
    status['installed_source_sha256'] = gate.get('source_sha256') if isinstance(gate, dict) else None
    status['gate_approved'] = gate.get('approved') if isinstance(gate, dict) else None
    status.update(extra or {})
    _atomic(state/STATUS, status)
    return status


SELLER_JOURNAL = 'decisions.jsonl'  # seller_r3.journal(): our own seller's decision rows (never buyer text)
STORAGE_NOTE = ('arrival time unknown (unit never observed on hand): supplied_at null; storage accrued from '
                'activation as a conservative assumption, not an observed arrival')


def seller_evidence(state, uid, act):
    """Our seller's own decision rows naming uid with a cost claim (complete lines only, ts >= activation, decided_by
    set): {'sku', 'cost', 'seller_rows': [line, sha256, event, ts, task], 'file', 'file_sha256'}; {'conflict': why} when
    a cost-claim row is corrupt or rows disagree on SKU/cost; None when there is no such row."""
    path = Path(state)/SELLER_JOURNAL
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    complete = raw[:raw.rfind(b'\n')+1]
    rows = []
    for n, line in enumerate(complete.split(b'\n')[:-1], 1):
        if uid.encode() not in line: continue
        try:
            d = json.loads(line)
        except ValueError:
            return {'conflict':f'{SELLER_JOURNAL} line {n} names the unit but is not valid JSON'}
        if not isinstance(d, dict) or d.get('unit') != uid or not d.get('decided_by') or d.get('cost') is None: continue
        if not num(d.get('ts')) or d['ts'] < act: continue
        if not num(d['cost']) or d['cost'] < 0 or not isinstance(d.get('sku'), str):
            return {'conflict':f'{SELLER_JOURNAL} line {n}: invalid cost/sku'}
        rows.append({'line':n, 'sha256':sha(line), 'event':d.get('event'), 'ts':d['ts'], 'task':d.get('task'),
                     'sku':d['sku'], 'cost':d['cost']})
    if not rows: return None
    if len({(r['sku'], r['cost']) for r in rows}) != 1:
        return {'conflict':f'{SELLER_JOURNAL} rows disagree on sku/cost for {uid}'}
    return {'sku':rows[0]['sku'], 'cost':rows[0]['cost'], 'seller_rows':rows, 'file':str(path), 'file_sha256':sha(raw)}


def _norm(url):
    return url.rstrip('/') if isinstance(url, str) and url.strip('/') else None


def observe(state, rows, inv, cache, now):
    """Idempotent, before any pending release/prune: (1) exact kit supply results of pilot requests from their
    pending rows, (2) every unit supplied at/after activation that was not in the baseline (cost fixed at arrival),
    first_seen/gone_at, (3) own-shop settle/broken and expire ledger entries of those units, keyed by ledger seq.
    A missing/corrupt config or ledger is never rewritten or reset. Returns True when the ledger changed."""
    state = Path(state)
    try:
        config = json.loads((state/CONFIG).read_text(encoding='utf-8'))
        act, base = config['activated_at'], set(config['baseline']['units'])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    led = load_ledger(state)
    if led is None or not _inventory_ok(inv): return False
    before = json.dumps(led, sort_keys=True)
    for r in rows:
        req = r.get('request')
        if r.get('one_day_pilot') != PILOT or not isinstance(req, dict) or not isinstance(req.get('invocation'), str):
            continue
        q = led['requests'].get(request_key(req))
        st = r.get('supply_result')
        if q is None or q.get('status') is not None or st not in ('supplied',)+RELEASED: continue
        total = (r.get('supply_evidence') or {}).get('total')
        q.update(status=st, total=total if st == 'supplied' and num(total) else None, resolved_at=now)
    onhand = set()
    for u in inv['units']:
        onhand.add(u['unit'])
        rec = led['units'].get(u['unit'])
        if rec is not None:
            rec['gone_at'] = None
            if num(u.get('expires_at')) and not (num(rec.get('expires_at')) and rec['expires_at'] <= u['expires_at']):
                rec['expires_at'] = u['expires_at']  # conservative: the earliest expiry ever reported
            continue
        if u['unit'] in base or not num(u.get('supplied_at')) or u['supplied_at'] < act-SKEW_S: continue
        led['units'][u['unit']] = {'sku':u['sku'], 'cost':u['cost'], 'kind':u.get('kind'), 'grade':u.get('grade'),
                                   'supplied_at':u['supplied_at'], 'expires_at':u.get('expires_at'),
                                   'first_seen':now, 'gone_at':None}
    for uid, rec in led['units'].items():
        if uid not in onhand and rec.get('gone_at') is None: rec['gone_at'] = now
    own = {_norm(x) for x in (cache or {}).get('own_urls', [])} - {None}
    try:
        catalog = json.loads((state/'catalog.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        catalog = {}
    sup = _supplied(led)
    # Never-observed supplied unit, restored only from exact evidence: an own-shop public settle after activation of a
    # unit id outside the baseline and the ledger, for a SKU with supplied-but-unobserved units, plus either
    #  (a) our seller's own decision rows naming that unit with one consistent SKU/cost (<= the SKU's unrepresented
    #      supplied cost; == it when exactly one unit is missing), or
    #  (b) exactly ONE missing unit with every supply total logged (exact residual).
    # Every supply after activation is a pilot request (stock 0 at activation). Contradictions are recorded in
    # 'conflicts' and fail acquisition closed; nothing is allocated by SKU/time proximity. Arrival time stays unknown.
    conflicts = led.setdefault('conflicts', {})
    for row in sorted(((cache or {}).get('rows') or [])+((cache or {}).get('price_rows') or []),
                      key=lambda r:r.get('seq') if isinstance(r, dict) and type(r.get('seq')) is int else -1):
        if not isinstance(row, dict) or row.get('kind') != 'settle': continue
        d = row.get('data')
        if not isinstance(d, dict) or _norm(d.get('shop')) not in own or not num(row.get('ts')) or row['ts'] < act: continue
        uid, sku = d.get('unit'), d.get('item')
        if not isinstance(uid, str) or uid in led['units'] or uid in base: continue
        miss = unattributed(led).get(sku)
        if not miss: continue
        exact = miss['missing'] == 1 and sup[sku]['exact']
        ev = seller_evidence(state, uid, act)
        if ev and 'conflict' not in ev and (ev['sku'] != sku or ev['cost'] > miss['cost'] or
                                            exact and ev['cost'] != miss['cost']):
            ev = {'conflict':f"seller sku/cost {ev['sku']}/{ev['cost']} vs settle {sku} and unrepresented supplied "
                             f"cost {miss['cost']} ({miss['missing']} missing)"}
        if ev and 'conflict' in ev:
            conflicts[uid] = ev['conflict']
            continue
        if not ev and not exact: continue
        evidence = {'settle_seq':row['seq'], 'settle_ts':row['ts'], 'settle_price':d.get('amount')}
        if ev: evidence.update(seller_rows=ev['seller_rows'], file=ev['file'], file_sha256=ev['file_sha256'])
        led['units'][uid] = {'sku':sku, 'cost':ev['cost'] if ev else miss['cost'],
                             'kind':(catalog.get(sku) or {}).get('kind'), 'grade':d.get('grade'), 'supplied_at':None,
                             'supplied_at_unknown':True, 'storage_from':act, 'storage_note':STORAGE_NOTE,
                             'expires_at':d.get('expires_at'), 'first_seen':now, 'gone_at':now,
                             'attributed':'exact_seller_decision' if ev else 'exact_residual_join',
                             'evidence_seq':row['seq'], 'evidence':evidence}
    # Units restored before this correction by the residual join carried the plan time as an assumed arrival: drop it.
    for uid, u in led['units'].items():
        if u.get('attributed') and not u.get('supplied_at_unknown'):
            u.update(previous_supplied_at_assumption=u.get('supplied_at'), supplied_at=None, supplied_at_unknown=True,
                     storage_from=act, storage_note=STORAGE_NOTE)
            ev = seller_evidence(state, uid, act)
            evidence = u.setdefault('evidence', {'settle_seq':u.get('evidence_seq')})
            if ev and 'conflict' not in ev and (ev['sku'], ev['cost']) == (u['sku'], u['cost']):
                evidence.update(seller_rows=ev['seller_rows'], file=ev['file'], file_sha256=ev['file_sha256'])
            elif ev:
                conflicts[uid] = ev.get('conflict') or \
                    f"seller sku/cost {ev['sku']}/{ev['cost']} vs recorded {u['sku']}/{u['cost']}"
    for row in list((cache or {}).get('rows') or [])+list((cache or {}).get('price_rows') or []):
        if not isinstance(row, dict) or type(row.get('seq')) is not int or not isinstance(row.get('data'), dict): continue
        kind, d = row.get('kind'), row['data']
        key = f"{kind}:{row['seq']}"
        if key in led['events']: continue
        if kind == 'settle' and d.get('unit') in led['units'] and _norm(d.get('shop')) in own and num(d.get('amount')):
            led['events'][key] = {'kind':kind, 'seq':row['seq'], 'ts':row.get('ts'), 'unit':d['unit'],
                                  'sku':d.get('item'), 'price':d['amount']}
        elif kind == 'broken' and d.get('unit') in led['units'] and _norm(d.get('shop')) in own:
            led['events'][key] = {'kind':kind, 'seq':row['seq'], 'ts':row.get('ts'), 'unit':d['unit'],
                                  'refunded':d['refunded'] if num(d.get('refunded')) else None}  # unknown, not 0
        elif kind == 'expire' and isinstance(d.get('units'), list) and set(d['units']) & set(led['units']):
            # the market names the shop here, not its URL: the unit ids (ours, from our inventory) identify it
            led['events'][key] = {'kind':kind, 'seq':row['seq'], 'ts':row.get('ts'),
                                  'units':sorted(set(d['units']) & set(led['units'])), 'shop':d.get('shop')}
    if json.dumps(led, sort_keys=True) == before: return False
    _atomic(state/LEDGER, led)
    return True
