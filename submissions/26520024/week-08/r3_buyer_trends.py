"""Bounded buyer-side trends from settlements, never advertisements or shop cash."""
import math
import statistics
from collections import defaultdict

from r3_strategy import buyer_fallback, expected_saved, savings_ceiling

VERSION = 'settled-trend-v2-margin-saved'
WINDOW = 6 * 3600


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def recent(memory, kind, now):
    # Sort by market event time: newly ingested historical backfill is not a trend.
    rows = memory.db.execute('SELECT * FROM events WHERE kind=? AND ts BETWEEN ? AND ? '
                             'ORDER BY ts DESC LIMIT 4000', (kind, now - WINDOW, now)).fetchall()
    return [memory._event(row) for row in rows]


def collect(memory, catalog, now):
    own, public, attempts = defaultdict(list), defaultdict(list), defaultdict(list)
    seen = set()
    for event in recent(memory, 'settlement', now):
        data = event['data']
        settled = data.get('settled', {})
        sku, ledger = settled.get('sku', data.get('sku')), settled.get('ledger')
        product = catalog.get(sku, {})
        price = data.get('receipt', {}).get('price')
        listed = product.get('list_price')
        if (type(ledger) is not int or ledger in seen or not number(price) or price < 0 or
                not number(listed) or listed <= 0 or price > 1.5 * listed or
                not number(settled.get('value')) or settled['value'] <= 0):
            continue
        seen.add(ledger)
        expires = settled.get('expires_at')
        at = data.get('settled_at', data.get('observed_at', event['ts']))
        hours = (expires - at) / 3600 if number(expires) and number(at) else None
        own[(data.get('shop'), sku)].append((price / listed, hours))
    seen = set()
    for event in recent(memory, 'market_settle', now):
        data = event['data']
        sku, seq = data.get('sku', data.get('item')), data.get('seq')
        product = catalog.get(sku, {})
        listed, price = product.get('list_price'), data.get('amount', data.get('price'))
        buyer = data.get('buyer')
        if (data.get('source') != 'public_ledger' or type(seq) is not int or seq in seen or
                not isinstance(buyer, str) or not buyer or not number(price) or price < 0 or
                not number(listed) or listed <= 0 or price > 1.5 * listed):
            continue
        seen.add(seq)
        public[sku].append((price / listed, buyer))
    payments = {e['key']: e['data'] for e in recent(memory, 'payment', now)}
    for event in recent(memory, 'negotiation', now):
        data = event['data']
        product = catalog.get(data.get('sku'), {})
        shop = data.get('shop')
        if not shop or not product or now - event['ts'] < 300:
            continue
        payment = payments.get(event['key'])
        if payment and (payment.get('order') != data.get('order') or
                str(payment.get('shop', '')).rstrip('/') != str(data.get('shop_url', '')).rstrip('/')):
            continue
        if (payment and payment.get('paid') is True and type(payment.get('ledger')) is int):
            outcome = 1
        elif data.get('outcome') == 'no_deal' or (payment and payment.get('paid') is False):
            outcome = 0
        else:
            # Pending receipts and our model/transport errors are not seller failures.
            continue
        attempts[(shop, product.get('kind'), product.get('category'))].append(outcome)
    return dict(own=own, public=public, attempts=attempts)


def enrich(candidate, trends, order):
    estimate = candidate['value_estimate']
    listed = candidate['list_price'] or 0
    horizon = (order.get('use_within_hours') or 0) if candidate['kind'] == 'food' else 0
    own = [ratio for ratio, hours in trends['own'].get((candidate['shop_name'], candidate['sku']), [])
           if candidate['kind'] != 'food' or (hours is not None and hours >= horizon)]
    public = trends['public'].get(candidate['sku'], [])
    basis, ratios = 'prior_or_older_own', []
    if len(own) >= 2:
        basis, ratios = 'own_shop_sku_6h', own
    elif len(public) >= 3 and len({buyer for _, buyer in public}) >= 2:
        basis, ratios = 'public_sku_6h_mixed_quality', [r for r, _ in public]
    if ratios and listed > 0:
        # Shrink tiny/mixed-quality samples toward the existing estimate.
        weight = min(.8 if own and basis.startswith('own') else .5, len(ratios) / (len(ratios) + 4))
        ratio = min(1.5, max(.1, statistics.median(ratios)))
        price = (1 - weight) * estimate['estimated_price'] + weight * listed * ratio
        estimate['estimated_price'] = round(price, 2)
        estimate['expected_surplus'] = round(estimate['expected_value'] - price, 2)
    attempts = trends['attempts'].get((candidate['shop_name'], candidate['kind'], candidate['category']), [])
    successes = sum(attempts)
    estimate.update(trend_basis=basis, trend_samples=len(ratios),
                    settlement_probability=round((successes + 2) / (len(attempts) + 4), 4),
                    completed_attempts=len(attempts), settled_attempts=successes)
    estimate['expected_saved'] = round(expected_saved(candidate, estimate['estimated_price']), 6)
    estimate['expected_saved_per_attempt'] = round(estimate['settlement_probability'] *
                                                  estimate['expected_saved'], 6)
    return candidate


def score(candidate, budget):
    estimate = candidate['value_estimate']
    price = estimate['estimated_price']
    saved = expected_saved(candidate, price)
    target = candidate.get('target_saved', .05)
    probability = estimate.get('settlement_probability', .5)
    lift = probability * max(0, saved - target)
    return (lift if price <= budget else -float('inf'), saved, -price)


def select(candidates, budget):
    best = max(candidates, key=lambda row: score(row, budget))
    return dict(shop_name=best['shop_name'], sku=best['sku'],
                reason='Recent settled price and smoothed own completion rate; exact order constraints retained.')


def opening(state):
    estimate = state.get('value_estimate', {})
    price = estimate.get('estimated_price')
    if not estimate.get('trend_samples') or not number(price) or price <= 0:
        return None
    return min(savings_ceiling(state), max(1, math.floor(price * .9)))


def offer(state):
    action = buyer_fallback(state)
    recommended = opening(state)
    if (recommended is not None and recommended > 0 and state.get('last_offer') is None and
            action['action'] == 'offer' and
            (state.get('current_ask') is None or state['current_ask'] > savings_ceiling(state))):
        action = dict(action='offer', price=recommended,
                      summary='Opening below recent settled prices, within budget and quality-adjusted ceiling.')
    return action


def initial_request(sku, order, accept):
    text = 'I want to buy one unit of SKU ' + sku
    if order['kind'] == 'food':
        # Public usage requirements, not the private budget or any seller-supplied instruction.
        hours = order.get('use_within_hours') or 0
        minimum = math.ceil(accept['min_hours_left'] * 1_000_000) / 1_000_000
        text += ('. I need to use within %g hours (use_within_hours=%g). '
                 'The certified remaining shelf life at checkout must be at least %.6f hours.'
                 % (hours, hours, minimum))
    return text
