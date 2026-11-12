"""Versioned Saved accounting; hidden acquisition costs remain scenarios."""
import math
import re
import time

LEGACY = 'list_discount_v34'
MARGIN = 'margin_share_v36'


def formula(journal):
    return journal.get('market_rules', {}).get('buyer_saved_formula', LEGACY)


def same_formula(row, version):
    return row.get('score_formula', LEGACY) == version


def cost_scenario(product, grade=None):
    """Public supply bounds, NOT a seller's unverified cost claim.

    Food can only degrade, so a current A/B implies supply grade at least A/B.
    Caps are conditional on the grade being verified again at checkout. Surge
    is nonnegative; omit it from the lower scenario. The official behavior for
    C>=L is unspecified: these scenarios describe positive-margin units only.
    """
    low = {'A': .55, 'B': .40, 'C': .25}.get(grade, .25)
    return {'unit_cost': math.floor(product['list_price']*low),
            'source': 'public_supply_lower_scenario', 'exact': False,
            'conditional_grade': grade, 'positive_margin_assumption': True,
            'c_ge_list_behavior': 'not_specified_by_public_rules'}


def score_inputs(current):
    return {'score_formula': current.get('score_formula', LEGACY),
            'unit_cost': current.get('unit_cost'),'cost_samples':current.get('cost_samples')}


def joint_actions(actions):
    """Explicit outside option: a high average is a reference, never a veto.

    Each action carries the official Saved/deal deltas. Forum/post deltas are
    zero here: a payment does not guarantee either a useful post or a vote.
    Their actual actions are evaluated by the background publication lane.
    """
    return actions+[{'id':'continue_search','score':0,'execution_seconds':1,
        'expected_delta':{'saved':0,'deals':0,'forum':0,'posts':0},
        'reason':'retain current score; inspect another offer or finish without purchase'}]


def metric(board, rules=None):
    named=(rules or {}).get('buyer_metric') or board.get('buyer_metric')
    if named in {'saved','surplus'}:return named
    own=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
    value=own.get('saved')
    return 'saved' if type(value) in (int,float) and -1<=value<=1 else 'surplus'


def state(journal,config):
    from .decision_view import fact
    return dict(fact(journal, ('buyer-state', bool(config.get('buyer_patch34_enabled')), config.get('buyer_forum_metric'), formula(journal)), lambda: _state(journal, config)))


def _state(journal,config):
    board=journal.snapshot('board.json',{})
    own=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
    rules=journal.get('market_rules',{})
    saved=config.get('buyer_patch34_enabled') and metric(board,rules)=='saved'
    return {'metric':'saved' if saved else 'surplus','saved':own.get('saved',0) or 0,
            'score_formula':formula(journal),
            'forum_metric':rules.get('buyer_forum_metric',config.get('buyer_forum_metric','percentile')),
            'deals':own.get('deals',0) or 0}


def contribution(price,list_price,failure=0,covered_failure=0, *, score_formula=LEGACY, unit_cost=None,cost_samples=None):
    """Healthy: 1-P/L; broken uninsured: -1; confirmed full refund: 0.

    For decisions failure is an upper risk scenario and coverage a lower one.
    """
    if list_price<=0 or not 0<=covered_failure<=failure<=1:
        raise ValueError('invalid_saved_inputs')
    if score_formula == MARGIN:
        if cost_samples is not None:
            if not isinstance(cost_samples,list) or not 1<=len(cost_samples)<=9:
                raise ValueError('invalid_cost_scenarios')
            return sum(contribution(price,list_price,failure,covered_failure,
                score_formula=MARGIN,unit_cost=c) for c in cost_samples)/len(cost_samples)
        if type(unit_cost) not in (int,float) or not math.isfinite(unit_cost) or not 0<=unit_cost<list_price:
            raise ValueError('cost_unknown_or_nonpositive_margin')
        healthy=min(1,(list_price-price)/(list_price-unit_cost))
    elif score_formula == LEGACY:
        healthy=1-price/list_price
    else:raise ValueError('unknown_saved_formula')
    return (1-failure)*healthy-(failure-covered_failure)


def delta(current,returned,completion=1):
    if not 0<=completion<=1:raise ValueError('invalid_completion')
    return completion*(returned-current['saved'])/(current['deals']+101)


def terms_delta(current,price,list_price,failure=0,covered=0,completion=1):
    absolute=(1-failure)*list_price-price+covered*price
    result={'surplus':completion*absolute,'deals':completion}
    if current['metric']=='saved':
        result['saved']=delta(current,contribution(price,list_price,failure,covered,**score_inputs(current)),completion)
    return result


def saved_price_cap(list_price,current,failure,covered,margin=.002):
    target=max(0,min(1,current['saved']+margin))
    if failure>=1:return 0,target
    required=(target+failure-covered)/(1-failure)
    version=current.get('score_formula',LEGACY)
    if version==MARGIN:
        if current.get('cost_samples') is not None:
            low,high=0,math.floor(list_price)
            if contribution(0,list_price,failure,covered,**score_inputs(current))<target:return 0,target
            while low<high:
                mid=(low+high+1)//2
                if contribution(mid,list_price,failure,covered,**score_inputs(current))>=target:low=mid
                else:high=mid-1
            return low,target
        cost=current.get('unit_cost')
        if type(cost) not in (int,float) or not math.isfinite(cost) or not 0<=cost<list_price:
            raise ValueError('cost_unknown_or_nonpositive_margin')
        if required>1:return 0,target
        cap=list_price-required*(list_price-cost)
    elif version==LEGACY:cap=list_price*(1-required)
    else:raise ValueError('unknown_saved_formula')
    return max(0,math.floor(cap)),target


def remember_refusal(journal,order,shop,receipt,reasons,now=None):
    """Remember only proven pre-payment unit refusals, never transport ambiguity."""
    now=time.time() if now is None else now
    reason='; '.join(reasons)
    if any(r.startswith(('payment failed:','payment not completed:')) for r in reasons):return
    unit=receipt.get('unit')
    if not unit:return
    expiry=None
    match=re.search(r'not paying: ([\d.]+) h of shelf life left',reason)
    if match:expiry=now+float(match[1])*3600
    elif 'not in stock for this order' not in reason:return
    rows=[r for r in journal.get('buyer_offer_blocks',[]) if r['until']>now and r['unit']!=unit]
    rows.append({'unit':unit,'shop':shop,'sku':receipt.get('item'),'expires_at':expiry,
                 'until':min(expiry,now+3600) if expiry else now+120})
    journal.put('buyer_offer_blocks',rows[-300:])
    journal.add('buyer_offer_refusal',{'unit':unit,'shop':shop,'sku':receipt.get('item'),
                                    'reason':'shelf_life' if expiry else 'stockout'},role='buyer')


def blocked_units(journal,shop,sku,order,now=None):
    now=time.time() if now is None else now
    return [r['unit'] for r in journal.get('buyer_offer_blocks',[]) if r['until']>now and
        r['shop']==shop and r['sku']==sku and (not r.get('expires_at') or
        r['expires_at']<now+order.get('use_within_hours',0)*3600+30)]


def route_availability(journal,shop,sku,order,now=None):
    """Recent proven refusals soften routing, never blacklist a whole seller.

    A unit unsuitable for a 24h need may still serve a 1h need. Replenishment
    can change availability, hence short memory and a strictly positive prior.
    """
    now=time.time() if now is None else now
    blocks=blocked_units(journal,shop,sku,order,now)
    return {'factor':1/(1+.6*min(3,len(blocks))), 'refused_units':len(blocks),
            'source':'need-scoped, expiring pre-payment refusals; availability scenario'}


def offer_ineligible(terms,order,config,now=None):
    """Reject explicit impossible terms before another negotiation or checkout."""
    now=time.time() if now is None else now
    if terms.get('cert') is False or terms.get('certificate') is False:return 'certificate_unavailable'
    expiry=terms.get('expires_at')
    left=terms.get('hours_left')
    if (order.get('kind')=='food' and type(left) in (int,float) and math.isfinite(left) and
            left<(order.get('use_within_hours') or 0)+config.get('checkout_buffer_seconds',30)/3600):
        return 'shelf_life'
    if (order.get('kind')=='food' and type(expiry) in (int,float) and math.isfinite(expiry) and
            expiry<now+order.get('use_within_hours',0)*3600+config.get('checkout_buffer_seconds',30)):
        return 'shelf_life'
    return None
