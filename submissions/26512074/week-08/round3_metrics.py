"""Measured opportunity cost, account reviews and aligned cohorts in the existing collector."""
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from optimizer import atomic,read
from strategy_policy import database


def quantile(values,p=.5):
    values=sorted(values)
    return values[min(len(values)-1,int((len(values)-1)*p))] if values else None


def order_key(record):
    return next((record[k] for k in ('order','order_id','id') if isinstance(record.get(k),str) and record[k]),None)


def reconcile_buyer_counts(events,baseline,checkpoint,start,end):
    """Keep official snapshot populations and ledger cohorts separate."""
    raw=[e for e in events if e['kind']=='settle' and e['data'].get('buyer')=='totato2203-r3' and start<=e['ts']<=end]
    unique={};conflicts=[]
    for e in raw:
        identity=order_key(e['data'])
        if not identity:continue
        if identity in unique and unique[identity]['data'].get('unit')!=e['data'].get('unit'):conflicts.append(identity)
        unique[identity]=e
    a=baseline.get('buyer',baseline);b=checkpoint.get('buyer',checkpoint)
    n0=a.get('deals');n1=b.get('deals');s0=a.get('saved');s1=b.get('saved')
    delta=n1-n0 if n0 is not None and n1 is not None else None
    change=s1*(n1+100)-s0*(n0+100) if None not in (n0,n1,s0,s1) else None
    all_purchases={e['data'].get('unit'):e for e in events if e['kind']=='settle' and e['data'].get('buyer')=='totato2203-r3'}
    old_changed={e['data'].get('unit') for e in events if e['kind'] in ('broken','refund') and start<=e['ts']<=end and e['data'].get('unit') in all_purchases and all_purchases[e['data']['unit']]['ts']<start}
    return dict(start=start,end=end,baseline_observed_at=baseline.get('at'),checkpoint_observed_at=checkpoint.get('at'),
        official_deals_before=n0,official_deals_after=n1,official_deals_delta=delta,
        public_settlement_events=len(raw),public_unique_orders=len(unique),duplicate_order_events=len(raw)-len(unique),conflicting_orders=conflicts,
        count_residual=delta-len(unique) if delta is not None else None,
        official_deal_membership_per_order='not exposed by board; equal aggregate counts do not expose membership',
        official_saved_before=s0,official_saved_after=s1,official_score_sum_change_estimate=change,
        inverse_net_contribution_estimate=change/delta if change is not None and delta and delta>0 else None,
        inverse_is_new_purchase_final_mean=False,prior_purchase_result_changes=len(old_changed),
        historical_score_delta=None,rounding_note='Rounded Saved and delayed old outcomes prevent exact attribution.')


def opening_profiles(timings,purchases,predictions):
    groups=defaultdict(lambda:dict(n=0,refused=0,floors=[],seconds=[]))
    for t in timings:
        identity=t.get('order_id');sale=purchases.get(identity);prediction=predictions.get(identity,{})
        decisions=t.get('decisions',[])
        for i,d in enumerate(decisions):
            if d.get('action')!='offer' or not d.get('first_offer') or not d.get('ask'):continue
            following=[]
            for x in decisions[i+1:]:
                if x.get('action')=='offer' and x.get('first_offer'):break
                following.append(x)
            refused=any(x.get('action')=='compare' and x.get('sku')==d.get('sku') for x in following)
            grade=d.get('grade') or (prediction.get('grade') if prediction.get('sku')==d.get('sku') else None)
            hours=d.get('warranty_hours',prediction.get('warranty_hours',0)) or 0
            keys=[d.get('shop','')]
            if grade:keys.insert(0,f"{d.get('shop')}|{d.get('sku')}|{grade}|{hours:g}")
            for key in keys:
                g=groups[key];g['n']+=1;g['refused']+=int(refused)
                if sale and sale['data'].get('shop')==d.get('shop') and sale['data'].get('sku',sale['data'].get('item'))==d.get('sku'):
                    g['floors'].append(sale['data']['amount']/d['ask'])
                    g['seconds'].append(sum(x.get('response_seconds',0) for x in following if x.get('action')=='compare'))
    return {k:dict(n=v['n'],explicit_refusal_rate=v['refused']/v['n'],settled_n=len(v['floors']),
        floor_ratio_median=quantile(v['floors']),avoidable_response_seconds=quantile(v['seconds']),
        source='observed first counter and same-shop settled price; floor sample is settlement-selected') for k,v in groups.items()}


def next_wait_groups(issued,results,purchases,now):
    groups={'settled':[], 'unsettled':[]}
    for i,(_,identity) in enumerate(issued[:-1]):
        next_at=issued[i+1][0]
        if identity in results and next_at>=results[identity] and next_at>=now-21600:
            groups['settled' if identity in purchases else 'unsettled'].append((next_at-results[identity],next_at))
    estimates={}
    for name,samples in groups.items():
        weights=[2**(-(now-ts)/3600) for _,ts in samples]
        estimates[name]=dict(n=len(samples),mean=sum(v*w for (v,_),w in zip(samples,weights))/sum(weights) if samples else None,
                            median=quantile([v for v,_ in samples]),p90=quantile([v for v,_ in samples],.9))
    return groups,estimates


def cached_waits(md,now):
    """Compatibility for a pre-v9 collector that cannot be restarted.

    Reuse at most 24k local events every 15 minutes, with no network/worker.
    The regular collector's new field takes precedence once it is available.
    """
    import sqlite3
    from contextlib import closing
    path=Path(md)/'buyer-wait-observation-v9.json';cached=read(path,{})
    if 0<=now-cached.get('at',0)<900:return cached['groups']
    orders={};results={};purchases=set()
    with closing(sqlite3.connect('file:'+(Path(md)/'strategy.sqlite3').resolve().as_posix()+'?mode=ro',uri=True,timeout=2)) as c:
        rows=c.execute("select payload from market_events where ts>=? and kind in ('order','result','settle') and json_extract(payload,'$.data.buyer')='totato2203-r3' order by ts desc limit 24000",(now-21600,)).fetchall()
    for raw, in rows:
        e=json.loads(raw);identity=e['data'].get('order')
        if e['kind']=='order':orders[identity]=e['ts']
        elif e['kind']=='result':results[identity]=e['ts']
        else:purchases.add(identity)
    _,groups=next_wait_groups(sorted((ts,o) for o,ts in orders.items()),results,purchases,now)
    atomic(path,dict(at=now,groups=groups,events=len(rows),source='bounded_existing_local_ledger_no_network'))
    return groups


def record_prediction(md,order,receipt,prediction,trace):
    identity=order_key(order)
    if not identity:return
    safe=dict(prediction,sku=receipt.get('item'),price=receipt.get('price'),grade=receipt.get('claim'),
              warranty_hours=receipt.get('warranty_hours',0),task=receipt.get('task'),trace=trace)
    with database(md,True) as c:c.execute('insert or replace into buyer_predictions values(?,?,?)',(identity,trace['started_at'],json.dumps(safe)))


def update(md,events,catalog,now):
    """Reuse the collector's rows; never make another market/network request."""
    from shop_profit import ownership_tags
    from patch36 import outcome,saved_objective,expected_margin_share
    md=Path(md);tags=ownership_tags();orders={};results={};purchases={};refunds={};use={};broken=set();reviews=[];costs=[];own_sales={}
    for e in events:
        d=e.get('data',{});k=e['kind'];identity=d.get('order');unit=d.get('unit')
        if k=='supply' and d.get('qty')==1 and isinstance(d.get('total'),(int,float)):
            costs.extend((u,d.get('sku'),d['total'],e['ts'],'public_qty1_exact') for u in d.get('units',[]))
        if k=='order' and d.get('buyer')=='totato2203-r3':orders[identity]=e['ts']
        if k=='result' and d.get('buyer')=='totato2203-r3':results[identity]=e['ts']
        if k=='settle':
            if d.get('buyer')=='totato2203-r3':purchases[identity]=e
            if d.get('shop') in tags:own_sales[identity]=d
        if k in ('refund','broken') and d.get('shop'):
            amount=d.get('amount',0) if k=='refund' else d.get('refunded',0)
            refunds[unit]=max(refunds.get(unit,0),amount or 0)
            if k=='broken':broken.add(unit)
        if k in ('use','consume','served') and unit:
            # Only explicit public use outcomes qualify; broken is not final use.
            if isinstance(d.get('success'),bool):use[unit]=d['success']
    # Absence of a break is evidence only over a contiguous public ledger span.
    segments=[];first=last=None;last_seq=None
    for e in sorted(events,key=lambda e:e['seq']):
        if last_seq is not None and e['seq']!=last_seq+1:
            if first is not None:segments.append((first,last))
            first=None
        if first is None:first=e['ts']
        last=e['ts'];last_seq=e['seq']
    if first is not None:segments.append((first,last))
    with database(md,True) as c:
        c.executemany('insert or ignore into unit_acquisitions values(?,?,?,?,?)',costs)
        if not c.execute("select 1 from meta where key='review_bootstrap_v8'").fetchone():
            slots=','.join('?' for _ in tags)
            query="select r.seq,r.ts,r.payload,s.payload sale from market_events r join market_events s on json_extract(s.payload,'$.data.order')=json_extract(r.payload,'$.data.order') and s.kind='settle' where r.kind='result' and json_extract(r.payload,'$.data.shop') in ("+slots+")"
            for r in c.execute(query,tuple(tags)).fetchall():
                d=json.loads(r['payload'])['data'];sale=json.loads(r['sale'])['data']
                if d.get('verified') and isinstance(d.get('stars'),(int,float)):
                    reviews.append((r['seq'],d.get('buyer'),d['stars'],r['ts'],sale.get('sku',sale.get('item')),sale.get('grade')))
            c.execute("insert into meta values('review_bootstrap_v8',?)",(str(now),))
        for e in events:
            d=e.get('data',{});sale=own_sales.get(d.get('order'))
            if e['kind']=='result' and d.get('verified') and sale and isinstance(d.get('stars'),(int,float)):
                reviews.append((e['seq'],d.get('buyer'),d['stars'],e['ts'],sale.get('sku',sale.get('item')),sale.get('grade')))
                task=(d.get('receipt') or {}).get('task')
                if task:c.execute('insert or replace into quote_context values(?,?,?)',(task,d.get('buyer'),'settled_verified'))
        c.executemany('insert or ignore into review_events values(?,?,?,?,?,?)',reviews)
        accounts={r['buyer']:dict(mean=r['mean'],n=r['n']) for r in c.execute('select buyer,avg(stars) mean,count(*) n from review_events group by buyer')}
        grade_reviews={r['grade']:dict(mean=r['mean'],n=r['n']) for r in c.execute('select grade,avg(stars) mean,count(*) n from review_events where at>? group by grade',(now-86400,))}
        predictions={r['order_id']:json.loads(r['payload']) for r in c.execute('select * from buyer_predictions where at>?',(now-86400,))}
        exact={r['unit']:r['cost'] for r in c.execute('select unit,cost from unit_acquisitions where at>?',(now-7*86400,))}
        local_orders={r['id']:json.loads(r['payload']) for r in c.execute('select id,payload from orders where at>?',(now-86400,))}
        access={r['task']:dict(r) for r in c.execute('select * from access_audit where at>?',(now-86400,))}
    observed_reviews=sum(r['n'] for r in accounts.values());official_reviews=read(md/'patch36-scoreboard.json',{}).get('seller',{}).get('reviews')
    atomic(md/'review-account-model.json',dict(at=now,accounts=accounts,grade_reviews=grade_reviews,
           represented_accounts=len(accounts),observed_account_mean=statistics.mean(v['mean'] for v in accounts.values()) if accounts else None,
           observed_reviews=observed_reviews,official_reviews=official_reviews,coverage_fraction=min(1,observed_reviews/official_reviews) if official_reviews else None,
           coverage='verified locally observed paid review accounts; not assumed complete'))
    access_results=[];blocked={str(s).casefold() for s in read(md.parent/'seller-access.json',{}).get('blocked_buyers',[])}
    for e in events:
        d=e.get('data',{});task=(d.get('receipt') or {}).get('task') if e['kind']=='result' else None
        if task not in access or not d.get('verified') or d.get('order') not in own_sales:continue
        sale=own_sales[d['order']];cost=exact.get(sale.get('unit'));buyer=d.get('buyer')
        access_results.append(dict(task=task,matched_pattern=access[task]['pattern'],actual_buyer=buyer,
            classification='missed_named_ban_due_to_unverified_identity' if str(buyer).casefold() in blocked else 'normal_paid_buyer_generic_false_positive_avoided',
            revenue=sale['amount'],known_net=sale['amount']-cost if cost is not None else None))
    atomic(md/'buyer-access-impact.json',dict(at=now,decisions=len(access),confirmed_outcomes=access_results,
        unknown_outcomes=len(access)-len(access_results),legacy_denial_lost_profit='unobserved counterfactual, not fabricated'))
    completed=sorted((ts,identity,results[identity]) for identity,ts in orders.items() if identity in results)
    issued=sorted((ts,identity) for identity,ts in orders.items())
    wait_groups,wait_estimates=next_wait_groups(issued,results,purchases,now)
    waits=wait_groups['settled']+wait_groups['unsettled']
    recent=[x for x in completed if x[0]>=now-21600];durations=[end-start for start,_,end in recent]
    values=[];kinds=defaultdict(list);shop_wins=defaultdict(lambda:[0,0]);rows=[];bias=defaultdict(list);outcomes=defaultdict(lambda:dict(n=0,normal=0,refunded=0,failed=0,unknown=0))
    for start,identity,end in recent:
        sale=purchases.get(identity);v=0
        if sale:
            d=sale['data'];sku=d.get('sku',d.get('item'));p=catalog.get(sku,{})
            if p.get('list_price'):
                score=outcome(p['list_price'],d['amount'],sku,d.get('grade'),p.get('kind','goods'),d.get('warranty_hours') or 0,d.get('shop'),md)
                v=saved_objective(score,1,md);kinds[p.get('kind','goods')].append(score)
        values.append(v)
    for identity,sale in purchases.items():
        d=sale['data'];shop_wins[d.get('shop')][1]+=1
    # Also include failed checkout attempts when their verified result names a shop.
    for e in events:
        d=e.get('data',{})
        if e['kind']=='result' and d.get('buyer')=='totato2203-r3' and d.get('shop'):shop_wins[d['shop']][0]+=1
    weights=[2**(-(now-start)/3600) for start,_,_ in recent]
    wait_weights=[2**(-(now-ts)/3600) for _,ts in waits]
    wait=sum(v*w for (v,_),w in zip(waits,wait_weights))/max(1e-9,sum(wait_weights)) if waits else 1.
    weighted_duration=sum(d*w for d,w in zip(durations,weights))/max(1e-9,sum(weights)) if durations else 15.
    cycle=weighted_duration+wait
    prior=quantile([v for v in values if v>0],.25) or 0
    gain=(sum(v*w for v,w in zip(values,weights))+4*prior)/max(1,sum(weights)+4)
    opportunity=dict(at=now,sample_orders=len(recent),effective_recent_orders=sum(weights),next_wait_mean=wait,next_wait_median=quantile([v for v,_ in waits]),next_wait_p90=quantile([v for v,_ in waits],.9),
                     public_settlements_per_hour=sum(e['ts']>=now-21600 for e in purchases.values())/6,
                     next_wait_by_outcome=wait_estimates,
                     mean_processing_seconds=statistics.mean(durations) if durations else None,median_processing_seconds=quantile(durations),p90_processing_seconds=quantile(durations,.9),
                     expected_gain_per_order=gain,all_per_second=max(0,gain)/max(1,cycle),cycle_seconds=cycle,
                     checkout_by_shop={k:dict(n=v[0],paid=v[1],probability=(v[1]+1)/(max(v)+2)) for k,v in shop_wins.items()},
                     kind_scores={k:dict(n=len(v),mean=statistics.mean(v)) for k,v in kinds.items()},
                     value_source='modeled class marginal contribution, actual order/result/settle timing; not official final score')
    from strategy_policy import load
    policy=load(md);timings=[]
    if policy.get('buyer_opening_learning_enabled') or policy.get('buyer_reconciliation_enabled'):
        path=md/'buyer-timing.jsonl'
        if path.exists():
            with path.open(encoding='utf8') as source:
                for line in source:
                    try:t=json.loads(line)
                    except ValueError:continue
                    if now-21600<=t.get('started_at',0)<=now:timings.append(t)
        opportunity['opening_profiles']=opening_profiles(timings,purchases,predictions)
        stages=defaultdict(lambda:defaultdict(list))
        for t in timings:
            for name,value in t.get('stages',{}).items():
                if isinstance(value,(int,float)):stages[t.get('version','unknown')][name].append(value)
        opportunity['stage_profiles']={version:{name:dict(n=len(v),median=quantile(v),p90=quantile(v,.9)) for name,v in fields.items()} for version,fields in stages.items()}
        opportunity['no_settlement_semantics']='Kit submits receipt=null on no-deal; no settle/q contribution observed. Distinct subsequent-order wait is charged; order and Deals counts are separate.'
    atomic(md/'buyer-opportunity.json',opportunity)
    for identity,e in purchases.items():
        d=e['data'];sku=d.get('sku',d.get('item'));p=catalog.get(sku,{})
        if not p.get('list_price'):continue
        unit=d.get('unit');prediction=predictions.get(identity);kind=p.get('kind','goods');cost=exact.get(unit)
        life=local_orders.get(identity,{}).get('use_within_hours') or 0
        horizon=6*3600+300 if kind=='goods' else 300
        mature=e['ts']+horizon<=now
        official_use=use.get(unit);use_source='explicit_use_event' if unit in use else None
        covered=any(start<=e['ts'] and stop>=e['ts']+horizon for start,stop in segments)
        if kind=='food' and identity in local_orders and isinstance(d.get('expires_at'),(int,float)):
            official_use=(d['expires_at']-e['ts'])/3600>=life;use_source='official_food_life_at_settle'
        elif kind=='goods' and unit in broken:
            official_use=False;use_source='official_break_within_6h'
        elif kind=='goods' and mature and covered:
            official_use=True;use_source='no_break_over_contiguous_6h_ledger'
        raw=None if cost is None or cost>=p['list_price'] else min(1,(p['list_price']-d['amount'])/(p['list_price']-cost))
        if cost is not None and d['amount']<cost:raw=1.
        final=0. if refunds.get(unit,0)>0 else -1. if official_use is False else raw if official_use is True else None
        row=dict(order=identity,settled_at=e['ts'],sku=sku,grade=d.get('grade'),kind=kind,price=d['amount'],
                 exact_cost=cost,price_score_exact=raw,predicted_score=prediction.get('expected_score') if prediction else None,
                 predicted_raw_score=prediction.get('raw_score') if prediction else None,refunded=refunds.get(unit,0)>0,
                 explicit_use=official_use,use_source=use_source,ledger_horizon_contiguous=covered,final_official_reconstructable=final is not None and mature,final_score=final if mature else None,
                 mature=mature,unconfirmed_reason='pending_horizon' if not mature else 'cost_unknown' if cost is None else 'nonpositive_margin_undefined' if raw is None else 'use_outcome_not_public' if final is None else None)
        rows.append(row)
        if mature:
            counts=outcomes[f'{kind}:{d.get("grade")}'];counts['n']+=1
            if refunds.get(unit,0)>0:counts['refunded']+=1
            elif official_use is True:counts['normal']+=1
            elif official_use is False:counts['failed']+=1
            else:counts['unknown']+=1
        if mature and raw is not None and prediction and prediction.get('raw_score') is not None:
            bias[f'{kind}:{d.get("grade")}'].append((raw-prediction['raw_score'],2**(-(now-e['ts'])/21600)))
    contract_outcomes=defaultdict(lambda:dict(n=0,normal=0,refunded=0,failed=0,unknown=0))
    from patch36 import warranty_bucket
    for r in rows:
        if not r['mature']:continue
        event=purchases.get(r['order'])
        hours=(event or {}).get('data',{}).get('warranty_hours') or 0
        counts=contract_outcomes[f"{r['kind']}:{r['grade']}:{warranty_bucket(hours)}"]
        counts['n']+=1
        if r['refunded']:counts['refunded']+=1
        elif r['explicit_use'] is True:counts['normal']+=1
        elif r['explicit_use'] is False:counts['failed']+=1
        else:counts['unknown']+=1
    summaries={}
    for hours in (6,12,24):
        rr=[r for r in rows if now-hours*3600<=r['settled_at']<=now];known=[r for r in rr if r['final_official_reconstructable']]
        summaries[str(hours)]=dict(purchases=len(rr),mature=sum(r['mature'] for r in rr),exact_cost=sum(r['exact_cost'] is not None for r in rr),
            final_known=len(known),final_score_mean=statistics.mean(r['final_score'] for r in known) if known and len(known)==len(rr) else None,
            known_subset_score_mean=statistics.mean(r['final_score'] for r in known) if known else None,known_subset_is_selected=len(known)!=len(rr),
            all_purchase_score_lower=(sum(r['final_score'] for r in known)-(len(rr)-len(known)))/len(rr) if rr else None,
            all_purchase_score_upper=(sum(r['final_score'] for r in known)+(len(rr)-len(known)))/len(rr) if rr else None,
            score_bounds_basis='Known reconstructed scores plus [-1,1] for every unknown or immature purchase; not official Saved.',
            pending=sum(not r['mature'] for r in rr),use_unknown=sum(r['mature'] and r['explicit_use'] is None and not r['refunded'] for r in rr),
            predicted_known=sum(r['predicted_score'] is not None for r in rr))
    atomic(md/'buyer-cohort-calibration.json',dict(at=now,windows=summaries,purchases=rows,
        contract_outcomes={k:dict(v,complete=v['unknown']==0) for k,v in contract_outcomes.items()},
        price_bias={k:dict(n=len(v),effective_n=sum(w for _,w in v),correction=sum(error*w for error,w in v)/(sum(w for _,w in v)+4)) for k,v in bias.items()},
        final_outcomes={k:dict(v,complete=v['unknown']==0,status='observed' if v['unknown']==0 else 'incomplete_final_use_no_risk_correction') for k,v in outcomes.items()},
        final_use_calibration='Official food-life-at-settle rule; goods break/refund or contiguous no-break 6h. Gaps remain unknown.'))
    if policy.get('buyer_reconciliation_enabled'):
        row_map={r['order']:r for r in rows};stage_data=defaultdict(lambda:defaultdict(list))
        for t in timings:
            identity=t.get('order_id');stages=dict(t.get('stages',{}));finish=t.get('finished_at',t.get('started_at',0)+t.get('elapsed_seconds',0))
            if identity in orders:stages['order_to_subprocess_start_seconds']=max(0,t['started_at']-orders[identity])
            if identity in results:stages['result_output_and_checkout_seconds']=max(0,results[identity]-finish)
            for name,value in stages.items():
                if isinstance(value,(int,float)):stage_data[t.get('version','unknown')][name].append((value,identity))
        diagnostics={}
        for version,fields in stage_data.items():
            diagnostics[version]={}
            for name,values in fields.items():
                ids=[i for _,i in values];known=[row_map[i]['final_score'] for i in ids if row_map.get(i,{}).get('final_official_reconstructable')]
                expected=[predictions[i]['expected_score'] for i in ids if predictions.get(i,{}).get('expected_score') is not None]
                diagnostics[version][name]=dict(n=len(values),median=quantile([v for v,_ in values]),p90=quantile([v for v,_ in values],.9),
                    actual_settled=sum(i in purchases for i in ids),expected_mean=statistics.mean(expected) if expected else None,
                    final_known=len(known),known_subset_mean=statistics.mean(known) if known else None,attribution='same-order outcome association, not stage causal effect')
        atomic(md/'buyer-stage-diagnostics.json',dict(at=now,versions=diagnostics))
        state=read(md/'strategy-v101-review.json',{}) or read(md/'strategy-v9-review.json',{})
        official=state.get('checkpoint') or read(md/'patch36-scoreboard.json',{})
        if state.get('baseline') and official.get('buyer'):
            end=state['cohort_end'] if state.get('checkpoint') else official.get('at',now)
            accounting=reconcile_buyer_counts(events,state['baseline'],official,state['start'],end)
            new=[r for r in rows if state['start']<=r['settled_at']<=end];known=[r for r in new if r['final_official_reconstructable']]
            pp=[predictions[r['order']]['expected_score'] for r in new if r['order'] in predictions and predictions[r['order']].get('expected_score') is not None]
            accounting.update(at=now,version=policy['version'],predicted_n=len(pp),predicted_mean=statistics.mean(pp) if pp else None,
                final_known=len(known),final_known_subset_mean=statistics.mean(r['final_score'] for r in known) if known else None,
                full_new_final_mean=statistics.mean(r['final_score'] for r in known) if known and len(known)==len(new) else None,
                refunded=sum(r['refunded'] for r in new),failed_observed=sum(r['explicit_use'] is False and not r['refunded'] for r in new),
                immature=sum(not r['mature'] for r in new),cost_or_outcome_unknown=sum(r['mature'] and not r['final_official_reconstructable'] for r in new))
            atomic(md/'buyer-v101-accounting.json',accounting)
