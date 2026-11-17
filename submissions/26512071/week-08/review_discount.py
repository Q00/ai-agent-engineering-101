"""Review-discount pilot (review-discount-v1): next-trade discount for a verifiable forum post about a real paid
deal at this shop, whatever the post says. Pure checks here; network only in refresh_evidence().

The coupon is a PUBLIC BEARER token: A2A gives the seller no authenticated buyer identity, so whoever sends
"REVIEW-COUPON <post id>" redeems it. Limits: one redemption per post, per original settlement, per author, per
GitHub owner.
Settings are initial agent-chosen assumptions (brief 2026-11-07), not tuned values; the config can only tighten them."""
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from evidence_memory import atomic_json, locked

PILOT = 'review-discount-v1'
CONFIG = 'review-discount-pilot.json'        # written by the parent at activation only: start/expiry epochs
EVIDENCE = 'review-discount-evidence.json'   # verified eligibility, written by refresh_evidence() outside seller turns
LEDGER = 'review-discount-ledger.json'       # reserved -> signed -> committed | reserved -> released | signed -> unknown
JOURNAL = 'review-discount-journal.jsonl'
LOCK = 'review-discount.lock'
EVIDENCE_VERSION = 1
MAX_PCT = 5            # discount <= 5% of the normal bounded opening quote
MAX_BUDGET = 10_000    # cumulative credits (reserved + committed) for the whole pilot
MAX_HOURS = 24         # explicit window; expiry is automatic, never renewed here
LIVE = ('reserved', 'signed', 'unknown', 'committed')   # states that hold the post/settlement/author/owner locks and budget
HELD = ('signed', 'unknown')   # signed receipt, not (yet) seen settled: never released; 'unknown' = settle unobservable


def _norm(url):
    return url.rstrip('/') if isinstance(url, str) and url.startswith('https://') and url.strip('/') else None


def _seqs(refs):
    for ref in refs if isinstance(refs, list) else []:
        seq = ref.get('seq') if isinstance(ref, dict) else ref
        if type(seq) is int:
            yield seq


def verify_post(post, rows, own_urls, owners, own_owner):
    """(eligibility, reason). Binds post id -> author (role buyer) -> registry owner -> a round-3 settle row as WE
    read it from the market ledger (never the post's embedded copy) at one of this agent's own registered URLs whose
    buyer is the author. The post text is never read: a negative post is as eligible as a positive one, and a found
    ref proves the deal exists, not that the opinion is true."""
    if not isinstance(post, dict) or type(post.get('id')) is not int:
        return None, 'malformed_post'
    author, owner = post.get('author'), post.get('owner')
    if post.get('role') != 'buyer':
        return None, 'author_not_buyer'
    if not isinstance(author, str) or not author or not isinstance(owner, str) or not owner:
        return None, 'malformed_post'
    if not isinstance(owners, dict) or owners.get(author) != owner:
        return None, 'owner_not_registry_owner'
    if owner == own_owner:
        return None, 'own_owner'
    own = {_norm(u) for u in own_urls or ()} - {None}
    if not own:
        return None, 'no_own_urls'
    reason = 'no_refs'
    for seq in _seqs(post.get('refs')):
        row = rows.get(seq) if isinstance(rows, dict) else None
        data = row.get('data') if isinstance(row, dict) else None
        if not isinstance(data, dict) or row.get('seq') != seq:
            reason = 'ref_unverified'
            continue
        if row.get('kind') != 'settle' or row.get('round') != 3:
            reason = 'ref_not_settle'
            continue
        if _norm(data.get('shop')) not in own:
            reason = 'ref_other_shop'
            continue
        if data.get('buyer') != author:
            reason = 'author_not_buyer_of_ref'
            continue
        ts, pts = row.get('ts'), post.get('ts')
        if type(ts) not in (int, float) or type(pts) not in (int, float) or pts <= ts:
            reason = 'post_not_after_settle'
            continue
        unit, sku, amount = data.get('unit'), data.get('sku') or data.get('item'), data.get('amount')
        if not isinstance(unit, str) or not unit or not isinstance(sku, str) or type(amount) not in (int, float):
            reason = 'ref_incomplete'
            continue
        return {'post': post['id'], 'author': author, 'owner': owner, 'settle_seq': seq, 'unit': unit, 'sku': sku,
                'amount': amount, 'settle_ts': ts, 'post_ts': pts, 'shop': _norm(data.get('shop')),
                'checkout_hash': data.get('checkout_hash')}, 'eligible'
    return None, reason


def load_config(state):
    """The pilot config, or None if missing/invalid. Hard ceilings in code: the file can only tighten them."""
    try:
        cfg = json.loads((Path(state)/CONFIG).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or cfg.get('pilot') != PILOT:
        return None
    start, end, pct, budget = (cfg.get(k) for k in ('activated_at', 'expires_at', 'max_pct', 'budget'))
    nums = all(type(v) in (int, float) and math.isfinite(v) for v in (start, end, pct, budget))
    if not nums or not 0 < end-start <= MAX_HOURS*3600 or not 0 < pct <= MAX_PCT or not 0 < budget <= MAX_BUDGET:
        return None
    if not isinstance(cfg.get('own_owner'), str) or not cfg['own_owner']:
        return None
    return cfg


def active(cfg, now):
    return cfg is not None and cfg['activated_at'] <= now < cfg['expires_at']


def _int(v, lo=None):
    return type(v) is int and (lo is None or v >= lo)


def _num(v):
    return type(v) in (int, float) and math.isfinite(v)


def _str(v):
    return isinstance(v, str) and bool(v)


def min_price(cost):
    """Lowest price on the discounted path: the smallest integer strictly above cost (cost + 1 for an integer cost)."""
    return math.floor(cost) + 1


def _valid_reservation(r):
    """Types and coherent amounts of one ledger row, exactly as reserve/mark_signed/reconcile write them."""
    if not isinstance(r, dict) or r.get('status') not in LIVE+('released',):
        return False
    if not all(_str(r.get(k)) for k in ('id', 'task', 'unit', 'sku', 'author', 'owner')) or not _int(r.get('post'), 1) \
            or not _int(r.get('settle_seq')) or not _num(r.get('cost')) or not _num(r.get('reserved_at')):
        return False
    q, d, dq = r.get('normal_quote'), r.get('discount'), r.get('discounted_quote')
    if not (_int(q, 1) and _int(d, 1) and _int(dq)) or dq != q-d or d > q*MAX_PCT//100 or dq < min_price(r['cost']):
        return False
    if r['status'] in HELD+('committed',) and not (_int(r.get('signed_price')) and r['signed_price'] >= min_price(r['cost'])
                                                    and _num(r.get('signed_at')) and _int(r.get('scan_after'))):
        return False
    return r['status'] != 'committed' or _int(r.get('redeem_seq'))


def load_ledger(state):
    """Reservations, or None when the file is corrupt: bad types/amounts, duplicate ids, two live rows sharing a task,
    post, original settlement, author or owner, a settle seq redeemed twice, or live discounts above MAX_BUDGET. The
    discounted path then fails closed (regular sales go on) and the file is left as it is for inspection."""
    path = Path(state)/LEDGER
    try:
        led = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'pilot': PILOT, 'reservations': []}
    except (OSError, ValueError):
        return None
    if not isinstance(led, dict) or led.get('pilot') != PILOT or not isinstance(led.get('reservations'), list):
        return None
    rs = led['reservations']
    if not all(_valid_reservation(r) for r in rs) or len({r['id'] for r in rs}) != len(rs):
        return None
    live = [r for r in rs if r['status'] in LIVE]
    if any(len({r[k] for r in live}) != len(live) for k in ('task', 'post', 'settle_seq', 'author', 'owner')):
        return None
    redeemed = [r['redeem_seq'] for r in rs if r['status'] == 'committed']
    if len(set(redeemed)) != len(redeemed) or sum(r['discount'] for r in live) > MAX_BUDGET:
        return None
    return led


def budget_view(led, budget):
    reserved = sum(r['discount'] for r in led['reservations'] if r['status'] in ('reserved',)+HELD)
    committed = sum(r['discount'] for r in led['reservations'] if r['status'] == 'committed')
    return {'reserved': reserved, 'committed': committed, 'available': budget-reserved-committed}


def load_evidence(state):
    try:
        ev = json.loads((Path(state)/EVIDENCE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(ev, dict) or ev.get('version') != EVIDENCE_VERSION or not isinstance(ev.get('eligible'), dict):
        return None
    return ev


def journal(state, row):
    """Append-only audit row; never raises (a broken journal must not stop trading)."""
    try:
        with open(Path(state)/JOURNAL, 'a', encoding='utf-8') as f:
            f.write(json.dumps(dict(row, pilot=PILOT, ts=time.time()), sort_keys=True, allow_nan=False) + '\n')
    except (OSError, TypeError, ValueError):
        pass


def discount_for(quote, cost, pct):
    """Credits off the normal quote: <= pct% of it, and the discounted price stays >= cost + 1 (positive margin)."""
    if type(quote) is not int or type(cost) not in (int, float) or not math.isfinite(cost):
        return 0
    return max(0, min(int(quote*pct)//100, quote-min_price(cost)))


# What the budget books. The only pilot-specific concession is the opening: the task starts at normal_quote - discount
# instead of normal_quote (and its floor is cost+1, not cost). Later concessions follow the ordinary schedule or the model,
# which may concede without a coupon too, so normal_quote - signed_price is NOT the pilot's cost.
ACCOUNTING = {'booked': 'discount', 'basis': 'opening_incentive_reservation_upper_bound', 'causal_cost': 'unknown',
              'note': 'discount = max opening-anchor reduction; the counterfactual price without the coupon is unobserved'}


def reserve(state, post_id, task, unit, normal_quote, now):
    """(reservation, reason). Local files only (no network): safe inside a seller turn. One lock covers budget and the
    post / original-settlement / author / task collision checks, so concurrent tasks cannot both win."""
    cfg = load_config(state)
    if cfg is None:
        return None, 'no_pilot'
    if not active(cfg, now):
        return None, 'inactive'
    ev = load_evidence(state)
    if ev is None:
        return None, 'evidence_unavailable'
    elig = ev['eligible'].get(str(post_id)) if type(post_id) is int else None
    if not isinstance(elig, dict) or elig.get('post') != post_id or not _int(elig.get('settle_seq')) \
            or not _str(elig.get('author')) or not _str(elig.get('owner')):
        return None, 'unverified'
    d = discount_for(normal_quote, unit.get('cost'), cfg['max_pct'])
    if d < 1:
        return None, 'no_margin'
    try:
        with locked(Path(state)/LOCK):
            led = load_ledger(state)
            if led is None:
                return None, 'ledger_corrupt'
            for r in led['reservations']:
                if r['status'] not in LIVE:
                    continue
                if r.get('task') == task:
                    return None, 'task_already_reserved'
                if r.get('post') == post_id or r.get('settle_seq') == elig['settle_seq'] or r.get('author') == elig['author'] \
                        or r.get('owner') == elig['owner']:   # one GitHub owner may run several buyer accounts
                    return None, 'already_used'
            if budget_view(led, cfg['budget'])['available'] < d:
                return None, 'budget'
            res = {'id': f'{PILOT}:{post_id}:{task}', 'post': post_id, 'settle_seq': elig['settle_seq'],
                   'author': elig['author'], 'owner': elig.get('owner'), 'evidence': elig, 'task': task,
                   'unit': unit.get('unit'), 'sku': unit.get('sku'), 'cost': unit.get('cost'),
                   'normal_quote': normal_quote, 'discount': d, 'discounted_quote': normal_quote-d,
                   'policy': {'max_pct': cfg['max_pct'], 'budget': cfg['budget'], 'floor': 'cost+1'},
                   'accounting': ACCOUNTING, 'status': 'reserved', 'reserved_at': now}
            led['reservations'].append(res)
            atomic_json(Path(state)/LEDGER, led)
    except (OSError, TimeoutError, KeyError, TypeError, ValueError):
        return None, 'ledger_unavailable'
    journal(state, {'event': 'reserved', **{k: res[k] for k in ('id', 'post', 'settle_seq', 'author', 'task', 'unit',
                                                                  'normal_quote', 'discount', 'discounted_quote', 'cost')}})
    return res, 'reserved'


def _update(state, fn):
    """Run fn(ledger) under the lock and persist if it returns a truthy result. Returns fn's result, or the failure."""
    try:
        with locked(Path(state)/LOCK):
            led = load_ledger(state)
            if led is None:
                return 'ledger_corrupt'
            out = fn(led)
            if out and out not in ('missing', 'signed_kept', 'unknown_kept', 'mismatch', 'expired'):
                atomic_json(Path(state)/LEDGER, led)
            return out
    except (OSError, TimeoutError, TypeError, ValueError):
        return 'ledger_unavailable'


def _cache_head(state):
    """Highest ledger seq the public-settlement cache had read (local file, no network), or None."""
    try:
        cache = json.loads((Path(state)/'public-settlements.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict):
        return None
    if _int(cache.get('cursor_seq')):
        return cache['cursor_seq']
    seqs = [r['seq'] for r in cache.get('rows') or [] if isinstance(r, dict) and _int(r.get('seq'))] \
        if isinstance(cache.get('rows'), list) else []
    return max(seqs, default=None)


def mark_signed(state, task, price, unit, sku, now, cost=None):
    """reserved -> signed BEFORE the discounted receipt is signed. Anything but 'signed' means: do not sign at the
    discounted price. Refused once the window has closed ('expired'), and below min_price of the reserved cost or of
    the fresh unit's `cost` re-read at sign time, whichever is higher. A receipt is not payment: this never marks the
    coupon redeemed. scan_after = the cache head now (or the reviewed deal's own settle seq): the new settle can only
    come later, so scan_ledger() pages from there."""
    head = _cache_head(state)
    if not active(load_config(state), now):
        out = 'expired'
        journal(state, {'event': 'mark_signed', 'task': task, 'unit': unit, 'price': price, 'outcome': out})
        return out

    def fn(led):
        r = next((r for r in led['reservations'] if r.get('task') == task and r['status'] == 'reserved'), None)
        if r is None:
            return 'missing'
        if cost is not None and not _num(cost):
            return 'mismatch'
        floor_cost = r['cost'] if cost is None else max(r['cost'], cost)
        if (r['unit'], r['sku']) != (unit, sku) or type(price) is not int or price < min_price(floor_cost):
            return 'mismatch'
        r.update(status='signed', signed_price=price, signed_at=now,
                 scan_after=max(r['settle_seq'], head if head is not None else r['settle_seq']))
        return 'signed'
    out = _update(state, fn)
    journal(state, {'event': 'mark_signed', 'task': task, 'unit': unit, 'price': price, 'outcome': out})
    return out


def release(state, task, why, now):
    """Free an UNSIGNED reservation (cancel, reject, stale, unit gone). A signed one is kept whatever the reason: the
    buyer can still complete checkout later, so releasing it could let one post be redeemed twice."""
    def fn(led):
        r = next((r for r in led['reservations'] if r.get('task') == task and r['status'] in ('reserved',)+HELD), None)
        if r is None:
            return 'missing'
        if r['status'] in HELD:
            return r['status'] + '_kept'
        r.update(status='released', released_at=now, released_why=why)
        return 'released'
    out = _update(state, fn)
    journal(state, {'event': 'release', 'task': task, 'why': why, 'outcome': out})
    return out


def _settles(rows, own):
    """Complete round-3 settle rows at an own (journal-registered) URL: int seq, finite ts, finite positive amount,
    unit, sku and a checkout_hash. Anything else is never a redemption."""
    for row in rows if isinstance(rows, list) else []:
        data = row.get('data') if isinstance(row, dict) else None
        if isinstance(data, dict) and row.get('kind') == 'settle' and row.get('round') == 3 and _int(row.get('seq')) \
                and _num(row.get('ts')) and _norm(data.get('shop')) in own and _num(data.get('amount')) \
                and data['amount'] > 0 and _str(data.get('unit')) and _str(data.get('sku')) \
                and _str(data.get('checkout_hash')):
            yield row, data


TASK_JOIN = 'absent: the public settle row has no task id; joined on own URL + unit + sku/item + amount + ts >= signed_at'


def reconcile(state, rows, own_urls, now):
    """Commit each signed reservation whose EXACT public settle row shows: round 3, own shop URL, unit, sku (and item
    if present), amount == signed price, settle ts >= signed_at (the public row has no task id; a unit is sold once,
    so the unit binds it). Buyer callbacks are never trusted. Two candidate rows = ambiguous: stays signed. A settle
    seq redeems at most one reservation. Returns the newly committed."""
    own = {_norm(u) for u in own_urls or ()} - {None}
    done = []

    def fn(led):
        used = {r.get('redeem_seq') for r in led['reservations'] if r['status'] == 'committed'}
        for r in led['reservations']:
            if r['status'] not in HELD:      # 'unknown' still commits if its exact row does show up
                continue
            hits = [(row, d) for row, d in _settles(rows, own) if row['seq'] not in used and d['unit'] == r['unit']
                    and d['sku'] == r['sku'] and d.get('item', r['sku']) == r['sku']
                    and d['amount'] == r['signed_price'] and row['ts'] >= r['signed_at']]
            if len(hits) != 1:
                if hits:
                    journal(state, {'event': 'reconcile_ambiguous', 'task': r['task'], 'seqs': [h[0]['seq'] for h in hits]})
                continue
            row, d = hits[0]
            used.add(row['seq'])
            r.update(status='committed', committed_at=now, redeem_seq=row['seq'], redeem_ts=row.get('ts'),
                     redeem_checkout_hash=d['checkout_hash'], task_join=TASK_JOIN, redeemed_by=d.get('buyer'),
                     redeemed_by_author=d.get('buyer') == r['author'], settled_amount=d.get('amount'),
                     opening_incentive_upper_bound=r['discount'], causal_cost='unknown',
                     ordinary_concession_below_discounted_quote=max(0, r['discounted_quote']-r['signed_price']))
            done.append(dict(r))
        return 'committed' if done else 'missing'
    out = _update(state, fn)
    if out in ('ledger_corrupt', 'ledger_unavailable'):
        return []
    for r in done:
        journal(state, {'event': 'committed', **{k: r.get(k) for k in (
            'id', 'post', 'settle_seq', 'author', 'task', 'unit', 'normal_quote', 'discount', 'signed_price', 'cost',
            'redeem_seq', 'redeem_checkout_hash', 'redeemed_by', 'redeemed_by_author', 'opening_incentive_upper_bound',
            'causal_cost', 'ordinary_concession_below_discounted_quote')}})
    return done


MAX_SCAN_PAGES = 5     # ledger pages per tick for held reservations; the cursor is persisted, the next tick resumes
UNKNOWN_WHY = 'ledger seq gap after signing: the settlement may be unobservable; held (budget + locks), not redeemed or released'


def scan_ledger(state, now, own_urls, fetch=None):
    """Held (signed/unknown) reservations: read ledger pages forward from their cursor (scan_after), so a settle that
    scrolled out of the newest-500 cache before it was seen is still found, commit exact matches, then advance the
    cursor. A missing seq range above a cursor turns the reservation 'unknown': still held, never released or assumed
    redeemed. A failed or malformed page stops the scan and moves no cursor past what was actually read."""
    led = load_ledger(state)
    held = [r for r in led['reservations'] if r['status'] in HELD] if led else []
    if not held:
        return {'pages': 0, 'committed': 0}
    fetch = fetch or _get
    top = start = min(r['scan_after'] for r in held)
    rows, gaps, pages, error = [], [], 0, None
    while pages < MAX_SCAN_PAGES:
        try:
            page = fetch(f'/api/ledger?after={top}')[0]
        except Exception as e:   # record the type only; nothing past `top` counts as read
            error = type(e).__name__
            break
        pages += 1
        if not isinstance(page, list):
            error = 'malformed_page'
            break
        new = sorted((r for r in page if isinstance(r, dict) and _int(r.get('seq')) and r['seq'] > top),
                     key=lambda r: r['seq'])
        if not new:
            break
        for r in new:
            if r['seq'] <= top:          # duplicate seq within the page
                continue
            if r['seq'] > top+1:
                gaps.append([top+1, r['seq']-1])
            top = r['seq']
            rows.append(r)
    committed = reconcile(state, rows, own_urls, now)

    def fn(led):
        moved = False
        for r in led['reservations']:
            if r['status'] not in HELD or r['scan_after'] >= top:
                continue
            hit = [g for g in gaps if g[1] > r['scan_after']]
            if hit:
                r.update(status='unknown', unknown_why=UNKNOWN_WHY, unknown_at=now,
                         unknown_gaps=r.get('unknown_gaps', []) + hit)
            r['scan_after'] = top
            moved = True
        return 'scanned' if moved else 'missing'
    _update(state, fn)
    out = {'pages': pages, 'from': start, 'to': top, 'gaps': gaps, 'error': error, 'committed': len(committed)}
    if gaps or error:
        journal(state, dict(out, event='scan_ledger'))
    return out


COUPON = re.compile(r'review[- ]?coupon\s*[:#]?\s*#?(\d{1,12})(?![\w])', re.I)


def parse_coupon(text):
    """The post id of exactly one 'REVIEW-COUPON <id>' token, else None. Public bearer: it names a post, never a person."""
    if not isinstance(text, str):
        return None
    hits = COUPON.findall(text)
    if len(hits) != 1 or len(re.findall(r'review[- ]?coupon', text, re.I)) != 1:
        return None
    n = int(hits[0])
    return n if n > 0 else None


# --- evidence refresh: fixed-origin bounded reads, run OUTSIDE seller turns (seller background thread)
BASE = 'https://market.ourolabs.ai'
TIMEOUT = 5            # socket timeout per GET
GET_DEADLINE_S = 5     # whole-body deadline: a slow drip is rejected, never parsed partially
MAX_BYTES = 2_000_000
MIN_INTERVAL = 60      # at most one refresh (and its few GETs) per minute
MAX_REF_FETCH = 5      # ledger GETs per refresh; the rest wait for the next refresh
SHOP_NAME = 'fair-furniture'
ALLOWED = re.compile(r'/api/board|/api/ledger\?after=\d{1,12}')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Every 3xx is an error: no redirect is followed (off-origin, same-origin or with credentials alike)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f'redirect refused ({code})', headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _get(path):
    """(json, sha256, bytes) of one allowlisted path on the fixed market origin. Raises on anything else, including
    any redirect and a final response URL other than the one requested. Nothing is returned if GET_DEADLINE_S since
    start has passed after the last read (EOF included). Bounds, honestly: opening (DNS by the OS only; connect, TLS,
    headers each by TIMEOUT per socket operation) is not inside the deadline; each body read gets the remaining
    budget as its socket timeout when the raw socket is reachable and open, so a failing GET normally ends within
    ~open time + GET_DEADLINE_S, not an exact 5 s wall. It runs in the seller's background thread, never in a turn."""
    if not isinstance(path, str) or not ALLOWED.fullmatch(path):
        raise ValueError('path not allowlisted')
    url = BASE + path
    req = urllib.request.Request(url, headers={'User-Agent': 'FairFurniture-review-discount/1',
                                               'Cache-Control': 'no-cache'})
    started, chunks, size = time.monotonic(), [], 0
    with _OPENER.open(req, timeout=TIMEOUT) as r:
        if r.geturl() != url:
            raise ValueError('final URL differs from the requested fixed-origin URL')
        while True:
            left = GET_DEADLINE_S-(time.monotonic()-started)
            if left <= 0:
                raise TimeoutError('read deadline')
            # Re-read every time: HTTPResponse closes its socket once a Content-Length body is consumed; setting a
            # timeout on that closed socket is WinError 10038 (public_evidence fix 6f1ba67). Skip only a closed one.
            sock = getattr(getattr(getattr(r, 'fp', None), 'raw', None), '_sock', None)
            if hasattr(sock, 'settimeout') and not getattr(sock, '_closed', False):
                sock.settimeout(min(TIMEOUT, left))
            chunk = r.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('byte limit')
            chunks.append(chunk)
    if time.monotonic()-started > GET_DEADLINE_S:   # the last read (or EOF) may itself have returned late
        raise TimeoutError('read deadline')
    raw = b''.join(chunks)
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest(), len(raw)


def _owners(board):
    """Board buyers: account name -> GitHub owner login (the market's registry of who owns which buyer)."""
    out = {}
    for b in board.get('buyers', []) if isinstance(board, dict) else []:
        owner = b.get('owner') if isinstance(b, dict) else None
        login = owner.get('login') if isinstance(owner, dict) else None
        if isinstance(login, str) and isinstance(b.get('name'), str):
            out[b['name']] = login
    return out


def _own_shop(board):
    """(current card URL, owner login) of this shop as the board lists it, (None, None) if absent."""
    for s in board.get('shops', []) if isinstance(board, dict) else []:
        if isinstance(s, dict) and s.get('name') == SHOP_NAME and isinstance(s.get('card'), dict):
            owner = s.get('owner')
            return _norm(s['card'].get('url')), owner.get('login') if isinstance(owner, dict) else None
    return None, None


def _candidates(forum, own):
    """Pre-filter only (nothing here is trusted): buyer posts whose market-resolved refs name an own shop URL."""
    for p in forum if isinstance(forum, list) else []:
        if not isinstance(p, dict) or p.get('role') != 'buyer' or type(p.get('id')) is not int:
            continue
        for ref in p.get('refs') if isinstance(p.get('refs'), list) else []:
            data = ref.get('data') if isinstance(ref, dict) else None
            if isinstance(data, dict) and _norm(data.get('shop')) in own and type(ref.get('seq')) is int:
                yield p
                break


RETAINED_NOTE = ('eligible entries are retained from the refresh that verified them (verified_at): their ledger row is '
                 'immutable, but board ownership, own URL and owner are not re-verified on later refreshes; a found ref '
                 'proves the deal exists, not the opinion or its truth')


def refresh_evidence(state, now, own_urls=None, fetch=None):
    """Re-verify forum candidates against the market's own ledger/board and write EVIDENCE atomically with provenance.
    Reads the kit's forum.json (newest 100 posts). No pilot config -> no network. A failed GET adds nothing; entries
    already verified against an (immutable, hash-chained) ledger row are kept. The current registry URL of this shop
    must be among own_urls (the supervisor's registered-URL journal), else nothing new is added."""
    state = Path(state)
    cfg = load_config(state)
    if cfg is None:
        return None
    fetch = fetch or _get
    if own_urls is None:
        from public_evidence import own_urls as registered
        own_urls = registered()
    own = {_norm(u) for u in own_urls} - {None}
    try:
        with locked(state/'review-discount-evidence.lock'):
            prev = load_evidence(state)
            if prev is None and (state/EVIDENCE).exists():   # corrupt: keep the bytes for inspection, add nothing
                journal(state, {'event': 'evidence_corrupt_kept'})
                return None
            prev = prev or {}
            last = prev.get('refreshed_at')
            if type(last) in (int, float) and 0 <= now-last < MIN_INTERVAL:
                return prev
            eligible = dict(prev.get('eligible') or {})
            try:
                raw = (state/'forum.json').read_bytes()
                forum, forum_sha = json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()
            except (OSError, ValueError):
                forum, forum_sha = [], None
            posts = forum if isinstance(forum, list) else []
            cands = [p for p in _candidates(posts, own) if str(p['id']) not in eligible]
            sources, rejected, rows, owners, confirmed, owner_ok = [], {}, {}, None, None, None

            def read(path):
                try:
                    body, sha, n = fetch(path)
                except Exception as e:   # any failure: record its type only, add nothing
                    sources.append({'path': path, 'url': BASE+path, 'fetched_at': now, 'ok': False,
                                    'error': type(e).__name__})
                    raise
                sources.append({'path': path, 'url': BASE+path, 'sha256': sha, 'bytes': n, 'fetched_at': now,
                                'ok': True})
                return body
            try:
                if cands:
                    board = read('/api/board')
                    url, login = _own_shop(board)
                    owners, confirmed, owner_ok = _owners(board), url in own, login == cfg['own_owner']
                    budget = MAX_REF_FETCH
                    for p in cands:
                        for seq in _seqs(p.get('refs')):
                            if seq in rows or budget <= 0:
                                continue
                            budget -= 1
                            page = read(f'/api/ledger?after={seq-1}')
                            row = next((r for r in page if isinstance(r, dict) and r.get('seq') == seq), None) \
                                if isinstance(page, list) else None
                            if row is not None:
                                rows[seq] = row
            except Exception:
                pass
            for p in cands:
                if owners is None or not confirmed or not owner_ok:
                    rejected[str(p['id'])] = ('board_unavailable' if owners is None else
                                              'own_url_unconfirmed' if not confirmed else 'own_owner_unconfirmed')
                    continue
                got, reason = verify_post(p, rows, own, owners, cfg['own_owner'])
                if got:
                    eligible[str(p['id'])] = dict(got, verified_at=now)
                else:
                    rejected[str(p['id'])] = reason
            ev = {'version': EVIDENCE_VERSION, 'pilot': PILOT, 'refreshed_at': now, 'own_urls': sorted(own),
                  'own_url_confirmed': confirmed, 'own_owner_confirmed': owner_ok, 'forum_sha256': forum_sha,
                  'sources': sources, 'retained_note': RETAINED_NOTE,
                  'eligible': eligible, 'rejected': rejected,
                  'counts': {'posts_scanned': len(posts), 'candidates': len(cands), 'eligible': len(eligible),
                             'rejected': len(rejected)},
                  'note': 'newest 100 forum posts per refresh; a found ref proves the deal exists, not the opinion'}
            atomic_json(state/EVIDENCE, ev)
            return ev
    except (OSError, TimeoutError, TypeError, ValueError):
        return None


def tick(state, now, own_urls=None, fetch=None):
    """One background step (seller daemon thread, never inside a seller turn): while the pilot is active, refresh the
    evidence (bounded GETs); always reconcile signed reservations against the public-settlement cache rows the restock
    hook already keeps (no extra read), so a late payment after expiry still commits exactly once."""
    state = Path(state)
    cfg = load_config(state)
    if cfg is None:
        return {'configured': False}
    if own_urls is None:
        from public_evidence import own_urls as registered
        own_urls = registered()
    out = {'configured': True, 'active': active(cfg, now), 'refreshed': False, 'committed': 0}
    if out['active']:
        out['refreshed'] = refresh_evidence(state, now, own_urls=own_urls, fetch=fetch) is not None
    try:
        cache = json.loads((state/'public-settlements.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cache = None
    if isinstance(cache, dict) and isinstance(cache.get('rows'), list):
        # Own URLs from the supervisor's registered journal only, never the cache's copy of them.
        out['committed'] = len(reconcile(state, cache['rows'], own_urls, now))
    # Then page the ledger forward for whatever is still held (no network when nothing is signed and unsettled).
    out['scan'] = scan_ledger(state, now, own_urls, fetch=fetch)
    out['committed'] += out['scan']['committed']
    return out


def card_text(state, now):
    """Neutral public description for the seller card, only while the pilot is active ('' otherwise)."""
    cfg = load_config(state)
    if not active(cfg, now):
        return ''
    return ('검증 가능한 실제 거래후기 게시글에 한해 다음 거래 할인; 평가 방향 무관(긍정·부정 모두, 별점·추천 요구 없음). '
            f'조건: 이 상점에서 결제(settle)된 거래의 ledger seq를 refs로 단 구매자 포럼 글. 상한: 정상 호가의 최대 {cfg["max_pct"]:g}%, '
            f'원가 이하 불가, 글·원거래·작성자·GitHub 소유자당 1회, 파일럿 총 {int(cfg["budget"]):,} 크레딧, 만료 epoch {int(cfg["expires_at"])} 자동 종료. '
            '첫 메시지에 "REVIEW-COUPON <글 id>". 공개 bearer 쿠폰: A2A에는 구매자 인증이 없어 글 id를 아는 누구나 쓸 수 있음. '
            '확인 실패 시 정상가.')
