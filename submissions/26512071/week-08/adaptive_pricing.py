"""Response-only margins: no buyer budgets, model calls, or settlement inference."""
MIN_OBSERVATIONS = 10
INITIAL, MIN_MARGIN, MAX_MARGIN, DELTA = .03, 0.0, .05, .01


def margin(rows, item):
    seen, outcomes = set(), []
    for row in rows:
        task = row.get('task')
        if (row.get('policy') == 'adaptive' and row.get('item') == item
                and row.get('outcome') in ('deal', 'reject', 'buyer canceled')
                and task and task not in seen and not row.get('test')):
            seen.add(task)
            outcomes.append(row['outcome'] == 'deal')
    value = INITIAL
    for start in range(0, len(outcomes) - MIN_OBSERVATIONS + 1, MIN_OBSERVATIONS):
        rate = sum(outcomes[start:start + MIN_OBSERVATIONS]) / MIN_OBSERVATIONS
        if rate >= .8:
            value += DELTA
        elif rate <= .5:
            value -= DELTA
        value = round(max(MIN_MARGIN, min(MAX_MARGIN, value)), 4)
    return value


def quote(reserve, margin_value, previous=None):
    opening = round(reserve * (1 + max(MIN_MARGIN, min(MAX_MARGIN, margin_value))))
    if previous is not None:
        opening = min(opening, previous - max(1, round(reserve * .03)))
    return max(reserve, opening)
