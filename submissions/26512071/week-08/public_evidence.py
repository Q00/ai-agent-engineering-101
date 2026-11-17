"""Public ledger sample: the newest 500 entries only, NOT complete market demand.
Network only in refresh() (one fixed GET); ingest()/from_cache() are pure. No model or forum calls."""
import argparse
import hashlib
import http.client
import json
import statistics
import time
import urllib.request
from pathlib import Path
from evidence_memory import atomic_json, load, locked, positive

SOURCE_URL = 'https://market.ourolabs.ai/api/ledger?after=-500'
CACHE = 'public-settlements.json'
# Fixed scoped read: this agent's own supervisor history, never an arbitrary log.
RUNTIME_EVENTS = Path(__file__).resolve().parent/'logs'/'runtime-events.jsonl'
MIN_INTERVAL = 60
TIMEOUT = 5  # socket timeout per GET (v2 used 3 s)
GET_DEADLINE_S = 5  # whole-body read deadline per GET: a slow drip is rejected, never parsed partially
READ_CHUNK = 65536
STALE_AFTER = 180
MAX_BYTES = 2_000_000
BACKOFF_MAX_S = 900  # consecutive failures wait 60, 120, 240, ... <= 900 s (Retry-After honoured within that)
FETCH_LOG_MAX = 20  # bounded attempt journal kept in the cache: timing and error type, never response bodies
PAGE = 500  # live page size (cursor probe 2026-11-05: after=N -> seq N+1..N+500, ascending)
PAGE2_START_BY_S = 5  # one gap page per tick, started only if the tail GET took <= 5 s: tick stays ~<= 15 s
GAP_MAX = 50  # explicit missing seq ranges kept; older ones dropped and counted, never reported complete


def _norm(url):
    return url.rstrip('/') if isinstance(url, str) and url.strip('/') else None


def own_urls(path=RUNTIME_EVENTS):
    """Shop URLs this agent actually registered with (supervisor 'registered' events)."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return []
    urls = []
    for line in raw[:raw.rfind(b'\n')+1].splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        url = row.get('url') if isinstance(row, dict) and row.get('event') == 'registered' else None
        if isinstance(url, str) and url.startswith('https://') and url not in urls:
            urls.append(url)
    return urls


def _own_totals(out):
    out['own_sku_counts'], out['own_sku_ids'] = {}, {}
    for seq in sorted(out['own_ledger'], key=int):
        sku = out['own_ledger'][seq]['sku']
        out['own_sku_counts'][sku] = out['own_sku_counts'].get(sku, 0)+1
        out['own_sku_ids'].setdefault(sku, []).append(int(seq))
    out['own_ledger_ids'] = sorted(int(seq) for seq in out['own_ledger'])
    return out


def ingest(rows, catalog, own, source_url=SOURCE_URL):
    """Valid R3 settles (row.kind='settle'; nested data: shop, item, amount), deduped by seq; own shop separated from public."""
    own = {_norm(u) for u in own} - {None}
    seen = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and type(row.get('seq')) is int and row['seq'] not in seen:
            seen[row['seq']] = row
    out = {'source_url':source_url, 'complete':False, 'note':'newest <=500 ledger entries; bounded sample, not complete',
           'sample':{'seq_min':min(seen), 'seq_max':max(seen)} if seen else None, 'settle_count':0, 'unmapped':0,
           'category_counts':{}, 'category_ids':{}, 'own_ledger':{}}
    for seq in sorted(seen):
        row, data = seen[seq], seen[seq].get('data')
        if row.get('kind') != 'settle' or row.get('round') != 3 or not isinstance(data, dict):
            continue
        shop, sku = _norm(data.get('shop')), data.get('item')
        if not shop or not isinstance(sku, str) or not sku or not positive(data.get('amount')):
            continue
        out['settle_count'] += 1
        if shop in own:
            out['own_ledger'][str(seq)] = {'sku':sku, 'price':data['amount']}
            continue
        item = catalog.get(sku)
        cat = item.get('category') if isinstance(item, dict) else None
        if not cat:
            out['unmapped'] += 1
            continue
        out['category_counts'][cat] = out['category_counts'].get(cat, 0)+1
        out['category_ids'].setdefault(cat, []).append(seq)
    return _own_totals(out)


def merge_own(evidence, persisted):
    """Own sales outlive the 500-entry window: union with the ledger IDs persisted by earlier refreshes."""
    if isinstance(persisted, dict):
        merged = {k:v for k, v in persisted.items() if k.isdigit() and isinstance(v, dict) and isinstance(v.get('sku'), str)}
        merged.update(evidence['own_ledger'])
        evidence['own_ledger'] = merged
    return _own_totals(evidence)


def load_cache(state):
    try:
        cache = load(Path(state)/CACHE, None)
    except (OSError, ValueError):
        return None
    return cache if isinstance(cache, dict) else None


def from_cache(cache, catalog, now):
    """Network-free view for plan(); a missing cache is reported as missing, never filled in."""
    if not cache:
        out = ingest([], catalog, [])
        out.update(fetched_at=None, age_s=None, stale=True, missing=True)
        return out
    out = merge_own(ingest(cache.get('rows', []), catalog, cache.get('own_urls', []), cache.get('source_url', SOURCE_URL)),
                    cache.get('own_ledger'))
    fetched = cache.get('fetched_at')
    age = now-fetched if type(fetched) in (int, float) else None
    out.update(fetched_at=fetched, age_s=age, missing=False,
               stale=cache.get('stale') is True or age is None or age > STALE_AFTER)
    return out


PRICE_VERSION = 'price-anchor-v3'  # persisted entries of other versions are never reused (step limit still applies)
FRESH_S = {'goods':1800, 'food':900}  # max age of both the cache fetch and each sampled settle
PRICE_ANCHORS = 'seller-price-policy.json'
ANCHOR_TTL = 900  # reuse a persisted anchor per comparability key for at most this long
PRICE_RETAIN_S = 7200  # price_rows kept across newest-500 tails by actual settle ts
PRICE_MAX_ROWS = 5000
PRICE_SCOPE = ('newest <=500 ledger entries per refresh; round-3 settles retained <=2 h by settle ts, '
               '<=5000 rows; bounded sample, not complete market demand')


def _life_bucket(expires_at, at):
    """Remaining food life at `at`: <3 h, 3..24 h, >=24 h. Missing/invalid/already expired -> None (never comparable)."""
    if not positive(expires_at) or not positive(at) or expires_at <= at:
        return None
    left = expires_at-at
    return 'lt3h' if left < 3*3600 else '3-24h' if left < 24*3600 else 'ge24h'


def _comparable(data, ts, unit, bucket):
    """Exact SKU+grade, observed kind (expires_at null = goods) and warranty (null -> 0); food also needs the same
    remaining-life bucket at settle ts as the unit has now. Missing or invalid metadata is excluded."""
    if not all(k in data for k in ('sku', 'item', 'grade', 'expires_at', 'warranty_hours')):
        return False
    warranty = 0 if data['warranty_hours'] is None else data['warranty_hours']
    if isinstance(warranty, bool) or not isinstance(warranty, (int, float)) or not 0 <= warranty < float('inf'):
        return False
    if data['expires_at'] is None:
        kind = 'goods'
    elif _life_bucket(data['expires_at'], ts) is None:
        return False
    else:
        kind = 'food'
    return (data['sku'] == unit['sku'] and data['item'] == unit['sku'] and data['grade'] == unit['grade']
            and kind == unit['kind'] and warranty == (unit.get('warranty_hours') or 0)
            and (kind == 'goods' or _life_bucket(data['expires_at'], ts) == bucket))


def _check_unit(unit):
    """Bad input is a caller bug, not a market signal: no anchor is invented without a finite cost..list range."""
    if not isinstance(unit, dict) or unit.get('kind') not in FRESH_S or not isinstance(unit.get('sku'), str) or not unit['sku'] or 'grade' not in unit:
        raise ValueError('price_anchor: unit needs sku, grade and kind goods|food')
    if not positive(unit.get('list_price')) or not positive(unit.get('cost')) or unit['cost'] > unit['list_price']:
        raise ValueError('price_anchor: cost and list_price must be finite, positive, cost <= list_price')
    if unit['kind'] == 'food' and not positive(unit.get('expires_at')):
        raise ValueError('price_anchor: food unit needs a finite positive expires_at')


def _unit_bucket(unit, now):
    return _life_bucket(unit['expires_at'], now) if unit['kind'] == 'food' else None


def _anchor_key(unit, now):
    """Goods keys are unchanged from v1; food keys add the unit's current life bucket so a bucket change never reuses."""
    parts = [unit['sku'], unit['grade'], unit['kind'], unit.get('warranty_hours') or 0]
    if unit['kind'] == 'food':
        parts.append(_unit_bucket(unit, now) or 'expired')
    return '|'.join(str(v) for v in parts)


def _valid_entry(entry):
    return (isinstance(entry, dict) and positive(entry.get('anchor')) and positive(entry.get('computed_at'))
            and type(entry.get('cache_version')) is int and entry['cache_version'] >= 1
            and isinstance(entry.get('ledger_ids'), list) and isinstance(entry.get('reason'), str))


TIER_RANK = {'legacy':0, 'baseline':1, 'proxy':2, 'exact':3}


def _reuse_blocker(prev, out, now, kind):
    """Why a persisted entry younger than ANCHOR_TTL must not be reused (None = reuse). Same-tier (or downgraded)
    evidence keeps the 900 s smoothing; a newer algorithm, a higher tier, a proxy whose guards or catalog no longer
    hold, or a source past its hard expiry forces a (step-limited) recalculation."""
    if prev.get('version') != PRICE_VERSION:
        return f'entry from {prev.get("version")}, now {PRICE_VERSION}'
    was = prev.get('tier') if prev.get('tier') in TIER_RANK else 'legacy'
    if TIER_RANK[out['tier']] > TIER_RANK[was]:
        return f'evidence tier upgraded {was} -> {out["tier"]}'
    if was == 'proxy' and out['tier'] != 'proxy':
        return 'previous proxy no longer passes its guards'
    if was == 'proxy' and (prev.get('proxy') or {}).get('catalog_sha256') != out['proxy']['catalog_sha256']:
        return 'catalog changed since previous proxy'
    src = (prev.get('provenance') or {}).get('fetched_at')
    if not positive(src) or now-src > FRESH_S[kind]:
        return f'its source is past the {FRESH_S[kind]} s hard expiry'
    return None


def price_anchor(state, unit, now=None):
    """Persisted per comparability key: reused <900 s while its inputs stay fresh and _reuse_blocker allows it, later
    moves <=10% of list per recalculation; cost..list clamps always reapplied. A reused entry reports its own
    (historical) source age and status. An unreadable/locked policy file never raises: label + no step.
    A missing/corrupt/malformed/stale settlement cache never activates a persisted market anchor: labelled baseline only."""
    _check_unit(unit)
    now = time.time() if now is None else now
    out = _compute_anchor(state, unit, now)
    if out['cache_problem']:
        out.update(recalculated=True, cache_version=None,
                   anchor_cache=f'fallback: {out["cache_problem"]}; baseline, not persisted, no persisted market anchor used')
        return out
    lst, cost = unit['list_price'], unit['cost']
    key, path = _anchor_key(unit, now), Path(state)/PRICE_ANCHORS
    try:
        with locked(Path(state)/'price-anchors.lock'):
            try:
                policy = load(path, {'anchors':{}})
            except (OSError, ValueError):
                policy = None
            if not isinstance(policy, dict) or not isinstance(policy.get('anchors'), dict):
                out.update(recalculated=True, cache_version=None, anchor_cache='fallback: unreadable '+PRICE_ANCHORS+'; not persisted, no step limit')
                return out
            prev = policy['anchors'].get(key)
            prev = prev if _valid_entry(prev) else None
            oldest = prev.get('sample_ts_min') if prev else None
            young = (prev and 0 <= now-prev['computed_at'] < ANCHOR_TTL and not out['stale']
                     and (oldest is None or (positive(oldest) and now-oldest <= FRESH_S[unit['kind']])))
            blocker = _reuse_blocker(prev, out, now, unit['kind']) if young else None
            if young and not blocker:
                src = prev['provenance']['fetched_at']
                # the LATEST fetch outcome wins over the reused entry's: a current GET failure must stay visible
                latest = out['fetch_status']
                failed = out['reason'].split('); ', 1)[0]+'); ' if latest == 'last-known-good' else ''
                out.update({k:prev[k] for k in ('reason', 'ledger_ids', 'sample_count', 'comparable_count', 'computed_at', 'cache_version')},
                           anchor=max(cost, min(lst, prev['anchor'])), recalculated=False, anchor_cache='reused',
                           source_cache_version=prev.get('source_cache_version'), provenance=prev['provenance'],
                           tier=prev['tier'], proxy=prev.get('proxy'), sample_ts_min=oldest,
                           fetch_status=latest, source_age_s=round(now-src, 3), source_historical=True)
                out['reason'] = failed+out['reason']+(f'; reused anchor {prev["anchor"]} computed {now-prev["computed_at"]:g} s ago (<{ANCHOR_TTL} s) '
                                  f'from a source fetched {now-src:g} s ago, re-clamped to cost..list {cost}..{lst}')
                return out
            if blocker:
                out['reason'] += f'; previous anchor not reused: {blocker}'
            if prev:
                step, target = max(1, lst//10), out['anchor']
                bounded = min(max(target, prev['anchor']-step), prev['anchor']+step)
                out['anchor'] = max(cost, min(lst, bounded))
                out['reason'] += (f'; step-limited from previous {prev["anchor"]} toward {target} by <= {step} (10% of list)' if bounded != target
                                  else f'; within step {step} (10% of list) of previous {prev["anchor"]}')
            out.update(recalculated=True, cache_version=(prev['cache_version']+1 if prev else 1), anchor_cache='recalculated')
            policy['anchors'][key] = {k:out[k] for k in ('anchor', 'reason', 'ledger_ids', 'sample_count', 'comparable_count',
                                                         'computed_at', 'version', 'sample_ts_min', 'cache_version',
                                                         'source_cache_version')}
            policy['anchors'][key].update(provenance=out.get('provenance'), tier=out['tier'], proxy=out['proxy'],
                                          fetch_status=out['fetch_status'])
            atomic_json(path, policy)
            return out
    except (TimeoutError, OSError, ValueError) as exc:
        out.update(recalculated=True, cache_version=None, anchor_cache=f'fallback: {type(exc).__name__} on {PRICE_ANCHORS}; not persisted')
        return out


def _valid_gaps(gaps):
    return [g for g in gaps if isinstance(g, dict) and type(g.get('lo')) is int and type(g.get('hi')) is int
            and g['lo'] <= g['hi'] and positive(g.get('detected_at'))] if isinstance(gaps, list) else []


def _coverage(rows, fetched_at, cache=None):
    """Window actually held. Rows without observed_at (pre-v2 caches) use the cache fetched_at, never the settle ts.
    Known missing seq ranges (v3 gaps) are listed; coverage is never claimed complete."""
    seqs = [r['seq'] for r in rows]
    seen = [r['observed_at'] if positive(r.get('observed_at')) else fetched_at for r in rows]
    seen = [t for t in seen if positive(t)]
    gaps = _valid_gaps((cache or {}).get('gaps'))
    return {'rows':len(seqs), 'seq_min':min(seqs, default=None), 'seq_max':max(seqs, default=None),
            'observed_from':min(seen, default=None), 'observed_to':max(seen, default=None),
            'retention_s':PRICE_RETAIN_S, 'max_rows':PRICE_MAX_ROWS, 'complete':False,
            'gaps':[[g['lo'], g['hi']] for g in gaps], 'gap_seqs':sum(g['hi']-g['lo']+1 for g in gaps),
            'gaps_dropped':(cache or {}).get('gaps_dropped', 0), 'gaps_expired':(cache or {}).get('gaps_expired', 0)}


def _last_known_good(cache):
    """v3 refresh marks a failed GET with a fetch_error record: its last verified rows stay usable until FRESH_S.
    A legacy stale mark without that record (or any other writer) stays a hard stale -> baseline."""
    err = cache.get('fetch_error')
    return cache.get('stale', False) is True and isinstance(err, dict) and isinstance(err.get('type'), str)


def _price_cache(state, now, kind):
    """(cache, None) for a well-formed cache fetched <= FRESH_S[kind] ago and either not marked stale or marked stale
    by a v3 failed GET (last verified data, labelled by the caller); else (None, label)."""
    try:
        cache = load(Path(state)/CACHE, None)
    except (OSError, ValueError):
        return None, f'corrupt {CACHE}'
    if cache is None:
        return None, f'missing {CACHE}'
    if (not isinstance(cache, dict) or not isinstance(cache.get('rows', []), list)
            or not isinstance(cache.get('price_rows', []), list) or not isinstance(cache.get('own_urls', []), list)
            or not isinstance(cache.get('own_ledger', {}), dict) or type(cache.get('stale', False)) is not bool
            or type(cache.get('cache_version', 0)) is not int or cache.get('cache_version', 0) < 0):
        return None, f'malformed {CACHE}'
    fetched = cache.get('fetched_at')
    if not positive(fetched) or fetched > now:
        return None, f'malformed fetched_at in {CACHE}'
    if (cache.get('stale', False) and not _last_known_good(cache)) or now-fetched > FRESH_S[kind]:
        err = cache['fetch_error'] if _last_known_good(cache) else None
        return None, (f'stale {CACHE} (fetched {now-fetched:g} s ago, max {FRESH_S[kind]} s for {kind}, marked stale {cache.get("stale", False)})'
                      + (f'; last GET failed: {err["type"]}' if err else ''))
    return cache, None


PROXY_MIN = {'settlements':5, 'SKUs':3, 'shops':2}
PROXY_BAND = (0.5, 2.0)  # catalog list within 0.5..2x the unit's: categories are broad/mislabeled (spray in sofas)
PROXY_RATIO = (0.2, 1.0)  # amount / catalog list outside this is rejected before any count, never clamped in
PROXY_MAX_SPREAD = 0.25  # MAD / (median of per-shop median ratios)


def _proxy(state, unit, now, pool, bucket, cache):
    """(proxy dict, None) or (None, why). Fresh non-own deduped settles of OTHER catalog SKUs in the unit's category,
    same kind/grade/warranty/food-life bucket, catalog list in PROXY_BAND x the unit's list, ratio inside PROXY_RATIO;
    each shop's median ratio counts once (one busy or washing shop cannot set the price). Disallowed while a known
    ledger gap was detected inside the freshness window. A ratio proxy, never an exact comparable."""
    cat = unit.get('category')
    if not isinstance(cat, str) or not cat:
        return None, 'unit has no category'
    fresh_gaps = [g for g in _valid_gaps(cache.get('gaps')) if now-g['detected_at'] <= FRESH_S[unit['kind']]]
    if fresh_gaps:
        return None, (f'known ledger gap detected within {FRESH_S[unit["kind"]]} s '
                      f'({sum(g["hi"]-g["lo"]+1 for g in fresh_gaps)} seq missing in {len(fresh_gaps)} gap(s))')
    try:
        raw = (Path(state)/'catalog.json').read_bytes()
        catalog = json.loads(raw)
    except (OSError, ValueError):
        return None, 'catalog.json unavailable'
    if not isinstance(catalog, dict):
        return None, 'catalog.json malformed'
    lst, picked, rejected = unit['list_price'], [], 0
    for seq, amount, ts, data in pool:
        sku = data.get('sku')
        item = catalog.get(sku) if isinstance(sku, str) else None
        if (sku == unit['sku'] or not isinstance(item, dict) or item.get('category') != cat or item.get('kind') != unit['kind']
                or not positive(item.get('list_price')) or not PROXY_BAND[0]*lst <= item['list_price'] <= PROXY_BAND[1]*lst
                or not _comparable(data, ts, dict(unit, sku=sku), bucket)):
            continue
        if not PROXY_RATIO[0] <= amount/item['list_price'] <= PROXY_RATIO[1]:
            rejected += 1
            continue
        picked.append((seq, amount/item['list_price'], ts, sku, item['list_price'], _norm(data.get('shop'))))
    note = f'; ratio outside {PROXY_RATIO[0]}..{PROXY_RATIO[1]}: {rejected} rejected' if rejected else ''
    have = {'settlements':len(picked), 'SKUs':len({p[3] for p in picked}), 'shops':len({p[5] for p in picked})}
    short = [f'{k} {have[k]} < {need}' for k, need in PROXY_MIN.items() if have[k] < need]
    if short:
        return None, ', '.join(short)+note
    by_shop = {}
    for p in picked:
        by_shop.setdefault(p[5], []).append(p[1])
    shop_medians = {shop:statistics.median(r) for shop, r in sorted(by_shop.items())}
    med = statistics.median(shop_medians.values())
    spread = statistics.median(abs(p[1]-med) for p in picked)/med
    if spread > PROXY_MAX_SPREAD:
        return None, f'ratio spread {spread:.3f} (MAD/median) > {PROXY_MAX_SPREAD}'+note
    return {'count':have['settlements'], 'sku_count':have['SKUs'], 'shop_count':have['shops'],
            'ledger_ids':[p[0] for p in picked], 'source_skus':{p[3]:p[4] for p in picked},
            'median_ratio':med, 'shop_medians':shop_medians, 'max_shop_share':round(max(map(len, by_shop.values()))/len(picked), 4),
            'spread':round(spread, 4), 'ratio_rejected':rejected,
            'uncertainty':f'MAD/median {spread:.3f} over {have["settlements"]} settlements, {have["shops"]} shops; ratio proxy',
            'band':list(PROXY_BAND), 'category':cat, 'catalog':'catalog.json', 'catalog_sha256':hashlib.sha256(raw).hexdigest(),
            'sample_ts_min':min(p[2] for p in picked), 'price':int(med*lst),
            'differences':'different SKUs only, never this unit\'s SKU (same catalog category, kind, grade, warranty, food-life bucket; catalog list '
                          f'within {PROXY_BAND[0]}..{PROXY_BAND[1]}x): price = median of per-shop median ratios x this list, '
                          'not an exact comparable'}, None


def _compute_anchor(state, unit, now):
    """Bounded-sample price anchor from the cached ledger only (price_rows + newest tail); never fetches, never a
    precise market price. One settle per seq, then per data.order and per data.unit (lowest seq wins)."""
    lst, cost = unit['list_price'], unit['cost']
    base = lst*9//10
    limitations = ['newest <=500 ledger entries per refresh, settles kept <=2 h; bounded sample, not complete market demand',
                   'food compared by remaining-life bucket at settle ts (<3 h, 3..24 h, >=24 h)',
                   'own shop settlements excluded', 'replayed order/unit under a new seq counted once']
    cache, problem = _price_cache(state, now, unit['kind'])
    if problem:
        return {'anchor':max(cost, min(lst, base)), 'ledger_ids':[], 'sample_count':0, 'comparable_count':0,
                'reason':f'baseline {base} (90% of list); no market evidence used: {problem}; clamped to cost..list {cost}..{lst}',
                'computed_at':now, 'version':PRICE_VERSION, 'stale':True, 'sample_ts_min':None, 'limitations':limitations,
                'cache_problem':problem, 'source_cache_version':None, 'provenance':None,
                'fetch_status':'unavailable', 'source_age_s':None, 'source_historical':False, 'tier':'baseline', 'proxy':None}
    own = {_norm(u) for u in cache.get('own_urls', [])+own_urls()} - {None}
    own_ids = {int(k) for k in cache.get('own_ledger', {}) if str(k).isdigit()}
    bucket = _unit_bucket(unit, now)
    rows = {}
    for row in cache.get('price_rows', [])+cache.get('rows', []):
        if isinstance(row, dict) and type(row.get('seq')) is int and row['seq'] not in rows:
            rows[row['seq']] = row
    orders, units, comparable, samples, pool = set(), set(), [], [], []
    for seq in sorted(rows):
        row, data = rows[seq], rows[seq].get('data')
        if row.get('kind') != 'settle' or row.get('round') != 3 or not isinstance(data, dict):
            continue
        order, sold = data.get('order'), data.get('unit')
        if (isinstance(order, str) and order in orders) or (isinstance(sold, str) and sold in units):
            continue  # replayed settlement under a new seq
        orders.update([order] if isinstance(order, str) and order else [])
        units.update([sold] if isinstance(sold, str) and sold else [])
        amount, ts = data.get('amount'), row.get('ts')
        if row['seq'] in own_ids or _norm(data.get('shop')) in own or not _norm(data.get('shop')):
            continue
        if not positive(amount) or not positive(ts) or ts > now:
            continue
        if now-ts <= FRESH_S[unit['kind']]:
            pool.append((row['seq'], amount, ts, data))  # proxy candidates: fresh, deduped, not own
        if not _comparable(data, ts, unit, bucket):
            continue
        if not lst*0.2 <= amount <= lst:  # outside policy bounds, not an invalid trade
            continue
        comparable.append(row['seq'])
        if now-ts <= FRESH_S[unit['kind']]:
            samples.append((row['seq'], amount, ts))
    age = round(now-cache['fetched_at'], 3)
    status, how_src = 'fresh', ''
    if _last_known_good(cache):
        err = cache['fetch_error']
        status = 'last-known-good'
        how_src = (f'last verified data fetched {age:g} s ago (latest GET failed: {err["type"]}'
                   + (f' {now-err["at"]:g} s ago' if positive(err.get('at')) and err['at'] <= now else '')
                   + f'; hard expiry {FRESH_S[unit["kind"]]} s); ')
    prices = sorted(a for _, a, _ in samples)
    n = len(prices)
    med = (prices[(n-1)//2]+prices[n//2])/2 if n else None
    if n >= 5:
        raw, how = int(med), f'median {med:g} of {n} fresh exact comparable settlements'
    elif n >= 3:
        raw, how = int((med+base)//2), f'blend 50:50 of median {med:g} over {n} fresh exact comparable settlements with baseline {base}'
    else:
        raw, how = base, f'baseline {base} (90% of list); only {n} fresh exact comparable settlements, fewer than 3'
    tier, proxy, used = ('exact' if n >= 3 else 'baseline'), None, samples
    if n < 3:
        proxy, why = _proxy(state, unit, now, pool, bucket, cache)
        if proxy:
            tier, used = 'proxy', [(s, None, proxy['sample_ts_min']) for s in proxy['ledger_ids']]
            raw = (proxy['price']+base)//2
            how = (f'proxy (not exact): median ratio {proxy["median_ratio"]:.4g} of catalog list over {proxy["count"]} fresh '
                   f'settlements of {proxy["sku_count"]} catalog SKUs from {proxy["shop_count"]} shops in category '
                   f'{proxy["category"]}, list band {PROXY_BAND[0]}..{PROXY_BAND[1]}x -> {proxy["price"]}, blend 50:50 '
                   f'with baseline {base}; exact comparable settlements only {n}'
                + (f'; {proxy["ratio_rejected"]} settlement(s) with ratio outside {PROXY_RATIO[0]}..{PROXY_RATIO[1]} rejected'
                   if proxy['ratio_rejected'] else ''))
        else:
            how += f'; proxy not used: {why}'
    coverage = _coverage(list(rows.values()), cache['fetched_at'], cache)
    if coverage['gaps']:
        how += f'; coverage incomplete: {coverage["gap_seqs"]} ledger seq missing in {len(coverage["gaps"])} gap(s)'
    anchor = max(cost, min(lst, raw))
    return {'anchor':anchor, 'reason':how_src+how+f'; clamped to cost..list {cost}..{lst}', 'ledger_ids':[s for s, _, _ in used],
            'fetch_status':status, 'source_age_s':age, 'source_historical':False, 'tier':tier, 'proxy':proxy,
            'sample_count':n, 'comparable_count':len(comparable), 'computed_at':now, 'version':PRICE_VERSION,
            'stale':False, 'sample_ts_min':min((t for _, _, t in used), default=None), 'limitations':limitations,
            'cache_problem':None, 'source_cache_version':cache.get('cache_version', 0),
            'provenance':{'cache_version':cache.get('cache_version', 0), 'fetched_at':cache['fetched_at'],
                          'source_url':cache.get('source_url'), 'scope':PRICE_SCOPE,
                          'coverage':coverage}}


def _fetch(url=SOURCE_URL):
    """One public GET, <= MAX_BYTES. Nothing is returned if GET_DEADLINE_S since start has passed after the last read
    (EOF included). Bounds, honestly: urlopen (connect, TLS, headers) is bounded per socket operation by TIMEOUT, DNS
    only by the OS; each body read gets the remaining budget as its socket timeout when the raw socket is reachable
    (else TIMEOUT), so a failing GET normally ends within ~GET_DEADLINE_S + one socket wait, not an exact 5 s wall."""
    start = time.monotonic()
    req = urllib.request.Request(url, headers={'Accept':'application/json'})
    chunks, size = [], 0
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        while True:
            left = GET_DEADLINE_S-(time.monotonic()-start)
            if left <= 0:
                raise TimeoutError(f'ledger body not complete within {GET_DEADLINE_S} s')
            # Re-read every time: HTTPResponse closes its socket (and drops fp) once a Content-Length body is consumed;
            # the EOF read then does no socket I/O. Only an already-closed socket is skipped; other errors propagate.
            sock = getattr(getattr(getattr(resp, 'fp', None), 'raw', None), '_sock', None)
            if hasattr(sock, 'settimeout') and not getattr(sock, '_closed', False):
                sock.settimeout(min(TIMEOUT, left))
            chunk = resp.read(min(READ_CHUNK, MAX_BYTES+1-size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('ledger response exceeds MAX_BYTES')  # reject, never parse a truncated body
    if time.monotonic()-start > GET_DEADLINE_S:  # the last read (or EOF) may itself have returned late
        raise TimeoutError(f'ledger body completed after {GET_DEADLINE_S} s deadline')
    body = json.loads(b''.join(chunks))
    if isinstance(body, list):
        return body
    for key in ('entries', 'ledger', 'rows', 'items'):
        if isinstance(body, dict) and isinstance(body.get(key), list):
            return body[key]
    raise ValueError('unrecognized ledger response shape')


def _price_rows(prev, rows, now):
    """Round-3 settles (seq, ts, kind, round, data) carried across newest-500 tails: kept while now-ts <= 2 h by the
    actual settle ts, newest 5000 by seq. observed_at = first refresh that saw the row (pre-v2: that cache's fetched_at)."""
    old_seen = prev.get('fetched_at') if positive(prev.get('fetched_at')) else None
    old = [r for key in ('price_rows', 'rows') if isinstance(prev.get(key), list) for r in prev[key]]
    kept = {}
    for row, seen in [(r, old_seen) for r in old]+[(r, now) for r in rows]:
        if not (isinstance(row, dict) and type(row.get('seq')) is int and row['seq'] not in kept
                and row.get('kind') == 'settle' and row.get('round') == 3 and isinstance(row.get('data'), dict)
                and positive(row.get('ts')) and now-row['ts'] <= PRICE_RETAIN_S):
            continue
        kept[row['seq']] = {'seq':row['seq'], 'ts':row['ts'], 'kind':'settle', 'round':3, 'data':row['data'],
                            'observed_at':row['observed_at'] if positive(row.get('observed_at')) else seen}
    return [kept[seq] for seq in sorted(kept)[-PRICE_MAX_ROWS:]]


def _retry_after(exc):
    try:
        value = float(exc.headers.get('Retry-After'))
    except (AttributeError, TypeError, ValueError):
        return None
    return value if 0 < value < float('inf') else None


def _journal(cache, entry):
    log = cache.get('fetch_log') if isinstance(cache.get('fetch_log'), list) else []
    return (log+[entry])[-FETCH_LOG_MAX:]


def _cursor_url(after):
    return f'{SOURCE_URL.rsplit("=", 1)[0]}={after}'


def _seqs(rows):
    return [r['seq'] for r in rows if isinstance(r, dict) and type(r.get('seq')) is int]


def _track_gaps(prev, rows, now):
    """Earlier gaps plus the range between the previous cursor and this tail's first seq; gaps detected > 2 h ago
    only hold settles past retention (expired); at most GAP_MAX newest kept (dropped counted)."""
    gaps = _valid_gaps(prev.get('gaps'))
    counts = {k:prev[k] if type(prev.get(k)) is int else 0 for k in ('gaps_dropped', 'gaps_expired')}
    cursor = prev['cursor_seq'] if type(prev.get('cursor_seq')) is int else max(_seqs(prev.get('rows') or []), default=None)
    tail = _seqs(rows)
    if cursor is not None and tail and min(tail) > cursor+1:
        gaps.append({'lo':cursor+1, 'hi':min(tail)-1, 'detected_at':now})
    live = [g for g in gaps if now-g['detected_at'] <= PRICE_RETAIN_S]
    counts['gaps_expired'] += len(gaps)-len(live)
    live.sort(key=lambda g: g['hi'])
    counts['gaps_dropped'] += max(0, len(live)-GAP_MAX)
    return live[-GAP_MAX:], counts, max(tail+([cursor] if cursor is not None else []), default=None)


def _fill_gap(gaps, counts, started, now):
    """At most one cursor page into the newest gap, from its top (fresher settles first). Returns (rows, status)."""
    if not gaps:
        return [], 'none'
    if time.monotonic()-started > PAGE2_START_BY_S:
        return [], 'skipped: tick deadline'
    gap = gaps[-1]
    after = max(gap['lo']-1, gap['hi']-PAGE)
    try:
        page = _fetch(_cursor_url(after))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return [], f'error: {type(exc).__name__}'
    have = {s for s in _seqs(page) if after < s <= gap['hi']}
    if not have:
        return page, f'empty page after={after}'
    gaps.remove(gap)
    rest, lo = [], None  # a page is not assumed contiguous: every seq it lacks stays a precise missing range
    for s in range(after+1, gap['hi']+2):  # <= PAGE+1 steps: after >= hi-PAGE
        if s in have or s > gap['hi']:
            if lo is not None:
                rest.append({'lo':lo, 'hi':s-1, 'detected_at':gap['detected_at']})
            lo = None
        elif lo is None:
            lo = s
    missing = sum(g['hi']-g['lo']+1 for g in rest)
    old = [r['ts'] for r in page if isinstance(r, dict) and positive(r.get('ts'))]
    if gap['lo'] <= after:
        if old and max(old) < now-PRICE_RETAIN_S:
            counts['gaps_expired'] += 1  # everything below this page settled even earlier: past retention
        else:
            rest.insert(0, {'lo':gap['lo'], 'hi':after, 'detected_at':gap['detected_at']})
    live = sorted(gaps+rest, key=lambda g: g['hi'])
    counts['gaps_dropped'] += max(0, len(live)-GAP_MAX)
    gaps[:] = live[-GAP_MAX:]
    return page, f'filled {after+1}..{max(have)}'+(f' ({missing} missing in page, kept as gaps)' if missing else '')


def refresh(state, now=None):
    """At most one GET per 60 s, longer after consecutive failures (exponential, <= BACKOFF_MAX_S, Retry-After
    honoured). On failure the old cache is kept, marked stale with a fetch_error record (type, detail, timing, last
    success): price_anchor may use those last verified rows until FRESH_S. Each success bumps cache_version."""
    state = Path(state)
    now = time.time() if now is None else now
    with locked(state/'public-evidence.lock'):
        cache = load_cache(state)
        if cache:
            last = max(cache.get('fetched_at') or 0, cache.get('attempted_at') or 0)
            due = cache.get('next_attempt_at')
            if 0 <= now-last < MIN_INTERVAL or (positive(due) and now < due <= now+BACKOFF_MAX_S):
                return cache
        urls = sorted(set(own_urls()) | set(cache.get('own_urls', []) if cache else []))
        started = time.monotonic()
        try:
            rows = _fetch()
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not cache:
                return None
            failures = (cache['failures'] if type(cache.get('failures')) is int and cache['failures'] >= 0 else 0)+1
            wait = min(BACKOFF_MAX_S, MIN_INTERVAL*2**min(failures-1, 10))
            wait = max(wait, min(BACKOFF_MAX_S, _retry_after(exc) or 0))
            err = {'type':type(exc).__name__, 'detail':str(exc)[:200], 'status':getattr(exc, 'code', None), 'at':now,
                   'elapsed_s':round(time.monotonic()-started, 3), 'last_success_at':cache.get('fetched_at')}
            cache.update(stale=True, attempted_at=now, last_error=err['type'], fetch_error=err, failures=failures,
                         next_attempt_at=now+wait, fetch_log=_journal(cache, dict(
                             at=now, ok=False, elapsed_s=err['elapsed_s'], error_type=err['type'], detail=err['detail'],
                             status=err['status'])))
            atomic_json(state/CACHE, cache)
            return cache
        prev = cache or {}
        gaps, counts, cursor = _track_gaps(prev, rows, now)
        filled, gap_fill = _fill_gap(gaps, counts, started, now)
        own = merge_own(ingest(rows+filled, {}, urls), prev.get('own_ledger'))['own_ledger']
        version = prev.get('cache_version') if type(prev.get('cache_version')) is int and prev['cache_version'] >= 0 else 0
        price_rows = _price_rows(prev, rows+filled, now)
        cache = {'source_url':SOURCE_URL, 'fetched_at':now, 'attempted_at':now, 'stale':False,
                 'own_urls':urls, 'rows':rows, 'own_ledger':own, 'cache_version':version+1,
                 'price_rows':price_rows, 'cursor_seq':cursor, 'gaps':gaps, **counts,
                 'fetch_error':None, 'failures':0, 'next_attempt_at':None,
                 'fetch_log':_journal(prev, dict(at=now, ok=True, elapsed_s=round(time.monotonic()-started, 3),
                                                 rows=len(rows), filled_rows=len(filled), gap_fill=gap_fill,
                                                 pages=1+gap_fill.startswith(('filled', 'empty', 'error'))))}
        cache['price_coverage'] = _coverage(price_rows, now, cache)
        atomic_json(state/CACHE, cache)
        return cache


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--state', required=True)
    got = refresh(ap.parse_args().state)
    print(json.dumps({'cached':bool(got), 'stale':(got or {}).get('stale'), 'fetched_at':(got or {}).get('fetched_at'),
                      'rows':len((got or {}).get('rows', [])), 'own_ledger_ids':sorted(int(k) for k in (got or {}).get('own_ledger', {}))}))
