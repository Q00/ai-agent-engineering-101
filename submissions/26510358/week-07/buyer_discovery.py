"""Bounded exploration of reliable suppliers; money decisions stay with the harness."""
import math


def discovery_pool(ranked, search_number, rule):
    if type(rule.get('enabled',False)) is not bool:
        raise ValueError('Discovery enabled must be boolean')
    if not ranked or not rule.get('enabled'):
        return ranked[:8], []
    for key in ('exploration_every','preferred_alternatives','max_candidates','min_recent_reviews'):
        if type(rule.get(key)) is not int or rule[key]<1:
            raise ValueError('Invalid discovery '+key)
    if rule['max_candidates']>8 or rule['preferred_alternatives']>=rule['max_candidates']:
        raise ValueError('Discovery candidate limits exceeded')
    for key in ('max_score_gap','min_recent_average'):
        value=rule.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=5:
            raise ValueError('Invalid discovery '+key)
    if search_number % rule['exploration_every']:
        return ranked[:rule['max_candidates']], []
    best=ranked[0]
    eligible=[]
    for candidate in ranked[1:]:
        past=candidate.get('personal_memory',{})
        if any(row.get('issues') or row.get('status') in ('disputed','untrusted') for row in past.get('recent',[])):
            continue
        if candidate['score']<best['score']-rule['max_score_gap']:
            continue
        ratings=[row['stars'] for row in candidate.get('reviews',[])]
        positive_reviews=(len(ratings)>=rule['min_recent_reviews'] and
                          sum(ratings)/len(ratings)>=rule['min_recent_average'])
        if not (positive_reviews or past.get('signature_verified_by_kit',0)>=1):
            continue
        eligible.append(candidate)
    eligible.sort(key=lambda s:(s.get('personal_memory',{}).get('trades',0),-s['score'],s['name']))
    preferred=eligible[:rule['preferred_alternatives']]
    names=[s['name'] for s in preferred]
    pool=[best,*preferred,*[s for s in ranked[1:] if s['name'] not in names]]
    return pool[:rule['max_candidates']],names
