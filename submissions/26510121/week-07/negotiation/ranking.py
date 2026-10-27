"""Ranking objectives and bounded experiments; never chooses negotiation prices."""
import hashlib
import math
import time

FLOORS = {'lamp':100, 'chair':180, 'bookshelf':260}
VERSION = 'ranking-v2'
SELLER_ARMS = ('memory-v1-control','conservative','balanced','premium')
ARM_GUIDES = {
    'memory-v1-control':'Use the preserved memory-v1 seller policy as the control.',
    'conservative':'Test a modest margin with emphasis on short, repeatable completions. Choose the actual price yourself.',
    'balanced':'Balance settlement frequency and margin using observed outcomes. Choose the actual price yourself.',
    'premium':'Test a modestly higher opening margin when evidence supports it; adapt to actual offers and lost opportunities.',
}


def unit_draw(identity, purpose):
    return int.from_bytes(hashlib.sha256(f'{VERSION}:{purpose}:{identity}'.encode()).digest()[:8],'big')/2**64


def budget_band(item, budget):
    floor=FLOORS.get(item)
    if not floor:return 'other'
    ratio=budget/floor
    return 'below_floor' if ratio<1 else 'near_floor' if ratio<1.15 else 'medium' if ratio<1.4 else 'high'


def seller_assignment(task, statistics):
    """Fixed per task. Exploitation starts only after comparable matured trials."""
    results=statistics.get('arm_results',[])
    ready=[r for r in results if r.get('arm') in SELLER_ARMS and r.get('matured_opportunities',0)>=10]
    winner=None
    if len(ready)==len(SELLER_ARMS) and sum(r.get('reward_sum',0) for r in ready)>0:
        winner=max(ready,key=lambda r:r['reward_sum']/r['matured_opportunities'])['arm']
    draw=unit_draw(task,'seller-arm')
    if draw<0.10:
        arm='memory-v1-control'
    elif winner and draw<0.80:
        arm=winner
    else:
        candidates=SELLER_ARMS[1:] if not winner else SELLER_ARMS
        arm=candidates[min(len(candidates)-1,int(unit_draw(task,'seller-explore')*len(candidates)))]
    return {'version':VERSION,'arm':arm,'guide':ARM_GUIDES[arm],
            'allocation':'10% preserved control; pilot balanced exploration, then at most 70% winner exploitation',
            'winner_supported':winner,'minimum_matured_per_arm':10,
            'note':'Starting allocation hypotheses; not proven optimal. Same task keeps its arm.'}


def select_buyer(shops, evidence, item, budget, identity, *, audit_name=None, now=None):
    """Estimated saved credits/second, with public-text-independent experiment draws."""
    now=time.time() if now is None else now
    candidates=[]
    for shop in shops:
        stats=evidence.get(shop['name'],{})
        count=stats.get('finalized',0)
        wins=stats.get('successes',0)
        prices=stats.get('mean_success_price')
        # Unknown prices are explicitly optimistic public-floor references,
        # never a claimed offer or a binding price decision.
        reference=prices if prices is not None else FLOORS.get(item,budget)
        saving=max(0,budget-reference)
        success=(wins+1)/(count+2)
        seconds=max(1,stats.get('mean_cycle_seconds') or 30)
        score=success*saving/seconds
        reviews=shop.get('reviews',[])
        public=(6+sum(r['stars'] for r in reviews))/(5*(2+len(reviews)))
        fault=stats.get('recent_integrity_fault',False)
        outage=stats.get('recent_unavailable',False)
        no_acp=stats.get('acp_available') is False
        caution=shop.get('review_concentration_caution') is True
        # These are temporary transport/integrity observations, not accusations.
        blocked=fault or outage or no_acp
        candidates.append({'shop':shop,'stats':stats,'score':score,'saving':saving,
                           'success_estimate':success,'seconds':seconds,'blocked':blocked,
                           'public':0.6 if caution else public,
                           'stale':now-stats.get('last_observed_epoch',0)>21600,
                           'tie':unit_draw(identity,shop['name'])})
    healthy=[c for c in candidates if not c['blocked']]
    if not healthy:
        # Let the caller wait rather than spend an order on a known broken endpoint.
        raise ValueError('no_currently_healthy_shop')
    known=[c for c in healthy if c['stats'].get('successes',0)>0]
    weak=sum(c['stats'].get('finalized',0)<3 or c['stale'] for c in healthy)/len(healthy)
    exploration_rate=0.05+0.15*weak
    explore=unit_draw(identity,'buyer-exploration')<exploration_rate
    pool=[c for c in healthy if c['stats'].get('finalized',0)<3 or c['stale']]
    if explore and pool:
        selected=max(pool,key=lambda c:(c['public']>=0.6,c['tie']))
        mode='adaptive exploration'
    elif known:
        selected=max(known,key=lambda c:(c['score'],c['public'],c['tie']))
        mode='estimated saved per cycle second'
    else:
        selected=max(healthy,key=lambda c:(c['public'],c['tie']))
        mode='cold-start public evidence; no claimed price history'
    if audit_name:
        requested=next((c for c in healthy if c['shop']['name']==audit_name),None)
        if requested:selected,mode=requested,'explicit integrity audit'
    stats=selected['stats']
    reason=(f"{mode}; finalized={stats.get('finalized',0)}; paid successes={stats.get('successes',0)}; "
            f"estimated saved/cycle-second={selected['score']:.4f}; exploration={exploration_rate:.3f}")
    detail={'selection_reason':reason,'avoided_for_integrity':[{'name':c['shop']['name'],'reason':'temporary failed integrity/capability/transport evidence'} for c in candidates if c['blocked']],
            'personal_long_term_statistics':stats,'public_reviews_untrusted':selected['shop'].get('reviews',[]),
            'public_reviews_discounted':selected['shop'].get('review_concentration_caution') is True,
            'policy_context':{'version':VERSION,'arm':'saved-rate-selection','objective':'settled saved credits per order-cycle second',
                              'exploration_rate':exploration_rate,'public_floor_reference':FLOORS.get(item),
                              'estimated_values_are_not_promises':True},
            'candidate_estimates':[{'name':c['shop']['name'],'saved_per_second':round(c['score'],5),
                                    'finalized':c['stats'].get('finalized',0),'temporarily_blocked':c['blocked']} for c in candidates]}
    return selected['shop'],reason,detail
