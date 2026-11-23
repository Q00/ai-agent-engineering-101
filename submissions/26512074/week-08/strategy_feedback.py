"""SKU/grade quote opportunities, unique paid attribution, bounded price feedback."""
import hashlib
import json
import math
import statistics
from pathlib import Path
from strategy_policy import database,load,read,warranty_rate,market_snapshot

_challenger_cache={}


def challenger(md,sku,grade,now):
    path=Path(md)/'strategy-feedback.json'
    try:stamp=path.stat().st_mtime_ns
    except FileNotFoundError:return None
    key=str(path.resolve());cached=_challenger_cache.get(key)
    if cached is None or cached[0]!=stamp:
        cached=(stamp,read(path));_challenger_cache[key]=cached
    state=cached[1]
    if not 0<=now-state.get('at',0)<1800:return None
    return state.get('challengers',{}).get(f'{sku}:{grade}',{}).get('arm')


def sampling_stats(values,paid):
    n=len(values);wins=[v for v,p in zip(values,paid) if p];losses=[v for v,p in zip(values,paid) if not p]
    a=len(wins)+.5;b=n-len(wins)+.5
    spread=(statistics.mean(wins) if wins else 0)-(statistics.mean(losses) if losses else 0)
    binomial_variance=spread*spread*a*b/((n+1)**2*(n+2))
    return statistics.mean(values),max(statistics.variance(values)/n if n>1 else 0,binomial_variance)


def market_price(md,unit,inventory,now,fallback=None):
    p=load(md);m=market_snapshot(md);s=m.get('skus',{}).get(unit['sku'],{})
    if unit.get('grade')=='expired' or (unit.get('expires_at') is not None and unit['expires_at']<=now):return None
    grade=s.get('prices',{}).get(str(unit.get('grade')),{});fresh=0<=now-m.get('at',0)<=p['max_market_age_seconds']
    reference=grade.get('base_median',grade.get('median')) if fresh and grade.get('n',0)>=p['min_trades'] else None
    reference=reference or (fallback or {}).get('market_reference') or unit['list_price']
    cost=unit['cost'];age=max(0,(now-unit.get('supplied_at',now))/3600)
    food=unit.get('kind')=='food';storage=0 if food else cost*inventory.get('storage_per_day',.01)/24
    floor=math.ceil(cost+storage)
    if fresh and grade.get('n',0)>=p['min_trades']:
        share=(s.get('own_trades',0)+1)/(s.get('trades',0)+s.get('competitors',0)+2)
        arrival=max(s.get('own_rate',0),s.get('market_rate',0)*share)
        stock=sum(u.get('sku')==unit['sku'] for u in inventory.get('units',[]))
        # Scarce stock can wait for the next arrival; excess units compete for
        # that arrival. Never raise the reservation value just for overstock.
        cycle=p['supply_cycle_seconds']/3600
        wait=min(p['lookback_seconds']/3600,max(cycle,1/max(arrival,.001)))/max(1,stock)
        if food and unit.get('expires_at'):wait=min(wait,max(0,(unit['expires_at']-now)/3600))
        chance=1-math.exp(-arrival*wait)
        waiting_value=max(0,chance*max(0,reference-cost)-storage*wait)
        floor=math.ceil(cost+storage+waiting_value)
    # Age alone is not evidence that a non-perishable product lost value.
    ask=max(floor,math.ceil(reference))
    clearance=False
    if food and unit.get('expires_at'):
        hours=(unit['expires_at']-now)/3600
        if hours<6:
            # Sunk cost must not force disposal while a positive recovery exists.
            floor=1;ask=max(1,math.floor(reference*max(.05,hours/6)));clearance=True
    inventory_state=None
    if p.get('inventory_velocity_enabled'):
        from patch36 import document
        plan=document(Path(md)/'inventory-plan.json')
        inventory_state=plan.get('groups',{}).get(f"{unit['sku']}:{unit.get('grade')}") if 0<=now-plan.get('at',0)<p['max_market_age_seconds'] else None
        if inventory_state and inventory_state['state'] in ('medium_demand_excess','low_demand_excess','high_demand_low_conversion'):
            pressure=inventory_state['qty']/max(1,inventory_state['target'])
            floor=math.ceil(cost+storage+max(0,floor-cost-storage)/(1+pressure))
            # Existing stock is sunk capital. A loss needs an actual peer
            # crossing, not merely positive interpolated Revenue slope.
            from score_coordination import all_value,boundary_value
            targets=document(Path(md)/'score-targets.json')
            # Pending loss quotes must not each claim the same Revenue crossing.
            from score_coordination import project_targets
            with database(md) as c:
                pending=list(c.execute('select quote,cost from price_quotes where paid is null and quote<cost and at>?',(now-900,)))
            if pending:targets=project_targets(targets,'shop',dict(book=sum(r['quote']-r['cost'] for r in pending),revenue=sum(r['quote'] for r in pending)))
            alternatives=[ask]+[math.ceil(cost+(1-q)*max(0,unit['list_price']-cost)) for q in p.get('seller_margin_share_targets',[])]
            if inventory_state['state']=='low_demand_excess':alternatives.append(math.ceil(min(reference,cost)-inventory_state['expected_storage']))
            choices=[]
            avoided_storage=inventory_state['expected_storage'] if p.get('terminal_slot_allocation_enabled') else 0
            if avoided_storage:
                # Existing goods keep Book cost if held, but storage still
                # erodes Book. Test the actual holding-cost-neutral boundary.
                alternatives.append(max(1,math.ceil(cost+storage-avoided_storage)))
            learned=document(Path(md)/'price-hierarchy.json').get('diagnostics',{}).get(f"{unit['sku']}:{unit.get('grade')}",{}).get('estimates',{})
            if p.get('contract_price_learning_enabled'):learned={}  # Contract-specific choice occurs below, once hours are known.
            for price in alternatives:
                if price<=0:continue
                net=price-cost-storage
                if net<0 and not p.get('terminal_slot_allocation_enabled'):
                    forecast=document(Path(md)/'procurement-prepared.json').get('candidates',[])
                    returns=[max(0,v['roi'])/max(.05,v['payback_hours']) for v in forecast if v.get('roi',0)>0]
                    if returns:net-=(-net)*statistics.median(returns)*min(inventory_state['expected_hours_to_sell'],inventory_state['remaining_round_hours'])
                terminal_net=net+avoided_storage
                gain=all_value(targets,'shop',dict(book=terminal_net,revenue=price),now)
                crossing=boundary_value(targets,'shop',dict(book=terminal_net,revenue=price),now)
                if terminal_net<0 and not (crossing is not None and crossing>0 and gain is not None and gain>0):continue
                # Use observed paid/quote conversion, shrunk by price distance.
                conversion=inventory_state['conversion']
                if learned:
                    def candidate_price(e):
                        a=e['arm']
                        return cost+(1+a)*max(0,unit['list_price']-cost) if a<0 else reference*a
                    estimate=min(learned.values(),key=lambda e:abs(candidate_price(e)-price))
                    conversion=estimate.get('conversion',conversion)
                if gain is not None:choices.append((min(1,conversion)*gain,price))
            if choices:
                selected=max(choices)[1];ask=selected
                if selected<cost:floor=selected
                else:floor=min(floor,selected)
    return dict(ask=ask,floor=floor,market_reference=reference,market_median=grade.get('median'),
                market_samples=grade.get('n',0),freshness_matched=False,clearance=clearance,
                cash_recovery=age>=6,market_fresh=fresh,inventory_state=inventory_state)


def quote(md,task,unit,inventory,base,now,inquiry=False):
    """Bind price once. Legacy open quotes remain in the original trials table."""
    from optimizer import db,append
    p=load(md)
    with db(md) as old:
        prior=old.execute('select * from trials where task=?',(task,)).fetchone()
    with database(md,True) as c:
        bound=c.execute('select * from price_quotes where task=?',(task,)).fetchone()
        if bound:
            return dict(base,ask=bound['base'],floor=min(base['floor'],bound['base']),reference=base.get('market_reference',unit['list_price']),
                        arm=round(bound['base']/max(1,unit['cost'])-1,6),locked_total=bound['quote'],locked_warranty=bound['warranty'],pricing_evidence=dict(strategy=p['version'],bound=True))
        if prior:
            return dict(base,ask=prior['ask'],floor=min(base['floor'],prior['ask']),reference=prior['reference'] or unit['list_price'],
                        arm=prior['arm'],pricing_evidence=dict(strategy='preserved-before-flow-update',bound=True))
        state=c.execute('select * from price_state where sku=? and grade=?',(unit['sku'],str(unit.get('grade')))).fetchone()
        champion=state['arm'] if state else 1.0
        if p.get('contract_price_learning_enabled'):
            from price_learning import contract_key
            from patch36 import document
            specific=document(Path(md)/'price-hierarchy.json').get('contract_champions',{}).get(contract_key(unit['sku'],unit.get('grade'),base.get('warranty_hours',0)))
            if specific:champion=specific['arm']
        digest=int(hashlib.sha256(task.encode()).hexdigest(),16)
        # Negative arms encode a stable margin-share target, not a multiplier.
        # Keep every previously verified champion, including premium arms.
        menu=list(p['price_multipliers'])+[-q for q in p.get('seller_margin_share_targets',[])]
        arms=[a for a in menu if a!=champion]
        explore=(digest%10000)/10000<p['price_exploration_fraction'] and not inquiry and not base.get('clearance') and base.get('market_fresh',False)
        arm=arms[(digest//10000)%len(arms)] if explore and arms else champion
        supported=challenger(md,unit['sku'],str(unit.get('grade')),now) if explore else None
        if supported in arms and int(hashlib.sha256((task+':challenger').encode()).hexdigest(),16)%2==0:
            arm=supported
        if base.get('clearance'):arm=1.0
    epoch=None;locked=None
    if p.get('inquiry_price_episodes_enabled') and not base.get('clearance'):
        from price_learning import quote_episode
        arm,epoch,locked,explore=quote_episode(md,task,unit,now,champion,menu,p,inquiry,base)
    if arm<0:
        economic_floor=math.ceil(unit['cost']+(0 if unit.get('kind')=='food' else unit['cost']*inventory.get('storage_per_day',.01)/24))
        ask=max(economic_floor,math.ceil(unit['cost']+(1+arm)*max(0,unit['list_price']-unit['cost'])))
        effective_floor=min(base['floor'],ask)
    else:
        ask=max(base['floor'],math.ceil(base['ask']*arm));effective_floor=base['floor']
    if locked is not None:ask=locked;effective_floor=min(effective_floor,ask)
    with database(md,True) as c:
        c.execute('insert into price_quotes(task,unit,sku,grade,at,arm,base,quote,cost,inquiry) values(?,?,?,?,?,?,?,?,?,?)',
                  (task,unit.get('unit'),unit['sku'],str(unit.get('grade')),now,arm,ask,ask,unit['cost'],int(inquiry)))
    if epoch is not None:
        from price_learning import remember_episode
        remember_episode(md,unit,epoch,arm,ask,now,hours=(base.get('warranty_hours',0) or 0) if p.get('contract_price_learning_enabled') else None)
    reference=base.get('market_reference') or unit['list_price']
    result=dict(base,ask=ask,floor=effective_floor,reference=reference,arm=round(ask/max(1,unit['cost'])-1,6),
                pricing_evidence=dict(strategy=p['version'],price_arm=arm,champion=champion,exploration=explore,supported_challenger=supported,offer_epoch=epoch))
    with db(md) as c:
        c.execute('insert or ignore into trials(task,sku,unit,at,arm,ask,cost,grade,stock,kind,reference,inquiry,strategy) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (task,unit['sku'],unit.get('unit'),now,result['arm'],ask,unit['cost'],unit.get('grade'),
                   sum(u['sku']==unit['sku'] for u in inventory.get('units',[])),unit.get('kind'),reference,int(inquiry),p['version']))
    append(Path(md)/'optimization/opening-prices.jsonl',dict(at=now,task=task,sku=unit['sku'],ask=ask,inquiry=inquiry,model_calls=0,**result['pricing_evidence']))
    return result


def finalize_quote(md,task,total,hours):
    with database(md,True) as c:
        c.execute('update price_quotes set quote=?,warranty=? where task=?',(total,hours,task))


def reconcile_paid(md,now):
    from shop_profit import ownership_tags
    from optimizer import db
    tags=ownership_tags()
    with database(md) as c:
        first=c.execute('select min(at) from price_quotes').fetchone()[0]
        if first is None:return dict(newly_attributed=0,ambiguous_or_legacy_unattributed=0)
        slots=','.join('?' for _ in tags)
        events=[json.loads(r[0]) for r in c.execute("select payload from market_events where ts>? and json_extract(payload,'$.data.shop') in ("+slots+") and kind in ('settle','result','broken','refund','expire') order by seq",(now-7*86400,*tags))]
    receipts={}
    refunds={}
    for e in events:
        d=e['data']
        if d.get('shop') not in tags:continue
        if e['kind']=='result' and d.get('verified') and d.get('receipt',{}).get('task'):
            receipts[d.get('order')]=d['receipt']
        if e['kind'] in ('broken','refund'):
            identity=d.get('unit')
            refunds[identity]=max(refunds.get(identity,0),d.get('refunded',0) if e['kind']=='broken' else d.get('amount',0))
    with db(md) as old:
        paid_trials=[dict(r) for r in old.execute('select task,unit,agreed,settled_at from trials where inquiry=0 and agreed is not null and at>?',(now-7*86400,))]
        units={r['unit']:dict(r) for r in old.execute('select * from units')}
        supplied={identity:r['supplied'] for identity,r in units.items()}
    by_unit={}
    for r in paid_trials:by_unit.setdefault((r['unit'],r['agreed']),[]).append(r)
    ambiguous=0;attributed=0
    with database(md,True) as c:
        for e in events:
            d=e['data']
            if e['kind']!='settle' or d.get('shop') not in tags or e['ts']<first:continue
            if c.execute('select task from price_quotes where settle_seq=?',(e['seq'],)).fetchone():continue
            verified=receipts.get(d.get('order'),{})
            task=verified.get('task')
            if not task:
                candidates={r['task'] for r in by_unit.get((d.get('unit'),d.get('amount')),[])
                            if r['settled_at'] is None or abs(r['settled_at']-e['ts'])<1}
                if len(candidates)!=1:ambiguous+=1;continue
                task=next(iter(candidates))
            row=c.execute('select * from price_quotes where task=? and settle_seq is null',(task,)).fetchone()
            if not row or row['at']>e['ts']:continue
            if row['inquiry'] and not verified:continue
            actual_unit=d.get('unit');actual_cost=row['cost']
            if row['unit']!=actual_unit:
                acquired=units.get(actual_unit,{})
                # The kit can fulfill a verified receipt with another unit.
                # Attribute only a matching SKU/grade with known actual cost.
                if not verified or verified.get('price')!=d.get('amount') or d.get('grade')!=row['grade'] or acquired.get('sku')!=row['sku'] or not isinstance(acquired.get('cost'),(int,float)):continue
                actual_cost=acquired['cost']
            storage=actual_cost*.01*max(0,e['ts']-supplied.get(actual_unit,e['ts']))/86400
            c.execute('update price_quotes set settle_seq=?,settled_at=?,paid=?,storage=?,unit=?,cost=?,inquiry=0 where task=?',
                      (e['seq'],e['ts'],d['amount'],storage,actual_unit,actual_cost,task));attributed+=1
        for identity,amount in refunds.items():
            c.execute('update price_quotes set refunds=max(refunds,?) where unit=? and settle_seq is not null',(amount,identity))
        for e in events:
            d=e['data']
            if e['kind']!='expire' or d.get('shop') not in tags or not isinstance(d.get('cost'),(int,float)):continue
            rows=c.execute('select task from price_quotes where unit=? and inquiry=0 and settled_at is null and at<=?',(d.get('unit'),e['ts'])).fetchall()
            for row in rows:
                c.execute('insert or ignore into price_losses values(?,?,?)',(e['seq'],row['task'],d['cost']/len(rows)))
    return dict(newly_attributed=attributed,ambiguous_or_legacy_unattributed=ambiguous)


def evaluate(md,now,force=False):
    from optimizer import atomic
    p=load(md);last=read(Path(md)/'strategy-feedback.json')
    from score_coordination import all_value
    targets=read(Path(md)/'score-targets.json',{})
    if not force and now-last.get('at',0)<p['price_review_seconds']:return last
    with database(md,True) as c:
        inquiry_filter='' if p.get('contract_price_learning_enabled') else 'inquiry=0 and '
        rows=[dict(r) for r in c.execute('select q.*,coalesce((select sum(amount) from price_losses l where l.task=q.task),0) expiry_loss from price_quotes q where '+inquiry_filter+'at between ? and ?',
                                        (now-p.get('price_feedback_window_seconds',43200),now-p['price_maturity_seconds']))]
        groups={};raw_groups={};paid_groups={}
        contexts={r['task']:r['buyer'] for r in c.execute('select task,buyer from quote_context')}
        for r in rows:
            # Match the observation horizon for paid and unpaid opportunities.
            # Counting recent failures while warranted successes are pending
            # makes conversion depend on time since quote, not price quality.
            if r['at']+min(6,r['warranty'])*3600+300>now:continue
            if r['settled_at'] is not None and r['settled_at']+min(6,r['warranty'])*3600+300>now:continue
            net=((r['paid']-r['cost']-r['refunds']-r['storage']) if r['paid'] is not None else 0)-r['expiry_loss']
            key=(r['sku'],r['grade'])
            stars=0
            if p.get('joint_seller_objective_enabled') and r['paid'] is not None:
                from review_policy import marginal
                stars=marginal(md,contexts.get(r['task']),r['grade'])
            modeled=all_value(targets,'shop',dict(book=net,revenue=r['paid'] or 0,stars_rank=stars),now)
            groups.setdefault(key,{}).setdefault(r['arm'],[]).append(modeled if modeled is not None else net)
            raw_groups.setdefault(key,{}).setdefault(r['arm'],[]).append(net)
            paid_groups.setdefault(key,{}).setdefault(r['arm'],[]).append(r['paid'] is not None)
        metrics=[];changes=[];challengers={}
        for (sku,grade),arms in groups.items():
            old=c.execute('select * from price_state where sku=? and grade=?',(sku,grade)).fetchone()
            champion=old['arm'] if old else 1.0
            if len(arms.get(champion,[]))>=2:
                cm,cv=sampling_stats(arms[champion],paid_groups[sku,grade][champion]);supported=[]
                for candidate,values in arms.items():
                    if candidate==champion or not 2<=len(values)<p['price_min_opportunities'] or sum(paid_groups[sku,grade][candidate])<2:continue
                    mean,variance=sampling_stats(values,paid_groups[sku,grade][candidate])
                    lower=mean-cm-1.96*math.sqrt(variance+cv)
                    if mean>0 and lower>0:supported.append(dict(arm=candidate,opportunities=len(values),advantage_lower=lower))
                if supported:challengers[f'{sku}:{grade}']=max(supported,key=lambda v:v['advantage_lower'])
            eligible={a:v for a,v in arms.items() if len(v)>=p['price_min_opportunities']}
            means={a:statistics.mean(v) for a,v in eligible.items()}
            best=max(means,key=means.get) if means else champion
            current=means.get(champion)
            if not p.get('contract_price_learning_enabled') and current is not None and best!=champion and (not old or now-old['at']>=p.get('price_policy_hold_seconds',0)):
                gain=means[best]-current
                error=math.sqrt(sum(statistics.variance(eligible[a])/len(eligible[a]) for a in (best,champion) if len(eligible[a])>1))
                if gain>max(abs(current)*p['price_min_relative_gain'],1.96*error) and means[best]>0:
                    reason='select_supported_margin_share' if best<0 else 'raise_supported_price' if best>champion else 'restore_better_price_multiple'
                    c.execute('insert or replace into price_state values(?,?,?,?,?)',(sku,grade,best,now,reason))
                    changes.append(dict(sku=sku,grade=grade,before=champion,after=best,reason=reason))
            metrics.append(dict(sku=sku,grade=grade,arms={str(a):dict(opportunities=len(v),known_contribution=sum(raw_groups[sku,grade][a]),contribution_per_opportunity=statistics.mean(raw_groups[sku,grade][a]),decision_value_per_opportunity=statistics.mean(v)) for a,v in arms.items()}))
        if p.get('hierarchical_pricing_enabled'):
            from price_learning import update as update_hierarchy
            challengers.update(update_hierarchy(md,rows,now,p,targets,c))
            changes.extend(read(Path(md)/'price-hierarchy.json',{}).get('policy_changes',[]))
    result=dict(at=now,version=p['version'],metrics=metrics,changes=changes,challengers=challengers,
                interpretation='Class ALL progress when scoreboard is fresh; profit fallback otherwise. Observational cohorts; no causal or awarded-point claim.')
    atomic(Path(md)/'strategy-feedback.json',result)
    return result
