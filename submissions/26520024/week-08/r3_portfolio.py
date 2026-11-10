"""Observed substitute coverage and small experiments, not semantic demand guesses."""
from collections import defaultdict
import math
import statistics


def observe_offers(memory, order, shops, now):
    rows = []
    complete = True
    for shop in shops:
        if not isinstance(shop.get('offers'), list):
            complete = False
            continue
        for offer in shop.get('offers', []):
            if (isinstance(offer, dict) and isinstance(offer.get('sku'), str)
                    and type(offer.get('qty')) is int and offer['qty'] >= 0):
                rows.append({'shop': shop['name'], 'sku': offer['sku'], 'qty': offer['qty']})
            else:
                complete = False
    memory.record('offer_snapshot', order['order'],
                  {'category': order['category'], 'rows': rows[:2000],
                   'truncated': len(rows) > 2000, 'scope': 'own_order_category_not_entire_market'}, ts=now)
    from r3_replenishment import record_supply_gap
    record_supply_gap(memory, order, rows[:2000], len(rows) > 2000 or not complete, now)


def diversified(rows, limit):
    """Reserve candidate space for eligible/related pilots before a global top-N cut."""
    ranked = sorted(rows, key=lambda r: (-r.get('portfolio_score', r['score']), r['list_price'], r['sku']))
    slots = max(1, limit // 4)
    pilots = sorted((r for r in ranked if r['demand_status'] in ('eligible_unseen', 'related_unseen')),
                    key=lambda r: r['demand_status'] != 'eligible_unseen')[:slots]
    proven = [r for r in ranked if r not in pilots][:max(0, limit - len(pilots))]
    # Interleave, so bounded model/fallback prefixes also see a pilot.
    result = []
    while proven or pilots:
        result.extend(proven[:2])
        del proven[:2]
        if pilots:
            result.append(pilots.pop(0))
    return result[:limit]


def enrich(inputs, memory, inventory, now, limit=24):
    offers = {}
    for event in memory.events('offer_snapshot', 1000):
        if 0 <= now - event['ts'] <= 1800:
            offers.setdefault(event['data']['category'], event['data'])
    outcomes = defaultdict(list)
    refunded = {e['data'].get('unit') for e in memory.events('market_broken', 2000)
                if e['data'].get('refunded') is True}
    for event in memory.events('seller_settlement', 2000):
        if 0 <= now - event['ts'] <= 7 * 86400:
            outcomes[event['data']['sku']].append(event['data'])
    # Fresh per-category snapshots prevent counting stale stock repeatedly.
    supply = defaultdict(lambda: {'shops': set(), 'units': 0})
    for snapshot in offers.values():
        for row in snapshot['rows']:
            if row['qty'] > 0:
                supply[row['sku']]['shops'].add(row['shop'])
                supply[row['sku']]['units'] += row['qty']
    surge = {r['sku']: r['factor'] for r in inventory.get('surge', [])}
    held = {u['sku'] for u in inventory.get('units', [])}
    covered = {o for r in inputs['candidates'] if r['sku'] in held for o in r['order_groups']}
    for row in inputs['candidates']:
        sku = row['sku']
        sample = outcomes[sku]
        margins = [(-r['cost'] - r.get('storage_estimate', 0) if r.get('unit') in refunded
                    else r.get('margin_after_storage', 0)) for r in sample]
        # Warranty exposure must mature before a sale can justify scaling.
        profitable = [r for r, margin in zip(sample, margins) if margin > 0
                      and now >= r.get('warranty_matures_at', 0)]
        ratio = statistics.median(r['price'] / r['list_price'] for r in sample) if sample else .85
        factor = surge.get(sku, 1) if 'surge' in inventory else 1.5
        cost = .75 * min(1.5, factor + .006) * row['list_price']
        margin = ratio * row['list_price'] - cost
        fresh_groups = set(row['order_groups']) - covered
        competitors = len(supply[sku]['shops'])
        known_supply = row['category'] in offers and not offers[row['category']].get('truncated')
        coverage_gain = len(fresh_groups) / max(1, len(set(row['order_groups'])))
        competition = 1 + competitors + math.log1p(supply[sku]['units']) if known_supply else 2
        diversity = 1 + .1 * min(5, row.get('distinct_buyers', 0))
        score = ((row['score'] + .25 * coverage_gain) * max(0., margin / max(1, cost))
                 * diversity / competition)
        row['portfolio_score'] = round(score, 6)
        row['portfolio'] = {'own_settled': len(sample), 'own_profitable': len(profitable),
            'median_sell_hours': statistics.median(r['holding_hours'] for r in sample) if sample else None,
            'competitor_shops': competitors if known_supply else None,
            'observed_units': supply[sku]['units'] if known_supply else None,
            'supply_scope': 'partial_own_order_snapshot', 'new_order_coverage': len(fresh_groups),
            'surge_factor': factor, 'estimated_margin': round(margin, 2),
            'price_ratio_basis': 'own_settled_median' if sample else 'explicit_0.85_prior',
            'observed_refunds': sum(r.get('unit') in refunded for r in sample),
            'scale_ready': len(profitable) >= 2 and sum(margins) > 0,
            'limitation': 'No causal demand, failure probability or complete competitor stock is inferred.'}
    from r3_competition import enrich as competitive_enrich, fresh_report
    competitive_enrich(inputs, fresh_report(memory.md, now))
    from r3_replenishment import enrich as replenish_enrich
    replenish_enrich(inputs, memory, inventory, now)
    inputs['candidates'] = diversified(inputs['candidates'], limit)
    return inputs


def select_next(rows, candidates, used_orders):
    def score(sku):
        candidate = candidates.get(sku, {})
        groups = set(candidate.get('order_groups', []))
        extra = len(groups - used_orders) / max(1, len(groups))
        return (candidate.get('portfolio_score', candidate.get('score', 0)) * (1 + extra),
                extra, -candidate.get('list_price', 0), sku)
    return max(rows, key=score)
