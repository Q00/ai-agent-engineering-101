"""A bounded, SKU-specific view of server ledger observations.

Executed prices are historical transactions, not live offers or latent demand.
Peer/forum prose never enters these numerical summaries.
"""
import math
import statistics
import time
from bisect import bisect_left
from collections import Counter

HORIZON = 21600
_BOOK_CACHE = {}

def price_frontier(prices):
    if len(prices)<12:return []
    ordered=sorted(prices);n=len(ordered)
    return [{'quantile':q,'price':ordered[int((n-1)*q)],
        'n_ge':n-bisect_left(ordered,ordered[int((n-1)*q)])} for q in (.75,.9)]


def market_book(journal, catalog, now=None):
    now = time.time() if now is None else now
    seq=journal.get('public_ledger_seq')
    cut=journal.get('market_rules',{}).get('buyer_score_epoch',0)
    cache_key=(str(getattr(journal,'md',id(journal))),len(catalog),seq,int(now//15),cut)
    if seq is not None and cache_key in _BOOK_CACHE:
        return _BOOK_CACHE[cache_key]
    grouped = {}
    for e in journal.events('public_settle', 20000):
        sku = e.get('sku') or e.get('item')
        price = e.get('amount', e.get('price'))
        if (sku not in catalog or e['_ts']<cut or not 0 <= now - e['_ts'] <= HORIZON or
                type(price) not in (int, float) or not math.isfinite(price) or price <= 0):
            continue
        row = grouped.setdefault(sku, {'prices': [], 'external_prices': [], 'buyers': Counter(),
                                      'terms': {}, 'term_refs': {}, 'sellers': set(), 'refs': [], 'grades': {},
                                      'seller_terms':{},
                                      'latest_at': e['_ts']})
        row['prices'].append(price)
        if e.get('buyer') != 'seochanit':
            row['external_prices'].append(price)
            row['buyers'][e.get('buyer', 'unknown')] += 1
        term = (e.get('grade'), e.get('warranty_hours') or 0)
        row['terms'].setdefault(term, []).append(price)
        row['seller_terms'].setdefault((e.get('shop','').rstrip('/'),*term),[]).append(price)
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
                                        'price_frontier':price_frontier(ps),
                                        'refs':row['term_refs'].get((g,w),[])}
                                       for (g,w),ps in row['terms'].items()],
                       'seller_prices':[{'shop':s,'grade':g,'warranty_hours':w,'n':len(ps),'median':statistics.median(ps)}
                           for (s,g,w),ps in sorted(row['seller_terms'].items(),key=lambda pair:-len(pair[1]))[:60]],
                       'low': prices[0], 'high': prices[-1], 'sellers': len(row['sellers']),
                       'per_seller_per_hour': len(prices) / 6 / max(1, len(row['sellers'])),
                       'refs': row['refs'], 'latest_at': row['latest_at'],
                       'grade_medians': {g: statistics.median(ps) for g, ps in row['grades'].items()},
                       'grade_prices': {g: {'n': len(ps), 'p25': sorted(ps)[int((len(ps)-1)*.25)],
                                           'p50': statistics.median(ps)} for g, ps in row['grades'].items()},
                       'note': 'observed 6h trades; availability, quality, warranty and traffic confound comparisons'}
    if seq is not None:
        if len(_BOOK_CACHE)>=8:_BOOK_CACHE.clear()
        _BOOK_CACHE[cache_key]=result
    return result


def buyer_price_actions(ask, cap, product, reference, ratio, turn, max_turns, previous_offer=None,
                        failure_stress=0, covered=False, covered_failure=None, saved_state=None,
                        counter_model=None,target_saved=None,path_model=None):
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
    def expected(price,completion):
        if saved_state and saved_state['metric']=='saved':
            from .buyer_scoring import contribution,terms_delta,score_inputs
            recovery=(failure_stress if covered else 0) if covered_failure is None else covered_failure
            value=contribution(price,L,failure_stress,recovery,**score_inputs(saved_state))
            return completion*value,terms_delta(saved_state,price,L,failure_stress,recovery,completion)
        return completion*surplus(price),{'surplus':completion*surplus(price),'deals':completion}
    if ask <= cap:
        value,change = expected(ask,1)
        actions.append({'id': 'accept', 'price': ask, 'score': value + (0 if saved_state else 1),
                        'completion_probability': 1,
                        'expected_delta': change})
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
    opening_bid=bid;bids={bid}
    if path_model is not None:
        # Near-ask actions let the whole Saved/deal objective price a smaller
        # discount, instead of considering only very deep target rewards.
        bids.update(min(cap,math.floor(ask*f)) for f in (.97,.99)
            if previous_offer is None or min(cap,math.floor(ask*f))>previous_offer)
    if target_saved and saved_state and saved_state.get('score_formula')=='margin_share_v36':
        from .buyer_scoring import saved_price_cap
        recovery=(failure_stress if covered else 0) if covered_failure is None else covered_failure
        for target in target_saved:
            if type(target) not in (int,float) or not 0<target<1:continue
            ceiling,_=saved_price_cap(L,dict(saved_state,saved=target),failure_stress,recovery,margin=0)
            if 0<ceiling<=cap and (previous_offer is None or ceiling>previous_offer):bids.add(ceiling)
    for bid in sorted(bids):
        if not (bid<ask and (previous_offer is None or bid>previous_offer)):continue
        probability = min(.85, .55 + .1 * turn)
        if counter_model is not None:
            from .policy_learning import counter_probability
            probability=counter_probability(counter_model,bid/max(1,ask),probability)
        score,change = expected(bid,probability)
        continuation=None
        if path_model and path_model.get('model'):
            from .negotiation_path import scenario
            continuation=scenario(path_model,bid,ask,cap)
            if continuation:
                probability=continuation['probability']
                values=[expected(p,probability) for p in continuation['prices']]
                score=sum(v[0] for v in values)/len(values) if values else 0
                change={k:sum(v[1].get(k,0) for v in values)/len(values) for k in ('saved','surplus','deals')} if values else {'saved':0,'surplus':0,'deals':0}
        actions.append({'id': 'counter' if bid==opening_bid else 'counter:'+str(bid),
                        'action':'counter','price': bid, 'score': score,
                        'completion_probability': probability,
                        'expected_delta': change,
                        'success_prior': 'shrunk observed first-counter completion; selected cohort' if counter_model is not None else
                            'engineering scenario, not an empirical success estimate'})
        if continuation:
            actions[-1].update(payment_price_scenarios=continuation['prices'],path_expected_seconds=continuation['seconds'],
                continuation_model_id=continuation['id'],success_prior='temporally validated exact-task final-price continuation scenario')
    return actions


def buyer_assessment(product, order, terms, journal, config, *, shop=None):
    from .state import warranty_hours
    cap = min(order['budget'], product['list_price'])
    required, stress, covered = 0, 0, 0
    risk = {}
    conditional=[];declared=None
    if product.get('kind') == 'goods':
        required = min(6, warranty_hours(terms.get('warranty_hours')))
        from .risk import goods_risk
        declared = terms.get('grade', terms.get('claim'))
        declared = declared if declared in ('A', 'B', 'C') else None
        if config.get('buyer_contract_routing_enabled') and declared is None:
            declared='A';conditional.append('grade')
        if config.get('buyer_contract_routing_enabled') and terms.get('warranty_hours') is None:
            required=6;conditional.append('warranty_hours')
        risk = goods_risk(journal, declared, required, config)
        if config.get('buyer_market_quality_enabled'):
            from .buyer_quality import estimate
            risk=estimate(journal,product,declared,required,shop,config)
        from .reversal import buyer_failure
        stress,covered=buyer_failure(risk,required,journal,config)
        if required < 6:
            cap = min(cap, int(product['list_price'] * (1 - stress) / max(.000001, 1 - covered)))
    from .buyer_scoring import state,saved_price_cap,cost_scenario,MARGIN
    current=state(journal,config)
    cost=None
    if current.get('score_formula')==MARGIN:
        # A plain peer cost field is never authoritative. Enforce any grade
        # used in this conditional scenario at the authoritative checkout.
        declared=declared or terms.get('grade',terms.get('claim'))
        declared=declared if declared in ('A','B','C') else None
        from .buyer_costs import scenario
        cost=scenario(journal,product,declared)
        current=dict(current,unit_cost=cost['unit_cost'],cost_samples=cost['cost_samples'])
    target=None;normalized_cap=None
    if current['metric']=='saved':
        normalized_cap,target=saved_price_cap(product['list_price'],current,stress,covered,
                                               config.get('buyer_saved_target_margin',.002))
        if not (config.get('buyer_joint_objective_enabled') and current.get('score_formula')==MARGIN):
            cap=min(cap,normalized_cap)
    negotiation=None
    if config.get('buyer_frontier_v23_enabled'):
        from .policy_learning import packet
        negotiation=packet(journal).get('counter_completion_by_kind',{}).get(product.get('kind'),{})
    path=None;targets=None;ahead=[]
    if config.get('service_contract_v26_enabled'):
        from .negotiation_path import context as path_context
        path=path_context(journal,product,declared,required)
        # Even a cold path model gets the new near-ask candidate grid; it does
        # not get to assert a learned completion probability before validation.
        path=path or {'cold_start':True}
        from .decision_view import fact
        board=fact(journal,'v26-bid-target-board',lambda:journal.snapshot('board.json',{}))
        ahead=[r['saved'] for r in board.get('buyers',[]) if type(r.get('saved')) in (int,float) and r['saved']>current.get('saved',0)]
        targets=sorted(set(config.get('buyer_saved_bid_targets',[.8,.9])+[
            current.get('saved',0)+config.get('buyer_saved_target_margin',.002)]+
            ([min(ahead)+.00001] if ahead else [])))
    if config.get('finalday_departments_enabled') and current.get('metric')=='saved':
        from .departments import buyer_score_department
        targets=buyer_score_department(journal,current,ahead,config)
    return {'cap': cap, 'required_warranty': required, 'failure': stress,
            'negotiation_model':negotiation,
            'required_grade':declared,'conditional_terms':conditional,
            'covered_failure': covered, 'risk_evidence': risk,'saved_state':current,'target_contribution':target,
            'cost_scenario':cost,'saved_reference_cap':normalized_cap,
            'saved_target_is_hard_cap':not (config.get('buyer_joint_objective_enabled') and current.get('score_formula')==MARGIN),
            'bid_targets':targets,'negotiation_path_model':path}


def buyer_terms(product, order, terms, journal, config):
    result = buyer_assessment(product, order, terms, journal, config)
    return result['cap'], result['required_warranty'], result['failure']
