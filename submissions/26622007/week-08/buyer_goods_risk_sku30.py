"""Goods-only default risk-SKU selection, with the historical finite trial API."""
import math
import hashlib
import json
import os
import re
import tempfile
import time

SWITCH = 'buyer_goods_risk_sku30.json'
KEY = '_goods_risk_sku30'
PREFIX = '.goods-risk-sku30-'


def ident(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', value) is not None


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
        return {k: c[k] for k in ('study', 'salt', 'start', 'end')} | {'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def arm(cfg, oid):
    return 'AB'[hashlib.sha256((cfg['salt'] + ':' + oid).encode()).digest()[0] & 1]


def switch_on(md, *, study, salt, start, end):
    now = time.time()
    if not (ident(study) and ident(salt) and all(number(t) for t in (now, start, end)) and
            0 < now < start < end == start + 1800):
        raise ValueError('future exact 1800-second window required')
    value = dict(enabled=True, study=study, salt=salt, start=start, end=end)
    with open(os.path.join(md, SWITCH), 'x', encoding='utf-8') as dst:
        json.dump(value, dst, allow_nan=False); dst.flush(); os.fsync(dst.fileno())
    return value


def number(value):
    return type(value) in (int, float) and -10**12 < value < 10**12 and math.isfinite(value)


def candidate(offers, baseline, budget, chance):
    """Same acceptable/in-stock list and existing estimator, including its zero-sale prior."""
    b = chance(baseline['sku'])
    if not number(b) or not 0 <= b <= 1 or not number(budget) or budget <= 0:
        return None
    best, risk = None, b
    for offer in offers:
        lp = offer.get('list_price')
        if (offer.get('sku') == baseline['sku'] or type(lp) is not int or
                not 0 < lp <= budget or not offer.get('qty')):
            continue
        c = chance(offer['sku'])
        if number(c) and 0 <= c < risk:
            best, risk = offer, c
    return best


def _shape(shop):
    return [(o.get('sku'), o.get('list_price'), o.get('qty')) for o in shop.get('usable') or []]


def reserve_default(order, shops, budget, baseline, chance, *, conflict=None):
    """Freeze normal selection in shop-local memory; no trial assignment or order fields.

    Ambiguous inventory stays at the baseline. The estimator (including its prior)
    is supplied by the existing caller, just as for the historical finite trial.
    """
    if (not isinstance(order, dict) or order.get('kind') != 'goods' or
            any(order.get(k) for k in ('_goods_take30', '_stock_evidence', '_goods_extra_quote_probe',
                                      '_quote_alignment', '_our_ask', '_moss_first'))):
        return
    try:
        if conflict is not None and conflict():
            return
    except Exception:  # unknown competing policy fails closed
        return
    pending, names = [], set()
    try:
        for shop in shops:
            offers = shop.get('usable') or []
            name = shop.get('name')
            if not offers or not isinstance(name, str) or name in names or KEY in shop:
                return
            names.add(name)
            skus = [o.get('sku') for o in offers]
            b = baseline(offers, budget)
            c = (candidate(offers, b, budget, chance)
                 if all(ident(sku) for sku in skus) and len(set(skus)) == len(skus) else None)
            pending.append((shop, dict(mode='default', entry=dict(url=shop.get('url'),
                baseline=b['sku'], candidate=c['sku'] if c is not None else None, shape=_shape(shop)))))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return
    for shop, ctx in pending:
        shop[KEY] = ctx


def reserve(md, order, shops, budget, baseline, chance, now=None, conflict=None, stats=None):
    """Freeze the same acceptable inventory before selection. No model work OFF.

    A tombstone makes retries/colliding processes untreated, never reassigned.
    Files retain shop identifiers/URLs and model evidence; no quote text, salt or credentials.
    """
    now = time.time() if now is None else now
    if not isinstance(order, dict) or order.get('kind') != 'goods':
        return None
    cfg = config(md, now)
    started = order.get('_buyer_started_at')
    if (cfg is None or not ident(order.get('order')) or not number(started) or
            not cfg['start'] <= started <= now < cfg['end'] or
            any(order.get(k) for k in ('_goods_take30', '_stock_evidence', '_goods_extra_quote_probe',
                                      '_quote_alignment', '_our_ask', '_moss_first'))):
        return None
    try:
        if conflict is not None and conflict():
            return None
    except Exception:  # another trial whose state is unknown never permits a new treatment
        return None
    note = dict(study=cfg['study'], arm=arm(cfg, order['order']), applied=False, eligible_shops=0)
    order[KEY] = note
    path = os.path.join(md, PREFIX + hashlib.sha256((cfg['study'] + ':' + order['order']).encode()).hexdigest())
    try:
        os.mkdir(path + '.seen')  # atomic, nonblocking, survives a failed snapshot
        choices = {}
        model = stats() if stats is not None else None
        def evidence(sku):
            if model is None or sku is None:
                return None
            observed = model['by_sku'].get(sku) or {'sold': 0, 'broke': 0}
            return dict(sold=observed['sold'], broke=observed['broke'], prior_only=observed['sold'] == 0,
                        prior_sold=model['sold'], prior_rate=model['rate'], prior_weight=20,
                        estimator='existing_break_chance_default')
        for shop in shops:
            offers = shop.get('usable') or []
            if not offers or not isinstance(shop.get('name'), str) or shop['name'] in choices:
                raise ValueError('ambiguous shops')
            b = baseline(offers, budget)
            c = candidate(offers, b, budget, chance)
            choices[shop['name']] = dict(url=shop.get('url'), baseline=b['sku'],
                candidate=c['sku'] if c is not None else None, shape=_shape(shop),
                baseline_chance=chance(b['sku']), candidate_chance=chance(c['sku']) if c is not None else None,
                baseline_evidence=evidence(b['sku']), candidate_evidence=evidence(c['sku']) if c is not None else None)
            note['eligible_shops'] += c is not None
        snapshot = dict(config={k: v for k, v in cfg.items() if k != 'salt'}, order=order['order'],
                        started=started, choices=choices, arm=note['arm'])
        fd, tmp = tempfile.mkstemp(prefix=PREFIX + 'tmp-', dir=md)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as dst:
                json.dump(snapshot, dst, allow_nan=False); dst.flush(); os.fsync(dst.fileno())
            os.link(tmp, path + '.json')  # immutable per-order audit snapshot
        finally:
            os.unlink(tmp)
        for shop in shops:
            shop[KEY] = dict(md=md, config=cfg, started=started, arm=note['arm'],
                             entry=choices[shop['name']], note=note)
        return note
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        note.update(arm=None, fallback='snapshot_unavailable')
        return None


def offer(shop, budget, baseline):
    """Pure preview. A quote/final-intent lock retains its SKU; unbound plans recheck expiry."""
    ctx = shop.get(KEY)
    if not isinstance(ctx, dict):
        return baseline(shop['usable'], budget)
    chosen = ctx.get('locked')
    offers = shop['usable']
    if chosen is not None:
        # Retain the exact offer, including duplicate-SKU listings, never reinterpret a sent task.
        return ctx['locked_offer']
    original = baseline(offers, budget)
    entry = ctx['entry']
    if ctx.get('mode') != 'default':
        cfg = config(ctx['md'])
        if (ctx['arm'] != 'B' or cfg is None or cfg != ctx['config'] or
                not cfg['start'] <= ctx['started'] <= time.time() < cfg['end']):
            return original
    if (entry['url'] != shop.get('url') or _shape(shop) != entry['shape'] or
            original.get('sku') != entry['baseline'] or entry['candidate'] is None):
        return original
    found = [o for o in offers if o.get('sku') == entry['candidate']]
    if len(found) != 1 or not 0 < found[0]['list_price'] <= budget:
        return original
    return found[0]


def lock(shop, selected, *, dispatched):
    """One lock for quote and direct final talk; applied is dispatch intent, never a preview."""
    ctx = shop.get(KEY)
    if isinstance(ctx, dict):
        if ctx.get('locked') is not None and ctx['locked'] != selected['sku']:
            raise ValueError('locked SKU mismatch')
        ctx['locked'] = selected['sku']
        ctx['locked_offer'] = dict(selected)
        if (dispatched and ctx.get('mode') != 'default' and ctx['arm'] == 'B' and ctx['entry']['candidate'] is not None and
                selected['sku'] == ctx['entry']['candidate'] and selected['sku'] != ctx['entry']['baseline']):
            ctx['note']['applied'] = True
    return selected


def prepare_final(shop, budget, baseline):
    # Bind before computing this SKU's cap/label, but guards may still skip negotiation entirely.
    return lock(shop, offer(shop, budget, baseline), dispatched=False)


def dispatch(shop, budget, baseline):
    return lock(shop, offer(shop, budget, baseline), dispatched=True)


def measurement(order):
    note = order.get(KEY)
    return dict(note) if isinstance(note, dict) else None


def applied_b(row):
    note = row.get('goods_risk_sku30') if isinstance(row, dict) else None
    return isinstance(note, dict) and note.get('arm') == 'B' and note.get('applied') is True
