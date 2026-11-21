"""Frozen public comparisons and read-only, order-matched overnight diagnostics."""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
import re
import sqlite3
import statistics
from pathlib import Path
from r3.state import ROOT, read_json, atomic_json

OLD='logs/peer-market-source-20261109T122827623381Z.json'
NEW='logs/peer-market-source-20261109T235759979196Z.json'
LIVE='logs/autonomous-verified-20261109T235801084011Z.json'
KST=timezone(timedelta(hours=9))


def own(board,role):return next(r for r in board[role] if r.get('name',r.get('player'))=='seochanit')
def med(rows):return statistics.median(rows) if rows else None
def mean(rows):return statistics.mean(rows) if rows else None


def main():
    old,new=[read_json(ROOT/p) for p in (OLD,NEW)]
    a,b=[s['responses']['/api/board']['data'] for s in (old,new)]
    start,end=[s['responses']['/api/board']['finished_at'] for s in (old,new)]
    hours=(end-start)/3600
    result={'sources':[OLD,NEW,LIVE],'start':start,'end':end,'hours':hours,
            'rules_unchanged':old['responses']['/agent.md']['data']==new['responses']['/agent.md']['data'],
            'official':{},'peers':{},'components':{}}
    for role,metric,fields in [('shops','all',['book','revenue','deals','stock_units','stock_cost','stars_rank','refunds','profit']),
                               ('buyers','all',['saved','deals','orders','served','forum','posts']),('players','total',['total'])]:
        def rank(board,row,field):
            rows=[r for r in board[role] if type(r.get(metric)) in (int,float) and type(r.get(field)) in (int,float)]
            return {'rank':1+sum(r[field]>row[field] for r in rows),'population':len(rows)}
        prior,current=own(a,role),own(b,role)
        result['official'][role]={'before':{k:prior.get(k) for k in [metric,*fields]},
            'after':{k:current.get(k) for k in [metric,*fields]},
            'before_rank':rank(a,prior,metric),'after_rank':rank(b,current,metric)}
        result['components'][role]={f:{'before':rank(a,prior,f),'after':rank(b,current,f)} for f in fields if type(prior.get(f)) in (int,float) and type(current.get(f)) in (int,float)}
        oldrows={r.get('name',r.get('player')):r for r in a[role]}
        rows=[]
        for row in b[role]:
            name=row.get('name',row.get('player'));p=oldrows.get(name)
            changes={f:row[f]-p[f] for f in [metric,*fields] if p and type(p.get(f)) in (int,float) and type(row.get(f)) in (int,float)}
            rows.append({'name':name,'new_participant':p is None,'current':{f:row.get(f) for f in [metric,*fields]},
                         'delta':changes,'book_per_hour':changes.get('book',0)/hours if p else None,
                         'deals_per_hour':changes.get('deals',0)/hours if p else None})
        result['peers'][role]=sorted(rows,key=lambda r:r['current'].get(metric) if type(r['current'].get(metric)) in (int,float) else -999,reverse=True)
    result['class']={}
    for f in ('student_shop','student_buyer','student_total'):
        result['class'][f]=[]
        for board in (a,b):
            rows=[r for r in board['players'] if r.get('student') and type(r.get(f)) in (int,float)]
            me=own(board,'players')
            result['class'][f].append({'score':me.get(f),'rank':1+sum(r[f]>me[f] for r in rows),'population':len(rows)})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN')
        def events(kind,t0=start,t1=end):return [dict(json.loads(d),_ts=t) for t,d in c.execute('SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts',(kind,t0,t1))]
        def kv(key,default=None):
            r=c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default
        executions=events('buyer_execution',start,end)
        patched=[r for r in executions if r.get('contract_revision')=='contract-memory-v1']
        patch=min(r['_ts'] for r in patched)
        all_outcomes=defaultdict(list)
        for r in events('outcome',patch-14400,end):all_outcomes[r['order']].append(r)
        public_breaks={r.get('unit'):r for r in events('public_broken',patch-14400,end) if r.get('unit')}
        def cohort(t0,t1):
            orders={r['order']:r for r in events('buyer_order',t0,t1)}
            closed={r['order']:r for r in events('market_result',t0,end) if r.get('order') in orders and r['_ts']<=t1}
            buys={r['order']:r for r in events('purchase',t0,end) if r.get('order') in orders and r['_ts']<=t1}
            attempts=[r for r in events('attempt',t0,t1) if r.get('order') in orders]
            quotes=[r for r in events('quote_comparison',t0,t1) if r.get('order') in orders]
            guards=[r for r in events('buyer_receipt_guard',t0,t1) if r.get('order') in orders]
            bykind={}
            for kind in ('food','goods'):
                ids={oid for oid,r in orders.items() if r.get('kind')==kind}
                finished=ids & closed.keys();paid=ids & buys.keys()
                ps=[buys[oid] for oid in paid]
                eligible=[r for r in ps if r.get('paid_at',r['_ts'])+21600<=end] if kind=='goods' else ps
                def quality(p):
                    rs=all_outcomes[p['order']]
                    broken=any(r.get('broken') for r in rs)
                    latest=rs[-1] if rs else {}
                    return {'broken':broken,'matured':any(r.get('matured') for r in rs),'latest':latest}
                quality_rows=[quality(p) for p in eligible]
                exact_breaks=[public_breaks[p['unit']] for p in eligible if p.get('unit') in public_breaks]
                final_contributions=[(0 if (public_breaks[p['unit']].get('refunded') or 0)>=p['price'] else -1)
                    if p.get('unit') in public_breaks else p['initial_saved_contribution'] for p in eligible]
                qs=[r for r in quotes if r['order'] in ids]
                ats=[r for r in attempts if r['order'] in ids]
                bykind[kind]={'orders':len(ids),'completed':len(finished),'pending':len(ids-finished),'paid':len(paid),
                    'paid_per_hour':len(paid)/((t1-t0)/3600),'conversion':len(paid)/len(finished) if finished else None,
                    'median_seconds':med([closed[oid]['_ts']-orders[oid]['_ts'] for oid in finished]),
                    'initial_saved_mean':mean([r['initial_saved_contribution'] for r in ps]),
                    'six_hour_eligible':len(eligible),'mature_records':sum(r['matured'] for r in quality_rows),
                    'broken':sum(r['broken'] for r in quality_rows),
                    'public_unit_breaks':len(exact_breaks),
                    'public_full_refunds':sum((r.get('refunded') or 0)>=next(p['price'] for p in eligible if p.get('unit')==r['unit']) for r in exact_breaks),
                    'final_saved_mean_from_unit_feed':mean(final_contributions),
                    'public_unit_assumptions':'Broken units matched by unit; absent break after completed six-hour window treated healthy only while public feed is caught up. Partial-refund case needs separate rule interpretation.',
                    'outcome_example':quality_rows[:1],
                    'attempt_reasons':dict(Counter(r.get('reason') or r.get('error_type') or r.get('kind') for r in ats)),
                    'affected_orders':dict(Counter({reason:len({r['order'] for r in ats if (r.get('reason') or r.get('error_type') or r.get('kind'))==reason}) for reason in {r.get('reason') or r.get('error_type') or r.get('kind') for r in ats}})),
                    'quotes':len(qs),'feasible_at_ask':sum(bool(r.get('feasible_at_ask')) for r in qs),
                    'above_cap':sum(r['ask']>r['assessment']['cap'] for r in qs)}
            return {'start':t0,'end':t1,'hours':(t1-t0)/3600,'kinds':bykind,'paid':len(buys),
                    'paid_per_hour':len(buys)/((t1-t0)/3600),
                    'guard_reasons':dict(Counter(reason for r in guards for reason in r.get('reasons',[]))),
                    'guard_orders':len({r['order'] for r in guards}),
                    'guard_by_shop':dict(Counter(r.get('shop') for r in guards))}
        result['patch']={'first_execution':patch,'executions':len(patched),
                         'comparison':'Four hours immediately before and after first revision event; all outcomes observed through frozen current cutoff.'}
        result['buyer_cohorts']={'before_4h':cohort(patch-14400,patch),
            'after_first_4h':cohort(patch,min(patch+14400,end)),
            'after_all':cohort(patch,end),'latest_4h':cohort(end-14400,end)}
        sales=list({r['unit']:r for r in events('shop_settled')}.values())
        owned={r['unit'] for r in events('shop_settled',0,end)}
        refunds=list({r['unit']:r for r in events('public_broken') if r.get('unit') in owned and (r.get('refunded') or 0)>0}.values())
        gm=sum(r['price']-r['cost'] for r in sales)
        gross=sum(r['price'] for r in sales);ref=sum(r['refunded'] for r in refunds)
        sb,sa=[own(board,'shops') for board in (a,b)]
        result['shop_local']={'sales':len(sales),'versions':dict(Counter(r.get('strategy_version') for r in sales)),
            'gross_revenue':gross,'gross_margin':gm,'below_cost':sum(r['price']<r['cost'] for r in sales),
            'refunds':len(refunds),'refund_amount':ref,'book_residual':gm-ref-(sa['book']-sb['book']),
            'revenue_residual':gross-ref-(sa['revenue']-sb['revenue']),
            'refund_sale_cohorts':dict(Counter(datetime.fromtimestamp(r.get('broke_at',r['_ts']),KST).date().isoformat() for r in refunds)),
            'latest_sale_at':max(r['_ts'] for r in sales),
            'top_skus':Counter({sku:sum(r['price']-r['cost'] for r in sales if r['sku']==sku) for sku in {r['sku'] for r in sales}}).most_common(6)}
        result['adaptive']={r:{k:kv('adaptive:'+r,{}).get(k) for k in ('version','patch','status','since','cooldown_until')} for r in ('shop','buyer')}
        result['strategy_actions']=events('strategy_change')
        result['feed_watermark_at_local_read']=kv('public_feed_caught_up_at',0)
        result['feed_watermark_relative_to_cutoff_seconds']=kv('public_feed_caught_up_at',0)-end
        result['late_events']={k:events(k,end-3600,end)[-3:] for k in ('strategy_error','market_control_error','merchant_context_error','forum_error')}
    live=read_json(ROOT/LIVE)['local']
    result['cash']={k:live.get(k) for k in ('cash','stock_units','stock_skus')}
    result['cash']['capacity']={k:v for k,v in live.get('cash_capacity',{}).items() if k!='reserve_release_schedule'}
    inventory=read_json(ROOT/'.market/inventory.json',{})
    units=inventory.get('units',[])
    result['inventory']={'count':len(units),'cost':sum(u['cost'] for u in units),
        'grades':dict(Counter(u.get('grade') for u in units)),'kinds':dict(Counter(u.get('kind') for u in units)),
        'older_than_24h':sum(end-u['supplied_at']>86400 for u in units),'snapshot_at':(ROOT/'.market/inventory.json').stat().st_mtime}
    result['limits']=['Same-name public peers; ALL ranks include published house participants; ties use competition rank.',
        'Each order is counted once; attempt counts are not lost orders. Window-end in-flight orders are pending.',
        'Frozen public cutoff and local snapshots can straddle a settlement. Book/revenue residuals are not assigned to an unobserved cost.',
        'Before/after windows have different demand/peers; not a causal experiment. Cohort maturity is evaluated at the current cutoff.',
        'Forward hourly-rate extrapolation is not a forecast. Model and supply changes remain potential confounders.']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/f'overnight-progress-{stamp}.json';atomic_json(path,result)
    print('SOURCE',path)
    print(json.dumps({k:v for k,v in result.items() if k not in ('peers','strategy_actions','late_events')},ensure_ascii=False))
    for role in ('shops','buyers','players'):
        print(role,json.dumps(result['peers'][role][:8],ensure_ascii=False))


if __name__=='__main__':main()
