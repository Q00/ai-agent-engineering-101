"""Hierarchical price evidence and short, consistent inquiry-to-purchase offers."""
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from optimizer import read,atomic
from score_coordination import all_value


def contract_key(sku,grade,hours):return f'{sku}:{grade}:{float(hours or 0):g}'


def update_contracts(md,rows,now,policy,targets,connection):
    """Same SKU/grade/exact warranty and shared market-time comparisons."""
    import sqlite3
    from contextlib import closing
    from strategy_feedback import sampling_stats
    old=read(md/'price-hierarchy.json',{});champions=old.get('contract_champions',{});diagnostics={};changes=[]
    with closing(sqlite3.connect('file:'+(md/'optimization.sqlite3').as_posix()+'?mode=ro',uri=True,timeout=5)) as c:
        trials={r[0]:dict(bid=r[1] is not None,agreed=r[2] is not None) for r in c.execute('select task,first_offer,agreed from trials where at>?',(now-policy['price_feedback_window_seconds'],))}
    initial={r['task']:r['inquiry'] for r in connection.execute('select task,inquiry from price_exposures where at>?',(now-policy['price_feedback_window_seconds'],))}
    states={(r['sku'],r['grade']):dict(r) for r in connection.execute('select * from price_state')}
    intended=defaultdict(set)
    for r in connection.execute('select sku,grade,hours,epoch,unit from price_contract_sessions where at>?',(now-policy['price_feedback_window_seconds'],)):
        intended[r['sku'],r['grade'],r['epoch'],r['unit']].add(r['hours'])
    grouped=defaultdict(list);funnel=defaultdict(lambda:dict(quote=0,bid=0,receipt_intent=0,paid=0,discovery_only=0,immature=0))
    for r in rows:
        key=contract_key(r['sku'],r['grade'],r['warranty']);stage=funnel[key];trial=trials.get(r['task'],{})
        stage['quote']+=1;stage['bid']+=int(trial.get('bid',False));stage['receipt_intent']+=int(trial.get('agreed',False));stage['paid']+=int(r.get('paid') is not None)
        terms=intended.get((r['sku'],r['grade'],int(r['at']//policy.get('quote_episode_seconds',180)),r['unit']))
        if terms and r['warranty'] not in terms:
            stage['contract_changed_after_quote']=stage.get('contract_changed_after_quote',0)+1;continue
        discovery=initial.get(r['task'],r['inquiry']) and not trial.get('bid')
        if discovery:stage['discovery_only']+=1;continue
        horizon=min(6,r['warranty'] or 0)*3600+300
        if r['at']+horizon>now or r.get('settled_at') is not None and r['settled_at']+horizon>now:
            stage['immature']+=1;continue
        net=(r['paid']-r['cost']-r['refunds']-r['storage']) if r.get('paid') is not None else 0
        net-=r.get('expiry_loss',0)
        value=all_value(targets,'shop',dict(book=net,revenue=r.get('paid') or 0),now)
        grouped[key].append(dict(arm=r['arm'],value=value if value is not None else net/max(1,r['cost']),net=net,revenue=r.get('paid') or 0,paid=r.get('paid') is not None,bucket=int(r['at']//policy['lookback_seconds']),row=r))
    for key,entries in grouped.items():
        row=entries[0]['row'];legacy=states.get((row['sku'],row['grade']),{})
        champion=champions.get(key,{}).get('arm',legacy.get('arm',1.));arms=defaultdict(list)
        for e in entries:arms[e['arm']].append(e)
        estimates={};supported=[]
        for arm,values in arms.items():
            mean,var=sampling_stats([v['value'] for v in values],[v['paid'] for v in values])
            estimates[str(arm)]=dict(arm=arm,n=len(values),paid=sum(v['paid'] for v in values),value=mean,se=math.sqrt(var),net=statistics.mean(v['net'] for v in values),revenue=statistics.mean(v['revenue'] for v in values),conversion=sum(v['paid'] for v in values)/len(values))
            if arm==champion:continue
            common={v['bucket'] for v in values}&{v['bucket'] for v in arms.get(champion,[])}
            a=[v for v in values if v['bucket'] in common];b=[v for v in arms.get(champion,[]) if v['bucket'] in common]
            if len(a)<2 or len(b)<2 or sum(v['paid'] for v in a)<2:continue
            am,av=sampling_stats([v['value'] for v in a],[v['paid'] for v in a]);bm,bv=sampling_stats([v['value'] for v in b],[v['paid'] for v in b])
            lower=am-bm-1.96*math.sqrt(av+bv)
            if am>0 and lower>0:supported.append(dict(arm=arm,advantage_lower=lower,matched_windows=len(common),opportunities=len(a)))
        candidate=max(supported,key=lambda x:x['advantage_lower'],default=None)
        previous=champions.get(key,{})
        if candidate and now-previous.get('at',0)>=policy['price_policy_hold_seconds']:
            champions[key]=dict(candidate,at=now,reason='mature_same_contract_shared_time_support')
            changes.append(dict(contract=key,before=champion,after=candidate['arm']));champion=candidate['arm']
        diagnostics[key]=dict(champion=champion,estimates=estimates,funnel=funnel[key],supported=candidate,
            comparison='same SKU grade exact warranty; shared market-time windows; not causal',
            inventory_basis='valid quoted unit recorded; remaining unit-cost heterogeneity not randomized')
    atomic(md/'price-hierarchy.json',dict(old,at=now,contract_diagnostics=diagnostics,contract_funnels=dict(funnel),contract_champions=champions,contract_policy_changes=changes,policy_changes=[]))
    return {}


def update(md,rows,now,policy,targets,connection):
    if policy.get('contract_price_learning_enabled'):return update_contracts(Path(md),rows,now,policy,targets,connection)
    md=Path(md);catalog=read(md/'catalog.json',{});inv=read(md/'inventory.json',{})
    market=read(md/'strategy-market.json',{});held={(u['sku'],u.get('grade')) for u in inv.get('units',[])}
    prior_diagnostics=read(md/'price-hierarchy.json',{}).get('diagnostics',{})
    inventory_groups=read(md/'inventory-plan.json',{}).get('groups',{})
    local=defaultdict(lambda:defaultdict(list));pools=defaultdict(lambda:defaultdict(list));diagnostics={};challengers={};policy_changes=[]
    for r in rows:
        if r.get('settled_at') is not None and r['settled_at']+min(6,r['warranty'])*3600+300>now:continue
        if r['at']+min(6,r['warranty'])*3600+300>now:continue
        paid=r.get('paid') is not None;net=(r['paid']-r['cost']-r['refunds']-r['storage']) if paid else 0
        net-=r.get('expiry_loss',0);cost=max(1,r['cost']);entry=(net/cost,(r.get('paid') or 0)/cost,paid,r['sku'],net,cost)
        key=(r['sku'],r['grade']);category=catalog.get(r['sku'],{}).get('category',catalog.get(r['sku'],{}).get('kind','unknown'))
        local[key][r['arm']].append(entry);pools[category,r['grade']][r['arm']].append(entry);pools['*',r['grade']][r['arm']].append(entry)
    states=[dict(r) for r in connection.execute('select * from price_state')]
    keys=set(local)|{(r['sku'],r['grade']) for r in states}|held
    state_map={(r['sku'],r['grade']):r for r in states}
    for sku,grade in keys:
        key=sku,grade;old=state_map.get(key,{});champion=old.get('arm',1.)
        product=catalog.get(sku,{});cat=product.get('category',product.get('kind','unknown'))
        samples=local[key];pool=pools[cat,grade] or pools['*',grade]
        recent=list(connection.execute('select at,settled_at,paid,cost,refunds,storage from price_quotes where sku=? and grade=? and inquiry=0 and at>?',(sku,grade,now-43200)))
        last_quote=max((r['at'] for r in recent),default=None);last_paid=max((r['settled_at'] for r in recent if r['settled_at'] is not None),default=None)
        reference=market.get('skus',{}).get(sku,{}).get('prices',{}).get(grade,{})
        previous=prior_diagnostics.get(f'{sku}:{grade}',{})
        cost=statistics.mean(r['cost'] for r in recent) if recent else product.get('list_price',1)*{'A':.65,'B':.5,'C':.35}.get(grade,.61)
        estimates={}
        for arm in set(samples)|set(pool):
            own=samples.get(arm,[]);others=[x for x in pool.get(arm,[]) if x[3]!=sku]
            if not others:others=[x for x in pools['*',grade].get(arm,[]) if x[3]!=sku]
            n=len(own);prior=others
            def estimate(index):
                fallback=statistics.mean(x[index] for x in prior) if prior else 0.
                return (sum(x[index] for x in own)+4*fallback)/(n+4) if prior else statistics.mean(x[index] for x in own) if own else 0.
            net,rev=estimate(0)*cost,estimate(1)*cost
            value=all_value(targets,'shop',dict(book=net,revenue=rev),now)
            if value is None:value=net/max(1,cost)
            values=[x[0] for x in own] or [x[0] for x in prior]
            # Include uncertainty in conversion even if a tiny sample paid twice.
            wins=sum(x[2] for x in own);effective=n+(min(4,len(prior)) if prior else 0)
            raw_variance=(statistics.variance(values) if len(values)>1 else 1.)/max(1,effective)
            prior_paid=statistics.mean(x[2] for x in prior) if prior else .5
            a=wins+min(4,len(prior))*prior_paid+.5;b=effective-(a-.5)+.5
            beta_variance=a*b/((effective+1)**2*(effective+2))
            paid_values=[x[0] for x in own+prior if x[2]]
            spread=statistics.mean(paid_values) if paid_values else 1.
            raw_variance=max(raw_variance,beta_variance*spread**2)
            raw_mean=net/max(1,cost)
            relative_se=math.sqrt(raw_variance)/max(.01,abs(raw_mean))
            se=abs(value)*relative_se
            if value==0:
                probe=all_value(targets,'shop',dict(book=math.sqrt(raw_variance)*cost,revenue=math.sqrt(raw_variance)*cost),now)
                se=abs(probe) if probe is not None else math.sqrt(raw_variance)
            estimates[str(arm)]=dict(arm=arm,n=n,pool_n=len(prior),paid=wins,value=value,se=se,net=net,revenue=rev,
                                    conversion=(wins+4*prior_paid)/(n+4))
        champion_estimate=estimates.get(str(champion));best=max(estimates.values(),key=lambda x:x['value'],default=None)
        if best and best['arm']!=champion and best['value']>0:
            advantage=best['value']-(champion_estimate or {}).get('value',0)
            uncertainty=1.96*math.sqrt(best['se']**2+(champion_estimate or {}).get('se',abs(best['value']))**2)
            if advantage>uncertainty and (best['paid']>=2 or best['pool_n']>=20):
                challengers[f'{sku}:{grade}']=dict(arm=best['arm'],opportunities=best['n'],advantage_lower=advantage-uncertainty,pooled=True)
                if best['n']>=2 and now-old.get('at',0)>=policy.get('price_policy_hold_seconds',3600):
                    connection.execute('insert or replace into price_state values(?,?,?,?,?)',(sku,grade,best['arm'],now,'hierarchical_mature_support'))
                    policy_changes.append(dict(sku=sku,grade=grade,before=champion,after=best['arm'],reason='hierarchical_mature_support'))
        inventory_group=inventory_groups.get(f'{sku}:{grade}',{})
        from capital_policy import remaining_hours
        validation_horizon=min(max(1,remaining_hours(now)*3600),max(policy['lookback_seconds'],3600/max(.001,inventory_group.get('rate',0))))
        stale=bool((sku,grade) in held and now-(last_paid or old.get('at',now))>validation_horizon)
        if len(recent)>=policy.get('warranty_prior_strength',4) and not last_paid:stale=bool((sku,grade) in held)
        diagnostics[f'{sku}:{grade}']=dict(at=now,champion=champion,last_valid_quote=last_quote,last_settlement=last_paid,
            competitor_price=reference.get('base_median',reference.get('median')),quotes=len(recent),settled=sum(r['paid'] is not None for r in recent),
            mature_net=0.,estimates=estimates,stock_present=(sku,grade) in held,
            stale=stale,revalidate=stale or len(samples.get(champion,[]))<2,
            validation_age_seconds=now-old.get('at',now),market_price_change=None,
            validation_horizon_seconds=validation_horizon,
            fallback_arm=best['arm'] if best and best['pool_n']>=20 and best['value']>0 else 1.)
        diagnostics[f'{sku}:{grade}']['mature_net']=sum(e[4] for values in samples.values() for e in values)
        old_ref=previous.get('last_validated_reference')
        if last_paid and last_paid!=previous.get('last_settlement'):old_ref=diagnostics[f'{sku}:{grade}']['competitor_price']
        diagnostics[f'{sku}:{grade}']['last_validated_reference']=old_ref
        if old_ref:diagnostics[f'{sku}:{grade}']['market_price_change']=diagnostics[f'{sku}:{grade}']['competitor_price']/old_ref-1 if diagnostics[f'{sku}:{grade}']['competitor_price'] else None
        connection.execute('insert or replace into price_diagnostics values(?,?,?,?)',(sku,grade,now,json.dumps(diagnostics[f'{sku}:{grade}'])))
    funnels=[dict(r) for r in connection.execute('''select e.sku,e.grade,e.epoch,sum(e.inquiry) inquiry_exposures,
        sum(case when e.inquiry=0 then 1 else 0 end) negotiation_entries,
        sum(case when q.paid is not null then 1 else 0 end) settlements,
        sum(case when e.inquiry=1 and q.paid is not null then 1 else 0 end) same_task_inquiry_settlements,
        coalesce(sum(q.paid),0) settled_revenue from price_exposures e left join price_quotes q on q.task=e.task
        where e.at>? group by e.sku,e.grade,e.epoch''',(now-43200,))]
    atomic(md/'price-hierarchy.json',dict(at=now,diagnostics=diagnostics,challengers=challengers,policy_changes=policy_changes,inquiry_funnel=funnels,level_order=['sku_grade','category_grade','market_grade']))
    return challengers


def quote_episode(md,task,unit,now,champion,menu,policy,inquiry,base):
    """A SKU/grade episode shares the exposed price, not just a random task arm."""
    from strategy_policy import database
    import hashlib
    md=Path(md);epoch=int(now//policy.get('quote_episode_seconds',180))
    contract_mode=policy.get('contract_price_learning_enabled');hours=base.get('warranty_hours',0) or 0
    key=f"{unit['sku']}:{unit.get('grade')}:{hours if contract_mode else 'legacy'}:{epoch}";digest=int(hashlib.sha256(key.encode()).hexdigest(),16)
    exploring=(digest%10000)/10000<policy['price_exploration_fraction']
    with database(md,True) as c:
        old=c.execute('select * from price_contract_sessions where sku=? and grade=? and hours=? and epoch=?',(unit['sku'],str(unit.get('grade')),hours,epoch)).fetchone() if contract_mode else c.execute('select * from price_sessions where sku=? and grade=? and epoch=?',(unit['sku'],str(unit.get('grade')),epoch)).fetchone()
        if old:
            arm=old['arm'];locked=old['base'] if old['unit']==unit.get('unit') else None
        else:
            arm=menu[(digest//10000)%len(menu)] if exploring else champion
            from patch36 import document
            hierarchy=document(md/'price-hierarchy.json');d=hierarchy.get('diagnostics',{}).get(f"{unit['sku']}:{unit.get('grade')}",{})
            if contract_mode:
                d=hierarchy.get('contract_diagnostics',{}).get(contract_key(unit['sku'],unit.get('grade'),hours),{})
            if exploring and policy.get('evidence_weighted_exploration_enabled'):
                from capital_policy import remaining_hours
                arm=exploration_arm(menu,d.get('estimates',{}),digest,remaining_hours(now),d.get('validation_horizon_seconds',7200)/3600)
            supported=hierarchy.get('challengers',{}).get(f"{unit['sku']}:{unit.get('grade')}",{}).get('arm')
            if contract_mode:supported=(d.get('supported') or {}).get('arm')
            if exploring and supported is not None and digest%2==0:arm=supported
            # Age or sparse sales alone is not evidence that a champion lost.
            # Only the mature, uncertainty-tested challenger may replace it.
            if d.get('stale') and not exploring and supported is not None:arm=supported
            locked=None
        c.execute('insert or ignore into price_exposures values(?,?,?,?,?,?,?)',(task,unit['sku'],str(unit.get('grade')),epoch,now,arm,int(inquiry)))
    return arm,epoch,locked,exploring


def exploration_arm(menu,estimates,digest,hours,validation_hours):
    """Keep the exploration share; spend it on plausible value or information.
    Sparse evidence never deletes an arm or a preserved premium champion.
    """
    rows=[estimates.get(str(a)) for a in menu if estimates.get(str(a))]
    best=max(rows,key=lambda r:r['value'],default=None)
    candidates=[];uncertainty=min(1,max(0,hours)/max(.01,validation_hours))
    scale=max([abs(r['value'])+r.get('se',0) for r in rows] or [1.])
    for arm in menu:
        r=estimates.get(str(arm))
        if r and best and r.get('n',0)>=8 and best.get('n',0)>=8:
            if r['value']+1.96*r.get('se',0)<best['value']-1.96*best.get('se',0):continue
        weight=max(0,r['value'])+1.96*r.get('se',0)*uncertainty if r else scale*max(.05,uncertainty)
        candidates.append((arm,max(scale*1e-6,weight)))
    if not candidates:return menu[(digest//10000)%len(menu)]
    target=((digest//10000)%1000000)/1000000*sum(w for _,w in candidates)
    for arm,weight in candidates:
        target-=weight
        if target<=0:return arm
    return candidates[-1][0]


def remember_episode(md,unit,epoch,arm,price,now,hours=None):
    from strategy_policy import database
    with database(md,True) as c:
        if hours is not None:c.execute('insert or ignore into price_contract_sessions values(?,?,?,?,?,?,?,?)',(unit['sku'],str(unit.get('grade')),hours,epoch,arm,unit.get('unit'),price,now))
        else:c.execute('insert or ignore into price_sessions values(?,?,?,?,?,?,?)',(unit['sku'],str(unit.get('grade')),epoch,arm,unit.get('unit'),price,now))


def lock_episode_terms(md,unit,epoch,hours,total,floor):
    from strategy_policy import database
    key=f"offer_terms:{unit['unit']}:{epoch}:{hours}"
    with database(md,True) as c:
        old=c.execute('select value from meta where key=?',(key,)).fetchone()
        if old:return json.loads(old['value'])
        terms=dict(total=total,floor=floor,hours=hours)
        c.execute('insert into meta values(?,?)',(key,json.dumps(terms)))
    return terms
