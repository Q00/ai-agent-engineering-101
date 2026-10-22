"""Compare public prices among trusted candidates, then trade with one shop.

Prices are evidence, not promises: the chosen shop still supplies an A2A quote.
Only the market kit can verify a receipt against the registered seller key.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import re
from statistics import median

import httpx

MAX_CANDIDATES = 3
RECENT_PRICES = 5
MIN_REVIEW_MEAN = 3
RECEIPT_COOLDOWN_SECONDS = 24 * 60 * 60
CONNECTION_COOLDOWN_SECONDS = 15 * 60


def valid_price(value):
    return (type(value) in (int, float) and math.isfinite(value)
            and value >= 0 and float(value).is_integer())


def age_seconds(row, now):
    try:
        stamp = datetime.fromisoformat(row['time'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, (now - stamp).total_seconds())
    except (KeyError, ValueError, TypeError, AttributeError):
        return 0  # Undated bad evidence is not silently treated as expired.


def inspect_public_price(shop, item):
    """GET only: inspecting a candidate must never create or accept a purchase."""
    info = {'name': shop['name'], 'card': None, 'public_price': None, 'source': 'unknown'}
    try:
        with httpx.Client(timeout=4) as client:
            response = client.get(shop['url'].rstrip('/') + '/.well-known/agent-card.json')
            response.raise_for_status()
            card = response.json()
            if not isinstance(card, dict) or not card.get('supportedInterfaces'):
                raise ValueError('invalid agent card')
            info['card'] = card
            prices = card.get('prices', {})
            value = prices.get(item) if isinstance(prices, dict) else None
            if valid_price(value):
                info.update(public_price=int(value), source='public card prices')
            # The extension is optional; its absence is not a trust failure.
            if 'quality-guarantee.json' in str(card.get('description', '')):
                try:
                    response = client.get(shop['url'].rstrip('/') + '/.well-known/quality-guarantee.json')
                    response.raise_for_status()
                    prices = response.json().get('final_prices', {})
                    value = prices.get(item) if isinstance(prices, dict) else None
                    if valid_price(value):
                        info.update(public_price=int(value), source='advertised final price')
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    pass
            if info['public_price'] is None:
                match = re.search(r'\b' + re.escape(item) + r'\b\s*[:=$]?\s*(\d+)(?:\.0+)?\b',
                                  str(card.get('description', '')), re.I)
                if match:
                    info.update(public_price=int(match.group(1)), source='advertised card text')
    except (httpx.HTTPError, ValueError, TypeError) as error:
        info['error'] = f'{type(error).__name__}: {str(error)[:160]}'
    return info


def compare_shops(shops, memory, item, budget, *, limit=MAX_CANDIDATES, now=None, inspect=None):
    """Return one selected shop, its public card, and transparent decision evidence."""
    if not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError('compare-shops must be between 1 and 3')
    now = now or datetime.now(timezone.utc)
    inspect = inspect or inspect_public_price
    candidates, excluded = [], []
    for shop in shops:
        personal = [r for r in memory if r['shop_name'] == shop['name']]
        reviews = shop['reviews'][:10]
        review_mean = sum(r['stars'] for r in reviews) / len(reviews) if reviews else 3
        reason = None
        if reviews and review_mean < MIN_REVIEW_MEAN:
            reason = 'recent review mean below 3'
        if any(r.get('outcome') == 'invalid_receipt' and
               age_seconds(r, now) < RECEIPT_COOLDOWN_SECONDS for r in personal[-20:]):
            reason = 'receipt mismatch within cooldown'
        if (len(personal) >= 2 and all(r.get('outcome') == 'unavailable' for r in personal[-2:])
                and age_seconds(personal[-1], now) < CONNECTION_COOLDOWN_SECONDS):
            reason = 'repeated connection failures within cooldown'
        if reason:
            excluded.append({'name': shop['name'], 'reason': reason})
            continue
        prices = [r['price'] for r in personal if r.get('item') == item
                  and r.get('outcome') == 'deal' and valid_price(r.get('price'))][-RECENT_PRICES:]
        history = median(prices) if prices else None
        own = personal[-10:]
        own_mean = sum(r['stars'] for r in own) / len(own) if own else 3
        candidates.append({'shop': shop, 'history_price': history,
                           'quality_score': 0.5 * own_mean + 0.5 * review_mean})

    def price_key(row, field):
        value = row[field]
        # Unknown prices remain candidates; a known above-budget quote may still be negotiable.
        category = 1 if value is None else 0 if value <= budget else 2
        return category, value if value is not None else math.inf, -row['quality_score'], row['shop']['name']

    candidates.sort(key=lambda row: price_key(row, 'history_price'))
    candidates = candidates[:limit]
    evidence = {'item': item, 'budget': budget, 'excluded': excluded, 'comparisons': []}
    if not candidates:
        return None, None, evidence
    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        inspected = list(pool.map(lambda row: inspect(row['shop'], item), candidates))
    available = []
    for row, info in zip(candidates, inspected):
        value = info.get('public_price')
        row['expected_price'] = int(value) if valid_price(value) else row['history_price']
        row['card'] = info.get('card')
        evidence['comparisons'].append({'name': row['shop']['name'], 'history_price': row['history_price'],
                                        'public_price': info.get('public_price'), 'source': info.get('source'),
                                        'expected_price': row['expected_price'], 'reachable': row['card'] is not None,
                                        'error': info.get('error')})
        if row['card'] is not None:
            available.append(row)
    if not available:
        return None, None, evidence
    chosen = min(available, key=lambda row: price_key(row, 'expected_price'))
    evidence['selected'] = chosen['shop']['name']
    evidence['reason'] = 'lowest affordable known item price among reachable trusted candidates; quality breaks ties'
    return chosen['shop'], chosen['card'], evidence
