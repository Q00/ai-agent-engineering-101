"""Patch-3.6 scenario forecasts vs observed board results, never a grade guarantee."""
import math
from r3_savings import RULE

WEIGHTS = {'shop': {'book': .5, 'revenue': .3, 'stars_rank': .2},
           'buyer': {'saved': .5, 'forum': .3, 'deals': .1, 'posts': .1}}


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def snapshot(board, name, now):
    if not isinstance(board, dict) or board.get('round') != 3:
        raise ValueError('Round 3 board required')
    cohorts, own = {}, {}
    for role, plural in (('shop', 'shops'), ('buyer', 'buyers')):
        rows = board.get(plural)
        if not isinstance(rows, list):
            raise ValueError('Board rows missing')
        fields = set(WEIGHTS[role]) | {'all', 'deals'}
        safe = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                continue
            value = {k: row[k] for k in fields if finite(row.get(k))}
            value['name'] = row['name']
            safe.append(value)
            if row['name'] == name:
                own[role] = value
        cohorts[role] = safe
    return {'at': now, 'own': own, 'cohorts': cohorts, 'source': 'public_board',
            'rule_scenario': RULE, 'cohort': 'public_rows_not_class_grade',
            'limitations': 'Midrank proxy; official tie handling/cohort and deployed rule may differ.'}


def score(own, cohorts):
    components, total = {}, 0.
    for role, weights in WEIGHTS.items():
        row = own.get(role, {})
        for field, weight in weights.items():
            value = row.get(field)
            if not finite(value):
                return None
            if field == 'forum':
                rank = max(0., min(1., value))
            else:
                peers = [r[field] for r in cohorts.get(role, [])
                         if r.get('name') != row.get('name') and finite(r.get(field))
                         and finite(r.get('all'))]
                # Explicit empirical midrank convention, not reverse-engineered grading.
                values = peers + [value]
                rank = (sum(v < value for v in values) + .5 * sum(v == value for v in values)) / len(values)
            components[role + '.' + field] = weight * rank
            total += weight * rank
    return {'total': total, 'components': components}


def compare_and_predict(current, previous, now, horizon=7200):
    if not current or not 0 <= now - current.get('at', 0) <= 900:
        return {'status': 'missing_or_stale_board', 'next_prediction': None}
    if current.get('rule_scenario') != RULE:
        return {'status': 'score_rule_changed', 'next_prediction': None}
    actual = score(current['own'], current['cohorts'])
    old = previous.get('score_observation') if previous else None
    forecast = previous.get('score_prediction') if previous else None
    rule_changed = bool(old and (old.get('rule_scenario') != RULE or
                                (forecast and forecast.get('rule') != RULE)))
    if rule_changed:
        old, forecast = None, None
    comparison = None
    if forecast and old and now >= forecast['due_at']:
        within = abs(now - forecast['due_at']) <= 900
        realized = score(current['own'], old['cohorts'])
        origin = score(old['own'], old['cohorts'])
        official = {}
        for role in ('shop', 'buyer'):
            a, b = current['own'].get(role, {}).get('all'), old['own'].get(role, {}).get('all')
            if finite(a) and finite(b):
                official[role] = a - b
        actual_delta = realized['total'] - origin['total'] if realized and origin else None
        comparison = {'on_schedule': within, 'predicted_delta': forecast['expected_delta'],
            'realized_fixed_cohort_delta': actual_delta,
            'prediction_error': actual_delta - forecast['expected_delta']
                if within and actual_delta is not None and forecast['expected_delta'] is not None else None,
            'official_all_delta': official,
            'metric_errors': {role: {field: current['own'][role][field] - value
                for field, value in row.items() if finite(value) and finite(current['own'].get(role, {}).get(field))}
                for role, row in forecast['projected_own'].items()},
            'attribution': 'Observed before/after; not causal policy lift. Official rules may differ.'}
    projected = {role: dict(row) for role, row in current['own'].items()}
    elapsed = current['at'] - old['at'] if old else 0
    # No fitted policy effect: a persistence/rate baseline is falsifiable and cheap.
    extrapolate = 3600 <= elapsed <= 4 * horizon
    for role, row in projected.items():
        for field in WEIGHTS[role]:
            value = row.get(field)
            before = old['own'].get(role, {}).get(field) if extrapolate else None
            if field in ('book', 'revenue', 'deals', 'posts') and finite(value) and finite(before):
                delta = (value - before) * horizon / elapsed
                row[field] = value + (delta if field == 'book' else max(0, delta))
    predicted = score(projected, current['cohorts'])
    return {'status': 'ok' if actual else 'incomplete_metrics', 'scenario_score': actual,
            'baseline_reset_reason': 'score_rule_changed' if rule_changed else None,
            'previous_prediction_comparison': comparison,
            'next_prediction': {'made_at': now, 'due_at': now + horizon, 'rule': RULE,
                'method': 'recent_metric_rates' if extrapolate else 'cold_start_persistence',
                'projected_own': projected,
                'expected_delta': predicted['total'] - actual['total'] if predicted and actual else None,
                'policy_lift_assumed': 0,
                'limits': 'Savings, rating and star quality held constant; peer positions frozen. Not final rank.'}}
