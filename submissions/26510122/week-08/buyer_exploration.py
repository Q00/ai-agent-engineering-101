"""An operator-controlled epsilon mixture around the buyer model policy.

Exploration samples uniformly from eligible shop/SKU pairs before the normal
shortlist. The model still decides the opening price and later negotiations.
No learned value table or automatic epsilon update is claimed.
"""
import math
import random

DEFAULT_EPSILON=0.10


def probability(value):
    if isinstance(value,bool):raise ValueError('epsilon must be a number between 0 and 1')
    result=float(value)
    if not math.isfinite(result) or not 0<=result<=1:
        raise ValueError('epsilon must be a finite number between 0 and 1')
    return result


def plan(candidates,epsilon=DEFAULT_EPSILON,rng=None):
    epsilon=probability(epsilon)
    if not candidates:raise ValueError('cannot select from an empty eligible pool')
    if len(candidates)==1:
        return {'mode':'single_candidate','epsilon':epsilon,'draw':None,'forced_candidate_id':None,
                'eligible_count':1,'uniform_exploration_probability_per_pair':0.}
    rng=rng or random.SystemRandom()
    draw=rng.random()
    explore=draw<epsilon
    target=rng.choice(candidates) if explore else None
    return {'mode':'explore' if explore else 'model','epsilon':epsilon,'draw':draw,
            'forced_candidate_id':target['id'] if target is not None else None,
            'eligible_count':len(candidates),'uniform_exploration_probability_per_pair':epsilon/len(candidates)}
