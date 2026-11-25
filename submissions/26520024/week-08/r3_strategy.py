"""Transparent estimates from settled observations, never claims of future demand."""
import math
import statistics
from r3_clock import clock as time
from r3_savings import RULE, break_risk, contribution, cost_prior, finite


def price_ratio(memory, catalog, category, now):
    ratios, refs = [], []
    for event in memory.events('market_settle', limit=2000):
        data = event['data']
        product = catalog.get(data.get('item'), {})
        price = data.get('amount', data.get('price'))
        listed = product.get('list_price', 0)
        if (product.get('category') != category or not 0 <= now - event['ts'] <= 48 * 3600 or
                type(price) not in (int, float) or not math.isfinite(price) or not listed or
                not 0 < price / listed <= 1.5 or data.get('source') != 'public_ledger'):
            continue
        if data.get('seq') in refs:
            continue
        ratios.append(price / listed)
        refs.append(data.get('seq'))
    return {'median': statistics.median(ratios) if len(ratios) >= 3 else None,
            'samples': len(ratios), 'refs': refs[:8], 'limit': 'settled-price sample, not demand elasticity'}


def buyer_evidence(memory):
    rows = memory.db.execute("SELECT * FROM context_events WHERE kind IN ('settlement','broken') "
                             "ORDER BY ts DESC LIMIT 2000").fetchall()
    return [memory._event(row) for row in rows]


def buyer_estimate(memory, candidate, now=None, *, records=None):
    now = time.time() if now is None else now
    if records is None:
        records = buyer_evidence(memory)
    purchases, broken = {}, set()
    for event in records:
        data = event['data']
        if data.get('shop', data.get('shop_name')) != candidate['shop_name'] or data.get('sku') != candidate['sku']:
            continue
        if event['kind'] == 'settlement':
            settled = data.get('settled', {})
            ledger = settled.get('ledger')
            if type(ledger) is int:
                purchases[ledger] = (event, data)
        elif data.get('attribution') == 'inferred_unique_name_time':
            broken.add(data.get('ledger'))
    mature = {key for key, (event, data) in purchases.items()
              if 6 * 3600 <= now - data.get('observed_at', event['ts']) <= 7 * 86400}
    failures = len(mature & broken)
    # 10% is an explicit conservative prior, not the market's hidden breakage rate.
    risk = (failures + 1) / (len(mature) + 10) if candidate['kind'] == 'goods' else 0.
    prices = [data.get('receipt', {}).get('price') for _, data in purchases.values()]
    prices = [p for p in prices if type(p) in (int, float) and math.isfinite(p) and p >= 0]
    listed = candidate.get('list_price') or 0
    estimate = statistics.median(prices) if prices else .8 * listed
    value = listed * (1 - risk)
    saved = expected_saved(dict(candidate, value_estimate={'break_risk_estimate': risk}), estimate)
    return {'expected_value': round(value, 2), 'estimated_price': round(estimate, 2),
            'expected_surplus': round(value - estimate, 2), 'break_risk_estimate': round(risk, 4),
            'expected_saved': round(saved, 6), **cost_prior(candidate),
            'mature_samples': len(mature), 'inferred_breaks': failures,
            'assumptions': 'Unit cost is an unobserved category prior, not the official deal score; '
                           '10% goods breakage prior; inferred links; food requires verified horizon; no refund credit'}


def expected_saved(candidate, price):
    """Patch 3.6: usable margin share capped at 1, unserved -1, refunded 0.

    Do not credit a seller's advertised warranty before checkout verifies coverage.
    """
    risk = break_risk(candidate)
    usable = contribution(candidate.get('list_price'), price,
                          cost_prior(candidate)['estimated_unit_cost'])
    return (1 - risk) * usable - risk


def savings_ceiling(state):
    listed = state.get('list_price')
    cost = cost_prior(state)['estimated_unit_cost']
    risk = break_risk(state)
    target = state.get('target_saved', .05)
    # Below-cost deals are capped: even a free item cannot beat 1 - 2*risk.
    if (cost is None or not finite(target) or target < 0 or risk >= 1 or
            target > 1 - 2 * risk + 1e-12):
        return 0
    price = listed - (listed - cost) * (target + risk) / (1 - risk)
    return min(state['budget'], max(0, math.floor(price + 1e-8)))


def buyer_objective(memory, policy, now):
    # Use a recent server observation, not a biased last-N local sample of Saved.
    latest = memory.events('score_snapshot', limit=1)
    buyer = latest[0]['data'].get('own', {}).get('buyer', {}) if latest else {}
    fresh = bool(latest and latest[0]['data'].get('rule_scenario') == RULE and
                 0 <= now - latest[0]['ts'] <= 900)
    saved = buyer.get('saved') if fresh else None
    if type(saved) not in (int, float) or not math.isfinite(saved):
        saved = None
    return {'target_saved': max(policy['buyer_min_saved'], saved or 0),
            'current_saved': saved, 'basis': 'fresh_board' if saved is not None else 'policy_floor',
            'saved_rule': RULE,
            'note': 'Board Saved = sum of contributions / (deals + 100). Expected marginal '
                    'contribution should exceed current Saved; unit cost is estimated, not a rank guarantee.'}


def seller_fallback(state):
    bounds, offer, previous = state['pricing'], state['incoming_offer'], state['previous_ask']
    if offer is not None and offer >= bounds['minimum']:
        price, action = offer, 'accept'
    else:
        price = bounds['ceiling'] if previous is None else max(bounds['minimum'],
                    min(bounds['ceiling'], (previous + bounds['minimum']) // 2))
        action = 'ask'
    return {'action': action, 'price': price, 'summary': 'Deterministic degraded policy within current price bounds.'}


def buyer_fallback(state):
    ceiling = savings_ceiling(state)
    ask, last = state.get('current_ask'), state.get('last_offer')
    if ceiling <= 0:
        return {'action': 'walk', 'price': None, 'summary': 'No positive risk-adjusted offer remains.'}
    if ask is not None and 0 <= ask <= ceiling:
        price = ask
    elif last is not None and last >= ceiling:
        return {'action': 'walk', 'price': None, 'summary': 'No positive risk-adjusted offer remains.'}
    elif last is None:
        price = min(ceiling, max(1, int((state.get('list_price') or ceiling) * .6)))
    else:
        turns_left = max(1, 5 - state.get('rounds', 0))
        price = min(ceiling, last + max(1, math.ceil((ceiling - last) / turns_left)))
    return {'action': 'offer', 'price': price, 'summary': 'Deterministic budget and expected-value bounded offer.'}


def selection_fallback(candidates, budget):
    def score(row):
        estimate = row.get('value_estimate', {})
        value = estimate.get('expected_value', row.get('list_price') or 0)
        price = estimate.get('estimated_price', value * .8)
        return (expected_saved(row, price) if price <= budget else -float('inf'), -price)
    selected = max(candidates, key=score)
    return {'shop_name': selected['shop_name'], 'sku': selected['sku'],
            'reason': 'Quality-adjusted estimated margin Saved; checkout enforces acceptance.'}
