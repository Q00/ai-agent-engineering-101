"""Allocate deployable capital to demand, cash turnover and bounded discovery.

Budgets are planning envelopes, not predictions. Genuine refund obligations
have already been fully collateralized by cash_capacity before this runs.
"""
from .limits import capital_caps,fraction as capital_fraction


def allocate(candidates, inv, capacity, config, existing_units, merchant=None):
    if config.get('shop_championship_enabled') and config.get('shop_championship_allocation_enabled',True):
        from .shop_growth import allocate_championship
        return allocate_championship(candidates,inv,capacity,config,existing_units,merchant or {})
    if config.get('shop_velocity_enabled') and config.get('shop_velocity_allocation_enabled',True):
        from .shop_growth import allocate_velocity
        return allocate_velocity(candidates,inv,capacity,config,existing_units,merchant or {})
    if config.get('shop_growth_v5'):
        return allocate_growth(candidates, inv, capacity, config, existing_units, merchant or {})
    total = capacity['spendable']
    from .shop_growth import planning_slots
    slots = planning_slots(config,existing_units,candidates)
    chosen, category_spent, bucket_spent = [], {}, {'core': 0, 'cashflow': 0, 'discovery': 0}
    pressure = min(1, capacity['refund_liability'] / max(1, inv.get('balance', 0)))
    core_fraction = max(.3, .7 - .5 * pressure)
    budgets = {'core': total * core_fraction,
               'cashflow': total * (1 - core_fraction - config['exploration_fraction']),
               'discovery': total * config['exploration_fraction']}
    if not capital_caps(config):budgets={k:total for k in budgets}
    for c in candidates:
        c['allocation_book'] = ('discovery' if c['evidence']['exploration'] else
                                'cashflow' if c['evidence']['kind'] == 'food' or c['evidence']['list_price'] < 100000
                                else 'core')
    def priority(c):
        # Absolute hourly rank progress matters, with capital efficiency as a
        # secondary discriminator. Neither is an identified sale probability.
        hold = max(1/3, c['evidence']['expected_holding_hours'])
        return ((c['rank_progress_surrogate'] or 0) / hold,
                c['score'] / hold, c['score'] / max(1, c['capital_commitment']))
    ordered = sorted(candidates, key=priority, reverse=True)
    used = 0

    def add(c, envelope):
        nonlocal slots, used
        if slots <= 0 or len(chosen) >= config['restock_max_lines']:
            return False
        if any(x['sku'] == c['sku'] for x in chosen):
            return False
        per_unit = c['capital_commitment'] / c['qty']
        cat_left = total * .6 - category_spent.get(c['category'], 0) if capital_caps(config) else total-used
        quantity = min(c['qty'], slots, int(max(0, min(total-used, envelope, cat_left)) / per_unit))
        if quantity < 1:
            return False
        fraction = quantity / c['qty']
        line = dict(c, qty=quantity, max_cost=c['max_cost']*fraction,
                    capital_commitment=c['capital_commitment']*fraction,
                    expected_delta={k: v*fraction for k,v in c['expected_delta'].items()}, score=c['score']*fraction)
        chosen.append(line)
        used += line['capital_commitment']
        slots -= quantity
        category_spent[c['category']] = category_spent.get(c['category'], 0) + line['capital_commitment']
        bucket_spent[c['allocation_book']] += line['capital_commitment']
        return True

    for book in ('core', 'cashflow', 'discovery'):
        for c in ordered:
            if c['allocation_book'] == book:
                add(c, budgets[book] - bucket_spent[book])
    # Unused working capital may fund observed demand. Discovery never borrows
    # the other books' budgets and cannot consume the whole supply window.
    for c in ordered:
        if c['allocation_book'] != 'discovery':
            add(c, total - used)
    for c in chosen:
        c['evidence'] = dict(c['evidence'], portfolio_budgets=budgets,
                             portfolio_used=bucket_spent, allocation_book=c['allocation_book'],
                             warranty_pressure=pressure)
    return chosen


def allocate_growth(candidates, inv, capacity, config, existing_units, merchant):
    """Breadth opens discoverable order baskets; depth keeps hot SKUs visible.

    Greedy coverage is a heuristic, not an optimality or traffic guarantee.
    All quantities still reserve worst-case acquisition and storage cost.
    """
    total = capacity['spendable']
    weights = merchant.get('coverage_weights', {})
    coverage = set()
    for unit in existing_units:
        coverage.update(merchant.get('skus', {}).get(unit['sku'], {}).get('coverage_keys', []))
    selected, cat_used = {}, {}
    used = probes = 0
    from .shop_growth import planning_slots
    slots = planning_slots(config,existing_units,candidates)
    def per_unit(c): return c['capital_commitment'] / c['qty']
    def feasible(c):
        quantity = selected.get(c['sku'], {}).get('qty', 0)
        cost = per_unit(c)
        return (slots > 0 and quantity < c['qty'] and used + cost <= total and
                (c['sku'] in selected or len(selected) < min(10, config['restock_max_lines'])) and
                (not capital_caps(config) or cat_used.get(c['category'], 0) + cost <= total*.6) and
                (not c['evidence']['exploration'] or probes+cost <= total*capital_fraction(config,'exploration_fraction',.1)))
    def gain(c):
        return sum(weights.get(k, 0) for k in c['evidence']['growth'].get('coverage_keys', []) if k not in coverage)
    def add(c):
        nonlocal slots, used, probes
        cost = per_unit(c)
        selected.setdefault(c['sku'], {'source': c, 'qty': 0, 'new_coverage_weight': gain(c)})['qty'] += 1
        used += cost
        slots -= 1
        cat_used[c['category']] = cat_used.get(c['category'], 0) + cost
        if c['evidence']['exploration']: probes += cost
        coverage.update(c['evidence']['growth'].get('coverage_keys', []))
    breadth_budget = total * capital_fraction(config,'assortment_coverage_fraction',.35)
    # A three-minute window admits only ten lines. Reserve line slots for
    # meaningful book growth; cheap discovery must not consume every slot.
    breadth_lines=max(1,config['restock_max_lines']//3)
    while True:
        feasible_lines = [c for c in candidates if feasible(c) and c['sku'] not in selected and gain(c) > 0
                          and len(selected)<breadth_lines
                          and used + per_unit(c) <= breadth_budget]
        if not feasible_lines: break
        c = max(feasible_lines, key=lambda c: (gain(c)/max(1, per_unit(c))**.5,
                                               c['score']/max(1, per_unit(c)), c['sku']))
        add(c)
    def yield_priority(c):
        holding = max(1/3, c['evidence']['expected_holding_hours'])
        return ((c.get('rank_progress_surrogate') or 0)/c['qty']/holding,
                c['score']/c['qty']/holding, gain(c), c['sku'])
    while True:
        available = [c for c in candidates if feasible(c)]
        if not available: break
        add(max(available, key=yield_priority))
    result = []
    for row in selected.values():
        c, quantity = row['source'], row['qty']
        fraction = quantity/c['qty']
        result.append(dict(c, qty=quantity, max_cost=c['max_cost']*fraction,
            capital_commitment=c['capital_commitment']*fraction, score=c['score']*fraction,
            expected_delta={k: v*fraction for k,v in c['expected_delta'].items()},
            allocation_book='coverage_and_depth', evidence=dict(c['evidence'],
                new_coverage_weight=row['new_coverage_weight'], portfolio_used=used,
                coverage_budget_scenario=breadth_budget)))
    return sorted(result,key=lambda c:c['expected_delta'].get('book',0),reverse=True)
