"""Explicit food scopes, .89 or .88 vs .90 first offer; finite pilot, default OFF.

The old food-ladder switch/history is never changed. Notes use food_ladder so its
existing B-trade learning exclusions also exclude this pilot's changed prices.
Only the first offer changes; no new rung, selector, cap, receipt, or counter rule.
"""
import fcntl
import hashlib
import json
import math
import os
import re
import time

SWITCH = 'buyer_food_open30.json'
JOURNAL = 'buyer_food_open30_assignments.json'
POLICY = 'food-first-089'
SHOP = 'cheapest-in-town'
SKU = 'G2108d9bc'
RETRY_SCOPE = 'food-code90-five-shops-v2'
SPEED_SCOPE = 'food-code90-five-shops-088-v3'
SPEED_POLICY = 'food-first-088'
RETRY_SHOPS = frozenset(('cheapest-in-town', 'YAHO1212', 'thisjonghoon', 'choo-chu', 'olstjr'))
MAX_SECONDS = 1800
MAX_ORDERS = 12
MAX_B = 6


def _epoch(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and abs(x) < 1e12 and math.isfinite(x)


def config(md, env=None, now=None):
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if any(str(env.get(k) or '').strip().lower() in ('0', 'false', 'no', 'off')
           for k in ('BUYER_FOOD_OPEN30', 'BUYER_FOOD_LADDER')) or not _epoch(now):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding='utf-8') as f:
            raw = f.read()
            mtime = os.fstat(f.fileno()).st_mtime
        d = json.loads(raw)
        if not isinstance(d, dict) or d.get('enabled') is not True:
            return None
        start, end = d.get('start'), d.get('end')
        if d.get('scope') not in (None, RETRY_SCOPE, SPEED_SCOPE):
            return None
        if not (_epoch(start) and _epoch(end) and 0 < start < end <= start + MAX_SECONDS
                and mtime <= start <= now < end
                and isinstance(d.get('study'), str) and d['study']
                and isinstance(d.get('salt'), str) and d['salt']):
            return None
        return {k: d[k] for k in ('study', 'salt', 'start', 'end')} | {
            'config_sha256': hashlib.sha256(raw.encode()).hexdigest()} | (
            {'scope': d['scope']} if d.get('scope') in (RETRY_SCOPE, SPEED_SCOPE) else {})
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def _arm(cfg, order_id):
    return 'B' if hashlib.sha256(f"{cfg['salt']}:{order_id}".encode()).digest()[0] % 2 else 'A'


def _target(cfg, shop, sku):
    if cfg.get('scope') in (RETRY_SCOPE, SPEED_SCOPE):
        return shop in RETRY_SHOPS and isinstance(sku, str) and re.fullmatch(r'G[0-9a-f]{8}', sku) is not None
    return shop == SHOP and sku == SKU


def _policy(cfg):
    return SPEED_POLICY if cfg.get('scope') == SPEED_SCOPE else POLICY


def _candidate(cfg, ask):
    return int(ask) * 88 // 100 if cfg.get('scope') == SPEED_SCOPE else int(.89 * ask)


def _valid_row(order_id, row, cfg, *, journal=True):
    """Validate persisted inputs, not just the price eventually sent to the seller."""
    try:
        fields = {'study', 'start', 'end', 'config_sha256', 'policy', 'arm', 'grade', 'steps',
                  'shop', 'sku', 'ask', 'baseline', 'candidate', 'applied', 'assigned_at',
                  'order', 'quote_task', 'limit'}
        if (not isinstance(row, dict) or not fields.issubset(row)
                or not isinstance(order_id, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,96}', order_id) is None
                or row['order'] != order_id or row['policy'] != _policy(cfg) or not _target(cfg, row['shop'], row['sku'])
                or row.get('scope') != cfg.get('scope')
                or row['arm'] != _arm(cfg, order_id) or row['grade'] is not None or row['steps'] != [.9]
                or any(row[k] != cfg[k] for k in ('study', 'start', 'end', 'config_sha256'))
                or not _epoch(row['assigned_at']) or not cfg['start'] <= row['assigned_at'] < cfg['end']
                or type(row['applied']) is not bool or (journal and row['applied'] is not False)
                or not (row['quote_task'] is None or isinstance(row['quote_task'], str))):
            return False
        ask, limit = row['ask'], row['limit']
        return (_epoch(ask) and int(ask) == ask and ask > 0 and _epoch(limit) and limit >= 1
                and type(row['baseline']) is int and type(row['candidate']) is int
                and row['baseline'] == min(int(.90 * ask), int(ask), int(limit))
                and row['candidate'] == _candidate(cfg, ask) and 0 < row['candidate'] < row['baseline'])
    except (ValueError, TypeError, OverflowError):
        return False


def _sync_directory(md):
    fd = os.open(md, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def assign(md, order, shop, quote, steps, resume, *, probing, code_priced, limit, env=None, now=None):
    """Persist assignment before returning it; journal failure leaves the original talk unchanged."""
    injected_now = now
    now = time.time() if now is None else now
    try:
        if not isinstance(order, dict) or not isinstance(quote, dict):
            return None
        ask = quote.get('ask')
        cfg = config(md, env, now)
        if cfg is None:
            return None
        sku = quote.get('sku')
        if (order.get('kind') != 'food' or not _target(cfg, shop, sku)
                or set(re.findall(r'\bG[0-9a-f]{8}\b', quote.get('text') or '')) != {sku}
                or tuple(steps or ()) != (.9,) or not resume or probing or not code_priced
                or quote.get('ask_src') != 'text' or not _epoch(ask) or int(ask) != ask or ask <= 0
                or not _epoch(limit) or limit < 1 or order.get('_one_won_default')
                or order.get('_food_quote_yield') or order.get('_b3') is not None
                or order.get('_food_ladder') is not None
                or (order.get('_food_shelf') or {}).get('would_drop')
                or not isinstance(order.get('order'), str)
                or re.fullmatch(r'[A-Za-z0-9_-]{1,96}', order['order']) is None):
            return None
        baseline, candidate = min(int(.90 * ask), int(ask), int(limit)), _candidate(cfg, ask)
        if not 0 < candidate < baseline:
            return None
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
                return None
            rows = journal['orders']
            if (any(not _valid_row(key, row, cfg) for key, row in rows.items())
                    or len(rows) > MAX_ORDERS or sum(x['arm'] == 'B' for x in rows.values()) > MAX_B):
                return None
            task = quote.get('task')
            task_id = task.get('id') if isinstance(task, dict) else None
            note = rows.get(order['order'])
            if note is not None:
                if any(note[k] != value for k, value in {'ask': ask, 'baseline': baseline,
                        'limit': limit, 'quote_task': task_id, 'shop': shop, 'sku': sku}.items()):
                    return None
            else:
                if len(rows) >= MAX_ORDERS or sum(x['arm'] == 'B' for x in rows.values()) >= MAX_B:
                    return None   # exposure bound, not a model-call cap; stop both arms together
                note = {k: cfg[k] for k in ('study', 'start', 'end', 'config_sha256')}
                note.update(policy=_policy(cfg), arm=_arm(cfg, order['order']), grade=None, steps=[.9], shop=shop, sku=sku,
                            ask=ask, limit=limit, baseline=baseline, candidate=candidate, applied=False, assigned_at=now,
                            order=order['order'], quote_task=task_id)
                if cfg.get('scope'):
                    note['scope'] = cfg['scope']
                if not _valid_row(order['order'], note, cfg):
                    return None
                rows[order['order']] = note
                tmp = path + f'.tmp.{os.getpid()}'
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
            # A prior unacknowledged rename must also be persisted before reuse.
            _sync_directory(md)
            # Recheck after I/O: expiry/OFF while assigning never permits a treatment.
            fresh = config(md, env, time.time() if injected_now is None else injected_now)
            if fresh is None or fresh['config_sha256'] != cfg['config_sha256']:
                return None
            return dict(note)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return None


def apply(decision, history, limit, steps, note, *, md=None, env=None, now=None):
    """Only lower the original, code-priced first offer; later turns/expired/OFF are identical."""
    if not isinstance(note, dict) or note.get('policy') not in (POLICY, SPEED_POLICY) or md is None:
        return decision
    cfg = config(md, env, now)
    if (cfg is None or not _valid_row(note.get('order'), note, cfg, journal=False)
            or limit != note.get('limit') or tuple(steps or ()) != (.9,) or any(who == 'you offered' for who, _ in history)
            or not decision or decision.get('action') != 'offer'
            or decision.get('shop_price') != note.get('ask') or decision.get('price') != note.get('baseline')):
        return decision
    note['first_observed'] = decision['price']
    if note.get('arm') != 'B':
        return decision
    price = note.get('candidate')
    if not isinstance(price, int) or isinstance(price, bool) or not 0 < price < decision['price'] or price > limit:
        return decision
    note.update(applied=True, first_observed=price)
    return {**decision, 'price': price}
