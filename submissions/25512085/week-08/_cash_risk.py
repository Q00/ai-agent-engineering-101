"""Internal refund estimate; observations and public dependencies stay in the facade."""


def refund_probability(policy, hub, sku, grade, catalog, now):
    if not policy.warranty_hours(grade, catalog.get(sku, {}).get('kind','goods')):
        return 0
    prior = policy.PRIOR_BREAK.get(grade, .35)
    own = hub.get('break_stats', {}).get(sku, {}).get(grade, {})
    market = hub.get('market_break_stats', {}).get(sku, {}).get(grade, {})
    q = (own.get('broken',0)+.5*market.get('broken',0)+20*prior)/(own.get('sold',0)+.5*market.get('sold',0)+20)
    return min(1, max(prior if grade == 'B' else 0, q))
