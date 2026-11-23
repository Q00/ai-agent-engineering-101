"""Local observations only: market access and signature verification stay in the kit."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


def append_event(path, event, **fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Single-process kit operation: one synchronous write per event, UTF-8 on disk.
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'time': now(), 'event': event, **fields},
                                ensure_ascii=True, allow_nan=False) + '\n')


def read_events(path, limit=200):
    from collections import deque
    try:
        with Path(path).open(encoding='utf-8') as stream:
            lines = deque(stream, maxlen=limit) if limit else stream
            rows = []
            for line in lines:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except json.JSONDecodeError:
                    continue
            return rows
    except FileNotFoundError:
        return []


def selection(shops, memory):
    """Direct receipt mismatches exclude a name when an alternative exists.

    Reviews influence a smoothed score; API errors and no-deals are not fraud.
    """
    blocked = {r.get('shop_name') for r in memory
               if r.get('result') == 'receipt_mismatch'}
    eligible = [i for i, s in enumerate(shops) if s['name'] not in blocked]
    if not eligible:
        return None, [], sorted(n for n in blocked if isinstance(n, str))
    scores = []
    for s in shops:
        stars = [r.get('stars') for r in s.get('reviews', []) if isinstance(r, dict)
                 and type(r.get('stars')) in (int, float)
                 and math.isfinite(r['stars']) and 1 <= r['stars'] <= 5]
        own = [r for r in memory if r.get('shop_name') == s['name']][-10:]
        score = (sum(stars) + 6) / (len(stars) + 2)
        score += 0.2 * sum(r.get('result') == 'purchased' for r in own)
        scores.append(round(score, 4))
    return max(eligible, key=lambda i: scores[i]), scores, sorted(
        n for n in blocked if isinstance(n, str))


def seller_feedback(item=None):
    phase = os.getenv('PRICE_PHASE', 'after')
    if phase not in ('before', 'after'):
        raise ValueError('PRICE_PHASE must be before or after')
    rows = read_events(ROOT / 'logs/challenge-1/seller-events.jsonl', 240)
    rows = [r for r in rows if r.get('run_id') == os.getenv('MARKET_RUN_ID', 'validation')]
    rows = [r for r in rows if item is None or r.get('item') == item]
    deals = [r for r in rows if r.get('event') == 'sale'][-12:]
    terminals = Counter(r.get('event') for r in rows
                        if r.get('event') in ('sale', 'reject', 'failed'))
    reviews = []
    try:
        # The kit removes our own shop from .market/shops.json. Own reviews must
        # come from an operator's dashboard export, never that filtered listing.
        snapshot = json.loads((ROOT / 'shop_feedback.json').read_text(encoding='utf-8-sig'))
        name = os.getenv('MARKET_SHOP_NAME', '26512076')
        shop = snapshot if snapshot.get('name') == name else None
        if shop:
            reviews = [{'stars': r.get('stars'), 'review': str(r.get('review', ''))[:300],
                        'round': r.get('round')} for r in shop.get('reviews', [])[:8]
                       if isinstance(r, dict)]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if phase == 'before':
        return {'phase': phase, 'feedback_enabled': False}
    return {'phase': phase, 'feedback_enabled': True,
            'recent_completed_sales': [{'item': r.get('item'), 'price': r.get('price')}
                                       for r in deals],
            'recent_terminal_counts': dict(terminals),
            'own_shop_reviews_untrusted': reviews,
            'coverage': 'Recent local observations, not the entire market; open tasks may be abandoned.'}


def receipt_problem(receipt, item, budget, offer, task):
    p = receipt.get('price')
    if type(p) not in (int, float) or not math.isfinite(p) or p <= 0 or p != math.floor(p):
        return 'Receipt price is not a positive whole number'
    if p > budget:
        return 'Receipt exceeds budget'
    if receipt.get('item') != item:
        return 'Receipt is for the wrong item'
    if offer is None or p != offer:
        return "Receipt price differs from the buyer's agreed offer"
    if receipt.get('task') != task:
        return 'Receipt task differs from the negotiation task'
    if not isinstance(receipt.get('shop'), str) or not receipt['shop']:
        return 'Receipt lacks a shop name'
    return None


async def model_request(client, *, log_event=None, **kwargs):
    """Bounded retries; never synthesize an agent decision after provider failure."""
    cooldown = ROOT / '.market/provider-cooldown.json'
    try:
        until = json.loads(cooldown.read_text()).get('until', 0)
        if time.time() < float(until):
            raise RuntimeError('Provider cooldown active; resume when quota permits')
    except (OSError, ValueError, TypeError):
        pass
    for attempt in range(3):
        request = dict(kwargs)
        if attempt == 2:
            request.pop('response_format', None)
        try:
            return await asyncio.wait_for(client.chat.completions.create(**request), timeout=40)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            transient = status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
            transient = transient or type(error).__name__ in (
                'APIConnectionError', 'APITimeoutError', 'TimeoutError', 'ConnectionError')
            if status == 400 and attempt < 2:
                if log_event:
                    log_event('provider_retry', status=status, attempt=attempt + 1)
                continue
            if not transient:
                raise
            delay = 2 * (attempt + 1)
            response = getattr(error, 'response', None)
            try:
                delay = max(delay, float(response.headers.get('retry-after', 0)))
            except (AttributeError, TypeError, ValueError):
                pass
            # Parse only the wait duration. Never write exception bodies/keys here.
            match = re.search(r'try again in (?:(\d+)m)?([\d.]+)s', str(error), re.I)
            if match:
                delay = max(delay, int(match[1] or 0) * 60 + float(match[2]) + 1)
            if not math.isfinite(delay):
                delay = 60
            if status == 429:
                cooldown.parent.mkdir(parents=True, exist_ok=True)
                temporary = cooldown.with_name(cooldown.name + f'.{os.getpid()}.tmp')
                temporary.write_text(json.dumps({'until': time.time() + delay}), encoding='utf-8')
                os.replace(temporary, cooldown)
            if log_event:
                log_event('provider_retry', status=status, attempt=attempt + 1,
                          wait_seconds=delay, error_type=type(error).__name__)
            if attempt == 2 or delay > 15:
                raise
            await asyncio.sleep(delay)
