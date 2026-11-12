"""Known-cost Buyer Saved scenarios for seller candidates, not promised rewards."""
import math
from .buyer_scoring import contribution,MARGIN


def benchmark(journal):
    from .decision_view import fact
    return fact(journal,'shop-buyer-score-benchmark-v23',lambda:_benchmark(journal))


def _benchmark(journal):
    values=sorted(r['saved'] for r in journal.snapshot('board.json',{}).get('buyers',[])
        if r.get('deals',0)>=50 and type(r.get('saved')) in (int,float) and 0<=r['saved']<=1)
    return values[len(values)//2] if values else .7


def target_prices(unit,risk,journal):
    L,C=unit['list_price'],unit['cost'];f=risk['failure_upper'];covered=risk['covered_lower']
    if not 0<=C<L or f>=1:return []
    targets={benchmark(journal),.8,.9}
    return [math.floor(L-(t+f-covered)/(1-f)*(L-C)) for t in targets if 0<=(t+f-covered)/(1-f)<=1]


def completion(base,unit,price,risk,journal,samples):
    L,C=unit['list_price'],unit['cost']
    if not 0<=C<L:return base,{'cost_domain':'unsupported_nonpositive_margin'}
    saved=contribution(price,L,risk['failure_upper'],risk['covered_lower'],score_formula=MARGIN,unit_cost=C)
    threshold=benchmark(journal)
    # A structural prior reacts to new incentives. Mature own quote outcomes
    # dominate it; neither the public median nor this logistic is a guarantee.
    prior=.08+.84/(1+math.exp(max(-30,min(30,-10*(saved-threshold)))))
    value=(max(0,samples)*base+4*prior)/(max(0,samples)+4)
    return value,{'expected_buyer_saved_scenario':saved,'market_saved_benchmark':threshold,
        'structural_completion_prior':prior,'own_quote_samples':samples,'not_causal_or_exact_reward':True}
