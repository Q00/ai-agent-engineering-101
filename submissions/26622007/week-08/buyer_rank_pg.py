"""Quotes ranked by the chance of a deal times the share of list it keeps (÷ seconds only with "per_time": true; owner 11/08, telegram 7959-7963,
7977; review/W-20261108-1655-AX가게별바이어/shop-profiles.md 5-6: replayed on 11/07 19:09 - 11/08 19:09, P x price took
+3% deals and +11-14% buyer ALL/h over the current order, ÷T within 1% of it).

Of the quotes within my limit (fits first, as before; the limits and guards are not touched here), best_quote keeps the
one with the most

    P(shop, kind) x g / T,   g = (1 - b)(1 - E[final] / list),   E[final] = ask x E[final / first ask](shop, kind)

(g = (1 - b)(1 - p) - q at a short warranty, buyer_short_warranty.expected), T = the quote seconds so far + the shop's
expected talk seconds + NEXT_ORDER_SECONDS. The share is of the final price against the list price (7961), never the cut
from the ask. P, E[final / first ask] and the talk seconds come from the last HOURS of measure/orders.jsonl, per shop and
kind, shrunk to the kind's pool: (k + SHRINK p) / (n + SHRINK). No talk seconds known: no ÷T.

The ÷T is only with "per_time": true in the switch; without it the key is P x g (main 11/08 19:22: the final replay,
shop-profiles.md 6, gave P x price +12% ALL/h and ÷T +9%).

No-fit learning uses the recorded selection tier and eligibility, not any-fit over all quotes. Legacy tier
unknowns are excluded and counted. Learning successes require an exact confirmed payment/settlement join;
delivered unknowns are excluded from resolved probability denominators and retain unknown economic value.
No-fit requires the versioned A/B switch documented in rank-integrity.md; old nofit switches are off.
A and B share adaptive statistics, so A is not a fixed holdout and this is not a full-ON causal estimate.

Switch: .market/buyer_rank_pg.json {"enabled": true}; missing, unreadable or anything else is off, and off is the
current order exactly. "gain_base": a number in (0, 1) ranks by P x (g - gain_base) where g is Patch 3.6's score
(buyer_margin_cap on, a quote not priced as a short warranty); missing or anything else is P x g as before. BUYER_RANK_PG=0 in the environment is off too. Read at every order, so no restart either way.

"ledger_cover": true in the switch (exactly true) reads the ledger back to the oldest paid seq of the order window
(LEDGER_MAX_BYTES at most) instead of the fixed TAIL_BYTES tail. Every order and both arms of any study read the same
stats, so turning it on is a time boundary common to all of them: turn it on at a recorded time and write that time
in the running studies' records. The cache is computed again at once when its ledger_cover differs from the switch,
and the order rows and journal carry "ledger_cover": true while the stats they used were so computed.
"""
import fcntl
import hashlib
import json
import math
import os
import time
import uuid

import buyer_food_ladder
import buyer_goods_grade
import buyer_goods_ladder
import buyer_goods_take30
import buyer_margin_cap
import buyer_goods_risk_sku30
import buyer_goods_quote_yield30
import buyer_goods_quote_inquiry30
import buyer_moss_first
import buyer_saved_floor
import buyer_warranty_said

SWITCH = "buyer_rank_pg.json"
CACHE = "buyer_rank_pg_cache.json"
HOURS = 24
NOFIT_HOURS = 3     # no-fit deal rates turned at 19:32 (short warranty on): ddolcom 112/198 before, 1/22 after
NOFIT_BAND = 0.20   # = market_buyer.TALK_OVER_SHARE: a kept ask over it ends with no talk, so it says nothing of the shop
SHRINK = 20.0
NEXT_ORDER_SECONDS = 20.0
REFRESH = 1800
TAIL_BYTES = 30_000_000
# With "ledger_cover": true in the switch, the ledger read doubles from TAIL_BYTES until it reaches the oldest
# paid seq of the order window: on 11/09 the 30 MB tail began at seq 4191008 and left 605 window payments unknown
# (680 of 1,896 confirmed; a full read leaves 0). The ceiling bounds the read; what it still misses stays unknown.
LEDGER_MAX_BYTES = 400_000_000
DONE = "TASK_STATE_COMPLETED"


def _num(x):
    try:
        return type(x) in (int, float) and math.isfinite(x)
    except OverflowError:
        return False


def _switch(md, env=None):
    """The switch's dict when it is on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_RANK_PG") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    return d if isinstance(d, dict) and d.get("enabled") is True else None


def enabled(md, env=None):
    return _switch(md, env) is not None


def _first_ask(r):
    for q in r.get("quotes") or ():
        if isinstance(q, dict) and q.get("shop") == r.get("shop") and _num(q.get("ask")) and q["ask"] > 0:
            return q["ask"]
    asks = r.get("asks")
    return asks[0] if isinstance(asks, list) and asks and _num(asks[0]) and asks[0] > 0 else None


def _new_cell():
    return dict(n=0, k=0, confirmed=0, refused=0, unknown=0, failed=0,
                ratio_sum=0.0, ratio_n=0, talk_sum=0.0, talk_n=0)


def _add(c, outcome, price, first, talk):
    c['n'] += 1
    c[outcome] += 1
    if outcome == 'confirmed':
        c['k'] += 1
        if first:
            c['ratio_sum'] += price / first
            c['ratio_n'] += 1
    if talk is not None and talk >= 0:
        c['talk_sum'] += talk
        c['talk_n'] += 1


def _summarize(c, pl=None):
    resolved = c['n'] - c['unknown']
    p = c['k'] / resolved if resolved else None
    ratio = c['ratio_sum'] / c['ratio_n'] if c['ratio_n'] else 1.0
    talk = c['talk_sum'] / c['talk_n'] if c['talk_n'] else None
    if pl is not None:
        p = (c['k'] + SHRINK * pl['p']) / (resolved + SHRINK) if pl['p'] is not None else None
        ratio = (c['ratio_sum'] + SHRINK * pl['ratio']) / (c['ratio_n'] + SHRINK)
        talk = (c['talk_sum'] + SHRINK * pl['talk']) / (c['talk_n'] + SHRINK) if pl['talk'] is not None else None
    return {**{k: c[k] for k in ('n', 'k', 'confirmed', 'refused', 'unknown', 'failed')},
            'resolved_n': resolved, 'p': p, 'ratio': ratio, 'talk': talk}


def stats_from_rows(rows, paid, now):
    """Learning cells use resolved observations only; delivered unknown is never a zero return.

    paid contains exact-join statuses from payment_outcomes, never bare paid=True.
    Non-delivered terminal orders (including NO_VALUE) are known opportunity failures.
    Legacy selection tiers are not reconstructed from whole-quote fit flags.
    """
    cell, pool, ncell, npool = {}, {}, {}, {}
    counts = dict(nofit_population_unknown=0, excluded_rows=0, malformed_rows=0, payment_unknown=0)
    for r in rows:
        if not isinstance(r, dict):
            counts['excluded_rows'] += 1
            counts['malformed_rows'] += 1
            continue
        ts, shop, kind = r.get('ts'), r.get('shop'), r.get('kind')
        oid = r.get('order')
        seconds = r.get('seconds')
        quotes = r.get('quotes')
        malformed = (not _num(ts) or not isinstance(shop, str) or not isinstance(kind, str)
                     or not isinstance(r.get('state'), (str, type(None)))
                     or not isinstance(oid, str) or not oid
                     or not isinstance(seconds, (dict, type(None)))
                     or (isinstance(seconds, dict) and any(v is not None and not _num(v) for v in seconds.values()))
                     or not isinstance(quotes, (list, type(None)))
                     or (isinstance(quotes, list) and any(not isinstance(q, dict) or not isinstance(q.get('shop'), str) for q in quotes))
                     or (r.get('price') is not None and not _num(r.get('price')))
                     or type(r.get('passed_to_kit')) not in (bool, type(None)))
        counts['malformed_rows'] += malformed
        if not (_num(ts) and now - HOURS * 3600 <= ts <= now and isinstance(shop, str) and kind in ('goods', 'food')):
            counts['excluded_rows'] += 1
            continue
        if not isinstance(r.get('state'), str):
            counts['excluded_rows'] += 1
            continue
        if buyer_goods_grade.lowered(r):    # arm B's lowered limit: not learned for both arms (review 8ca80f5 I1)
            counts['excluded_rows'] += 1
            continue
        if buyer_margin_cap.lowered(r):     # a limit the margin cap lowered (0: no talk held) says nothing of the shop
            counts['excluded_rows'] += 1
            continue
        if buyer_goods_ladder.laddered(r):  # arm B's 0.8 openings: not learned for both arms (review 459089c I2)
            counts['excluded_rows'] += 1
            continue
        if buyer_food_ladder.laddered(r):   # food-ladder arm B's 0.8 (C 0.7) openings, the same
            counts['excluded_rows'] += 1
            continue
        if buyer_saved_floor.floored(r):    # saved-floor arm B's floor / no band, the same (GPT Pro 1638 P1)
            counts['excluded_rows'] += 1
            continue
        if buyer_warranty_said.said_b(r):   # warranty-said arm B's 6 h pricing, the same (review 08b282d I2)
            counts['excluded_rows'] += 1
            continue
        if buyer_moss_first.applied_b(r):   # moss-first arm B's code-priced first turn, the same (review 5445a75 I1)
            counts['excluded_rows'] += 1
            continue
        if buyer_goods_take30.applied_b(r) or buyer_goods_risk_sku30.applied_b(r) or buyer_goods_quote_yield30.applied_b(r) or buyer_goods_quote_inquiry30.applied_b(r):  # actual treatment only
            counts['excluded_rows'] += 1
            continue
        qs = [q for q in quotes if isinstance(q, dict)] if isinstance(quotes, list) else []
        safe = dict(r, quotes=qs)
        first = _first_ask(safe)
        delivered = r.get('state') == DONE and r.get('passed_to_kit') is True
        outcome = paid.get(oid, 'unknown') if delivered and isinstance(oid, str) else 'unknown' if delivered else 'failed'
        if r.get('state') == DONE and type(r.get('passed_to_kit')) is not bool:
            outcome = 'unknown'
        if delivered and not (_num(r.get('price')) and r['price'] > 0):
            outcome = 'unknown'
        if outcome not in ('confirmed', 'refused', 'unknown', 'failed'):
            outcome = 'unknown'
        counts['payment_unknown'] += outcome == 'unknown'
        seconds = seconds if isinstance(seconds, dict) else {}
        talk = seconds.get('end') - seconds.get('quotes') if _num(seconds.get('end')) and _num(seconds.get('quotes')) else None
        kept = next((q for q in qs if q.get('shop') == shop and _num(q.get('ask')) and _num(q.get('limit'))), None)
        selection = r.get('rank_selection')
        known = (isinstance(selection, dict) and selection.get('version') == VERSION
                 and selection.get('tier') in ('normal', 'short') and type(selection.get('eligible')) is bool
                 and isinstance(selection.get('shops'), list) and shop in selection['shops'])
        counts['nofit_population_unknown'] += not known and kind == 'goods' and ts >= now - NOFIT_HOURS * 3600
        if known and selection['eligible'] and kind == 'goods' and ts >= now - NOFIT_HOURS * 3600 and kept is not None \
                and 0 < kept['limit'] and 0 < kept['ask'] <= kept['limit'] * (1 + NOFIT_BAND):
            for c in (ncell.setdefault((shop, kind), _new_cell()), npool.setdefault(kind, _new_cell())):
                _add(c, outcome, r.get('price'), first, talk)
        for c in (cell.setdefault((shop, kind), _new_cell()), pool.setdefault(kind, _new_cell())):
            _add(c, outcome, r.get('price'), first, talk)
    pools = {k: _summarize(c) for k, c in pool.items()}
    shops = {f'{shop}|{kind}': _summarize(c, pools[kind]) for (shop, kind), c in cell.items()}
    npools = {k: _summarize(c) for k, c in npool.items()}
    nshops = {f'{shop}|{kind}': _summarize(c, npools[kind]) for (shop, kind), c in ncell.items()}
    return dict(pool=pools, shops=shops, nofit=dict(pool=npools, shops=nshops), counts=counts)


def _tail(path, nbytes=TAIL_BYTES, diagnostics=None):
    """Bounded tail or full streaming read, with explicit collection/parse coverage."""
    info = diagnostics if diagnostics is not None else {}
    info.update(missing=False, truncated=False, malformed_lines=0, rows=0, first_seq=None,
                first_ts=None, first_row_sha256=None, max_seq=None)
    try:
        f = open(path, 'rb')
    except OSError:
        info['missing'] = True
        return
    with f:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - nbytes) if nbytes is not None else 0
        info.update(bytes_at_open=size, truncated=start > 0)
        f.seek(start)
        if start:
            f.readline()  # discard the possible partial first line, never claim prefix completeness
        while f.tell() < size:
            raw = f.readline()
            if not raw:
                break
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError):
                info['malformed_lines'] += 1
                continue
            if not isinstance(row, dict):
                info['malformed_lines'] += 1
                continue
            if info['first_row_sha256'] is None:
                info.update(first_row_sha256=hashlib.sha256(raw).hexdigest(),
                            first_seq=row.get('seq') if type(row.get('seq')) is int else None,
                            first_ts=row.get('ts') if _num(row.get('ts')) else None)
            info['rows'] += 1
            seq = row.get('seq')
            if type(seq) is int:
                info['max_seq'] = max(seq, info['max_seq']) if info['max_seq'] is not None else seq
            yield row


def _seq(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return value if type(value) is int else None


def payment_outcomes(orders, payments, ledger, buyer='nocha12'):
    """ax-paid-deals contract: last payment; exact order/seq/buyer/amount settle join.

    A paid boolean alone is not confirmation. Unknown means no economic observation.
    Order IDs are opaque strings (including URL-shaped IDs), never parsed as o-N.
    """
    want = {r.get('order') for r in orders if isinstance(r, dict) and isinstance(r.get('order'), str) and r['order']}
    pays = {}
    for p in payments:
        if isinstance(p, dict) and isinstance(p.get('order'), str) and p['order'] in want:
            pays[p['order']] = p
    seqs = {_seq(p.get('ledger')) for p in pays.values() if p.get('paid') is True} - {None}
    settles = {}
    for s in ledger:
        if isinstance(s, dict) and s.get('k') == 'settle' and type(s.get('seq')) is int and s['seq'] in seqs:
            settles[s['seq']] = s
    outcomes = {}
    for oid in want:
        p = pays.get(oid, {})
        if p.get('paid') is False:
            outcomes[oid] = 'refused'
        elif p.get('paid') is True:
            s = settles.get(_seq(p.get('ledger')), {})
            outcomes[oid] = 'confirmed' if (s.get('order') == oid and s.get('buyer') == buyer
                and _num(p.get('price')) and p['price'] > 0 and _num(s.get('amount'))
                and s['amount'] == p['price']) else 'unknown'
        else:
            outcomes[oid] = 'unknown'
    return outcomes


def compute(md, now, buyer='nocha12'):
    # Bounded observation for adaptive ranking, not a full-epoch economic certification.
    # Tail misses remain unknown; full-epoch ITT uses reconcile_itt instead.
    inputs = {'orders': {}, 'payments': {}, 'ledger': {}}
    orders = list(_tail(os.path.join(md, 'measure', 'orders.jsonl'), TAIL_BYTES, inputs['orders']))
    cover = (_switch(md) or {}).get('ledger_cover') is True
    if cover:
        payments = list(_tail(os.path.join(md, 'payments.jsonl'), 4_000_000, inputs['payments']))
        need = _need_seq(orders, payments)
        ledger, nbytes = _ledger_cover(os.path.join(md, 'ledger_rows.jsonl'), need, inputs['ledger'])
    else:
        payments = _tail(os.path.join(md, 'payments.jsonl'), 4_000_000, inputs['payments'])
        need, nbytes = None, TAIL_BYTES
        ledger = _tail(os.path.join(md, 'ledger_rows.jsonl'), TAIL_BYTES, inputs['ledger'])
    outcomes = payment_outcomes(orders, payments, ledger, buyer)
    result = stats_from_rows(orders, outcomes, now)
    result['counts']['malformed_input_lines'] = sum(v.get('malformed_lines', 0) for v in inputs.values())
    result['observation'] = {'inputs': inputs, 'bounded': True, 'unknown_is_zero': False,
                             'order_tail_bytes': TAIL_BYTES, 'payment_tail_bytes': 4_000_000, 'ledger_tail_bytes': nbytes,
                             'ledger_cover': cover, 'ledger_need_seq': need}
    return result


def _need_seq(orders, payments):
    """The oldest ledger seq a paid payment of the window points at (the last payment per order, as payment_outcomes)."""
    want = {r.get('order') for r in orders if isinstance(r, dict) and isinstance(r.get('order'), str) and r['order']}
    pays = {}
    for p in payments:
        if isinstance(p, dict) and isinstance(p.get('order'), str) and p['order'] in want:
            pays[p['order']] = p
    seqs = {_seq(p.get('ledger')) for p in pays.values() if p.get('paid') is True} - {None}
    return min(seqs) if seqs else None


def _first_seq_at(path, nbytes):
    """(seq of the first whole row of the last nbytes, file size), reading only that row: None when there is none."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - nbytes)
            f.seek(start)
            if start:
                f.readline()
            for _ in range(64):
                raw = f.readline()
                if not raw:
                    break
                try:
                    row = json.loads(raw)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    continue
                if isinstance(row, dict) and type(row.get('seq')) is int:
                    return row['seq'], size
            return None, size
    except OSError:
        return None, 0


def _ledger_cover(path, need, info):
    """(streamed ledger rows, bytes) from a tail reaching seq `need`: the tail size doubles from TAIL_BYTES up to
    LEDGER_MAX_BYTES, probing only the first row's seq at each size, then the rows stream once as the fixed tail does
    (no list of the ledger is held: review 2 I-a)."""
    n = min(TAIL_BYTES, LEDGER_MAX_BYTES)
    while need is not None and n < LEDGER_MAX_BYTES:
        first, size = _first_seq_at(path, n)
        if n >= size or (first is not None and first <= need):
            break
        n = min(n * 2, LEDGER_MAX_BYTES)
    return _tail(path, n, info), n


def _read_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None, None
    if not isinstance(d, dict) or d.get("version") != VERSION or not _num(d.get("at")) or not isinstance(d.get("stats"), dict):
        return None, None
    return d["at"], d["stats"]


def stats(md, now=None):
    """The cached stats, computed again from the log when older than REFRESH (one buyer run at a time; the others go on
    with the cached ones, or None)."""
    now = time.time() if now is None else now
    path = os.path.join(md, CACHE)
    cover = ((_switch(md) or {}).get('ledger_cover') is True)
    fresh = lambda at, st: (at is not None and 0 <= now - at < REFRESH and isinstance(st, dict)
                            and isinstance(st.get('observation', {}), dict)
                            and (st.get('observation', {}).get('ledger_cover') is True) == cover)
    at, st = _read_cache(path)
    if fresh(at, st):
        return st
    try:
        with open(path + ".lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return st
            at, st = _read_cache(path)
            if fresh(at, st):
                return st
            try:
                st = compute(md, now)
            except Exception:   # noqa: BLE001 - a broken log is no ranking until the next REFRESH
                st = {"pool": {}, "shops": {}, "observation": {"ledger_cover": cover, "failed": True}}
            tmp = f"{path}.{os.getpid()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"version": VERSION, "at": now, "stats": st}, f, sort_keys=True)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except OSError:
        pass
    return st


JOURNAL = 'buyer_rank_pg_assignments.jsonl'
VERSION = 2


def _experiment(sw, now):
    return (sw.get('nofit') == 'ab' and sw.get('nofit_version') == VERSION
            and isinstance(sw.get('nofit_experiment'), str) and bool(sw['nofit_experiment'].strip())
            and _num(sw.get('nofit_epoch')) and 0 < sw['nofit_epoch'] <= now)


def _journal(rk, event):
    """Locked append + fsync; preserve and isolate any interrupted prior tail."""
    row = {k: v for k, v in metadata(rk).items() if k not in ('error', 'nofit_scores', 'scores')}
    row.update(event=event, ts=rk.get("_at", time.time()))
    path = os.path.join(rk['_journal_md'], JOURNAL)
    data = (json.dumps(row, sort_keys=True, ensure_ascii=True) + '\n').encode('utf-8')
    try:
        with open(path, 'a+b', buffering=0) as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            if f.seek(0, os.SEEK_END):
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b'\n':
                    # Never truncate or classify incomplete evidence. A failed or
                    # killed writer must not join its tail to this assignment.
                    data = b'\n' + data
            view = memoryview(data)
            while view:
                n = os.write(f.fileno(), view)
                if n <= 0:
                    raise OSError('short journal write')
                view = view[n:]
            os.fsync(f.fileno())
        return True
    except (OSError, ValueError, TypeError):
        return False


def metadata(rk):
    return {k: v for k, v in rk.items() if k != 'stats' and not k.startswith('_')}


def selection_recorded(rk):
    """Persist selection separately from the late terminal order record."""
    if rk is not None and rk.get('_journal_md') is not None:
        if not _journal(rk, 'selection'):
            rk['journal_error'] = True


def unselected(rk, reason):
    if rk is not None:
        rk.update(treatment='untreated', untreated_reason=reason)
        selection_recorded(rk)


def state(md, order, env=None, now=None):
    """Assign durably before touching the cache. Missing statistics never erase ITT."""
    now = time.time() if now is None else now
    sw = _switch(md, env)
    if sw is None:
        return None
    gb = sw.get('gain_base')
    rk = dict(stats={}, per_time=sw.get('per_time') is True, nofit=False)
    if _num(gb) and 0 < gb < 1:
        rk['gain_base'] = gb
    active = _experiment(sw, now)
    if active:
        oid = order.get('order') if isinstance(order, dict) else None
        valid = isinstance(oid, str) and bool(oid)
        arm = nofit_arm(sw, order)
        rk.update(experiment=sw['nofit_experiment'], version=VERSION, epoch=sw['nofit_epoch'],
                  invocation=uuid.uuid4().hex, order=oid if valid else None,
                  kind=order.get('kind') if isinstance(order, dict) and isinstance(order.get('kind'), str) else None,
                  nofit_arm=arm, assignment='assigned' if arm else 'unknown',
                  treatment='unknown', untreated_reason='selection_pending' if valid else 'invalid_order_id',
                  _journal_md=md, _at=now)
        # Persist assignment before cache work, even if cache raises BaseException or this run is terminated.
        if not _journal(rk, 'assignment'):
            rk.update(journal_error=True, treatment='untreated', untreated_reason='journal_unavailable')
    else:
        rk['untreated_reason'] = 'inactive_or_legacy_switch'
    try:
        st = stats(md, now)
        rk['stats'] = st if isinstance(st, dict) else {}
        obs = rk['stats'].get('observation') if isinstance(rk['stats'].get('observation'), dict) else {}
        if obs.get('ledger_cover') is True and not obs.get('failed'):
            rk['ledger_cover'] = True       # the time boundary common to all arms, on the row and in the journal
        nf = rk['stats'].get('nofit')
        nf = nf if isinstance(nf, dict) else {}
        cells = [c for name in ('pool', 'shops') if isinstance(nf.get(name), dict) for c in nf[name].values()]
        unavailable = not any(isinstance(c, dict) and _num(c.get('p')) and 0 <= c['p'] <= 1 for c in cells)
        reason = 'cache_unavailable' if unavailable else 'selection_pending'
    except Exception:
        rk['stats'] = {}
        unavailable, reason = True, 'cache_exception'
    if active and not rk.get('journal_error'):
        if unavailable or rk['assignment'] == 'unknown':
            rk.update(treatment='untreated', untreated_reason=reason if rk['assignment'] != 'unknown' else 'invalid_order_id')
        rk['nofit'] = rk['nofit_arm'] == 'B' and not unavailable and rk['assignment'] == 'assigned'
        if not _journal(rk, 'cache'):
            rk.update(nofit=False, journal_error=True, treatment='untreated', untreated_reason='journal_unavailable')
    return rk


def nofit_arm(sw, order):
    """Versioned experiment; legacy nofit switches cannot activate this implementation."""
    oid = order.get('order') if isinstance(order, dict) else None
    if not isinstance(sw, dict) or not _experiment(sw, float('inf')) or not isinstance(oid, str) or not oid:
        return None
    return 'B' if int(hashlib.sha256(oid.encode('utf-8', 'surrogatepass')).hexdigest(), 16) % 2 else 'A'


def reconcile_itt(md, experiment, epoch, end=None):
    """Read-only union of ALL epoch order rows and early journal, with unknowns preserved.

    Full files intentionally: a tail can silently drop the beginning of an epoch.
    Missing assignment is unknown, never reconstructed as a successful treatment.
    """
    end = time.time() if end is None else end
    entries = {}
    unlinked = 0
    invalid_timestamps = 0
    diagnostics = {'orders': {}, 'journal': {}}
    for i, row in enumerate(_tail(os.path.join(md, 'measure', 'orders.jsonl'), None, diagnostics['orders'])):
        ts = row.get('ts')
        if not _num(ts):
            invalid_timestamps += 1
        if not (_num(ts) and epoch <= ts < end):
            continue
        oid = row.get('order')
        meta = row.get('rank_pg')
        meta = meta if isinstance(meta, dict) else {}
        key = ('order', oid) if isinstance(oid, str) and oid else ('invocation', meta['invocation']) if isinstance(meta.get('invocation'), str) else ('row', i)
        unlinked += key[0] == 'row'
        entries[key] = dict(assignment='unknown', treatment='unknown', source='order', has_order=True,
                            order=oid if isinstance(oid, str) and oid else None, ts=ts,
                            kind=row.get('kind') if isinstance(row.get('kind'), str) else None,
                            state=row.get('state') if isinstance(row.get('state'), str) else None, _terminal_metadata=False)
        if meta.get('experiment') == experiment and meta.get('epoch') == epoch and meta.get('version') == VERSION:
            entries[key].update(meta)
            entries[key]['_terminal_metadata'] = True
    for row in _tail(os.path.join(md, JOURNAL), None, diagnostics['journal']):
        if row.get('experiment') != experiment or row.get('epoch') != epoch or row.get('version') != VERSION:
            continue
        ts = row.get('ts')
        if not (_num(ts) and epoch <= ts < end):
            continue
        oid = row.get('order')
        token = row.get('invocation')
        if not isinstance(token, str):
            continue
        key = ('order', oid) if isinstance(oid, str) and oid else ('invocation', token)
        entry = entries.setdefault(key, dict(assignment='unknown', treatment='unknown', source='journal', has_order=False))
        # Terminal measurement is newer than selection; journal fills missing early-exit metadata only.
        if not entry.get('_terminal_metadata'):
            entry.update(row)
    assignments = dict(A=0, B=0, unknown=0)
    treatments = dict(applied=0, untreated=0, unknown=0)
    for entry in entries.values():
        arm = entry.get('nofit_arm') if entry.get('assignment') == 'assigned' else None
        assignments[arm if arm in ('A', 'B') else 'unknown'] += 1
        t = entry.get('treatment')
        treatments[t if isinstance(t, str) and t in treatments else 'unknown'] += 1
        entry.pop('_terminal_metadata', None)
    return dict(experiment=experiment, version=VERSION, epoch=epoch, end=end, total=len(entries),
                assignment=assignments, treatment=treatments, orders=list(entries.values()), inputs=diagnostics,
                unlinked_order_rows=unlinked, coverage_certified=False,
                excluded_invalid_timestamps=invalid_timestamps,
                all_observed_rows_linked=not unlinked and not invalid_timestamps and not any(
                    v.get('missing') or v.get('malformed_lines') for v in diagnostics.values()))


def cell(rk, shop, kind):
    """(P, E[final / first ask], talk seconds or None) for shop and kind: its shrunk cell, else the kind's pool, else
    (None, 1.0, None)."""
    st = (rk or {}).get("stats") or {}
    c = (st.get("shops") or {}).get(f"{shop}|{kind}") or (st.get("pool") or {}).get(kind) or {}
    p, ratio, talk = c.get("p"), c.get("ratio"), c.get("talk")
    return (p if _num(p) and 0 <= p <= 1 else None,
            ratio if _num(ratio) and ratio > 0 else 1.0,
            talk if _num(talk) and talk >= 0 else None)


def nofit_p(rk, shop, kind):
    """P(deal | no ask within my limit) for shop and kind: its shrunk cell, else the kind's no-fit pool, else None."""
    nf = ((rk or {}).get("stats") or {}).get("nofit") or {}
    c = (nf.get("shops") or {}).get(f"{shop}|{kind}") or (nf.get("pool") or {}).get(kind) or {}
    p = c.get("p")
    return p if _num(p) and 0 <= p <= 1 else None


def score(rk, shop, kind, ask, list_price, g_of, quote_seconds, penalty=0.0, base=None):
    """P x (g - penalty - base) / T for one quote, or None when it cannot be scored (no P, no price). g_of(p): the share
    of list a deal at p = price / list keeps in expectation (the caller's, with the break chance and any short warranty).
    base (the switch's gain_base, the caller passes it only where g is Patch 3.6's score): a deal moves Saved by
    (g - S) / (deals + 101), so P x g ranked a likely deal that lowers Saved over a less likely one that raises it;
    base = S less what one more deal is worth in the deals percentile, in units of Saved (see the switch's why)."""
    if not (_num(ask) and _num(list_price) and list_price > 0):
        return None
    P, ratio, talk = cell(rk, shop, kind)
    if P is None:
        return None
    g = g_of(ask * ratio / list_price) - penalty - (base or 0.0)
    if g <= 0:          # a deal that keeps nothing: ranked by g alone (P and T would flip it), under any that keeps some
        return g
    if talk is None or (rk or {}).get("per_time") is not True:
        return P * g
    return P * g / (max(0.0, quote_seconds or 0.0) + talk + NEXT_ORDER_SECONDS)
