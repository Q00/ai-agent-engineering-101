"""A bounded, SKU-specific view of server ledger observations.

Executed prices are historical transactions, not live offers or latent demand.
Peer/forum prose never enters these numerical summaries.
"""
import math
import statistics
import time
from collections import Counter

HORIZON = 21600


def market_book(journal, catalog, now=None):
    now = time.time() if now is None else now
    grouped = {}
    for e in journal.events('public_settle', 20000):
        sku = e.get('sku') or e.get('item')
        price = e.get('amount', e.get('price'))
        if (sku not in catalog or not 0 <= now - e['_ts'] <= HORIZON or
                type(price) not in (int, float) or not math.isfinite(price) or price <= 0):
            continue
        row = grouped.setdefault(sku, {'prices': [], 'external_prices': [], 'buyers': Counter(),
                                      'terms': {}, 'term_refs': {}, 'sellers': set(), 'refs': [], 'grades': {},
                                      'latest_at': e['_ts']})
        row['prices'].append(price)
        if e.get('buyer') != 'seochanit':
            row['external_prices'].append(price)
            row['buyers'][e.get('buyer', 'unknown')] += 1
        term = (e.get('grade'), e.get('warranty_hours') or 0)
        row['terms'].setdefault(term, []).append(price)
        row['sellers'].add(e.get('shop', 'unknown'))
        row['grades'].setdefault(e.get('grade', 'unknown'), []).append(price)
        # Journal IDs preserve the server sequence. Never invent one from a price.
        prefix, _, seq = e.get('_event_id', '').partition(':')
        if prefix == 'public' and seq.isdigit() and len(row['refs']) < 4:
            row['refs'].append(int(seq))
        if prefix=='public' and seq.isdigit() and len(row['term_refs'].get(term,[]))<3:
            row['term_refs'].setdefault(term,[]).append(int(seq))
    result = {}
    for sku, row in grouped.items():
        prices = sorted(row['prices'])
        result[sku] = {'samples': len(prices), 'median': statistics.median(prices),
                       'external_samples': len(row['external_prices']),
                       'external_median': statistics.median(row['external_prices']) if row['external_prices'] else None,
                       'external_buyers': row['buyers'].most_common(5),
                       'term_prices': [{'grade': g, 'warranty_hours': w, 'n': len(ps), 'median': statistics.median(ps),
                                        'refs':row['term_refs'].get((g,w),[])}
                                       for (g,w),ps in row['terms'].items()],
                       'low': prices[0], 'high': prices[-1], 'sellers': len(row['sellers']),
                       'per_seller_per_hour': len(prices) / 6 / max(1, len(row['sellers'])),
                       'refs': row['refs'], 'latest_at': row['latest_at'],
                       'grade_medians': {g: statistics.median(ps) for g, ps in row['grades'].items()},
                       'grade_prices': {g: {'n': len(ps), 'p25': sorted(ps)[int((len(ps)-1)*.25)],
                                           'p50': statistics.median(ps)} for g, ps in row['grades'].items()},
                       'note': 'observed 6h trades; availability, quality, warranty and traffic confound comparisons'}
    return result


def buyer_price_actions(ask, cap, product, reference, ratio, turn, max_turns, previous_offer=None,
                        failure_stress=0, covered=False, covered_failure=None):
    """Monotone concessions with a final feasible acceptance; no repeated bids.

    Completion priors below are explicit engineering scenarios. They are not
    measured counterfactual success rates. Safety caps are applied by the caller.
    """
    actions = []
    if cap < 1:
        return actions
    L = product['list_price']
    def surplus(price):
        recovery = (failure_stress if covered else 0) if covered_failure is None else covered_failure
        return (1 - failure_stress) * L - price + price * recovery
    if ask <= cap:
        value = surplus(ask)
        actions.append({'id': 'accept', 'price': ask, 'score': value + 1,
                        'completion_probability': 1,
                        'expected_delta': {'surplus': value, 'deals': 1}})
    if turn >= max_turns - 1:
        return actions
    opening = min(ask * ratio, reference * ratio / .92)
    if previous_offer is None:
        bid = max(1, min(cap, int(opening)))
    else:
        # Close half the remaining distance to a feasible ask, never lower or
        # repeat our previous bid even when the peer repeats/decreases its ask.
        destination = min(ask, cap)
        bid = min(cap, max(previous_offer + 1, math.ceil((previous_offer + destination) / 2)))
    if bid < ask and (previous_offer is None or bid > previous_offer):
        probability = min(.85, .55 + .1 * turn)
        score = probability * surplus(bid)
        actions.append({'id': 'counter', 'price': bid, 'score': score,
                        'completion_probability': probability,
                        'expected_delta': {'surplus': score, 'deals': probability},
                        'success_prior': 'engineering scenario, not an empirical success estimate'})
    return actions


def buyer_assessment(product, order, terms, journal, config):
    from .state import warranty_hours
    cap = min(order['budget'], product['list_price'])
    required, stress, covered = 0, 0, 0
    risk = {}
    if product.get('kind') == 'goods':
        required = min(6, warranty_hours(terms.get('warranty_hours')))
        from .risk import goods_risk
        declared = terms.get('grade', terms.get('claim'))
        declared = declared if declared in ('A', 'B', 'C') else None
        risk = goods_risk(journal, declared, required, config)
        stress, covered = risk['failure_upper'], risk['covered_lower']
        if required < 6:
            cap = min(cap, int(product['list_price'] * (1 - stress) / max(.000001, 1 - covered)))
    return {'cap': cap, 'required_warranty': required, 'failure': stress,
            'covered_failure': covered, 'risk_evidence': risk}


def buyer_terms(product, order, terms, journal, config):
    result = buyer_assessment(product, order, terms, journal, config)
    return result['cap'], result['required_warranty'], result['failure']
