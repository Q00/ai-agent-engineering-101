"""microecon-v2 (v1 + exact capital-time turnover priority): deterministic, read-only microeconomic views for the one-day pilot (no model calls, no network).

accounting(): arithmetic views of ALREADY APPROVED capital (one-day caps), never new money and never additive: the
stage pool, lifetime and cash views overlap. The legacy UNKNOWN reserve stays a separate full liability. The cash room
is the pilot's own (balance - legacy - new reserves - floor): reported as is, nothing is subtracted twice.

contributions(): own realized contribution per SKU from the one-day pilot ledger only (units observed or exactly
backfilled after activation, joined to own-shop settle events by exact unit id + SKU by oneday_pilot.observe):
price - cost - goods storage - observed refunds. It is NOT oneday_pilot.losses() (gross, never netted) and never feeds
it. Unknown refund amounts, conflicting/duplicate sales, SKU mismatches and invalid cost/time are held out of ranking.

capital_time() (v2): per SKU, own net contribution per won-hour of EXACT unit capital exposure, sum over units of
cost x hours held (never aggregate profit / median hold), with censored exposure (on-hand units so far, gone-without-
settle units to gone_at, expired units to expiry) in the denominator and their incurred storage / write-offs in the
numerator. Realized history, not a forecast. food_rotation() (v2): food eligibility from own rotation evidence.
"""
import hashlib
import json
import os
import statistics
from pathlib import Path

from oneday_pilot import num, kst
from public_evidence import FRESH_S, _comparable, _coverage, _life_bucket, _norm, _price_cache, _valid_gaps, own_urls

MICRO_POLICY = 'microecon-v2'
STATUS = 'microeconomics-status.json'
COVERAGE_NOTE = ('own one-day pilot ledger only: units supplied at/after activation (or exactly backfilled), joined to '
                 'own-shop settle events by exact unit id + SKU; sales before activation, other sellers and anything '
                 'outside the public newest-500 sample / persisted own ledger are absent. A sample, not complete '
                 'history; not causal demand.')


def _caps(ctx):
    return ctx.get('caps') if isinstance(ctx.get('caps'), dict) else {}


def accounting(ctx, inv):
    """Views over the context the hook already computed. None where the context did not reach that guard."""
    units = inv.get('units') if isinstance(inv, dict) and isinstance(inv.get('units'), list) else []
    stock = sum(x['cost'] for x in units if isinstance(x, dict) and num(x.get('cost')))
    caps, new = _caps(ctx), ctx.get('new_reserve')
    cap = ctx.get('stage_cap')
    pool = max(0, cap-stock-new) if num(cap) and num(new) else None
    life = max(0, caps['lifetime_spend']-ctx['lifetime_committed']) \
        if num(caps.get('lifetime_spend')) and num(ctx.get('lifetime_committed')) else None
    mode = ctx.get('loss_ceiling_mode')
    head = caps['gross_loss']-ctx['gross_loss'] if mode != 'disabled' and num(caps.get('gross_loss'))         and num(ctx.get('gross_loss')) else None
    dims = [pool, life, ctx.get('cash_room'), ctx.get('bank_room')]
    act = max(0, min(dims)) if ctx.get('active') is True and all(num(d) for d in dims) else 0
    fmode = ctx.get('financial_limits_mode')
    if fmode == 'disabled':  # amendment #8: the only money bound is balance - in-flight reserves (ctx room)
        act = max(0, ctx['room']) if ctx.get('active') is True and num(ctx.get('room')) else 0
    return {'inventory_cost':stock, 'new_pending_reserve':new, 'legacy_UNKNOWN_reserve':ctx.get('legacy_reserve'),
            'stage':ctx.get('stage'), 'stage_cap':cap, 'free_pool':pool, 'lifetime_committed':ctx.get('lifetime_committed'),
            'lifetime_spend_remaining':life, 'gross_loss':ctx.get('gross_loss'),
            'gross_loss_headroom':None if head is None else max(0, head), 'loss_ceiling_mode':mode,
            'financial_limits_mode':fmode, 'financial_limits':ctx.get('financial_limits'),
            'cash_room':ctx.get('cash_room'),
            'bank_room':ctx.get('bank_room'), 'observed_balance':inv.get('balance') if isinstance(inv, dict) else None,
            'actionable_acquisition_room':act, 'active':ctx.get('active') is True, 'reason':ctx.get('reason'),
            'additive':False,
            'note':'overlapping views of already approved capital; none is new money; do not add them. cash_room = '
                   'observed_balance - legacy - new reserves - cash floor (subtracted once). actionable = min(free_pool, '
                   'lifetime, cash, bank) only while the pilot is active.'}


def _sku_row():
    return {'n':0, 'ledger_ids':[], 'unit_ids':[], 'revenue':0, 'cost':0, 'storage':0, 'refunds':0,
            'net_contribution':0, 'positive_contribution':0, 'negative_contribution':0, 'turnover_hours':[],
            'refund_unknown':False, 'evidence_conflict':False, 'rejected_unit_ids':[]}


def contributions(led, inv, now, activated_at=None):
    rate = inv.get('storage_per_day') if num(inv.get('storage_per_day')) else .01
    conflicts = led.get('conflicts') if isinstance(led.get('conflicts'), dict) else {}
    # Events are keyed like losses(): e['units'] (list) or [e['unit']]. Same seq = same ledger entry: identical
    # payloads deduplicate, differing payloads conflict.
    payloads, sales, refunds, bad = {}, {}, {}, {}
    for e in led['events'].values():
        kind, seq = e.get('kind'), e.get('seq')
        if kind not in ('settle', 'broken'): continue
        targets = [t if isinstance(t, str) else repr(t)
                   for t in (e['units'] if isinstance(e.get('units'), list) else [e.get('unit')])]
        if type(seq) is not int:
            for uid in targets:
                if kind == 'settle': bad[uid] = (e.get('sku'), 'settle without an integer ledger seq')
                else: refunds.setdefault(uid, {})[('?', id(e))] = None
            continue
        payloads.setdefault((kind, seq), set()).add(json.dumps(e, sort_keys=True))
        for uid in targets:
            if kind == 'settle':
                if len(targets) != 1: bad[uid] = (e.get('sku'), f'settle {seq} names several units')
                sales.setdefault(uid, {})[seq] = e
            else:  # several units on one refund: the per-unit amount is unknown, never the total, never 0
                ok = len(targets) == 1 and num(e.get('refunded')) and e['refunded'] >= 0
                refunds.setdefault(uid, {})[seq] = e['refunded'] if ok else None
    clash = {k for k, v in payloads.items() if len(v) > 1}
    skus, units, rejected = {}, {}, {}

    def reject(uid, sku, why):
        rejected[uid] = why
        if isinstance(sku, str):
            row = skus.setdefault(sku, _sku_row())
            row['rejected_unit_ids'].append(uid)
            row['evidence_conflict'] = True

    for uid, (sku, why) in sorted(bad.items(), key=lambda x:str(x[0])):
        reject(uid, (led['units'].get(uid) or {}).get('sku', sku) if isinstance(uid, str) else sku, why)
    for uid in sorted(k for k in sales if k not in led['units'] and k not in rejected):
        reject(uid, next(iter(sales[uid].values())).get('sku'), 'settle names a unit with no recorded cost')
    for uid, rec in sorted(led['units'].items()):
        es = sales.get(uid)
        if not es or uid in rejected: continue
        sku = rec.get('sku')
        if any(('settle', s) in clash for s in es):
            reject(uid, sku, 'one ledger seq carries conflicting settle payloads')
            continue
        if len(es) > 1:
            reject(uid, sku, 'unit settled more than once')
            continue
        e = next(iter(es.values()))
        if uid in conflicts:
            reject(uid, sku, f'ledger conflict: {conflicts[uid]}')
            continue
        if e.get('sku') != sku:
            reject(uid, sku, f"settle SKU {e.get('sku')} != unit SKU {sku}")
            continue
        if not num(rec.get('cost')) or rec['cost'] < 0 or not num(e.get('price')) or e['price'] < 0 \
                or not num(e.get('ts')):
            reject(uid, sku, 'invalid cost, price or sale time')
            continue
        if num(rec.get('supplied_at')): start, basis = rec['supplied_at'], 'supplied_at_actual'
        elif num(rec.get('storage_from')): start, basis = rec['storage_from'], 'activation_bound_conservative'
        elif num(activated_at) and rec.get('supplied_at_unknown'):
            start, basis = activated_at, 'activation_bound_conservative'
        elif rec.get('kind') == 'food': start, basis = None, 'food_no_storage_arrival_unknown'
        else:
            reject(uid, sku, 'arrival time unknown: storage and turnover unknown')
            continue
        if start is not None and e['ts'] < start:
            reject(uid, sku, 'sale before arrival')
            continue
        store = rec['cost']*rate*(e['ts']-start)/86400 if rec.get('kind') == 'goods' else 0
        bs = refunds.get(uid, {})
        unknown = any(v is None for v in bs.values()) or any(('broken', s) in clash for s in bs)
        refund = sum(v for v in bs.values() if v is not None)
        turn = (e['ts']-start)/3600 if basis == 'supplied_at_actual' else None
        net = None if unknown else e['price']-rec['cost']-store-refund
        units[uid] = {'sku':sku, 'settle_seq':e.get('seq'), 'price':e['price'], 'cost':rec['cost'], 'storage':store,
                      'storage_basis':basis, 'refunds':refund, 'refund_unknown':unknown,
                      'refund_seqs':sorted(s for s in bs if type(s) is int),
                      'turnover_hours':turn, 'net_contribution':net, 'sold_at':e['ts']}
        row = skus.setdefault(sku, _sku_row())
        row['n'] += 1
        row['ledger_ids'].append(e.get('seq'))
        row['unit_ids'].append(uid)
        row['revenue'] += e['price']
        row['cost'] += rec['cost']
        row['storage'] += store
        row['refunds'] += refund
        if unknown: row['refund_unknown'] = True
        else:
            row['positive_contribution' if net > 0 else 'negative_contribution'] += net
        if turn is not None: row['turnover_hours'].append(turn)
    for row in skus.values():
        row['ledger_ids'].sort(key=lambda s:(type(s) is not int, s if type(s) is int else 0))
        row['net_contribution'] = None if row['refund_unknown'] else \
            row['positive_contribution']+row['negative_contribution']
        row['return_on_capital'] = row['net_contribution']/row['cost'] \
            if row['net_contribution'] is not None and row['cost'] > 0 else None
        turns = row.pop('turnover_hours')
        row['turnover_samples'] = len(turns)
        row['median_turnover_hours'] = statistics.median(turns) if turns else None
    out = {'policy_version':MICRO_POLICY, 'skus':skus, 'units':units, 'rejected':rejected, 'held':held(inv, now),
           'complete':False, 'coverage_note':COVERAGE_NOTE}
    out['velocity'] = capital_time(led, inv, now, out, rate)
    return out


MIN_TURN_SAMPLES = 2  # exact-clock sold units needed before capital-time ranks a SKU (same bar as MIN_SALES)
HOLD_FLOOR_H = 1/3  # bounded policy smoothing ASSUMPTION (~ the restock windows observed so far), not a guaranteed
# market schedule: it only stops near-zero holds from dominating the division
VELOCITY_FORMULA = ('return_per_capital_hour = (sum net contribution of exact-clock sold units - expired write-offs - '
                    'storage already incurred on held goods) / (sum cost x max(hours held, floor) of those sold units + '
                    'sum cost x hours of censored exposure: on-hand units so far, gone-without-settle units to gone_at, '
                    'expired units to expiry). Only evidence with ts <= now; unknown-clock units excluded from both '
                    'sides. Realized own history: not a forecast, not causal demand.')


def capital_time(led, inv, now, facts, rate=.01):
    """Per SKU exact unit capital-time (see VELOCITY_FORMULA). Unknown arrival of a held unit cannot be bounded: the SKU
    is not qualified (sparse fallback), never guessed."""
    import oneday_pilot
    out = {}

    def row(sku):
        return out.setdefault(sku, {'exact_samples':0, 'excluded_clock_unknown':0, 'excluded_future':0,
                                    'sold_net':0, 'sold_capital_hours':0, 'censored_units':0,
                                    'censored_capital_hours':0, 'censored_storage':0, 'censored_clock_unknown':0,
                                    'expired_units':0, 'expired_cost':0, 'expired_capital_hours':0,
                                    'provisional_units':0, 'provisional_capital_hours':0, 'refund_unknown':False,
                                    'unit_ids':[], 'min_samples':MIN_TURN_SAMPLES, 'hold_floor_hours':HOLD_FLOOR_H,
                                    'formula':VELOCITY_FORMULA})
    sold = facts['units']
    for uid, x in sorted(sold.items()):
        r = row(x['sku'])
        start = (led['units'].get(uid) or {}).get('supplied_at')
        if not num(x.get('sold_at')) or x['sold_at'] > now or (num(start) and start > now):
            r['excluded_future'] += 1
        elif x['turnover_hours'] is None:
            r['excluded_clock_unknown'] += 1
        elif x['net_contribution'] is None:
            r['refund_unknown'] = True
        else:
            r['exact_samples'] += 1
            r['sold_net'] += x['net_contribution']
            r['sold_capital_hours'] += x['cost']*max(x['turnover_hours'], HOLD_FLOOR_H)
            r['unit_ids'].append(uid)
    onhand = {x['unit'] for x in inv.get('units') or [] if isinstance(x, dict) and isinstance(x.get('unit'), str)}
    states = oneday_pilot.losses(led, inv, now)['units']
    expire_ts = {}
    for e in led['events'].values():
        if e.get('kind') == 'expire' and num(e.get('ts')):
            for uid in e.get('units') or [e.get('unit')]: expire_ts[uid] = min(e['ts'], expire_ts.get(uid, e['ts']))
    for uid, rec in sorted(led['units'].items()):
        if uid in sold or uid in facts['rejected'] or uid in onhand or not num(rec.get('cost')): continue
        st = (states.get(uid) or {}).get('state')
        if st not in ('expired', 'unaccounted'): continue
        r = row(rec['sku'])
        start = rec['supplied_at'] if num(rec.get('supplied_at')) else rec.get('storage_from')
        end = expire_ts.get(uid, rec.get('expires_at')) if st == 'expired' else rec.get('gone_at')
        if not num(start) or not num(end):
            r['excluded_clock_unknown'] += 1
            continue
        if start > now:
            r['excluded_future'] += 1
            continue
        hours = max(0, min(end, now)-start)/3600
        if st == 'expired':
            r['expired_units'] += 1
            r['expired_cost'] += rec['cost']
            r['expired_capital_hours'] += rec['cost']*hours
        else:  # gone without an observed settle: exposure to gone_at, no presumed profit
            r['provisional_units'] += 1
            r['provisional_capital_hours'] += rec['cost']*hours
    for x in inv.get('units') or []:
        if not isinstance(x, dict) or not isinstance(x.get('sku'), str) or not num(x.get('cost')) or x['cost'] < 0:
            continue
        r = row(x['sku'])
        if not num(x.get('supplied_at')):
            r['censored_clock_unknown'] += 1
            continue
        if x['supplied_at'] > now:
            r['excluded_future'] += 1
            continue
        if x.get('kind') == 'food' and num(x.get('expires_at')) and x['expires_at'] <= now:
            # still listed on hand but past expiry: a write-off (cost) with exposure to expiry, not censored stock
            r['expired_units'] += 1
            r['expired_cost'] += x['cost']
            r['expired_capital_hours'] += x['cost']*max(0, x['expires_at']-x['supplied_at'])/3600
            continue
        age = (now-x['supplied_at'])/3600
        r['censored_units'] += 1
        r['censored_capital_hours'] += x['cost']*age
        if x.get('kind') == 'goods': r['censored_storage'] += x['cost']*rate*age/24
    for r in out.values():
        r['numerator'] = r['sold_net']-r['expired_cost']-r['censored_storage']
        r['denominator'] = r['sold_capital_hours']+r['censored_capital_hours']+r['expired_capital_hours'] \
            +r['provisional_capital_hours']
        r['return_per_capital_hour'] = r['numerator']/r['denominator'] if r['denominator'] > 0 else None
        r['return_per_capital_day'] = None if r['return_per_capital_hour'] is None else 24*r['return_per_capital_hour']
        why = ('refund amount unknown' if r['refund_unknown'] else
               f"{r['exact_samples']} exact-clock sold units < {MIN_TURN_SAMPLES}" if r['exact_samples'] < MIN_TURN_SAMPLES
               else 'held unit arrival unknown: censored exposure unbounded' if r['censored_clock_unknown'] else
               'no capital-time' if r['return_per_capital_hour'] is None else None)
        r['qualified'], r['why'] = why is None, why
    return out


ROTATION_MIN_SAMPLES = 3  # exact-clock sold units of the SKU before its own rotation replaces the fixed 72 h
# rotation/buffer are sample-based estimates from our own past holds: not a guarantee about future sell-through


def food_rotation(facts, led, inv, now, catalog):
    """Per food SKU with own pilot units: empirical rotation = the SLOWEST exact hold among sold units (sold_at <= now),
    censored held ages and gone-without-settle holds; actual shelf = min(catalog shelf_hours, observed own units'
    expires_at - supplied_at). Qualified (>= ROTATION_MIN_SAMPLES exact sales): eligible iff actual shelf > rotation +
    buffer, buffer = max(own rotation, the slowest exact/censored hold of ANY own food unit): the threshold is at least
    twice the SKU's slowest hold and never below the pooled own food tail (no fixed constant). Otherwise the original
    fixed rule (catalog shelf > 72 h) stays."""
    import oneday_pilot
    seen = {}
    for uid, rec in led['units'].items(): seen.setdefault(rec.get('sku'), []).append(('ledger', uid, rec))
    for x in inv.get('units') or []:
        if isinstance(x, dict): seen.setdefault(x.get('sku'), []).append(('held', x.get('unit'), x))
    out, pooled = {}, []
    for sku, recs in sorted((k, v) for k, v in seen.items() if isinstance(k, str)):
        c = catalog.get(sku)
        if not isinstance(c, dict) or c.get('kind') != 'food': continue
        holds = [x['turnover_hours'] for x in facts['units'].values() if x['sku'] == sku
                 and x['turnover_hours'] is not None and num(x.get('sold_at')) and x['sold_at'] <= now]
        censored, lives = [], []
        for src, uid, x in recs:
            t0, exp = x.get('supplied_at'), x.get('expires_at')
            if num(t0) and num(exp) and exp > t0: lives.append((exp-t0)/3600)
            if not num(t0) or t0 > now: continue
            if src == 'held': censored.append((now-t0)/3600)
            elif uid not in facts['units'] and num(x.get('gone_at')):
                censored.append(max(0, min(x['gone_at'], now)-t0)/3600)
        shelf = c.get('shelf_hours') if num(c.get('shelf_hours')) else None
        actual = min([v for v in [shelf]+lives if v is not None], default=None)
        r = {'exact_samples':len(holds), 'censored_holds':len(censored), 'catalog_shelf_hours':shelf,
             'observed_min_life_hours':min(lives) if lives else None, 'actual_shelf_hours':actual,
             'min_samples':ROTATION_MIN_SAMPLES, '_holds':holds+censored}
        pooled += holds+censored
        out[sku] = r
    tail = max(pooled, default=None)
    for r in out.values():
        holds = r.pop('_holds')
        if r['exact_samples'] >= ROTATION_MIN_SAMPLES:
            rot = max(holds)
            buf = max(rot, tail)
            r.update(qualified=True, basis='empirical_own_rotation', rotation_hours=rot, buffer_hours=buf,
                     buffer_basis='own_slowest_hold' if buf == rot else 'pooled_own_food_slowest_hold',
                     pooled_food_slowest_hold_hours=tail, threshold_hours=rot+buf)
        else:
            r.update(qualified=False, basis='fallback_original_72h', rotation_hours=None, buffer_hours=None,
                     pooled_food_slowest_hold_hours=tail, threshold_hours=oneday_pilot.ROTATION_H,
                     actual_shelf_hours=r['catalog_shelf_hours'])
        r['eligible_by_shelf'] = bool(num(r['actual_shelf_hours']) and r['actual_shelf_hours'] > r['threshold_hours'])
    return out


MIN_SALES = 2  # same evidence bar as the original eligibility: two distinct exact own settle ids
EVIDENCE = ('n', 'ledger_ids', 'unit_ids', 'revenue', 'cost', 'storage', 'refunds', 'net_contribution',
            'return_on_capital', 'median_turnover_hours', 'turnover_samples')


def _basis(s):
    if not s or s['n'] == 0: return 'no_own_realized_sale'
    if s['evidence_conflict']: return 'evidence_conflict'
    if s['refund_unknown']: return 'refund_unknown'
    if s['n'] < MIN_SALES: return 'sparse'
    if s['net_contribution'] is None or s['return_on_capital'] is None or s['net_contribution'] <= 0:
        return 'net_contribution_not_positive'
    return None


PROMOTABLE = ('no_own_realized_sale', 'sparse')  # own evidence absent/thin, not negative, conflicting or unknown


def _unsold_on_hand(why, v):
    """turnover-followup-v1 (2026-11-14): a SKU with ZERO own realized sales in the pilot ledger that already holds
    on-hand units (censored exposure) gets no new buy line until one of its units settles. The greedy room waterfall
    in restock_r3._oneday_plan buys any candidate whose upper bound fits the leftover room, so a demoted (last) SKU
    still took every remainder too small for the proven-turnover SKUs above it. First exploration (nothing on hand) is
    unchanged; one realized sale ('sparse') lifts it; a qualified positive public market-proxy promotion (tier 3) is
    unchanged. Not a quantity cap, markdown, sale or expiry: held units stay."""
    return why == 'no_own_realized_sale' and isinstance(v, dict) and not v.get('exact_samples') \
        and (v.get('censored_units') or 0)+(v.get('censored_clock_unknown') or 0) > 0


def rank(cands, facts, public=None):
    """Stable rerank of the ORIGINAL eligible candidates (same tuples: no new SKU, bound or hold limit).
    1. supported_positive_turnover: supported positive own contribution (v1 basis) AND qualified exact capital-time
       (>= MIN_TURN_SAMPLES exact-clock sales, bounded censoring) with a positive return per capital-hour; by that
       return, then the qualified public market-proxy return (tie-break only; none last), then baseline position.
    2. supported_positive_contribution_sparse_turnover: supported positive contribution whose capital-time is not
       qualified (explicit fallback): the v1 order (return on capital, public, median turnover, baseline).
    3. no/sparse own sales with a qualified positive public market-proxy return (by that return), then the rest in
       baseline relative order (incl. turnover_contribution_not_positive: write-offs/storage eat the realized margin).
    Sparse, unknown, conflicting or non-positive evidence demotes, it never removes. No weights, no forecast.
    Only exception (_unsold_on_hand): an unpromoted SKU with zero own sales and units already on hand is reported in
    the table (tier held_unsold_no_realized_sale, last) but not returned as a buy candidate."""
    skus = facts.get('skus', {}) if isinstance(facts, dict) else {}
    vel = facts.get('velocity', {}) if isinstance(facts, dict) else {}
    public = public or {}
    lead, fallback, promoted, rest, held = [], [], [], [], []
    for i, c in enumerate(cands):
        s, pub, v = skus.get(c[0]), public.get(c[0]), vel.get(c[0])
        why = _basis(s)
        rpch = v.get('return_per_capital_hour') if isinstance(v, dict) and v.get('qualified') else None
        if not why and rpch is not None and rpch <= 0: why = 'turnover_contribution_not_positive'
        tier = 'baseline_order' if why else 'supported_positive_turnover' if rpch is not None else \
            'supported_positive_contribution_sparse_turnover'
        row = {'sku':c[0], 'baseline_index':i, 'tier':tier,
               'basis':why or ('return_per_capital_hour_exact_unit_exposure' if rpch is not None else
                               'sparse_turnover_fallback_return_on_capital')}
        if s: row.update({k:s[k] for k in EVIDENCE if k in s})
        if isinstance(v, dict): row['velocity'] = dict(v)
        ret = pub['market_proxy_return'] if pub and pub.get('use') else None
        if pub is not None:
            row['public'] = dict(pub, role='not_applied' if ret is None else
                                 'secondary_after_own_return' if not why else
                                 'promotes_no_or_sparse_own_evidence' if why in PROMOTABLE and ret > 0 else
                                 f'not_applied: own basis {why}' if why not in PROMOTABLE else
                                 'not_applied: market-proxy return not positive')
        row['_ret'] = ret
        if not why: (lead if rpch is not None else fallback).append((c, row))
        elif row.get('public', {}).get('role') == 'promotes_no_or_sparse_own_evidence': promoted.append((c, row))
        elif _unsold_on_hand(why, v):
            row.update(tier='held_unsold_no_realized_sale', buy_candidate=False)
            held.append((c, row))
        else: rest.append((c, row))
    lead.sort(key=lambda x:(-x[1]['velocity']['return_per_capital_hour'], x[1]['_ret'] is None, -(x[1]['_ret'] or 0),
                            x[1]['baseline_index']))
    fallback.sort(key=lambda x:(-x[1]['return_on_capital'], x[1]['_ret'] is None, -(x[1]['_ret'] or 0),
                                x[1]['median_turnover_hours'] is None, x[1]['median_turnover_hours'] or 0,
                                x[1]['baseline_index']))
    promoted.sort(key=lambda x:(-x[1]['_ret'], x[1]['baseline_index']))
    out = lead+fallback+promoted+rest
    for _, r in out+held: r.pop('_ret')
    return [c for c, _ in out], [r for _, r in out+held]


PUBLIC_MIN = {'settlements':3, 'shops':2}
CONSIDERED_MAX = 50  # newest same-SKU non-own rows kept per stratum with their exclusion reason
USED_ROLES = ('secondary_after_own_return', 'promotes_no_or_sparse_own_evidence')
PUBLIC_LABEL = ('market proxy: per-shop-balanced median of fresh exact comparable non-own public settle prices vs OUR '
                'observed acquisition cost in the same stratum; not public seller margin (their cost is unknown), not '
                'a future or guaranteed margin; storage/refunds excluded; own realized contribution is reported '
                'separately')


TERMS_WARRANTY = 'seller_r3.terms() warranty_hours=0 (held unit, not yet sold)'


def _own_rows(cache):
    """Our own shop's public settle rows by seq (exact sale-time grade/expiry/warranty as the market recorded them)."""
    own = {_norm(u) for u in cache.get('own_urls', [])+own_urls()} - {None}
    out = {}
    for row in cache.get('price_rows', [])+cache.get('rows', []):
        if isinstance(row, dict) and type(row.get('seq')) is int and row.get('kind') == 'settle' \
                and isinstance(row.get('data'), dict) and _norm(row['data'].get('shop')) in own:
            out.setdefault(row['seq'], row)
    return out


def _own_strata(sku, inv, led, facts, now, own_rows):
    """Strata actually observed on OUR units of this SKU, with per-unit provenance:
    held units -> current inventory snapshot grade/life bucket now, warranty per seller_r3.terms() (0);
    settled units -> ONLY our exact own public settle row (same seq + unit + SKU): sale-time grade, life bucket at
    the sale, raw warranty as recorded (null compared as 0 by public_evidence._comparable). Without that row a goods
    unit keeps its ledger grade (fixed at arrival by the supply contract); a food unit is omitted (its grade ages)."""
    strata, omitted = {}, []

    def add(grade, kind, bucket, warranty_raw, cost, uid, prov):
        if not isinstance(grade, str) or kind not in FRESH_S or not num(cost) or cost <= 0:
            omitted.append({'unit':uid, 'reason':'grade/kind/cost unusable'})
            return
        if kind == 'food' and bucket is None:
            omitted.append({'unit':uid, 'reason':'food life bucket unknown or expired'})
            return
        if warranty_raw is not None and (not num(warranty_raw) or warranty_raw < 0):
            omitted.append({'unit':uid, 'reason':f'warranty {warranty_raw!r} unusable'})
            return
        warranty = 0 if warranty_raw is None else warranty_raw
        s = strata.setdefault((grade, kind, warranty, bucket), {'costs':[], 'unit_ids':[], 'provenance':[],
                                                                'warranty_raw':set()})
        s['costs'].append(cost)
        s['unit_ids'].append(uid)
        s['provenance'].append(dict(prov, unit=uid, grade=grade, life_bucket=bucket, warranty_observed=warranty_raw))
        s['warranty_raw'].add(prov['source'] if prov['source'] == 'held_current_snapshot' else repr(warranty_raw))

    for x in inv.get('units') or []:
        if isinstance(x, dict) and x.get('sku') == sku:
            add(x.get('grade'), x.get('kind'), _life_bucket(x.get('expires_at'), now), 0, x.get('cost'), x.get('unit'),
                {'source':'held_current_snapshot', 'warranty_basis':TERMS_WARRANTY})
    for uid, x in sorted((facts or {}).get('units', {}).items()):
        if x.get('sku') != sku: continue
        rec = (led or {}).get('units', {}).get(uid) or {}
        row = own_rows.get(x.get('settle_seq'))
        d = row['data'] if row else {}
        if row and d.get('unit') == uid and d.get('item') == sku and num(row.get('ts')):
            kind = 'goods' if d.get('expires_at') is None else 'food'
            add(d.get('grade'), kind, _life_bucket(d.get('expires_at'), row['ts']) if kind == 'food' else None,
                d.get('warranty_hours'), x.get('cost'), uid,
                {'source':'own_public_settle_row', 'settle_seq':row['seq'], 'warranty_basis':'as recorded on our settle'})
        elif rec.get('kind') == 'goods':
            add(rec.get('grade'), 'goods', None, 0, x.get('cost'), uid,
                {'source':'ledger_unit_goods_grade_fixed_at_arrival', 'settle_seq':x.get('settle_seq'),
                 'warranty_basis':'seller_r3.terms() warranty_hours=0; no own settle row in the bounded cache'})
        else:
            omitted.append({'unit':uid, 'settle_seq':x.get('settle_seq'),
                            'reason':'food sold with no sale-time grade evidence (no exact own settle row seq+unit+SKU '
                                     'in the bounded cache); arrival grade is not the sale grade'})
    return strata, omitted


def _exclusion(data, ts, unit, bucket, amount, lst, now, kind):
    """First failing reason for one same-SKU non-own row (diagnostic; inclusion is decided by _comparable)."""
    if now-ts > FRESH_S[kind]: return f'stale: settle {now-ts:.0f} s old > {FRESH_S[kind]} s'
    missing = [k for k in ('sku', 'item', 'grade', 'expires_at', 'warranty_hours') if k not in data]
    if missing: return f'missing metadata {missing}'
    if data['grade'] != unit['grade']: return f"grade {data['grade']} != own {unit['grade']}"
    w = 0 if data['warranty_hours'] is None else data['warranty_hours']
    if w != unit['warranty_hours']: return f"warranty {data['warranty_hours']} != own {unit['warranty_hours']}"
    if (data['expires_at'] is None) != (kind == 'goods'): return 'kind (food/goods) differs'
    if kind == 'food' and _life_bucket(data['expires_at'], ts) != bucket:
        return f"life bucket {_life_bucket(data['expires_at'], ts)} != own {bucket}"
    if not _comparable(data, ts, unit, bucket): return 'not comparable (public_evidence._comparable)'
    if not lst*.2 <= amount <= lst: return f'amount {amount} outside 0.2..1.0 x list {lst}'
    return 'comparable'


def _public_rows(cache, now):
    """Same bounded filters as public_evidence._compute_anchor: one settle per seq, then per order and per unit
    (lowest seq wins), round 3, non-own shop, positive amount, settle ts in the past. No network."""
    own = {_norm(u) for u in cache.get('own_urls', [])+own_urls()} - {None}
    own_ids = {int(k) for k in cache.get('own_ledger', {}) if str(k).isdigit()}
    rows = {}
    for row in cache.get('price_rows', [])+cache.get('rows', []):
        if isinstance(row, dict) and type(row.get('seq')) is int and row['seq'] not in rows: rows[row['seq']] = row
    orders, units, out = set(), set(), []
    for seq in sorted(rows):
        row, data = rows[seq], rows[seq].get('data')
        if row.get('kind') != 'settle' or row.get('round') != 3 or not isinstance(data, dict): continue
        order, sold = data.get('order'), data.get('unit')
        if (isinstance(order, str) and order in orders) or (isinstance(sold, str) and sold in units): continue
        orders.update([order] if isinstance(order, str) and order else [])
        units.update([sold] if isinstance(sold, str) and sold else [])
        shop, amount, ts = _norm(data.get('shop')), data.get('amount'), row.get('ts')
        if seq in own_ids or not shop or shop in own: continue
        if not num(amount) or amount <= 0 or not num(ts) or ts <= 0 or ts > now: continue
        out.append((seq, amount, ts, data, shop))
    return out


def _quartiles(xs):
    if len(xs) < 2: return xs[0], xs[0]
    q = statistics.quantiles(xs, n=4, method='inclusive')
    return q[0], q[2]


def public_stats(state, cands, facts, inv, led, catalog, now):
    """Per original candidate: the qualified public group (>= 3 fresh exact comparable non-own settlements from >= 2
    shops, no fresh coverage gap) in OUR observed stratum, or use=False with the reason. Never calls price_anchor and
    never writes seller-price-policy.json."""
    caches, rows = {}, {}
    for kind in FRESH_S:
        cache, problem = _price_cache(state, now, kind)
        gaps = [g for g in _valid_gaps(cache.get('gaps')) if now-g['detected_at'] <= FRESH_S[kind]] if cache else []
        caches[kind] = (cache, problem or (f'coverage gap: {sum(g["hi"]-g["lo"]+1 for g in gaps)} ledger seq missing '
                                           f'in {len(gaps)} gap(s) detected within {FRESH_S[kind]} s' if gaps else None))
        if cache and not caches[kind][1]: rows[kind] = _public_rows(cache, now)
    read = next((c for c, _ in caches.values() if c), None)
    own_rows = _own_rows(read) if read else {}
    if read:
        held = {}
        for r in read.get('price_rows', [])+read.get('rows', []):
            if isinstance(r, dict) and type(r.get('seq')) is int: held.setdefault(r['seq'], r)
        coverage = dict(_coverage(list(held.values()), read['fetched_at'], read), fetched_at=read['fetched_at'],
                        source_url=read.get('source_url'), cache_version=read.get('cache_version'))
    else:
        coverage = {'complete':False, 'unavailable':{k:v[1] for k, v in caches.items()}}
    out = {}
    for cand in cands:
        sku = cand[0]
        c = catalog.get(sku) or {}
        kind, lst = c.get('kind'), c.get('list_price')
        res = {'use':False, 'label':PUBLIC_LABEL, 'strata':[]}
        out[sku] = res
        if kind not in FRESH_S or not num(lst) or lst <= 0:
            res['reason'] = 'catalog kind/list unusable'
            continue
        cache, problem = caches[kind]
        if cache:
            res.update(fetched_at=cache.get('fetched_at'), source_age_s=now-cache['fetched_at'],
                       source_url=cache.get('source_url'), cache_version=cache.get('cache_version'))
        if problem:
            res['reason'] = f'public cache not usable: {problem}'
            continue
        strata, omitted = _own_strata(sku, inv, led, facts, now, own_rows)
        res['omitted_own_units'] = omitted
        if not strata:
            res['reason'] = 'no own acquisition cost observed for this SKU (no held or settled own unit stratum)'
            continue
        for (grade, k, warranty, bucket), own in sorted(strata.items(), key=lambda x:str(x[0])):
            unit = {'sku':sku, 'grade':grade, 'kind':k, 'warranty_hours':warranty}
            same = [r for r in rows[kind] if r[3].get('item') == sku]
            considered = [{'seq':seq, 'shop':shop, 'amount':amount,
                           'reason':_exclusion(data, ts, unit, bucket, amount, lst, now, kind)}
                          for seq, amount, ts, data, shop in same]
            picked = [(seq, amount, shop) for seq, amount, ts, data, shop in same
                      if now-ts <= FRESH_S[kind] and _comparable(data, ts, unit, bucket) and lst*.2 <= amount <= lst]
            by_shop = {}
            for seq, amount, shop in picked: by_shop.setdefault(shop, []).append(amount)
            meds = sorted(statistics.median(v) for v in by_shop.values())
            cost = statistics.median(own['costs'])
            s = {'stratum':{'grade':grade, 'kind':k, 'warranty_hours':warranty, 'life_bucket':bucket},
                 'warranty_basis':TERMS_WARRANTY if own['warranty_raw'] == {'held_current_snapshot'} else
                 'compared as 0 when recorded null (public_evidence._comparable); observed: '+', '.join(sorted(own['warranty_raw'])),
                 'settlements':len(picked), 'shops':len(by_shop), 'ledger_ids':[p[0] for p in picked],
                 'considered':considered[-CONSIDERED_MAX:], 'considered_total':len(considered),
                 'own_cost_median':cost, 'own_unit_ids':sorted(own['unit_ids']), 'own_units':len(own['unit_ids']),
                 'own_provenance':own['provenance']}
            if len(picked) >= PUBLIC_MIN['settlements'] and len(by_shop) >= PUBLIC_MIN['shops']:
                q25, q75 = _quartiles(meds)
                med = statistics.median(meds)
                s.update(qualified=True, median=med, q25=q25, q75=q75,
                         shop_medians={k2:statistics.median(v) for k2, v in sorted(by_shop.items())},
                         market_proxy_return=(med-cost)/cost)
            else:
                s.update(qualified=False, why=f'{len(picked)} fresh exact comparable settlements from {len(by_shop)} '
                                              f'non-own shops (need >= {PUBLIC_MIN["settlements"]} from >= '
                                              f'{PUBLIC_MIN["shops"]})')
            res['strata'].append(s)
        good = sorted((s for s in res['strata'] if s['qualified']), key=lambda s:(-s['own_units'], str(s['stratum'])))
        if not good:
            res['reason'] = '; '.join(f"{s['stratum']}: {s['why']}" for s in res['strata'])
            continue
        res.update({k:good[0][k] for k in ('stratum', 'settlements', 'shops', 'ledger_ids', 'median', 'q25', 'q75',
                                           'shop_medians', 'own_cost_median', 'own_unit_ids', 'market_proxy_return',
                                           'own_provenance', 'warranty_basis')},
                   use=True, reason='qualified: stratum with the most own units')
    summary = {'eligible_candidates':len(cands), 'qualified':sum(v['use'] for v in out.values()),
               'used':sum(v['use'] for v in out.values()),
               'comparable_rows_read':{k:len(v) for k, v in rows.items()}, 'own_settle_rows_read':len(own_rows),
               'coverage':coverage, 'public_market_used':None,
               'cache_problem':{k:v[1] for k, v in caches.items()},
               'source_age_s':{k:(now-v[0]['fetched_at'] if v[0] else None) for k, v in caches.items()},
               'min_group':PUBLIC_MIN, 'freshness_s':FRESH_S, 'complete':False,
               'note':'bounded public sample (newest <=500 per refresh, settles kept <=2 h, <=5000 rows); not complete '
                      'market history, not causal demand'}
    return out, summary


BOARD = 'microeconomics-board.json'
BOARD_STALE_S = 3600


def _board_sha(board):
    return hashlib.sha256(json.dumps(board, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def board_wrapper(board, fetched_at, source_url, own_shop=None):
    """What the parent installs as .market/microeconomics-board.json from a board fetch it made itself."""
    w = {'fetched_at':fetched_at, 'source_url':source_url, 'source_sha256':_board_sha(board), 'board':board}
    if own_shop: w['own_shop'] = own_shop
    return w


def _own_shop(inv, wrapper):
    names = set()
    for x in (inv or {}).get('units') or []:
        cert = (x.get('cert') or {}).get('cert') if isinstance(x, dict) and isinstance(x.get('cert'), dict) else None
        if isinstance(cert, dict) and isinstance(cert.get('shop'), str): names.add(cert['shop'])
    if len(names) == 1: return names.pop(), 'inventory unit certificates'
    if isinstance(wrapper.get('own_shop'), str): return wrapper['own_shop'], 'board wrapper own_shop'
    return None, None


def board_comparison(state, inv, now):
    """Cached board context for the status file only: never read by the ranking. Board profit/book are shop totals,
    never a unit margin or a cost."""
    out = {'used_in_selection':False, 'file':BOARD,
           'note':'board book/revenue/profit are shop-level totals: not a unit margin, not a cost; context only, never '
                  'used in selection; category composition of other shops is unobserved'}
    try:
        w = json.loads((Path(state)/BOARD).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return dict(out, status='missing')
    except (OSError, ValueError):
        return dict(out, status='invalid')
    if not isinstance(w, dict) or not isinstance(w.get('board'), dict):
        return dict(out, status='invalid')
    fetched = w.get('fetched_at')
    future = num(fetched) and fetched > now  # clock skew or a later fetch: age unknown, never shown as fresh
    out.update(source_url=w.get('source_url'), source_sha256=w.get('source_sha256'), fetched_at=fetched,
               age_s=now-fetched if num(fetched) and not future else None, fetched_after_evaluation=future,
               stale=not num(fetched) or future or now-fetched > BOARD_STALE_S, stale_after_s=BOARD_STALE_S)
    if w.get('source_sha256') != _board_sha(w['board']): return dict(out, status='hash_mismatch')
    b = w['board']
    shop, how = _own_shop(inv, w)
    if not shop: return dict(out, status='own_identity_unknown')
    out['own_identity'] = {'shop':shop, 'from':how}
    students = [p for p in b.get('players') or [] if isinstance(p, dict) and p.get('student') is True
                and p.get('house') is not True and num(p.get('student_total'))]
    me = next((p for p in students if shop in ((p.get('shop') or {}).get('name'), (p.get('buyer') or {}).get('name'))), None)
    if me is None: return dict(out, status='own_not_on_board')
    shops = {s['name']:s for s in b.get('shops') or [] if isinstance(s, dict) and isinstance(s.get('name'), str)}

    def rank(p):
        return 1+sum(q['student_total'] > p['student_total'] for q in students)

    def ctx(p):
        s = shops.get((p.get('shop') or {}).get('name')) or {}
        return {'player':p.get('player'), 'student_total':p['student_total'], 'rank':rank(p),
                'shop_all':(p.get('shop') or {}).get('all'), 'buyer_all':(p.get('buyer') or {}).get('all'),
                **{k:s.get(k) for k in ('book', 'revenue', 'profit', 'deals')}}
    r = rank(me)
    out['own'] = dict(ctx(me), denominator=len(students),
                      tied_with=sorted(p['player'] for p in students if p is not me and p['student_total'] == me['student_total']))
    out['rank_rule'] = 'competition rank: 1 + students with strictly higher student_total'
    out['denominator_rule'] = 'board players with student=true, house not true, and a numeric student_total'
    out['near_peers'] = sorted((ctx(p) for p in students if p is not me and abs(rank(p)-r) <= 2),
                               key=lambda x:(x['rank'], str(x['player'])))
    own_shop = shops.get(shop) or {}
    out['own_shop'] = {k:own_shop.get(k) for k in ('deals', 'book', 'revenue', 'profit', 'stock_cost', 'stars')}
    pop = [s for s in shops.values() if num(s.get('deals')) and s['deals'] > 0]
    stat = lambda k, f: f([s[k] for s in pop if num(s.get(k))]) if any(num(s.get(k)) for s in pop) else None
    out['shop_population'] = {'population':'board shops with deals > 0', 'n':len(pop),
                              'known_fields':['book', 'revenue', 'profit', 'deals'],
                              'median_book':stat('book', statistics.median), 'mean_book':stat('book', statistics.fmean),
                              'median_revenue':stat('revenue', statistics.median),
                              'mean_revenue':stat('revenue', statistics.fmean),
                              'category_composition':'unobserved'}
    out['status'] = 'ok'
    return out


def public_market_used(table):
    """True only when a qualified public statistic entered this ordering's sort key for some candidate."""
    return any((r.get('public') or {}).get('role') in USED_ROLES for r in table or [])


NEW_GOODS_POLICY = 'market-new-goods-v1'
NEW_GOODS_TIER = 'qualified_new_market_goods'
NEW_GOODS_MIN = {'settlements':40, 'shops':7, 'buyers':3}
NEW_GOODS_WINDOW_S = 7200  # = public_evidence.PRICE_RETAIN_S: settles older than this are not counted
NEW_GOODS_GRADES = ('A', 'B', 'C')
NEW_GOODS_LABEL = ('fresh independent public paid demand for this goods SKU (deduplicated seq/order/unit, non-own '
                   'shops, last 2 h of a fresh gap-free bounded cache); a public observation, not complete global demand, '
                   'not a forecast and not a guaranteed sale or margin. Our cost is drawn at supply (unknown before the '
                   'buy) and reserved at the contract upper bound 1.125 x list; compatibility = public median / that '
                   'bound. Warranty null is reported as null (unobserved/mixed), never coerced to 0 h.')


def _own_paid(evidence, sku):
    led = evidence.get('own_ledger') if isinstance(evidence, dict) and isinstance(evidence.get('own_ledger'), dict) else {}
    return sorted(k for k, v in led.items() if isinstance(v, dict) and v.get('sku') == sku and num(v.get('price'))
                  and v['price'] > 0)


def _held_or_requested(sku, inv, led):
    """Units on hand or pilot requests not released (supply result pending/unknown/supplied) for this SKU."""
    units = [x.get('unit') for x in (inv.get('units') or []) if isinstance(x, dict) and x.get('sku') == sku]
    import oneday_pilot
    reqs = [k for k, r in ((led or {}).get('requests') or {}).items()
            if isinstance(r, dict) and r.get('sku') == sku and r.get('status') not in oneday_pilot.RELEASED]
    return units, reqs


def _new_goods_row(row, now, lst):
    """(data, None) for one deduplicated non-own settle usable as exact paid goods evidence, else (None, reason)."""
    seq, amount, ts, data, shop = row
    if now-ts > NEW_GOODS_WINDOW_S: return None, 'stale'
    if not (isinstance(data.get('order'), str) and data['order'] and isinstance(data.get('unit'), str) and data['unit']):
        return None, 'missing order/unit'
    if data.get('sku') != data.get('item'): return None, 'sku/item mismatch'
    if not (isinstance(data.get('buyer'), str) and data['buyer']): return None, 'missing buyer'
    if data.get('grade') not in NEW_GOODS_GRADES: return None, 'grade not A/B/C'
    if data.get('claim') is not None and data.get('claim_honest') is not True: return None, 'claim not honest'
    if 'expires_at' not in data or data['expires_at'] is not None: return None, 'not goods (expires_at)'
    w = data.get('warranty_hours')
    if w is not None and (not num(w) or w < 0): return None, 'warranty malformed'
    if amount > lst*1.5: return None, 'amount > 1.5 x list'
    return data, None


def new_goods(state, catalog, inv, evidence, led, exclude, now):
    """market-new-goods-v1 (2026-11-14, user-authorized high-beta exploration within the active global caps): goods SKUs
    NOT in the old own-proven candidates admitted from fresh public paid evidence: >= 40 deduplicated exact
    order+unit+seq settles, >= 7 distinct non-own shops, >= 3 distinct buyers within NEW_GOODS_WINDOW_S of a goods-fresh
    (public_evidence.FRESH_S), not-stale, gap-free cache; catalog kind goods with positive finite list. Ranked by paid
    count, shops, compatibility (median / upper bound), SKU. Same tuple shape as oneday_pilot.candidates (contract
    upper bound reservation, no held_max, no new per-SKU cap). A SKU with units on hand or an unreleased request and
    zero exact own paid sales is reported but never returned (no re-buy before an own paid sale; turnover-followup-v1
    rule). Returns (candidates, table rows, summary)."""
    import oneday_pilot
    summary = {'policy':NEW_GOODS_POLICY, 'min':NEW_GOODS_MIN, 'window_s':NEW_GOODS_WINDOW_S, 'qualified':0,
               'label':NEW_GOODS_LABEL, 'complete':False}
    cache, problem = _price_cache(state, now, 'goods')
    if not problem and cache.get('stale'): problem = 'cache marked stale (last GET failed)'
    if not problem and _valid_gaps(cache.get('gaps')):
        problem = f"coverage gap: {len(_valid_gaps(cache.get('gaps')))} live missing seq range(s)"
    if problem: return [], [], dict(summary, reason=f'public cache not usable: {problem}')
    raw = [r for r in cache.get('price_rows', [])+cache.get('rows', []) if isinstance(r, dict)]
    rows = _public_rows(cache, now)
    window = [r for r in rows if now-r[2] <= NEW_GOODS_WINDOW_S]
    summary.update(fetched_at=cache['fetched_at'], source_age_s=now-cache['fetched_at'],
                   source_url=cache.get('source_url'), cache_version=cache.get('cache_version'),
                   future_rows=sum(1 for r in raw if num(r.get('ts')) and r['ts'] > now),
                   window_from=min((r[2] for r in window), default=None), deduplicated_rows=len(rows))
    rejected, groups = {}, {}
    for r in rows:
        data = r[3]
        sku = data.get('item')
        if not isinstance(sku, str) or sku in exclude: continue
        c = catalog.get(sku)
        if not isinstance(c, dict) or c.get('kind') != 'goods' or not num(c.get('list_price')) or c['list_price'] <= 0:
            continue
        ok, why = _new_goods_row(r, now, c['list_price'])
        if why:
            if why != 'stale': rejected[why] = rejected.get(why, 0)+1
            continue
        groups.setdefault(sku, []).append(r)
    summary['rows_rejected'] = sum(rejected.values())+summary['future_rows']
    summary['rejected_reasons'] = dict(rejected, future=summary['future_rows'])
    out, table, near = [], [], []
    for sku, grp in groups.items():
        lst = catalog[sku]['list_price']
        shops, buyers = {r[4] for r in grp}, {r[3]['buyer'] for r in grp}
        ok = len(grp) >= NEW_GOODS_MIN['settlements'] and len(shops) >= NEW_GOODS_MIN['shops'] \
            and len(buyers) >= NEW_GOODS_MIN['buyers']
        if not ok:
            near.append({'sku':sku, 'settlements':len(grp), 'shops':len(shops), 'buyers':len(buyers)})
            continue
        ub = oneday_pilot.upper_bound(lst)
        med = statistics.median(r[1] for r in grp)
        grades, warr, by_grade = {}, {}, {}
        for r in grp:
            g, w = r[3]['grade'], r[3].get('warranty_hours')
            grades[g] = grades.get(g, 0)+1
            key = 'null' if w is None else str(float(w))
            warr[key] = warr.get(key, 0)+1
            by_grade.setdefault(g, []).append(r[1])
        m = {'settlements':len(grp), 'shops':len(shops), 'buyers':len(buyers), 'ledger_ids':sorted(r[0] for r in grp),
             'median_paid':med, 'median_paid_by_grade':{g:statistics.median(v) for g, v in sorted(by_grade.items())},
             'grades_observed':grades, 'warranty_hours_observed':warr, 'list_price':lst, 'unit_upper_bound':ub,
             'compatibility_median_over_upper_bound':med/ub, 'window_s':NEW_GOODS_WINDOW_S, 'label':NEW_GOODS_LABEL}
        units, reqs = _held_or_requested(sku, inv, led)
        paid = _own_paid(evidence, sku)
        row = {'sku':sku, 'baseline_index':None, 'tier':NEW_GOODS_TIER, 'buy_candidate':True,
               'basis':'fresh_independent_public_paid_demand_not_own_margin', 'market':m,
               'own_paid_ids':paid, 'held_unit_ids':units, 'unreleased_request_keys':reqs}
        if (units or reqs) and not paid:
            row.update(tier='held_unsold_no_realized_sale', buy_candidate=False,
                       basis='held/requested with zero own paid sales: no re-buy from the market tier')
        table.append(row)
    table.sort(key=lambda r:(-r['market']['settlements'], -r['market']['shops'],
                             -r['market']['compatibility_median_over_upper_bound'], r['sku']))
    for r in table:
        if r['buy_candidate']:
            out.append((r['sku'], r['market']['unit_upper_bound'], None, r['market']['compatibility_median_over_upper_bound']))
    summary.update(qualified=len(out), held_excluded=[r['sku'] for r in table if not r['buy_candidate']],
                   near_misses=sorted(near, key=lambda x:(-x['settlements'], x['sku']))[:10])
    return out, table, summary


def evaluate(state, inv, catalog, evidence, stage, now):
    """(baseline candidates, ranked candidates, table, facts) from the current pilot ledger; on any error the baseline
    order is kept and the error is reported. The food shelf filter uses own rotation evidence (food_rotation) only when
    it computes; on any error the original fixed-72 h candidates and their order are kept.
    market-new-goods-v1: qualified new goods (new_goods) are placed BEFORE the old ranked candidates so the greedy
    room waterfall in restock_r3._oneday_plan sizes them first; the old ranking then fills the remainder. Any error in
    that tier keeps the old ranking unchanged."""
    import oneday_pilot
    base = original = oneday_pilot.candidates(catalog, evidence, stage)
    try:
        led = oneday_pilot.load_ledger(state)
        try:
            act = json.loads((Path(state)/oneday_pilot.CONFIG).read_text(encoding='utf-8'))['activated_at']
        except (OSError, ValueError, TypeError, KeyError):
            act = None
        facts = contributions(led, inv, now, act) if led is not None else None
        public = None
        if facts is not None:
            facts['food_rotation'] = food_rotation(facts, led, inv, now, catalog)
            base = oneday_pilot.candidates(catalog, evidence, stage, facts['food_rotation'])
            try:
                public, facts['public_summary'] = public_stats(state, base, facts, inv, led, catalog, now)
            except Exception as e:  # public evidence is secondary: own ranking stands without it
                facts['public_summary'] = {'used':0, 'error':repr(e)}
        ranked, table = rank(base, facts, public)
        if facts is not None and isinstance(facts.get('public_summary'), dict):
            facts['public_summary']['public_market_used'] = public_market_used(table)
        if facts is not None:
            try:
                new, new_table, facts['new_goods_summary'] = new_goods(state, catalog, inv, evidence, led,
                                                                       {c[0] for c in base}, now)
                ranked, table = new+ranked, new_table+table
            except Exception as e:  # exploration tier is additive: the old ranking stands without it
                facts['new_goods_summary'] = {'policy':NEW_GOODS_POLICY, 'qualified':0, 'error':repr(e)}
        return base, ranked, table, facts, None
    except Exception as e:  # advisory layer: never blocks or widens the existing guarded plan
        return original, original, [{'sku':c[0], 'baseline_index':i, 'tier':'baseline_order',
                                     'basis':'microeconomics_error'} for i, c in enumerate(original)], None, repr(e)


def write_status(state, ctx, inv, now, ranked=None, facts=None, decision=None, chosen=None, error=None):
    """Every normal hook. outcome: requested (a buy line was planned; supply result still unknown), none (active, no
    line), blocked (pilot inactive: reason). acquisition_claimed is always False: arrivals are proven only by the
    pilot ledger/kit log, never by this file."""
    chosen = list(chosen or [])
    active = ctx.get('active') is True
    status = {'policy_version':MICRO_POLICY, 'updated_at':now, 'updated_kst':kst(now), 'active':active,
              'reason':ctx.get('reason'), 'decision_reason':(decision or {}).get('reason'),
              'outcome':'requested' if chosen else 'none' if active else 'blocked', 'acquisition_claimed':False,
              'chosen':chosen, 'ranked':[r['sku'] for r in ranked or []], 'ranking':ranked or [],
              'chosen_evidence':{r['sku']:r for r in ranked or [] if r['sku'] in chosen},
              'accounting':accounting(ctx, inv), 'error':error}
    if isinstance(facts, dict):
        status.update(skus=facts['skus'], units=facts['units'], rejected=facts['rejected'], held=facts['held'],
                      complete=False, coverage_note=facts['coverage_note'], velocity=facts.get('velocity'),
                      food_rotation=facts.get('food_rotation'), velocity_formula=VELOCITY_FORMULA)
    else:
        status.update(complete=False, coverage_note=COVERAGE_NOTE)
    status['public_summary'] = facts.get('public_summary') if isinstance(facts, dict) else None
    status['new_goods_summary'] = facts.get('new_goods_summary') if isinstance(facts, dict) else None
    try:
        status['board'] = board_comparison(state, inv, now)
    except Exception as e:
        status['board'] = {'status':'error', 'error':repr(e), 'used_in_selection':False}
    tmp =Path(state)/(STATUS+'.tmp')
    with open(tmp, 'w', encoding='utf-8') as s:
        json.dump(status, s, indent=2, ensure_ascii=False, sort_keys=True)
        s.flush()
        os.fsync(s.fileno())
    os.replace(tmp, Path(state)/STATUS)
    return status


def held(inv, now):
    """On-hand units: age, goods storage accrued, food life left, break-even (cost + storage so far). Advisory only:
    no price is changed from this."""
    rate = inv.get('storage_per_day') if num(inv.get('storage_per_day')) else .01
    out = {}
    for x in inv.get('units') or []:
        if not isinstance(x, dict) or not isinstance(x.get('unit'), str) or not num(x.get('cost')) or x['cost'] < 0:
            continue
        # a future or missing arrival is unknown age/storage, never negative
        age = (now-x['supplied_at'])/3600 if num(x.get('supplied_at')) and x['supplied_at'] <= now else None
        store = x['cost']*rate*age/24 if age is not None and x.get('kind') == 'goods' else 0 if x.get('kind') == 'food' else None
        life = (x['expires_at']-now)/3600 if x.get('kind') == 'food' and num(x.get('expires_at')) else None
        out[x['unit']] = {'sku':x.get('sku'), 'kind':x.get('kind'), 'cost':x['cost'], 'age_hours':age,
                          'storage_accrued':store, 'remaining_life_hours':life,
                          'break_even':None if store is None else x['cost']+store}
    return out
