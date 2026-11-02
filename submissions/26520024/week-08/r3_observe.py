"""Optional read-only public evidence collector; never authenticates or trades."""
import json
import os
import time
from urllib.request import Request, urlopen

from r3_common import atomic_json, load_policy, read_json, state_dir
from r3_memory import Memory

BASE = 'https://market.ourolabs.ai'


def public_get(path):
    if not (path.startswith('/api/ledger?after=') or path == '/api/shops'):
        raise ValueError('public endpoint not allowed')
    request = Request(BASE + path, headers={'Accept': 'application/json'}, method='GET')
    with urlopen(request, timeout=8) as response:
        if response.geturl().split('?')[0] != (BASE + path).split('?')[0]:
            raise ValueError('unexpected endpoint redirect')
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('public response too large')
    return json.loads(raw)


def refresh_public(md=None, *, get=None, now=None):
    md = state_dir(md)
    now = time.time() if now is None else now
    if os.environ.get('R3_ENABLE_LIVE') != '1' or not load_policy(md)['public_observation_enabled']:
        return {'status': 'disabled', 'added': 0}
    get = get or public_get
    cursor_path = md / 'public-cursor.json'
    cursor = read_json(cursor_path, {})
    if now - cursor.get('polled_at', 0) < 60:
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
                    if row.get('kind') == 'settle' and isinstance(row.get('data'), dict):
                        stamp = row.get('ts')
                        if type(stamp) not in (int, float) or not 0 < stamp <= now + 60:
                            raise ValueError('invalid ledger timestamp')
                        data = {k: row['data'][k] for k in ('item', 'price', 'shop', 'buyer', 'unit', 'name')
                                if k in row['data']}
                        data.update(seq=row['seq'], source='public_ledger', observed_at=now)
                        added += memory.record('market_settle', row['seq'], data, ts=stamp)
                    after = row['seq']
                atomic_json(cursor_path, {'after': after, 'polled_at': now})
                if len(rows) < 500:
                    break
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
        return {'status': 'updated', 'added': added, 'after': after}
    except (OSError, ValueError, TypeError) as exc:
        # No retry loop on the fast trade path; already persisted entries are idempotent.
        return {'status': 'unavailable', 'added': added, 'error': type(exc).__name__}
