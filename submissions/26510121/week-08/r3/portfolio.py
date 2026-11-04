"""Allocate deployable capital to demand, cash turnover and bounded discovery.

Budgets are planning envelopes, not predictions. Genuine refund obligations
have already been fully collateralized by cash_capacity before this runs.
"""


def allocate(candidates, inv, capacity, config, existing_units):
    total = capacity['spendable']
    slots = config['inventory_target_units'] - len(existing_units)
    chosen, category_spent, bucket_spent = [], {}, {'core': 0, 'cashflow': 0, 'discovery': 0}
    pressure = min(1, capacity['refund_liability'] / max(1, inv.get('balance', 0)))
    core_fraction = max(.3, .7 - .5 * pressure)
    budgets = {'core': total * core_fraction,
               'cashflow': total * (1 - core_fraction - config['exploration_fraction']),
               'discovery': total * config['exploration_fraction']}
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
        cat_left = total * .6 - category_spent.get(c['category'], 0)
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
