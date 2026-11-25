"""Patch 3.6 contributions and explicitly estimated buyer-side margin inputs."""
import math

RULE = 'announced_patch_3_6_margin_saved'


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def contribution(listed, price, cost, *, usable=True, refunded=False):
    """One deal, before the board's sum / (deals + 100) aggregation.

    The normal formula requires a positive margin. Unknown/invalid margins fail
    closed; this is not a claim about the server's cost >= list edge handling.
    """
    if refunded:
        return 0.
    if not usable:
        return -1.
    if (not all(finite(v) for v in (listed, price, cost)) or
            not 0 <= cost < listed or price < 0):
        return -1.
    return min(1., (listed - price) / (listed - cost))


def cost_prior(candidate):
    """Never treat seller text, supply surge, or our own stock as their unit cost.

    The announcement gives food/furniture means. For other goods use the
    published grade mixture and cost-range midpoints: .7*.65+.2*.50+.1*.35.
    These priors omit the unknown unit grade, acquisition time and surge.
    """
    if candidate.get('kind') == 'food':
        ratio, basis = .52, 'patch_3_6_food_mean'
    elif str(candidate.get('category', '')).split('>')[0] == '가구/침구':
        ratio, basis = .61, 'patch_3_6_furniture_bedding_mean'
    else:
        ratio, basis = .59, 'published_goods_grade_mix_midpoints'
    listed = candidate.get('list_price')
    return {'estimated_unit_cost': listed * ratio if finite(listed) and listed > 0 else None,
            'cost_ratio_prior': ratio, 'cost_basis': basis, 'cost_is_observed': False,
            'saved_rule': RULE}


def break_risk(candidate):
    estimate = candidate.get('value_estimate', {})
    risk = estimate.get('break_risk_estimate')
    if risk is None:
        listed = candidate.get('list_price')
        value = estimate.get('expected_value', listed)
        risk = 1 - value / listed if finite(listed) and listed > 0 and finite(value) else 1.
    return min(1., max(0., risk)) if finite(risk) else 1.
