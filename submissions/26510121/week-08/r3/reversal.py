"""Compact terminal-score observations and time-valued contract routing.

No LLM or payment work on this path. Risk bounds remain separate from means.
"""
from datetime import datetime
import math
import time
from .decision_view import fact
from .state import identity
from .decision_view import history

KEY='reversal-policy:v28'

def brief(row):
    if not row:return {'status':'no_fresh_terminal_policy'}
    return {k:row.get(k) for k in ('at','remaining_hours','buyer_paid_last_hour','buyer_forum_max_gain','feed_current','research_age_seconds')} | {
        'saved_scenarios_mean_and_terminal':[[v['future_mean'],v['terminal_saved']] for v in row.get('buyer_saved_scenarios',[])],
        'risk_mode':'validated mature means only; upper stress and full refund cash retained'}

def validate_mean(rows):
    """Temporal holdout with a full 6h10m quality-maturity embargo."""
    from .risk import wilson
    rows=sorted(rows)
    if len(rows)<60:return {'validated':False,'reason':'sparse_mature_cohort'}
    test=rows[int(len(rows)*.7):]
    train=[r for r in rows if r[0]<=test[0][0]-22200]
    if len(train)<30 or len(test)<20:return {'validated':False,'reason':'insufficient_embargoed_split'}
    k=sum(r[1] for r in train);p=(k+1)/(len(train)+2);upper=wilson(k,len(train))
    brier=sum((p-r[1])**2 for r in test)/len(test)
    bound_brier=sum((upper-r[1])**2 for r in test)/len(test)
    observed=sum(r[1] for r in test)/len(test)
    tolerance=max(.05,2*math.sqrt(max(.001,p*(1-p))/len(test)))
    valid=brier<bound_brier and abs(p-observed)<=tolerance
    return {'validated':valid,'train':len(train),'test':len(test),'embargo_seconds':22200,
        'brier':brier,'upper_as_point_brier':bound_brier,'forecast':p,'observed':observed,
        'limitation':'selected temporal predictive check; not causal or shift-proof'}

def fresh(journal,now):
    return 0<=now-journal.get('public_feed_caught_up_at',0)<180

def mean_refund(risk,journal,config,now=None):
    now=time.time() if now is None else now
    mean=risk.get('refund_mean');upper=risk.get('refund_upper',1)
    if (not config.get('finalday_departments_enabled') and config.get('reversal_v28_enabled') and risk.get('warranty_hours',0)>=6 and fresh(journal,now) and risk.get('mean_validated') and risk.get('mature_samples',0)>=40
            and type(mean) in (int,float) and math.isfinite(mean) and 0<=mean<=upper<=1):
        return mean
    return upper

def buyer_failure(risk,warranty,journal,config,now=None):
    now=time.time() if now is None else now
    q=risk['failure_upper'];covered=risk['covered_lower'];mean=risk.get('failure_mean')
    if (config.get('reversal_v28_enabled') and warranty>=6 and fresh(journal,now) and risk.get('mean_validated')
            and risk.get('mature_samples',0)>=40 and type(mean) in (int,float)
            and math.isfinite(mean) and 0<=mean<=q<=1):
        return mean,mean
    return q,covered

def route_key(candidate,journal,config,now=None):
    seconds=max(1,candidate.get('execution_seconds',1))
    old=candidate.get('score',0)/seconds
    if not config.get('reversal_v28_enabled'):return (old,)
    now=time.time() if now is None else now
    delta=candidate.get('expected_delta',{})
    board=fact(journal,'v28-routing-board',lambda:journal.snapshot('board.json',{}))
    gain=None
    if 0<=now-journal.get('board_checked_at',0)<180:
        from .meta import rank_progress
        gain=rank_progress(dict(board,buyer_forum_metric='raw'),'buyer','seochanit',delta)
    if gain is None:gain=delta.get('saved',0)
    if not math.isfinite(gain):gain=0
    candidate.setdefault('evidence',{})['v28_route_utility']={
        'all_progress_scenario':gain,'seconds':seconds,'value_per_second':gain/seconds,
        'scope':'ranking surrogate; not guaranteed future score'}
    return (gain/seconds,delta.get('saved',0)/seconds,old)

def record_skip(journal,order,candidate,assessment,ask,task=None,now=None):
    """Record our own outside-option decision, never label it a peer refusal."""
    now=time.time() if now is None else now
    from .contracts import shop_identity
    row={'order':order['order'],'shop_identity':shop_identity(journal,candidate['shop']),
        'shop':candidate['shop'],'sku':candidate['sku'],'kind':order.get('kind'),
        'use_within_hours':order.get('use_within_hours',0),'ask':ask,
        'grade':assessment.get('required_grade'),'warranty_hours':assessment.get('required_warranty',0),
        'score_formula':assessment['saved_state'].get('score_formula'),'task':task,
        'reason':'own_objective_skip','not_seller_refusal':True}
    journal.add('buyer_economic_skip_v28',row,eid='economic-skip:'+identity([order['order'],candidate['shop'],candidate['sku'],task]),role='buyer',ts=now)

def prune_routes(candidates,order,journal,config,now=None):
    if not config.get('reversal_v28_enabled'):return candidates
    from .contracts import shop_identity
    from .buyer_scoring import same_formula,formula
    now=time.time() if now is None else now
    skips=[r for r in history(journal,'buyer_economic_skip_v28',1000)
        if 0<=now-r['_ts']<900 and same_formula(r,formula(journal))]
    if not skips:return candidates
    paid=history(journal,'purchase',1000);kept=[];paused=[]
    for c in candidates:
        owner=shop_identity(journal,c['shop']);term=c.get('evidence',{}).get('seller_term_prior',{})
        matches={r['order']:r for r in skips if r['shop_identity']==owner and r['sku']==c['sku']
            and r['kind']==order.get('kind') and r.get('grade')==term.get('grade')
            and r.get('warranty_hours',0)==(term.get('warranty_hours') or 0)
            and (order.get('kind')!='food' or r.get('use_within_hours')==order.get('use_within_hours',0))}
        rows=list(matches.values())
        if len(rows)<3:kept.append(c);continue
        last=max(r['_ts'] for r in rows)
        improved=c['reference_price']<min(r['ask'] for r in rows)*.97 or route_key(c,journal,config,now)[0]>0
        recovered=any(p.get('sku')==c['sku'] and p.get('shop')==c['shop'] and p.get('paid_at',p['_ts'])>=last for p in paid)
        probe=int(identity([order['order'],owner,c['sku']])[:8],16)%100<config.get('buyer_contract_probe_percent',8)
        blocked=now-last<120 and not improved and not recovered and not probe
        c.setdefault('evidence',{})['v28_economic_route']={'own_skipped_orders':len(rows),'paused':blocked,
            'until':last+120,'probe':probe,'not_seller_refusal':True}
        (paused if blocked else kept).append(c)
    if not kept and paused:
        c=paused[0];key='v28-economic-probe:'+identity([c['shop'],c['sku']])
        if now-journal.get(key,0)>=60:kept=[c];journal.put(key,now)
    if paused:journal.add('buyer_economic_routes_paused',{'order':order['order'],'paused':len(paused),'kept':len(kept)},role='buyer')
    return kept

def context(journal,now=None):
    now=time.time() if now is None else now
    row=journal.get(KEY,{})
    if (not -2<=now-row.get('at',0)<180 or now>=row.get('expires_at',0)
            or row.get('rules_hash')!=journal.get('market_rules',{}).get('sha256')):return {}
    return row

def update(journal,config,now=None):
    if not config.get('reversal_v28_enabled'):return None
    now=time.time() if now is None else now
    board=journal.snapshot('board.json',{})
    own=next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
    end=config.get('market_end_at');hours=max(0,(datetime.fromisoformat(end).timestamp()-now)/3600) if end else None
    purchases=[r for r in journal.events('purchase',3000) if 0<=now-r.get('paid_at',r['_ts'])<=3600]
    saved=own.get('saved');deals=own.get('deals');scenarios=[]
    if hours is not None and type(saved) in (int,float) and type(deals) is int:
        count=math.floor(hours*len({r.get('order') for r in purchases}))
        for reward in (.85,.9,.95,1.):
            scenarios.append({'future_mean':reward,'deals':count,
                'terminal_saved':(saved*(deals+100)+count*reward)/(deals+100+count)})
    old=journal.get('research-last-result',{})
    row={'at':now,'expires_at':now+180,'rules_hash':journal.get('market_rules',{}).get('sha256'),
        'remaining_hours':hours,'buyer_paid_last_hour':len({r.get('order') for r in purchases}),
        'buyer_saved_scenarios':scenarios,'buyer_forum_max_gain':.3*(1-own.get('forum',0)) if type(own.get('forum')) in (int,float) else None,
        'feed_current':fresh(journal,now),'research_age_seconds':max(0,now-old['at']) if old.get('at') else None,
        'risk_mode':'mature mean for expected fully-covered payoff; upper bound retained for stress and full refund cash',
        'not_a_forecast_or_authorization':True}
    row['id']=identity(row);journal.put(KEY,row)
    journal.add('reversal_policy_prepared',row)
    return row
