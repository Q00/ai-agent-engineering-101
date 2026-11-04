"""A bounded, SKU-specific view of server ledger observations.

Executed prices are historical transactions, not live offers or latent demand.
Peer/forum prose never enters these numerical summaries.
"""
import math
import statistics
import time

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
        row = grouped.setdefault(sku, {'prices': [], 'sellers': set(), 'refs': [], 'grades': {},
                                      'latest_at': e['_ts']})
        row['prices'].append(price)
        row['sellers'].add(e.get('shop', 'unknown'))
        row['grades'].setdefault(e.get('grade', 'unknown'), []).append(price)
        # Journal IDs preserve the server sequence. Never invent one from a price.
        prefix, _, seq = e.get('_event_id', '').partition(':')
        if prefix == 'public' and seq.isdigit() and len(row['refs']) < 4:
            row['refs'].append(int(seq))
    result = {}
    for sku, row in grouped.items():
        prices = sorted(row['prices'])
        result[sku] = {'samples': len(prices), 'median': statistics.median(prices),
                       'low': prices[0], 'high': prices[-1], 'sellers': len(row['sellers']),
                       'per_seller_per_hour': len(prices) / 6 / max(1, len(row['sellers'])),
                       'refs': row['refs'], 'latest_at': row['latest_at'],
                       'grade_medians': {g: statistics.median(ps) for g, ps in row['grades'].items()},
                       'note': 'observed 6h trades; availability, quality, warranty and traffic confound comparisons'}
    return result


def buyer_price_actions(ask, cap, product, reference, ratio, turn, max_turns, previous_offer=None,
                        failure_stress=0, covered=False):
    """Monotone concessions with a final feasible acceptance; no repeated bids.

    Completion priors below are explicit engineering scenarios. They are not
    measured counterfactual success rates. Safety caps are applied by the caller.
    """
    actions = []
    L = product['list_price']
    def surplus(price):
        return (1 - failure_stress) * L - price + (price * failure_stress if covered else 0)
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


def buyer_terms(product, order, terms, journal, config):
    from .state import warranty_hours
    cap = min(order['budget'], product['list_price'])
    required, stress = 0, 0
    if product.get('kind') == 'goods':
        required = 6 if warranty_hours(terms.get('warranty_hours')) >= 6 else 0
        from .meta import observed_breaks
        risk = observed_breaks(journal, product.get('sku') or terms.get('sku'))
        stress = config['unknown_goods_failure_stress']
        if risk['mature_samples'] >= 10:
            n = risk['mature_samples']
            stress = min(1, (risk['broken'] + 1) / (n + 2) + 1 / (n + 2) ** .5)
        if not required:
            cap = min(cap, int(product['list_price'] * (1 - stress)))
    return cap, required, stress
