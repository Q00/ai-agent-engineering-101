"""Optional read-only public evidence collector; never authenticates or trades."""
import json
import os
import httpx
from r3_clock import clock as time

from r3_common import atomic_json, load_policy, read_json, state_dir
from r3_memory import Memory

BASE = 'https://market.ourolabs.ai'


def public_get(path):
    if not (path.startswith('/api/ledger?after=') or path in ('/api/shops', '/api/board?round=3')):
        raise ValueError('public endpoint not allowed')
    # Match the kit transport; environment proxy caches previously returned stale boards.
    try:
        with httpx.Client(timeout=8, transport=httpx.HTTPTransport(retries=0)) as client:
            with client.stream('GET', BASE + path, headers={'Accept': 'application/json',
                               'Cache-Control': 'no-cache'}) as response:
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError('unexpected endpoint redirect')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 4_000_000:
                        raise ValueError('public response too large')
    except httpx.HTTPError as exc:
        raise OSError('public GET failed') from exc
    return json.loads(raw)


def retain(memory, row, now):
    if row.get('round', 3) != 3 or row.get('kind') not in ('settle', 'broken') or not isinstance(row.get('data'), dict):
        return 0
    stamp = row.get('ts')
    if type(stamp) not in (int, float) or not 0 < stamp <= now + 60:
        raise ValueError('invalid ledger timestamp')
    data = {k: row['data'][k] for k in ('item', 'sku', 'price', 'amount', 'order', 'task',
            'shop', 'buyer', 'unit', 'name', 'grade', 'claim_honest', 'warranty_hours',
            'refunded', 'hours_after_sale') if k in row['data']}
    data.update(seq=row['seq'], source='public_ledger', observed_at=now)
    return memory.record('market_settle' if row['kind']=='settle' else 'market_broken',
                         row['seq'], data, ts=stamp)


def refresh_public(md=None, *, get=None, now=None):
    md = state_dir(md)
    now = time.time() if now is None else now
    if os.environ.get('R3_ENABLE_LIVE') != '1' or not load_policy(md)['public_observation_enabled']:
        return {'status': 'disabled', 'added': 0}
    get = get or public_get
    cursor_path = md / 'public-cursor.json'
    cursor = read_json(cursor_path, {})
    if 0 <= now - cursor.get('polled_at', 0) < 60:
        return {'status': 'cached', 'added': 0}
    after = cursor.get('after', -500)
    added = 0
    try:
        with Memory(md) as memory:
            for _ in range(4):
                rows = get('/api/ledger?after=' + str(after))
                if not isinstance(rows, list):
                    raise ValueError('ledger must be a list')
                for row in rows[:500]:
                    if not isinstance(row, dict) or type(row.get('seq')) is not int or row['seq'] <= 0:
                        raise ValueError('invalid ledger sequence')
                    if row['seq'] <= after:
                        continue
                    added += retain(memory, row, now)
                    after = row['seq']
                atomic_json(cursor_path, {'after': after, 'polled_at': now})
                if len(rows) < 500:
                    break
            # Preserve the backfill cursor for recovery, but never wait days for live trends.
            try:
                tail = get('/api/ledger?after=-500')
                if not isinstance(tail, list):
                    raise ValueError('ledger tail must be a list')
                valid = [r for r in tail[:500] if isinstance(r, dict) and type(r.get('seq')) is int and r['seq'] > 0]
                for row in valid:
                    added += retain(memory, row, now)
                atomic_json(md/'public-tail.json', {'at': now, 'last_seq': max((r['seq'] for r in valid), default=None),
                            'sampled_rows': len(valid), 'scope': 'latest_500_entries_not_complete_history'})
            except (OSError, ValueError, TypeError):
                pass
            shops = get('/api/shops')
            shops = shops.get('shops', []) if isinstance(shops, dict) else shops
            if isinstance(shops, list):
                for shop in shops:
                    if not isinstance(shop, dict) or shop.get('name') != os.environ.get('R3_SHOP_NAME', 'leeuichann'):
                        continue
                    for review in shop.get('reviews', [])[:10]:
                        if isinstance(review, dict):
                            import hashlib
                            data = {k: review[k] for k in ('stars', 'review', 'round', 'ledger') if k in review}
                            data.update(shop=shop['name'], trust='customer_claim_not_independent_fact')
                            key = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
                            memory.record('shop_review', key, data)
            # Rankings are an independent optional observation, never a trading dependency.
            board_stamp = read_json(md / 'score-poll.json', {}).get('at', 0)
            if now - board_stamp >= 300:
                atomic_json(md / 'score-poll.json', {'at': now})
                try:
                    from r3_scores import snapshot
                    raw_board = get('/api/board?round=3')
                    board = snapshot(raw_board,
                                     os.environ.get('R3_SHOP_NAME', 'leeuichann'), now)
                    memory.record('score_snapshot', str(now), board, ts=now)
                    atomic_json(md / 'score-latest.json', board)
                    from r3_competition import refresh
                    refresh(memory, raw_board, shops, now)
                except (OSError, ValueError, TypeError, KeyError):
                    pass
        return {'status': 'updated', 'added': added, 'after': after}
    except (OSError, ValueError, TypeError) as exc:
        # No retry loop on the fast trade path; already persisted entries are idempotent.
        return {'status': 'unavailable', 'added': added, 'error': type(exc).__name__}
