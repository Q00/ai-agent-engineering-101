"""Per-shop accept band on top of plan A (owner 11/08, telegram 7924 "로그 기반으로 가게별 특수 전략을 짜보자";
review/W-20261108-1655-AX가게별바이어/shop-profiles.md).

Goods deals were lost mostly at two shops that never come down off their ask (25620027, mjforge: median lowest ask /
first ask 1.00) and whose failed talks ended 7-11% over my limit. Replayed on 11/07 17h - 11/08 17h with profiles from
the 24 h before, a percent per such shop took 2,063 deals against plan A's 1,946 (mean saved .354 against .358).

A shop gets its own percent when, over the last 24 h of measure/orders.jsonl, it had MIN_TALKS talks or more, its median
lowest ask / first ask is HOLDS or more, at least NEAR_SHARE of its failed talks ended within MAX_PERCENT of my limit, and
the kit paid PAID_SHARE or more of its deals (when PAID_MIN are known): the QUANTILE of those near misses, cut to
MAX_PERCENT, and per order to what keeps SAVED_FLOOR of list in expectation. Never under plan A's percent; every other
shop, a new one, or too few talks keeps plan A. Computed again from the log every REFRESH seconds (one run at a time).

Switch: .market/buyer_shop_profile.json {"enabled": true}; missing, unreadable or anything else is off, and off is plan
A exactly. BUYER_SHOP_PROFILE=0 in the environment is off too. Read at every order, so no restart either way.
"""
import fcntl
import json
import os
import time

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

SWITCH = 'buyer_shop_profile.json'
CACHE = 'buyer_shop_profile_cache.json'


def enabled(md, env=None):
    env = os.environ if env is None else env
    if str(env.get('BUYER_SHOP_PROFILE') or '').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    try:
        with open(os.path.join(md, SWITCH), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get('enabled') is True




BASE_PERCENT = 105
MAX_PERCENT = 110
HOURS = 24
MIN_TALKS = 30
HOLDS = 0.99          # median lowest ask / first ask at or over it: the shop does not come down
NEAR_SHARE = 0.3      # share of its failed talks that ended within MAX_PERCENT of my limit
QUANTILE = 0.8
PAID_MIN = 10
PAID_SHARE = 0.8


def _num(x):
    return type(x) in (int, float) and x > 0


def profile_from_rows(rows, paid, now):
    talks = {}
    for r in rows:
        ts, asks, limit, shop = r.get('ts'), r.get('asks'), r.get('limit'), r.get('shop')
        if not (_num(ts) and now - HOURS * 3600 <= ts <= now and isinstance(shop, str) and _num(limit)
                and isinstance(asks, list) and asks and all(_num(a) for a in asks)):
            continue
        if r.get('state') in (None, 'NO_VALUE'):
            continue
        if buyer_goods_grade.lowered(r):   # arm B's lowered limit says nothing of plan A's (review d85a9e9 I1)
            continue
        if buyer_margin_cap.lowered(r):    # the margin cap's lower limit had no accept band: nothing of the band's
            continue
        if buyer_goods_ladder.laddered(r):   # arm B's 0.8 openings say nothing of plan A's (review 459089c I2)
            continue
        if buyer_food_ladder.laddered(r):    # food-ladder arm B's 0.8 (C 0.7) openings, the same
            continue
        if buyer_saved_floor.floored(r):     # saved-floor arm B's floor / no band, the same (GPT Pro 1638 P1)
            continue
        if buyer_warranty_said.said_b(r):    # warranty-said arm B's 6 h pricing, the same (review 08b282d I2)
            continue
        if buyer_moss_first.applied_b(r):    # moss-first arm B's code-priced first turn, the same (review 5445a75 I1)
            continue
        if buyer_goods_take30.applied_b(r) or buyer_goods_risk_sku30.applied_b(r) or buyer_goods_quote_yield30.applied_b(r) or buyer_goods_quote_inquiry30.applied_b(r):  # actual treatment only
            continue
        talks.setdefault(shop, []).append(r)
    out = {}
    for shop, rs in talks.items():
        if len(rs) < MIN_TALKS:
            continue
        holds = sorted(min(r['asks']) / r['asks'][0] for r in rs)
        if holds[len(holds) // 2] < HOLDS:
            continue
        done = [r.get('state') == 'TASK_STATE_COMPLETED' and r.get('price') is not None for r in rs]
        deal = [r for r, d in zip(rs, done) if d]
        # misses on price only (a talk that ended under my limit failed on something else), and the deals a band took
        # over my limit, so a shop raised once does not drop out when its near misses turn into deals
        over = [min(r['asks']) / r['limit'] for r, d in zip(rs, done) if not d and min(r['asks']) > r['limit']]
        over += [r['price'] / r['limit'] for r in deal if _num(r['price']) and r['price'] > r['limit']]
        pays = [paid[r['order']] for r in deal
                if r.get('passed_to_kit') is True and isinstance(r.get('order'), str) and r['order'] in paid]
        if len(pays) >= PAID_MIN and sum(pays) / len(pays) < PAID_SHARE:
            continue
        near = sorted(x for x in over if x <= MAX_PERCENT / 100)
        if not over or len(near) < NEAR_SHARE * len(over):
            continue
        pct = int(near[max(0, int(len(near) * QUANTILE) - 1)] * 100 + 1e-9)
        pct = min(MAX_PERCENT, pct)
        if pct > BASE_PERCENT:
            out[shop] = pct
    return out


SAVED_FLOOR = 0.25    # a deal keeps at least this share of list in expectation: price <= list (1 - 0.25 / (1 - b))
REFRESH = 1800        # seconds a computed profile is used before it is computed again from the log
TAIL_BYTES = 30_000_000   # past 24 h of measure/orders.jsonl (about 22 MB a day on 11/08)


def _tail(path, nbytes=TAIL_BYTES):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            lines = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return
    for x in lines[1:] if size > nbytes else lines:
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def compute(md, now):
    paid = {p['order']: p['paid'] for p in _tail(os.path.join(md, 'payments.jsonl'), 4_000_000)
            if isinstance(p.get('order'), str) and isinstance(p.get('paid'), bool)}
    return profile_from_rows(_tail(os.path.join(md, 'measure', 'orders.jsonl')), paid, now)


def _read_cache(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None, {}
    if not isinstance(d, dict) or not _num(d.get('at')) or not isinstance(d.get('shops'), dict):
        return None, {}
    return d['at'], {k: v for k, v in d['shops'].items() if type(v) is int and BASE_PERCENT < v <= MAX_PERCENT}


def profiles(md, now=None):
    """{shop: percent}: the cached profile, computed again from the log when older than REFRESH. Only one buyer run
    computes it at a time; the others go on with the cached one (or none)."""
    now = time.time() if now is None else now
    path = os.path.join(md, CACHE)
    fresh = lambda at: at is not None and 0 <= now - at < REFRESH
    at, shops = _read_cache(path)
    if fresh(at):
        return shops
    try:
        with open(path + '.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return shops
            at, shops = _read_cache(path)      # another run may have just computed it
            if fresh(at):
                return shops
            try:
                shops = compute(md, now)
            except Exception:   # noqa: BLE001 - a broken log is no profile until the next REFRESH, not a parse every order
                shops = {}
            tmp = f'{path}.{os.getpid()}.tmp'
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump({'at': now, 'shops': shops}, f, sort_keys=True)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except OSError:
        pass
    return shops


def percent(md, shop, base, list_price, chance, limit, env=None, now=None, cap=None):
    """The share (%) of my limit I take a counter up to with this shop: base (plan A's, or None) when the switch is off,
    the shop has no profile, or the profile would not raise it; else the shop's percent, cut so that a deal keeps
    SAVED_FLOOR of list in expectation and the deal stays within cap (market_buyer.value_limit: budget, max_price_share,
    max_spend_per_deal). Never under base; plan A off (base None) is off here too. buyer_accept_band.ceiling keeps it
    under the budget."""
    try:
        if base is None or not enabled(md, env):
            return base
        pct = profiles(md, now).get(shop)
    except Exception:   # noqa: BLE001 - a profile never stops an order
        return base
    if pct is None or not _num(list_price) or not _num(limit):
        return base
    b = min(max(chance or 0, 0.0), 0.99)
    floor_cap = list_price * (1 - SAVED_FLOOR / (1 - b))
    pct = min(pct, int(floor_cap * 100 // limit))
    if _num(cap):
        pct = min(pct, int(cap * 100 // limit))
    return pct if pct > base else base
