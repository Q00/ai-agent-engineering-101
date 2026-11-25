"""A small resident-authored plan, not permanent rules or director instructions."""
from __future__ import annotations

import uuid

from context_compaction import bounded,text

SHORT={'type':'string','minLength':1,'maxLength':160}
PLAN_SCHEMA={'type':'object','properties':{
    'scene_id':{'type':['string','null']},
    'previous_plan_id':{'type':['string','null']},
    'update':{'type':'string','enum':['start','keep','revise','finish','cancel']},
    'interpretation':SHORT,
    'intention':SHORT,
    'next_steps':{'type':'array','items':SHORT,'minItems':0,'maxItems':3},
    'action_indexes':{'type':'array','items':{'type':'integer','minimum':0,'maximum':2},'minItems':0,'maxItems':3},
    'reason':SHORT},
    'required':['scene_id','previous_plan_id','update','interpretation','intention','next_steps','action_indexes','reason'],
    'additionalProperties':False}


def planning_view(state):
    plan=state.get('life_plan')
    brief=None if not plan else {
        'id':plan['id'],'scene_id':plan['scene_id'],'status':plan['status'],
        'interpretation':text(plan['interpretation'],55),'intention':text(plan['intention'],70),
        'next_steps':[text(s,40) for s in plan['next_steps'][:2]]}
    history=[{'id':s['id'],'title':text(s['title'],35),
              'facts':[text(f,75) for f in s.get('facts',[])[:1]]}
             for s in state.get('scene_history',[])[-2:]]
    feedback=[{k:text(r.get(k),55) for k in ('kind','status','effect','reason')}
              for r in state.get('last_action_results',[])][-3:]
    return bounded({'previous_plan':brief,'recent_situations':history,'last_action_results':feedback},
                   ('recent_situations','last_action_results'),1400)


def save_plan(world,state,value,actions,now):
    """Validate references, then record this resident's provisional intentions."""
    if not isinstance(value,dict) or set(value)!=set(PLAN_SCHEMA['required']):
        raise ValueError('invalid resident plan fields')
    scene_id=(state.get('scene') or {}).get('id')
    previous=state.get('life_plan');previous_id=previous['id'] if previous else None
    if value['scene_id']!=scene_id or value['previous_plan_id']!=previous_id:
        raise ValueError('stale scene or previous plan reference')
    mode=value['update'];active=previous and previous['status']=='active'
    if mode not in ('start','keep','revise','finish','cancel'):
        raise ValueError('unknown plan update')
    if mode=='start' and active:raise ValueError('an active plan needs keep/revise/finish/cancel')
    if mode!='start' and not active:raise ValueError('there is no active plan to update')
    for key in ('interpretation','intention','reason'):
        if not isinstance(value[key],str) or not value[key].strip() or len(value[key])>160:
            raise ValueError('invalid short plan text')
    steps=value['next_steps'];indexes=value['action_indexes']
    if not isinstance(steps,list) or len(steps)>3 or any(not isinstance(s,str) or not s.strip() or len(s)>160 for s in steps):
        raise ValueError('invalid next steps')
    if (not isinstance(indexes,list) or len(indexes)>3 or any(type(i) is not int or not 0<=i<len(actions) for i in indexes)
            or len(set(indexes))!=len(indexes)):
        raise ValueError('plan references an unavailable action')
    # keep means retain the intention/steps; interpretation and rationale may change.
    intention=previous['intention'] if mode=='keep' else value['intention'].strip()
    next_steps=previous['next_steps'] if mode=='keep' else [s.strip() for s in steps]
    plan={'id':uuid.uuid4().hex,'previous_plan_id':previous_id,'scene_id':scene_id,'update':mode,
          'status':'finished' if mode=='finish' else 'cancelled' if mode=='cancel' else 'active',
          'interpretation':value['interpretation'].strip(),'intention':intention,'next_steps':next_steps,
          'action_indexes':indexes,'reason':value['reason'].strip(),'recorded_at':now,
          'outcomes':[state['last_action_results'][i] for i in indexes]}
    state['life_plan']=plan
    world.event(state,'life_plan_updated',plan['intention'],now,plan=plan,
                note='Resident-authored provisional interpretation/plan, not an external fact or a permanent rule.')
    return plan
