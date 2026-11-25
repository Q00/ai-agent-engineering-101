"""Opt-in finite buzllightyear food counter floor; default OFF, no first-price change.

A keeps its already-computed decision. B offers at most 67% of list once,
then walks if the seller still wants more. The selector and hard limits stay unchanged;
a supplementary B receipt guard never replaces the existing receipt checks.
"""
import fcntl
import hashlib
import json
import math
import os
import re
import time

SWITCH = 'buyer_food_floor30.json'
JOURNAL = 'buyer_food_floor30_assignments.json'
POLICY = 'food-counter-saved033'
SHOP = 'buzllightyear'
MAX_SECONDS = 1800
MAX_ORDERS = 24
MAX_B = 12


def _epoch(v):
    return type(v) in (int, float) and abs(v) < 1e12 and math.isfinite(v)


def _money(v):
    return type(v) is int and 0 < v < 10**12


def _id(v):
    return isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', v) is not None


def config(md, env=None, now=None):
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if not _epoch(now) or str(env.get('BUYER_FOOD_FLOOR30') or '').lower().strip() in ('0', 'false', 'off', 'no'):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding='utf-8') as f:
            raw = f.read()
            mtime = os.fstat(f.fileno()).st_mtime
        d = json.loads(raw)
        if not isinstance(d, dict) or d.get('enabled') is not True or not all(_id(d.get(k)) for k in ('study', 'salt')):
            return None
        s, e = d.get('start'), d.get('end')
        if not (_epoch(s) and _epoch(e) and 0 < s < e <= s + MAX_SECONDS and mtime <= s <= now < e):
            return None
        return {k: d[k] for k in ('study', 'salt', 'start', 'end')} | {
            'config_sha256': hashlib.sha256(raw.encode()).hexdigest()}
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def _arm(cfg, order_id):
    return 'AB'[hashlib.sha256((cfg['salt'] + ':' + order_id).encode()).digest()[0] % 2]


def _valid(row, key, cfg, journal=True):
    try:
        required = ('study', 'start', 'end', 'config_sha256', 'policy', 'order', 'arm', 'shop', 'sku',
                    'quote_task', 'quote_ask', 'list_price', 'limit', 'baseline', 'last_offer', 'candidate',
                    'assigned_at', 'applied', 'grade', 'steps')
        if not isinstance(row, dict) or any(k not in row for k in required):
            return False
        if (not _id(key) or row['order'] != key or row['policy'] != POLICY or row['shop'] != SHOP
                or re.fullmatch(r'G[0-9a-f]{8}', row['sku']) is None
                or row['arm'] != _arm(cfg, key) or row['grade'] is not None or row['steps'] != [.9]
                or type(row['applied']) is not bool or (journal and row['applied'])
                or any(row[k] != cfg[k] for k in ('study', 'start', 'end', 'config_sha256'))
                or not _epoch(row['assigned_at']) or not cfg['start'] <= row['assigned_at'] < cfg['end']
                or not _id(row['quote_task'])):
            return False
        if not all(_money(row[k]) for k in ('quote_ask', 'list_price', 'limit', 'baseline', 'last_offer', 'candidate')):
            return False
        return (row['candidate'] == min(row['limit'], row['list_price'] * 67 // 100)
                and row['baseline'] > row['candidate'] and row['baseline'] <= row['quote_ask']
                and row['last_offer'] <= row['quote_ask'])
    except (ValueError, TypeError, OverflowError):
        return False


def _sync_dir(md):
    fd = os.open(md, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def receipt_problem(md, order, receipt, catalog, *, now=None, env=None):
    """Only an active, validated B trial adds a cap; all existing receipt checks still run."""
    try:
        cfg = config(md, env, now)
        note = (order or {}).get('_food_floor30')
        if (cfg is None or (order or {}).get('_food_floor30_disabled') or not isinstance(note, dict)
                or note.get('baseline_fallback') or note.get('arm') != 'B' or note.get('applied') is not True
                or not _valid(note, (order or {}).get('order'), cfg, journal=False)):
            return None
        with open(os.path.join(md, JOURNAL), encoding='utf-8') as f:
            backed = json.load(f)
        rows = backed.get('orders') if isinstance(backed, dict) else None
        if (not isinstance(backed, dict) or backed.get('config_sha256') != cfg['config_sha256']
                or not isinstance(rows, dict) or len(rows) > MAX_ORDERS
                or any(not _valid(v, k, cfg) for k, v in rows.items())
                or sum(v['arm'] == 'B' for v in rows.values()) > MAX_B):
            return None
        stored = rows.get(order['order'])
        if stored is None or any(note.get(k) != v for k, v in stored.items() if k != 'applied'):
            return None
        fresh = config(md, env, now)
        if fresh is None or fresh['config_sha256'] != cfg['config_sha256']:
            return None
        sku = (receipt or {}).get('item')
        lp = ((catalog or {}).get(sku) or {}).get('list_price')
        price = (receipt or {}).get('price')
        if not _money(lp) or not _epoch(price) or price <= 0:
            return 'food floor trial cannot bound the shipped SKU price'
        cap = lp * 67 // 100
        return f'food floor trial receipt price exceeds {cap} for {sku}' if price > cap else None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return None  # unavailable durable identity disables this extra policy, never a baseline receipt


def turn(decision, history, limit, steps, *, md, order, shop, sku, quote, list_price,
         probing=False, now=None, env=None):
    """Journal an eligible counter before any B decision; expiry/OFF/IO failure keeps baseline."""
    injected = now
    now = time.time() if now is None else now
    try:
        cfg = config(md, env, now)
        if (cfg is None or not isinstance(order, dict) or order.get('kind') != 'food' or shop != SHOP
                or order.get('_food_floor30_disabled') or not isinstance(quote, dict) or quote.get('sku') != sku
                or not isinstance(sku, str) or re.fullmatch(r'G[0-9a-f]{8}', sku) is None
                or set(re.findall(r'\bG[0-9a-f]{8}\b', quote.get('text') or '')) != {sku}
                or not _id(order.get('order')) or not _money(list_price) or not _money(limit)
                or tuple(steps or ()) != (.9,) or probing
                or (order.get('_food_ladder') is not None and
                    (not isinstance(order['_food_ladder'], dict) or order['_food_ladder'].get('policy') != POLICY))):
            return decision, None
        task = quote.get('task')
        task_id = task.get('id') if isinstance(task, dict) else None
        if not _id(task_id):
            return decision, None
        prior = order.get('_food_floor30')
        if prior is not None:
            if (not isinstance(prior, dict) or (prior.get('arm') == 'B' and prior.get('applied') is not True)
                    or not _valid(prior, order['order'], cfg, journal=False)
                    or any(prior[k] != v for k, v in {'shop': shop, 'sku': sku, 'quote_task': task_id,
                        'quote_ask': quote.get('ask'), 'list_price': list_price, 'limit': limit}.items())):
                return decision, None
            with open(os.path.join(md, JOURNAL) + '.lock', 'a', encoding='utf-8') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with open(os.path.join(md, JOURNAL), encoding='utf-8') as f:
                    backed = json.load(f)
                rows = backed.get('orders') if isinstance(backed, dict) else None
                if (not isinstance(backed, dict) or backed.get('config_sha256') != cfg['config_sha256'] or not isinstance(rows, dict)
                        or len(rows) > MAX_ORDERS or any(not _valid(v, k, cfg) for k, v in rows.items())
                        or sum(v['arm'] == 'B' for v in rows.values()) > MAX_B):
                    return decision, None
                stored = rows.get(order['order'])
                if stored is None or any(prior.get(k) != v for k, v in stored.items() if k != 'applied'):
                    return decision, None
                fresh = config(md, env, time.time() if injected is None else injected)
                if fresh is None or fresh['config_sha256'] != cfg['config_sha256']:
                    return decision, None
            if prior['arm'] == 'A':
                return decision, dict(prior)
            # The floor bid has already been attempted: never take a higher counter while active.
            offered = [p for who, p in history if who == 'you offered']
            if (not decision or decision.get('action') == 'offer' and
                    (not _money(decision.get('price')) or decision['price'] > prior['candidate'])):
                if offered and offered[-1] < prior['candidate'] and decision and decision.get('price') == prior['baseline']:
                    return {**decision, 'price': prior['candidate']}, dict(prior)
                return {'shop_price': prior['baseline'], 'action': 'walk_away', 'price': 0}, dict(prior)
            return decision, dict(prior)
        if not isinstance(decision, dict) or decision.get('action') != 'offer':
            return decision, None
        p = decision.get('shop_price')
        if (not _money(p) or decision.get('price') != p or not _money(quote.get('ask'))
                or not history or len(history) % 2 or len(history) > 6
                or any(who != ('shop asked' if i % 2 == 0 else 'you offered') or not _money(v)
                       for i, (who, v) in enumerate(history))
                or history[0] != ('shop asked', quote['ask'])
                or p > history[-2][1]):
            return decision, None
        cap = min(limit, list_price * 67 // 100)
        if not 0 < cap < p:
            return decision, None
        identity = dict(shop=shop, sku=sku, quote_task=task_id, quote_ask=quote['ask'],
                        list_price=list_price, limit=limit, baseline=p, last_offer=history[-1][1], candidate=cap)
        path = os.path.join(md, JOURNAL)
        with open(path + '.lock', 'a', encoding='utf-8') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(path, encoding='utf-8') as f:
                    journal = json.load(f)
            except FileNotFoundError:
                journal = {'config_sha256': cfg['config_sha256'], 'orders': {}}
            if (not isinstance(journal, dict) or journal.get('config_sha256') != cfg['config_sha256']
                    or not isinstance(journal.get('orders'), dict)):
                return decision, None
            rows = journal['orders']
            if (any(not _valid(row, key, cfg) for key, row in rows.items()) or len(rows) > MAX_ORDERS
                    or sum(x['arm'] == 'B' for x in rows.values()) > MAX_B):
                return decision, None
            note = rows.get(order['order'])
            if note is None:
                if len(rows) >= MAX_ORDERS or sum(x['arm'] == 'B' for x in rows.values()) >= MAX_B:
                    return decision, None
                note = {k: cfg[k] for k in ('study', 'start', 'end', 'config_sha256')}
                note.update(identity, policy=POLICY, order=order['order'], arm=_arm(cfg, order['order']),
                            assigned_at=now, applied=False, grade=None, steps=[.9])
                if not _valid(note, order['order'], cfg):
                    return decision, None
                rows[order['order']] = note
                tmp = path + '.tmp.' + str(os.getpid())
                created = False
                try:
                    with open(tmp, 'x', encoding='utf-8') as f:
                        created = True
                        json.dump(journal, f)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)
                finally:
                    if created and os.path.exists(tmp):
                        os.unlink(tmp)
            elif any(note[k] != v for k, v in identity.items()):
                return decision, None
            _sync_dir(md)
            fresh = config(md, env, time.time() if injected is None else injected)
            if fresh is None or fresh['config_sha256'] != cfg['config_sha256']:
                return decision, None
        note = dict(note)
        if note['arm'] == 'A':
            return decision, note
        note['applied'] = True
        if note['last_offer'] >= cap:
            return {'shop_price': p, 'action': 'walk_away', 'price': 0}, note
        return {**decision, 'price': cap}, note
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return decision, None
