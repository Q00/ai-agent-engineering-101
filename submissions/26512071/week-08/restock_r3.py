"""bounded-v2 restock: own SKU reorders, public ledger sample (newest 500, not complete), local biased sample.
plan() is network-free; only the CLI refreshes the public cache. No model/forum calls."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time
from evidence_memory import sync, summary, locked, atomic_json
from public_evidence import from_cache, load_cache, refresh
import supply_journal
import risk_pilot
import oneday_pilot
import microeconomics

POLICY = 'bounded-v2'
TARGET = 2  # units held (on hand + outstanding) for a proven own SKU
MAX_UNITS = 12  # pilot ceiling: on hand + outstanding + newly planned
MAX_EXPLORATION = 3  # unproven units held (on hand + outstanding + planned)
HIGH_MIN, HIGH_MAX = 60000, 100000  # high-price goods band: HIGH_MIN < list <= HIGH_MAX
HIGH_TARGET = 1  # units held for a qualified high-price SKU
HIGH_SHARE = .4  # high-price exposure ceiling as a share of the 5% exposure budget
HIGH_REALIZED = .75  # median own realized price must reach this share of list
PER_CALL = 20  # one-day buy line: bounded kit request guard per call, explicitly NOT a per-SKU inventory cap
# demand-cover-v1 (2026-11-14): hold about COVER_H hours of evidenced demand. A policy ASSUMPTION (restock windows were
# observed every ~20-90 min), not a per-SKU cap: the target grows with the evidenced rate without any fixed ceiling.
COVER_H = 6
DEMAND_WINDOWS_H = (3, 12)  # own exact pilot settles counted per window; the faster rate is used (never starves a mover)
RECENT_SUPPLIED_S = 600  # a 'supplied' row this new may not be on hand in inventory.json yet: counted as unconfirmed
SIZING_POLICY = ('demand-cover-v1: qty = min(need, money room // contract upper bound, PER_CALL), need = ceil(rate x '
                 'COVER_H) - on hand - unconfirmed supply; rate = own exact pilot settles (max over 3 h/12 h windows), '
                 'else qualified public paid / shops / window, else a 1-unit probe only when nothing is held or '
                 'unconfirmed; no per-SKU held_max, no SKU-share concentration (retired); global stage/lifetime/loss/'
                 'cash caps as the config mode says (financial_limits disabled since #8: room = balance - in-flight '
                 'reserves, up to the kit 10 lines); worstbound-market-v1: public rate only when public median paid >= '
                 'contract worst-bound unit cost, else a 1-unit probe')
COVERAGE = ('source_url', 'fetched_at', 'age_s', 'stale', 'missing', 'complete', 'note', 'sample', 'settle_count',
            'unmapped', 'category_counts', 'own_sku_counts', 'own_ledger_ids')
SOURCES = ('seller_r3.py', 'restock_r3.py', 'public_evidence.py', 'supply_journal.py', 'risk_pilot.py',
           'review_discount.py', 'oneday_pilot.py', 'evidence_memory.py', 'harness.py',
           'microeconomics.py', 'openai_harness.py', 'sol_policy.py', 'forum_terra.py', 'review_r3.py', 'forum_r3.py',
           'warranty.py')
# reviewed-hash gate: every module the hooks import from this folder (review_discount/harness/openai_harness/sol_policy:
# seller_r3; forum_terra/forum_r3: review_r3 hook). sol-forum-v1 grew it 11 -> 15; warranty-72h-v1 -> 16 (seller_r3 and
# oneday_pilot import warranty.py); no name was removed.


def plan(state, now=None, invocation=None):
    try:
        with locked(Path(state)/'restock-plan.lock'):
            return _plan(state, now, invocation)
    except (OSError, ValueError, TypeError, KeyError):
        return {'buy':[]}


def _plan(state, now=None, invocation=None):
    state = Path(state)
    now = time.time() if now is None else now
    try:
        inv = json.loads((state/'inventory.json').read_text(encoding='utf-8'))
        catalog = json.loads((state/'catalog.json').read_text(encoding='utf-8'))
        if inv.get('round',0)<3 or not isinstance(inv.get('balance'),(int,float)) or not inv.get('start'):
            return {'buy':[]}
        mem = sync(state)
        observations = summary(mem, catalog)
        # Local buyer settled sample: biased by our own orders, labelled separately from public evidence.
        local = observations['settled_categories']
        local_ids = {}
        for key, p in mem.get('purchases', {}).items():
            cat = (catalog.get(p.get('sku')) or {}).get('category')
            if cat and key.isdigit(): local_ids.setdefault(cat, []).append(int(key))
        cache = load_cache(state)
        evidence = from_cache(cache, catalog, now)
        public, own = evidence['category_counts'], evidence['own_sku_counts']
        observations.update(policy_version=POLICY, local_biased_counts=local,
                            local_biased_memory_ids=sorted(i for ids in local_ids.values() for i in ids),
                            local_note='own buyer purchases; biased by our own orders, NOT market demand',
                            public_coverage={k:evidence[k] for k in COVERAGE})
        observations['mode'] = 'own-sku-and-public-sample-priority' if own or public else \
            'local-biased-priority' if local else 'limited-exploration-no-settled-data'
        journal = state/'restock-pending.json'
        pending = json.loads(journal.read_text()) if journal.exists() else []
        units = inv.get('units',[])
        owned = {u['sku'] for u in units}
        # Age alone never clears an unmatched row: an unobserved arrival is an unknown outcome, not a failure.
        # Only an exact new-unit match or an operator-written failure note resolves it.
        # Rows with a request id get their own invocation's exact kit log result; the audit is durable before any
        # release. Only a provable no-unit result releases; 'supplied' stays counted (sold identity unknown).
        # Armed pilot with a missing/corrupt ledger or changed legacy rows: no resolve/reconcile/rewrite, no buy.
        armed = risk_pilot.armed(state)
        ledger = risk_pilot.load_ledger(state)
        frozen_ids, complete = risk_pilot.frozen(pending, ledger)
        day = oneday_pilot.configured(state)
        if armed and not complete:
            if day:
                oneday_pilot.write_status(state, {'active':False, 'reason':'legacy_rows_changed'}, now)
                _micro_status(state, {'active':False, 'reason':'legacy_rows_changed'}, inv, None, now)
            return {'buy':[]}
        bad = oneday_pilot.preflight(state) if day else None
        if bad:  # one-day integrity fails: no supply resolution/reconciliation/pruning, pending bytes untouched
            oneday_pilot.write_status(state, {'active':False, 'reason':bad}, now)
            _micro_status(state, {'active':False, 'reason':bad}, inv, None, now)
            return {'buy':[]}
        audit = supply_journal.resolve(state, pending, catalog)
        if audit:
            with open(state/'restock-supply-evidence.jsonl', 'a', encoding='utf-8') as stream:
                for a in audit: stream.write(json.dumps(dict(a, at=now), ensure_ascii=False, sort_keys=True)+'\n')
                stream.flush()
                os.fsync(stream.fileno())
        # Frozen legacy UNKNOWN rows (risk_pilot ledger) are never released, matched or pruned: full reserve held.
        failed = [r for r in pending if r.get('matched_unit_id') is None and id(r) not in frozen_ids and (
            _operator_failed(r) or r.get('supply_result') in supply_journal.RELEASED)]
        pending = _reconcile([r for r in pending if r not in failed], units, frozen_ids)
        if day:  # before pruning/release: supply results, arrivals, sales and losses of pilot units, idempotent
            oneday_pilot.observe(state, pending+failed, inv, cache, now)
        pending = _prune_matched(pending, now)
        outstanding = [r for r in pending if r.get('matched_unit_id') is None]
        held = _held_signal(outstanding, failed, now)
        if day:
            frozen_ids, complete = risk_pilot.frozen(pending, ledger)
            day_ctx = oneday_pilot.context(state, now, inv, pending, frozen_ids, complete, ledger)
            oneday_pilot.write_status(state, day_ctx, now)
            # microecon-v1 views + ranking preview on every normal hook, before any window/inactive return
            micro = microeconomics.evaluate(state, inv, catalog, evidence, day_ctx.get('stage', 1), now)
            _micro_status(state, day_ctx, inv, micro, now)
        window = inv.get('restock')
        if isinstance(window, dict) and window.get('open') is False and not (
                type(window.get('opens_at')) in (int, float) and math.isfinite(window['opens_at'])):
            # Closed with a missing/bad open time: fail closed, never reserve against an unknown window.
            d = {'reason':'window_invalid', 'chosen':[], 'outstanding':held}
            if day: d = _micro_decision(state, day_ctx, inv, micro, now, d)
            _decide(state, now, observations, d, {}, {})
            return {'buy':[]}
        if isinstance(window, dict) and window.get('open') is False and window['opens_at'] > now+90:
            # The kit waits only 90 s per line: reserving now would lock cash for lines it will skip.
            d = {'reason':'window', 'planned_opens_at':window['opens_at'], 'chosen':[], 'outstanding':held}
            if day: d = _micro_decision(state, day_ctx, inv, micro, now, d)
            _decide(state, now, observations, d, {}, {})
            return {'buy':[]}
        have = {}
        for x in [u['sku'] for u in units]+[r['sku'] for r in outstanding]: have[x] = have.get(x,0)+1
        frozen_ids, complete = risk_pilot.frozen(pending, ledger)
        if day:
            if day_ctx['active'] and invocation:
                return _oneday_plan(state, now, inv, catalog, evidence, pending, units, have, day_ctx, invocation,
                                    observations, held, micro)
            # Configured one-day pilot inactive/expired/blocked: no buy and never a fallthrough to any other path.
            atomic_json(journal, pending)
            try:
                _decide(state, now, observations, _micro_decision(
                    state, day_ctx, inv, micro, now, {'reason':'one_day_pilot_inactive', 'chosen':[], 'outstanding':held,
                                                      'one_day_pilot':_brief(day_ctx)}), {}, {})
            except (OSError, ValueError, TypeError, KeyError):
                pass
            return {'buy':[]}
        pilot = risk_pilot.context(state, now, inv, pending, frozen_ids, complete, ledger)
        if pilot['active'] and invocation:
            return _pilot_plan(state, now, inv, catalog, evidence, pending, units, have, pilot, invocation,
                               observations, held)
        if armed:
            # Inactive/expired/blocked pilot or no invocation id: deny new buys, never fall through to baseline.
            # Reconciliation (integrity verified above) is still persisted so arrivals keep matching.
            atomic_json(journal, pending)
            try:
                _decide(state, now, observations, {'reason':'risk_pilot_inactive', 'chosen':[], 'outstanding':held,
                                                   'risk_pilot':pilot}, {}, {})
            except (OSError, ValueError, TypeError, KeyError):
                pass
            return {'buy':[]}
        # Cost is at most 75% list under the live supply contract; reserve conservatively at full list.
        allowance = min(100000, inv['balance']-.8*inv['start']-sum(r['reserve'] for r in outstanding))
        slots = min(3,MAX_UNITS-len(units)-len(outstanding))
        exposure = .05*inv['start']-sum(u.get('cost',0) for u in units)-sum(r['reserve'] for r in outstanding)
        skip = owned | {r['sku'] for r in outstanding}
        categories = {u.get('category') for u in units} | {r['category'] for r in outstanding}
        baseline = {}
        for u in units:
            if isinstance(u.get('unit'), str): baseline.setdefault(u['sku'], []).append(u['unit'])
        # Proven = two distinct exact own settle IDs; such a SKU may repeat up to TARGET units held.
        proven_skus = {x for x,ids in evidence['own_sku_ids'].items() if len(set(ids))>=2}
        # Legacy holdings above MAX_EXPLORATION only block new exploration; nothing is sold to get under it.
        explored = sum(n for x,n in have.items() if x not in proven_skus)
        # High-price goods need two exact own settle IDs whose median realized price is >= 75% list.
        # Public category frequency never substitutes for own evidence.
        high_skus = set()
        for x in proven_skus:
            paid = _realized(evidence['own_ledger'], x)
            if _high(catalog.get(x)) and len(paid)>=2 and statistics.median(paid)>=HIGH_REALIZED*catalog[x]['list_price']:
                high_skus.add(x)
        held_high = {x for x in have if _high(catalog.get(x))}
        # Concentration at full list for on hand and outstanding high-price units (contract cost is
        # at most 75% list, but no cheaper cost draw is assumed).
        high_room = HIGH_SHARE*.05*inv['start'] \
            - sum(catalog[u['sku']]['list_price'] for u in units if _high(catalog.get(u['sku']))) \
            - sum(max(r['reserve'], catalog[r['sku']]['list_price']) for r in outstanding if _high(catalog.get(r['sku'])))
        # Own SKU reorders first, then public category sample, then local biased sample.
        candidates = sorted(catalog.items(), key=lambda x:(-own.get(x[0],0), -public.get(x[1].get('category'),0),
                                                           -local.get(x[1].get('category'),0), hashlib.sha256(x[0].encode()).hexdigest()))
        buy=[]
        evidence_ids, memory_ids = {}, {}
        for sku,c in candidates:
            price=c.get('list_price',0)
            planned = sum(x['qty'] for x in buy)
            if planned>=slots: break
            proven, high = sku in proven_skus, sku in high_skus
            if high:
                if held_high-{sku}: continue  # at most one high-price SKU across stock, pending and plan
                need = HIGH_TARGET-have.get(sku,0)
            elif proven:
                # No duplicate-category exclusion: bounded by TARGET and the 5% exposure instead.
                need = TARGET-have.get(sku,0)
            elif sku in skip or c.get('category') in categories or explored>=MAX_EXPLORATION:
                continue
            else:
                need = 1
            if need<=0 or not (high or 0<price<=60000): continue
            if c.get('kind')=='food' and c.get('shelf_hours',0)<24: continue
            qty = min(need, slots-planned, int(min(allowance, exposure, high_room if high else exposure)//price))
            if qty<=0: continue
            buy.append({'sku':sku,'qty':qty})
            evidence_ids[sku] = sorted(set(evidence['own_sku_ids'].get(sku,[]))|set(evidence['category_ids'].get(c['category'],[])))
            memory_ids[sku] = sorted(local_ids.get(c['category'],[]))
            for k in range(qty):  # one row per planned unit, each matched to at most one new unit
                pending.append({'sku':sku,'at':now,'reserve':price,'category':c['category'],'policy_version':POLICY,
                                'baseline_unit_ids':sorted(baseline.get(sku,[])),'matched_unit_id':None,
                                'evidence_ids':evidence_ids[sku],'local_biased_memory_ids':memory_ids[sku]})
                if invocation: pending[-1]['request'] = supply_journal.request(invocation, len(buy)-1, k, buy[-1])
            categories.add(c['category'])
            if not proven: explored+=qty
            if high:
                held_high.add(sku)
                high_room-=qty*price
            allowance-=qty*price
            exposure-=qty*price
        atomic_json(journal, pending)
        chosen = [x['sku'] for x in buy]
        try:
            _decide(state, now, observations, {'reason':'chosen' if chosen else 'no_eligible_candidate', 'chosen':chosen,
                                               'outstanding':held, 'risk_pilot':pilot}, evidence_ids, memory_ids)
        except (OSError, ValueError, TypeError, KeyError):
            pass  # pending is already persisted: an evidence write failure must not hide its reservations
        return {'buy':buy}
    except (OSError,ValueError,TypeError,KeyError):
        return {'buy':[]}


def _pilot_plan(state, now, inv, catalog, evidence, pending, units, have, pilot, invocation, observations, held):
    """Risk-exception pilot: cheapest proven (two exact own settle ids) low-price SKUs only, under the pilot room.
    Lifetime counters are durable BEFORE the pending rows, so a crash can only over-count, never recycle."""
    proven = {x for x,ids in evidence['own_sku_ids'].items() if len(set(ids))>=2}
    baseline = {}
    for u in units:
        if isinstance(u.get('unit'), str): baseline.setdefault(u['sku'], []).append(u['unit'])
    room_units, money = pilot['room_units'], pilot['room_money']
    buy, rows, evidence_ids = [], [], {}
    for sku, c in sorted(catalog.items(), key=lambda x:(x[1].get('list_price', 0), x[0])):
        price = c.get('list_price', 0)
        if room_units<=0 or len(buy)>=pilot['per_run_lines']: break
        if sku not in proven or _high(c) or not (type(price) in (int, float) and 0<price<=60000): continue
        if c.get('kind')=='food' and c.get('shelf_hours',0)<24: continue
        qty = min(TARGET-have.get(sku,0), room_units, int(money//price))
        if qty<=0: continue
        buy.append({'sku':sku, 'qty':qty})
        evidence_ids[sku] = sorted(set(evidence['own_sku_ids'].get(sku, [])))
        for k in range(qty):
            rows.append({'sku':sku, 'at':now, 'reserve':price, 'category':c['category'], 'policy_version':POLICY,
                         'risk_pilot':risk_pilot.PILOT, 'baseline_unit_ids':sorted(baseline.get(sku, [])),
                         'matched_unit_id':None, 'evidence_ids':evidence_ids[sku], 'local_biased_memory_ids':[],
                         'request':supply_journal.request(invocation, len(buy)-1, k, buy[-1])})
        room_units -= qty
        money -= qty*price
    if rows:
        try:
            risk_pilot.record(state, {risk_pilot.request_id(r['request']):{'sku':r['sku'], 'list':r['reserve'], 'at':now}
                                      for r in rows})
        except (OSError, ValueError, TypeError, KeyError):
            buy, rows = [], []  # no durable lifetime count, no purchase
    atomic_json(state/'restock-pending.json', pending+rows)
    try:
        _decide(state, now, observations, {'reason':'risk_pilot_chosen' if buy else 'risk_pilot_no_eligible_candidate',
                                           'chosen':[x['sku'] for x in buy], 'outstanding':held, 'risk_pilot':pilot},
                evidence_ids, {})
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {'buy':buy}


def _brief(ctx):
    return {k:ctx.get(k) for k in ('pilot', 'active', 'reason', 'stage', 'stage_cap', 'room', 'concurrent',
                                    'lifetime_committed', 'gross_loss', 'cash_room', 'expires_at')}


def _demand(sku, facts, table, units, pending, now):
    """demand-cover-v1 need for one SKU (see SIZING_POLICY). Rate evidence, first that exists: own exact pilot settles
    (microeconomics facts['units'], sold_at in (now - w h, now], fastest of DEMAND_WINDOWS_H); for a qualified new-goods
    SKU, public paid settles / distinct shops / window hours (an average shop's share, not ours); else none.
    On hand excludes food already past expiry. Unconfirmed supply = one-day rows with no supply result yet plus
    'supplied' rows newer than RECENT_SUPPLIED_S; refused/not_attempted rows, older 'supplied' rows (already on hand or
    sold) and frozen legacy rows (money reserve only, never unit slots) do not count. Missing facts leave only the
    probe: an error fails toward buying less."""
    sold = [x['sold_at'] for x in ((facts or {}).get('units') or {}).values()
            if isinstance(x, dict) and x.get('sku') == sku and oneday_pilot.num(x.get('sold_at')) and x['sold_at'] <= now]
    counts = {w:sum(1 for t in sold if t > now-w*3600) for w in DEMAND_WINDOWS_H}
    rate, basis, market = max(n/w for w, n in counts.items()), 'own_exact_settles', None
    if rate <= 0:
        row = next((r for r in table or [] if isinstance(r, dict) and r.get('sku') == sku
                    and r.get('tier') == microeconomics.NEW_GOODS_TIER), None)
        m = row.get('market') if row else None
        if isinstance(m, dict) and all(oneday_pilot.num(m.get(k)) and m[k] > 0 for k in ('settlements', 'shops', 'window_s')):
            market = {k:m.get(k) for k in ('settlements', 'shops', 'window_s', 'median_paid', 'unit_upper_bound')}
            # worstbound-market-v1: our cost is unknown before the buy; public demand sizes a line only when the public
            # median paid covers the contract worst-bound cost, else one probe (own exact settles then size it)
            if oneday_pilot.num(m.get('median_paid')) and oneday_pilot.num(m.get('unit_upper_bound')) \
                    and m['median_paid'] >= m['unit_upper_bound']:
                rate, basis = m['settlements']/m['shops']/(m['window_s']/3600), 'public_per_shop_share'
            else:
                basis = 'public_below_worst_bound_probe'
    on_hand = sum(1 for x in units if isinstance(x, dict) and x.get('sku') == sku
                  and not (x.get('kind') == 'food' and oneday_pilot.num(x.get('expires_at')) and x['expires_at'] <= now))
    unconfirmed = sum(1 for r in pending if isinstance(r, dict) and r.get('sku') == sku and r.get('matched_unit_id') is None
                      and r.get('one_day_pilot') and (r.get('supply_result') is None or (
                          r.get('supply_result') == 'supplied' and now-r.get('at', 0) < RECENT_SUPPLIED_S)))
    if rate > 0: target = math.ceil(rate*COVER_H)
    else:
        target = 1 if on_hand+unconfirmed == 0 else 0
        if basis != 'public_below_worst_bound_probe': basis = 'no_rate_evidence_probe'
    return {'rate_per_hour':rate, 'basis':basis, 'own_settles_by_window_h':counts, 'market':market,
            'cover_hours':COVER_H, 'target':target, 'on_hand':on_hand, 'unconfirmed':unconfirmed,
            'need':max(0, target-on_hand-unconfirmed)}


def _oneday_plan(state, now, inv, catalog, evidence, pending, units, have, ctx, invocation, observations, held,
                 micro=None):
    """One-day pilot: proven own SKUs by evidence ratio, each unit reserved at the contract upper bound, within the
    money room (stage, lifetime, cash, bank). Requests are durable in the pilot ledger BEFORE pending rows.
    microecon-v1 only reorders those same candidates (supported positive own contribution first).
    slot-recovery-v1 (user1570233935940554783): no per-SKU held_max and no SKU-share concentration cap for any eligible
    candidate; PER_CALL is a bounded kit request guard, not an inventory cap. Every candidate's sizing is recorded.
    demand-cover-v1: each line is also bounded by its evidenced demand (_demand), so room left by a covered SKU flows
    to the next ranked SKU instead of a 20-unit lump of stock that is not selling."""
    if micro is None: micro = microeconomics.evaluate(state, inv, catalog, evidence, ctx['stage'], now)
    room = ctx['room']
    baseline = {}
    for u in units:
        if isinstance(u.get('unit'), str): baseline.setdefault(u['sku'], []).append(u['unit'])
    buy, rows, requests, evidence_ids, sizing = [], [], {}, {}, []
    for sku, ub, _retired_held, ratio in micro[1]:
        if len(buy) >= ctx['per_run_lines']:
            sizing.append({'sku':sku, 'unit_ub':ub, 'qty':0, 'limit':'per_run_lines'})
            continue
        by_room = int(room//ub)
        demand = _demand(sku, micro[3], micro[2], units, pending, now)
        qty = max(0, min(by_room, PER_CALL, demand['need']))
        sizing.append({'sku':sku, 'unit_ub':ub, 'qty':qty, 'room_before':room, 'demand':demand,
                       'limit':'demand_cover' if demand['need'] < min(by_room, PER_CALL) else
                       'per_call_transport_guard' if PER_CALL < by_room else 'room'})
        if qty <= 0: continue
        buy.append({'sku':sku, 'qty':qty})
        evidence_ids[sku] = sorted(set(evidence['own_sku_ids'].get(sku, [])))
        req = supply_journal.request(invocation, len(buy)-1, 0, buy[-1])
        requests[oneday_pilot.request_key(req)] = {'sku':sku, 'qty':qty, 'unit_ub':ub, 'ub':qty*ub, 'at':now,
                                                   'status':None, 'total':None, 'evidence_ratio':round(ratio, 4),
                                                   'evidence_ids':evidence_ids[sku]}
        for k in range(qty):
            rows.append({'sku':sku, 'at':now, 'reserve':ub, 'category':catalog[sku]['category'], 'policy_version':POLICY,
                         'one_day_pilot':oneday_pilot.PILOT, 'baseline_unit_ids':sorted(baseline.get(sku, [])),
                         'matched_unit_id':None, 'evidence_ids':evidence_ids[sku], 'local_biased_memory_ids':[],
                         'request':supply_journal.request(invocation, len(buy)-1, k, buy[-1])})
        room -= qty*ub
    if rows:
        try:
            oneday_pilot.record_requests(state, requests)
        except (OSError, ValueError, TypeError, KeyError):
            buy, rows = [], []  # no durable lifetime count, no purchase
    atomic_json(state/'restock-pending.json', pending+rows)
    decision = {'reason':'one_day_pilot_chosen' if buy else 'one_day_pilot_no_eligible_candidate',
                'chosen':[x['sku'] for x in buy], 'outstanding':held, 'one_day_pilot':_brief(ctx), 'sizing':sizing,
                'sizing_policy':SIZING_POLICY}
    try:
        _decide(state, now, observations, _micro_decision(state, None, inv, micro, now, decision), evidence_ids, {})
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        frozen_ids, complete = risk_pilot.frozen(pending+rows, risk_pilot.load_ledger(state))
        after = oneday_pilot.context(state, now, inv, pending+rows, frozen_ids, complete, risk_pilot.load_ledger(state))
        oneday_pilot.write_status(state, after, now)
        # accounting after this plan's reservations; outcome = this plan's buy line (requested, not acquired)
        _micro_status(state, dict(after, active=ctx['active'], reason=ctx['reason']), inv, micro, now, decision,
                      decision['chosen'])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {'buy':buy}


def _micro_status(state, ctx, inv, micro, now, decision=None, chosen=()):
    """microecon-v1 status file; a write failure never blocks or widens the guarded plan."""
    try:
        _, _, table, facts, error = micro or (None, None, [], None, None)
        microeconomics.write_status(state, ctx, inv, now, table, facts, decision, chosen, error)
    except Exception:
        pass


def _micro_decision(state, ctx, inv, micro, now, decision):
    """Decision row (+ status when ctx is given): the ranking actually used and each chosen SKU's evidence."""
    table = micro[2] if micro else []
    chosen = decision.get('chosen') or []
    if ctx is not None: _micro_status(state, ctx, inv, micro, now, decision, chosen)
    return dict(decision, microeconomic_policy=microeconomics.MICRO_POLICY,
                microeconomics={'ranked':[r['sku'] for r in table], 'error':micro[4] if micro else None,
                                'public_market_used':microeconomics.public_market_used(table),
                                'chosen_evidence':{r['sku']:r for r in table if r['sku'] in chosen}})


def _high(c):
    if not isinstance(c, dict) or c.get('kind')!='goods': return False
    return type(c.get('list_price')) in (int, float) and HIGH_MIN<c['list_price']<=HIGH_MAX


def _realized(ledger, sku):
    """Finite positive own settle prices for sku, one per distinct exact own ledger ID."""
    return [v['price'] for k,v in ledger.items() if k.isdigit() and isinstance(v, dict) and v.get('sku')==sku
            and type(v.get('price')) in (int, float) and math.isfinite(v['price']) and v['price']>0]


def _reconcile(pending, units, frozen_ids=()):
    """slot-recovery-v1 (exact evidence only): NO new automatic row->unit match. A new same-SKU unit absent from a row's
    baseline is not proof that it is that row's unit (overlapping requests, unique or not), and the kit supply log
    carries no unit id, so no exact request+result+unit join exists. Rows stay outstanding at full reserve until an
    exact supply failure (supply_journal RELEASED) or an operator failure note; never by age (no TTL). Rows matched
    before this change keep their historical matched_unit_id/reserve untouched."""
    return pending


def _operator_failed(r):
    """Operator-evidenced supply failure: a non-empty operator_failure note written by hand into the row."""
    return isinstance(r.get('operator_failure'), str) and r['operator_failure'].strip() != ''


def _prune_matched(pending, now):
    """Matched rows (reserve 0) may leave after 300 s, but are kept while any outstanding same-SKU row's
    baseline lacks their unit ID, so that unit cannot be assigned a second time."""
    open_rows = [r for r in pending if r.get('matched_unit_id') is None]
    def guards(r):
        return any(o['sku']==r['sku'] and isinstance(o.get('baseline_unit_ids'), list)
                   and r['matched_unit_id'] not in o['baseline_unit_ids'] for o in open_rows)
    return [r for r in pending if r.get('matched_unit_id') is None or now-r['at']<300 or guards(r)]


def _held_signal(outstanding, failed, now):
    """Visible count/age of unresolved reservations so no slot stays silently blocked."""
    ages = [now-r['at'] for r in outstanding]
    by_sku = {}
    for r in outstanding: by_sku[r['sku']] = by_sku.get(r['sku'],0)+1
    return {'count':len(outstanding), 'oldest_age_s':max(ages) if ages else None,
            'over_300s':sum(a>=300 for a in ages), 'legacy':sum(not isinstance(r.get('baseline_unit_ids'), list)
                                                                for r in outstanding),
            'reserve':sum(r['reserve'] for r in outstanding), 'by_sku':by_sku,
            'operator_failed_cleared':[{'sku':r['sku'], 'at':r['at'], 'note':r.get('operator_failure') or r['supply_result']}
                                       for r in failed],
            'supplied_unmatched':sum(r.get('supply_result')=='supplied' for r in outstanding),
            'unknown':sum('supply_result' not in r for r in outstanding)}


def _decide(state, now, observations, decision, evidence_ids, memory_ids):
    observations['decision'] = decision
    atomic_json(state/'restock-observation.json', observations)
    coverage = observations['public_coverage']
    row = dict(decision, at=now, policy_version=POLICY, evidence_ids=evidence_ids, local_biased_memory_ids=memory_ids,
               public_coverage={k:coverage[k] for k in ('sample','settle_count','fetched_at','stale','missing')})
    with open(state/'restock-decisions.jsonl', 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True)+'\n')

def approved(state):
    """Explicit deployment gate: subprocess hooks reload source without a kit restart. Only write this approval
    file AFTER independent review and full passing tests; every SOURCES file must match its reviewed hash."""
    try:
        approval = json.loads((Path(state)/'seller-v2-approved.json').read_text(encoding='utf-8'))
        ok = approval.get('policy_version') == POLICY and approval.get('approved') is True
        source = Path(__file__).resolve().parent
        hashes = approval.get('source_sha256', {})
        return ok and isinstance(hashes, dict) and all(
            hashes.get(name) == hashlib.sha256((source/name).read_bytes()).hexdigest() for name in SOURCES)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def run(state, approved, now=None):
    """One hook invocation: durable invocation journal row (id, kit log offset, holds) BEFORE output, then the
    marker line and the buy line carrying the same id. Unapproved runs read pending but never rewrite it."""
    state = Path(state)
    now = time.time() if now is None else now
    inv = supply_journal.begin(state, now)
    buy = plan(state, now, inv)['buy'] if approved else []
    try:
        rows = json.loads((state/'restock-pending.json').read_text(encoding='utf-8'))
        outstanding = [r for r in rows if r.get('matched_unit_id') is None]
        holds = _held_signal(outstanding, [], now)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        holds = None
    out = [supply_journal.marker(inv)]
    if not approved: out.append('[restock] bounded-v2 awaiting reviewed activation; no purchases')
    try:
        with open(state/'restock-invocations.jsonl', 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(inv, approved=approved, buy=buy, holds=holds), ensure_ascii=False,
                                    sort_keys=True)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        buy = []  # no durable trace, no purchase (planned rows stay held as unknown)
    return out+[supply_journal.buy_line(inv, buy)]


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--state',required=True)
    state=ap.parse_args().state
    try:
        refresh(state)  # CLI only; plan() itself stays network-free and reads the cache
    except (OSError,ValueError,TypeError):
        pass
    for line in run(state, approved(state)):
        print(line, flush=True)
