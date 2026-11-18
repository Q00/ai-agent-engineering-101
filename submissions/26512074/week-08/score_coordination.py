"""Use the actual student scoreboard gaps to coordinate Book and Revenue growth."""
from pathlib import Path
from optimizer import atomic
from bisect import bisect_left,bisect_right
import math

WEIGHTS={'shop':{'book':.5,'revenue':.3,'stars_rank':.2},'buyer':{'saved':.5,'forum':.3,'deals':.1,'posts':.1}}


def saved_trades(saved,deals,target,expected_score):
    """Solve the cumulative Saved denominator exactly, with fixed future q."""
    if target<=saved:return 0
    if expected_score<=target:return None
    return math.floor((target-saved)*(deals+100)/(expected_score-target))+1


def required_average(saved,deals,target,new_deals):
    if new_deals<=0:return None
    return (target*(deals+100+new_deals)-saved*(deals+100))/new_deals


def progress(values,value):
    """Interpolated peer-rank progress, not a claim of awarded ALL points."""
    samples=sorted(values);values=sorted(set(samples))
    if len(values)<2:return None
    def rank(x):return (bisect_left(samples,x)+bisect_right(samples,x)-1)/2/(len(samples)-1)
    if value<=values[0]:return rank(values[0])
    if value>=values[-1]:return rank(values[-1])
    i=bisect_right(values,value)-1;lo,hi=values[i:i+2]
    return rank(lo)+(value-lo)/(hi-lo)*(rank(hi)-rank(lo))


def all_value(targets,side,deltas,now):
    if not targets or not 0<=now-targets.get('at',0)<900:return None
    components=targets.get('components',{}).get(side,{})
    total=0.;used=False
    for field,delta in deltas.items():
        if field not in WEIGHTS[side] or field not in components:continue
        c=components[field];current=c['current']
        if field=='forum':gain=min(1,max(0,current+delta))-current
        else:
            samples=list(c['values'])
            if targets.get('peer_only_marginal') and current in samples:samples.remove(current)
            before=progress(samples,current);after=progress(samples,current+delta)
            if before is None or after is None:continue
            gain=after-before
        total+=WEIGHTS[side][field]*gain;used=True
    return total if used else None


def boundary_value(targets,side,deltas,now):
    """Count actual peer crossings alongside interpolation; peers held fixed."""
    if not targets or not 0<=now-targets.get('at',0)<900:return None
    total=0.;used=False
    for name,delta in deltas.items():
        c=targets.get('components',{}).get(side,{}).get(name)
        if not c:continue
        old=c['current'];values=list(c['values'])
        if name=='forum':gain=min(1,max(0,old+delta))-old
        else:
            if old in values:values.remove(old)
            if not values:continue
            rank=lambda x:(sum(v<x for v in values)+.5*sum(v==x for v in values))/len(values)
            gain=rank(old+delta)-rank(old)
        total+=WEIGHTS[side][name]*gain;used=True
    return total if used else None


def project_targets(targets,side,deltas):
    import copy
    result=copy.deepcopy(targets)
    for field,delta in deltas.items():
        c=result.get('components',{}).get(side,{}).get(field)
        if not c:continue
        old=c['current'];values=c['values']
        if old in values:values.remove(old)
        c['current']=old+delta;values.append(c['current']);values.sort()
    return result


def snapshot_targets(md,board,now):
    previous=read_targets(md)
    students={p.get('shop',{}).get('name') for p in board.get('players',[]) if p.get('student')}
    rows=[s for s in board.get('shops',[]) if s.get('name') in students]
    own=next((s for s in board.get('shops',[]) if s.get('name')=='totato2203-r3'),None)
    if not own:return {}
    result=dict(at=now,scope='students' if rows else 'public',targets={})
    from strategy_policy import load
    result['peer_only_marginal']=bool(load(md).get('peer_only_marginal_enabled'))
    result['components']={}
    for side,table in [('shop','shops'),('buyer','buyers')]:
        names={(p.get(side) or {}).get('name') for p in board.get('players',[]) if p.get('student')}
        peers=[r for r in board.get(table,[]) if r.get('name') in names] if names else board.get(table,[])
        current=next((r for r in board.get(table,[]) if r.get('name')=='totato2203-r3'),{})
        result['components'][side]={field:dict(current=current[field],values=sorted(r[field] for r in peers if isinstance(r.get(field),(int,float)) and math.isfinite(r[field])))
                                    for field in WEIGHTS[side] if isinstance(current.get(field),(int,float))}
    result['interpretation']='Interpolated class peer-rank progress; forum actual value. Not awarded or causal ALL gains.'
    velocities={}
    elapsed=(now-previous.get('at',now))/3600
    if elapsed>0:
        for side,fields in result['components'].items():
            for field,c in fields.items():
                old=previous.get('components',{}).get(side,{}).get(field,{}).get('current')
                if old is not None:
                    key=f'{side}:{field}';raw=(c['current']-old)/elapsed
                    velocities[key]=.8*previous.get('velocities_per_hour',{}).get(key,raw)+.2*raw
    result['velocities_per_hour']=velocities
    players=sorted((p for p in board.get('players',[]) if p.get('student')),key=lambda p:-p.get('student_total',0))
    player=next((p for p in players if p.get('player')=='totato2203'),None)
    plans=[]
    if player:
        result['class_player']=dict(rank=players.index(player)+1,shop=player.get('student_shop'),buyer=player.get('student_buyer'),total=player['student_total'])
        buyer_map={r['name']:r for r in board.get('buyers',[])}
        result['competitive_forum_targets']=[dict(owner=p['player'],author=(p.get('buyer') or {}).get('name'),gap=p['student_total']-player['student_total'],
            forum=buyer_map.get((p.get('buyer') or {}).get('name'),{}).get('forum')) for p in players if p['student_total']>player['student_total']]
        for peer in players:
            gap=peer['student_total']-player['student_total']
            if gap<=0:continue
            routes=[]
            for side,fields in result['components'].items():
                for field,c in fields.items():
                    current=c['current'];weight=WEIGHTS[side][field]
                    if field=='forum':goal=current+(gap+1e-6)/weight
                    else:
                        goal=None
                        for candidate in sorted(set(c['values'])):
                            if candidate<=current:continue
                            change=boundary_value(result,side,{field:candidate-current+max(1e-9,abs(candidate)*1e-12)},now)
                            if change is not None and change>gap:
                                goal=candidate+max(1e-9,abs(candidate)*1e-12);break
                    if goal is None or field=='forum' and goal>1:continue
                    velocity=velocities.get(f'{side}:{field}',0)
                    routes.append(dict(side=side,field=field,additional=goal-current,target_value=goal,
                        estimated_hours=(goal-current)/velocity if velocity>0 else None,
                        peers_fixed=True,estimated_not_awarded=True))
            plans.append(dict(player=peer['player'],gap=gap,routes=sorted(routes,key=lambda r:r['estimated_hours'] if r['estimated_hours'] is not None else math.inf)))
    result['overtake_candidates']=sorted(plans,key=lambda p:min((r['estimated_hours'] for r in p['routes'] if r['estimated_hours'] is not None),default=math.inf))
    from capital_policy import remaining_hours
    result['remaining_round_hours']=remaining_hours(now)
    from optimizer import read
    kind_scores=read(Path(md)/'buyer-opportunity.json',{}).get('kind_scores',{})
    deals=result['components'].get('buyer',{}).get('deals',{}).get('current',0)
    boundaries=[]
    for side,fields in result['components'].items():
        for field,c in fields.items():
            higher=[v for v in c['values'] if v>c['current']]
            if field=='forum':continue
            if not higher:continue
            target=min(higher)+max(1e-9,abs(min(higher))*1e-12)
            entry=dict(side=side,field=field,current=c['current'],target=target,additional=target-c['current'],
                       estimated_all=boundary_value(result,side,{field:target-c['current']},now),peers_fixed=True)
            if field=='saved':
                entry['trades_by_kind']={k:dict(q=v['mean'],observations=v['n'],trades=saved_trades(c['current'],deals,target,v['mean'])) for k,v in kind_scores.items()}
                rates={'official_deals':velocities.get('buyer:deals'),'public_settlements':read(Path(md)/'buyer-opportunity.json',{}).get('public_settlements_per_hour')}
                entry['remaining_requirements']={name:dict(rate=r,new_deals=int(max(0,r)*result['remaining_round_hours']),required_average=required_average(c['current'],deals,target,int(max(0,r)*result['remaining_round_hours'])),source=name) for name,r in rates.items() if r is not None and r>0}
            boundaries.append(entry)
    result['next_boundaries']=boundaries
    for candidate in result['overtake_candidates']:
        for route in candidate['routes']:route['estimated_before_round_end']=route['estimated_hours'] is not None and route['estimated_hours']<=result['remaining_round_hours']
        def combined(hours):
            deltas={side:{field:max(0,velocities.get(f'{side}:{field}',0))*hours for field in fields} for side,fields in result['components'].items()}
            gain=sum(boundary_value(result,side,changes,now) or 0 for side,changes in deltas.items())
            return gain,deltas
        horizon=result['remaining_round_hours'];gain,_=combined(horizon)
        if gain>candidate['gap']:
            lo,hi=0.,horizon
            for _ in range(20):
                mid=(lo+hi)/2
                if combined(mid)[0]>candidate['gap']:hi=mid
                else:lo=mid
            predicted,deltas=combined(hi)
            candidate['combined_route']=dict(estimated_hours=hi,component_deltas=deltas,stepped_gain=predicted,
                model='recent observed velocity, parallel components, peers held fixed; not awarded points')
        else:candidate['combined_route']=None
    result['overtake_candidates'].sort(key=lambda p:min([p['combined_route']['estimated_hours']] if p['combined_route'] else [r['estimated_hours'] for r in p['routes'] if r['estimated_hours'] is not None] or [math.inf]))
    for field in ('book','revenue'):
        value=own.get(field,0)
        higher=sorted((s for s in rows or board.get('shops',[]) if isinstance(s.get(field),(int,float)) and s[field]>value),key=lambda s:s[field])
        next_shop=higher[0] if higher else None
        result['targets'][field]=dict(current=value,next_shop=next_shop['name'] if next_shop else None,
                                     gap=next_shop[field]-value if next_shop else None)
    atomic(Path(md)/'score-targets.json',result)
    return result


def read_targets(md):
    from optimizer import read
    return read(Path(md)/'score-targets.json',{})


def procurement_value(candidate,targets,now):
    units=candidate['contribution']/max(1e-9,candidate.get('expected_net',candidate['contribution']))
    modeled=all_value(targets,'shop',dict(book=candidate['contribution'],revenue=candidate.get('expected_sale',0)*units),now)
    if modeled is not None:return modeled
    if not targets or not 0<=now-targets.get('at',0)<900:return candidate['contribution']
    t=targets.get('targets',{});book=t.get('book',{}).get('gap');revenue=t.get('revenue',{}).get('gap')
    # With no higher peer in a component, prioritize remaining rank opportunities.
    book_gain=.5*candidate['expected_net']/max(1,book) if book is not None else 0
    revenue_gain=.3*candidate['expected_sale']/max(1,revenue) if revenue is not None else 0
    if not book_gain and not revenue_gain:return candidate['contribution']
    units=candidate['contribution']/max(1e-9,candidate['expected_net'])
    return units*(book_gain+revenue_gain)
