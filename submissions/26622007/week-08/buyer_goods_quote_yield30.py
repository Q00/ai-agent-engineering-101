"""Normal goods quote-slot selection and its historical finite trial.

Assignment is ITT; applied means a new initial shop's thread actually started,
not an answer, agreement or payment. Audit files contain hashes, never salt/text.
"""
import hashlib
import json
import os
import tempfile
import time
from urllib.parse import urlsplit

from buyer_goods_risk_sku30 import arm, ident, number

SWITCH = 'buyer_goods_quote_yield30.json'
DEFAULT_SWITCH = 'buyer_goods_quote_yield.json'
KEY = '_goods_quote_yield30'
PREFIX = '.goods-quote-yield30-'


def default_enabled(md):
    """Normal policy is on unless explicitly killed; malformed/unreadable is off."""
    try:
        with open(os.path.join(md, DEFAULT_SWITCH), encoding='utf-8') as src:
            raw = src.read(1025)
        value = json.loads(raw)
        return len(raw) <= 1024 and isinstance(value, dict) and value.get('enabled') is True
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError, RecursionError):
        return False


def switch_default(md, *, enabled):
    """Explicit reversible operator helper; never changes the finite study file."""
    if type(enabled) is not bool:
        raise ValueError('enabled must be boolean')
    fd, tmp = tempfile.mkstemp(prefix='.goods-quote-yield-default-', dir=md)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as dst:
            json.dump(dict(enabled=enabled), dst); dst.flush(); os.fsync(dst.fileno())
        os.replace(tmp, os.path.join(md, DEFAULT_SWITCH))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _default_on(md, now, started, oid):
    if not default_enabled(md):
        return False
    # An active, pending or malformed finite controller owns this boundary.
    # Only absence, explicit OFF or a valid expired window permits new normal orders.
    try:
        with open(os.path.join(md, SWITCH), encoding='utf-8') as src:
            raw = src.read(16385)
            mtime = os.fstat(src.fileno()).st_mtime
        c = json.loads(raw)
        if len(raw) > 16384 or not isinstance(c, dict):
            return False
        if ident(c.get('study')):
            try:
                os.stat(os.path.join(md, PREFIX + _hash((c['study'] + ':' + oid).encode()) + '.seen'))
                return False  # a historical assignment must never be reassigned as normal
            except FileNotFoundError:
                pass
        if c.get('enabled') is False:
            return True
        return (c.get('enabled') is True and all(ident(c.get(k)) for k in ('study','salt')) and
                all(number(v) for v in (now,c.get('start'),c.get('end'))) and
                0 < c['start'] < c['end'] == c['start'] + 1800 and mtime <= c['start'] and now >= started >= c['end'])
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError, RecursionError):
        return False


def config(md, now=None):
    now = time.time() if now is None else now
    try:
        with open(os.path.join(md, SWITCH), encoding='utf-8') as src:
            raw = src.read(16385)
            mtime = os.fstat(src.fileno()).st_mtime
        c = json.loads(raw)
        if (len(raw) > 16384 or not isinstance(c, dict) or c.get('enabled') is not True or
                not all(ident(c.get(k)) for k in ('study', 'salt')) or
                not all(number(v) for v in (now, c.get('start'), c.get('end'))) or
                not 0 < c['start'] < c['end'] == c['start'] + 1800 or
                not mtime <= c['start'] <= now < c['end']):
            return None
        return {k:c[k] for k in ('study','salt','start','end')} | {'sha256': _hash(raw.encode())}
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def switch_on(md, *, study, salt, start, end):
    if not (ident(study) and ident(salt) and all(number(v) for v in (start,end)) and
            0 < time.time() < start < end == start + 1800):
        raise ValueError('future exact 1800-second window required')
    value = dict(enabled=True, study=study, salt=salt, start=start, end=end)
    with open(os.path.join(md, SWITCH), 'x', encoding='utf-8') as dst:
        json.dump(value, dst, allow_nan=False); dst.flush(); os.fsync(dst.fileno())
    return value


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _write(path, value):
    data = _bytes(value)
    with open(path, 'xb') as dst:
        dst.write(data); dst.flush(); os.fsync(dst.fileno())
    return _hash(data)


def _unknown(ctx):
    ctx['note']['trace'] = 'unknown'
    ctx['disabled'] = True


def assign(md, order, now=None):
    """Called before selection, including no-stock and give-up; no model/file writes OFF."""
    if not isinstance(order, dict) or order.get('kind') != 'goods' or KEY in order:
        return
    now = time.time() if now is None else now
    cfg = config(md, now)
    started = order.get('_buyer_started_at')
    if cfg is None:
        if (ident(order.get('order')) and number(now) and number(started) and 0 < started <= now and
                _default_on(md, now, started, order['order'])):
            order[KEY] = dict(mode='default', md=md, oid=order['order'], started=started,
                              note=dict(applied=False))
        return
    if (cfg is None or not ident(order.get('order')) or not number(started) or
            not cfg['start'] <= started <= now < cfg['end']):
        return
    oid = order['order']
    ref = _hash((cfg['study'] + ':' + oid).encode())
    path = os.path.join(md, PREFIX + ref)
    note = dict(study=cfg['study'], arm=arm(cfg, oid), applied=False, ref=ref)
    ctx = dict(md=md, cfg=cfg, oid=oid, started=started, path=path, note=note)
    order[KEY] = ctx
    try:
        os.mkdir(path + '.seen')  # nonblocking immutable order reservation, even after a failed write
        ctx['assignment'] = _write(path + '.json', dict(config=cfg['sha256'], order=_hash(oid.encode()),
            started=started, arm=note['arm'], reason='assigned'))
    except (OSError, ValueError, TypeError, RecursionError):
        _unknown(ctx)


def measurement(order):
    ctx = order.get(KEY)
    if isinstance(ctx, dict) and ctx.get('mode') == 'default':
        return None  # normal rows learn normally; no permanent trial note/journal
    return dict(ctx['note']) if isinstance(ctx, dict) and isinstance(ctx.get('note'), dict) else None


def fields(order):
    note = measurement(order)
    return {'goods_quote_yield30': note} if note is not None else {}


def applied_b(row):
    note = row.get('goods_quote_yield30') if isinstance(row, dict) else None
    return isinstance(note, dict) and note.get('arm') == 'B' and note.get('applied') is True


def _active(ctx, order):
    if ctx.get('mode') == 'default':
        return (not ctx.get('disabled') and order.get('kind') == 'goods' and
                order.get('order') == ctx['oid'] and order.get('_buyer_started_at') == ctx['started'] and
                _default_on(ctx['md'], time.time(), ctx['started'], ctx['oid']))
    if (ctx.get('disabled') or ctx['note']['arm'] != 'B' or order.get('kind') != 'goods' or
            order.get('order') != ctx['oid'] or order.get('_buyer_started_at') != ctx['started'] or
            config(ctx['md']) != ctx['cfg']):
        return False
    try:
        with open(ctx['path'] + '.json', 'rb') as src:
            if _hash(src.read(16385)) != ctx['assignment']:
                raise ValueError('assignment changed')
        return True
    except (OSError, ValueError, KeyError):
        _unknown(ctx)
        return False


def _pool(pool, budget, selected):
    seen, urls, result = set(), set(), []
    for s in pool:
        name, url, offers = s.get('name'), s.get('url'), s.get('usable')
        u = urlsplit(url) if isinstance(url, str) else None
        if (not isinstance(name, str) or not name or name in seen or url in urls or
                u is None or u.scheme not in ('https','http') or not u.hostname or u.username or u.password or
                not isinstance(offers, list) or not offers or len(offers) > 512):
            raise ValueError('ambiguous pool')
        skus = [o.get('sku') for o in offers]
        if not all(ident(sku) for sku in skus) or len(set(skus)) != len(skus):
            raise ValueError('ambiguous SKU')
        o = selected(s, budget)
        if o not in offers or type(o.get('list_price')) is not int or o['list_price'] <= 0:
            raise ValueError('invalid selected offer')
        seen.add(name); urls.add(url)
        result.append([name, url, offers, o])
    return _hash(_bytes(result))


def _source(order):
    path = order.get('_stock_shops_path')
    if not path:
        return None
    with open(path, 'rb') as src:
        data = src.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError('listing too large')
    return _hash(data)


def plan(order, pool, k, fits, rates, rival, budget, selected, chance, cap, rows, others_first, conflict, baseline_now):
    """Rank the entire already-filtered pool; preserve the highest-review fresh slot."""
    ctx = order.get(KEY)
    if not isinstance(ctx, dict) or not _active(ctx, order) or ctx.get('planned'):
        return pool
    ctx['planned'] = True
    try:
        if k < 2 or conflict():
            return pool
        fingerprint = _pool(pool, budget, selected)
        source = _source(order)
        by_name = {s['name']:s for s in pool}
        offers = {n:selected(s,budget) for n,s in by_name.items()}
        chances = {sku:chance(sku) for sku in {o['sku'] for o in offers.values()}}
        counts, sums = {}, {}
        for row in rows():
            if row.get('kind') != 'goods' or applied_b(row):
                continue
            for q in row.get('quotes') or ():
                if not isinstance(q, dict) or not isinstance(q.get('shop'), str):
                    continue
                name = q['shop']; o = offers.get(name)
                if o is None or q.get('sku') != o['sku'] or type(q.get('list_price')) is not int or q['list_price'] != o['list_price']:
                    continue
                counts[name] = counts.get(name,0) + 1
                reward = 0
                ask, b = q.get('ask'), chances[o['sku']]
                if (number(ask) and number(b) and 0 <= b <= 1 and 0 < ask <= o['list_price'] and
                        not q.get('refused') and not str(q.get('lost') or '').startswith('refused')):
                    limit = cap(by_name[name], o, b, q)
                    if number(limit) and ask <= limit:
                        reward = (1-b)*(1-ask/o['list_price'])
                sums[name] = sums.get(name,0) + reward
        n = sum(counts.values())
        if not n:
            return pool
        mean = sum(sums.values()) / n
        def score(s):
            name = s['name']
            paid = rates[0].get(name,rates[1]) if rates and rates[1] is not None else 1
            if not number(paid) or not 0 <= paid <= 1:
                raise ValueError('invalid paid rate')
            return (sums.get(name,0)+2*mean)/(counts.get(name,0)+2)*paid
        fresh = sorted((s for s in pool if fits[s['name']] <= 0), key=lambda s:-len(s.get('reviews') or []))[:1]
        ranked = sorted((s for s in pool if s not in fresh), key=lambda s:-score(s))
        head = ranked[:max(0,k-2)] + fresh
        proposed = others_first(head + [s for s in pool if s not in head], rival)
        names = lambda ss:[s['name'] for s in ss[:max(0,k-1)]]
        if set(names(proposed)) == set(names(pool)):
            return pool  # reorder alone is never a treatment
        snapshot = dict(assignment=ctx.get('assignment', 'default'), pool=fingerprint, source=source,
                        k=k, baseline=names(pool), proposed=names(proposed), reason='changed_initial_set')
        ctx['snapshot'] = (_hash(_bytes(snapshot)) if ctx.get('mode') == 'default' else
                           _write(ctx['path'] + '.plan.json', snapshot))
        ctx.update(pool=list(pool), proposed=proposed, fingerprint=fingerprint, source=source,
                   k=k, budget=budget, selected=selected, conflict=conflict, baseline_now=baseline_now, sent=set())
        return proposed
    except Exception:  # unknown model/cap/conflict IO cannot authorize a new dispatch
        _unknown(ctx)
        return pool


def bind(order, early, quotable):
    ctx = order.get(KEY)
    if isinstance(ctx, dict) and 'snapshot' in ctx:
        ctx['early'], ctx['quotable'] = early, quotable


def dispatch_shops(order, shops, budget):
    """Run after stock_current's reset, and recheck at every thread-start boundary."""
    ctx = order.get(KEY)
    if (not isinstance(ctx, dict) or 'snapshot' not in ctx or
            order.get('_stock_phase', 'prefetch') != 'prefetch'):
        yield from shops
        return
    if ctx.get('dispatching'):
        return  # one initial-batch caller owns the immutable plan, including failed partial starts
    ctx['dispatching'] = True
    baseline = ctx['pool']
    if shops is not ctx.get('early'):
        _unknown(ctx)
        ctx['quotable'][:] = baseline
        ctx['early'][:] = baseline[:max(0,ctx['k']-1)]
        shops[:] = ctx['early']
        yield from shops
        return
    expected = [s['name'] for s in baseline[:max(0,ctx['k']-1)]]
    valid = [s['name'] for s in shops] == expected and budget == ctx['budget']
    try:
        fresh = ctx['baseline_now']()
        if fresh is None:
            valid = False
        elif [s['name'] for s in fresh[0]] != [s['name'] for s in baseline]:
            valid = False  # A's stock reset uses the original pool, never listing copies.
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _unknown(ctx); valid = False
    for i in range(len(expected)):
        try:
            valid = (valid and _active(ctx,order) and not ctx['conflict']() and
                _source(order) == ctx['source'] and
                _pool(baseline,budget,ctx['selected']) == ctx['fingerprint'] and
                order.get('_stock_k') == ctx['k'] and
                [s['name'] for s in order.get('_stock_baseline',())] == [s['name'] for s in baseline])
            if ctx.get('mode') != 'default':
                with open(ctx['path'] + '.plan.json','rb') as src:
                    valid = valid and _hash(src.read(16385)) == ctx['snapshot']
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            _unknown(ctx); valid = False
        chosen = ctx['proposed'] if valid else baseline
        ctx['quotable'][:] = chosen
        shops[:] = chosen[:len(expected)]
        # Partial dispatch never asks twice or increases the slot count on fallback.
        remaining = [s for s in shops[i:] if s['name'] not in ctx['sent']]
        if not remaining:
            break
        s = remaining[0]
        ctx['pending'] = (s, dict(ctx['selected'](s,budget)), valid)
        yield s
    shops[:] = ctx.get('asked', [])
    ctx['quotable'][:] = shops + [s for s in ctx['quotable'] if s['name'] not in ctx['sent']]


def dispatched(order, shop, offer):
    """Called only after Thread.start returns successfully; no RPC/ACK interpretation."""
    ctx = order.get(KEY)
    if not isinstance(ctx, dict) or 'pending' not in ctx:
        return
    s, frozen, valid = ctx.pop('pending')
    if s is not shop or frozen != offer or shop['name'] in ctx['sent'] or order.get('order') != ctx['oid']:
        _unknown(ctx)
        return
    ctx['sent'].add(shop['name'])
    ctx.setdefault('asked', []).append(shop)
    baseline = {s['name'] for s in ctx['pool'][:max(0,ctx['k']-1)]}
    if valid and shop['name'] not in baseline:
        ctx['note']['applied'] = True
