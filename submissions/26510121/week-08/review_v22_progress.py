"""Frozen Shop/Buyer diagnostics; GET only, no LLM, trades or policy writes."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
import re
import sqlite3
import statistics
import time
from r3.state import ROOT,read_json,policy
from compare_market_peers import capture

KST=timezone(timedelta(hours=9))
BASE21='logs/autonomous-deploy-before-20261112T082837273128Z.json'
BASE22='logs/autonomous-deploy-before-20261112T093651139559Z.json'


def iso(t):return datetime.fromtimestamp(t,KST).isoformat()


def dist(values):
    values=sorted(v for v in values if type(v) in (int,float) and math.isfinite(v))
    return {'n':len(values),'median':statistics.median(values) if values else None,
            'p95':values[max(0,math.ceil(.95*len(values))-1)] if values else None,
            'max':max(values) if values else None}


def ranks(board,role):
    fields={'shops':['all','book','revenue','stars_rank','deals'],
            'buyers':['all','saved','forum','deals','posts'],'players':['total']}[role]
    key='player' if role=='players' else 'name'
    rows=[r for r in board[role] if type(r.get(fields[0])) in (int,float)]
    me=next(r for r in rows if r[key]=='seochanit')
    return {m:{'value':me.get(m),'rank':1+sum(r[m]>me[m] for r in rows if type(r.get(m)) in (int,float)),
        'population':len(rows)} for m in fields}


def main():
    source=capture();end=source['responses']['/api/board']['finished_at'];stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    inv=read_json(ROOT/'.market/inventory.json');inv_at=(ROOT/'.market/inventory.json').stat().st_mtime
    runtime=read_json(ROOT/'.market/runtime.json');start=runtime['started_at'];duration=end-start
    health=read_json(ROOT/'.market/shop-health.json');cfg=policy();catalog=read_json(ROOT/'.market/catalog.json')
    source['local']={'inventory_at':inv_at,'inventory':dict(inv,units=[{k:u.get(k) for k in
        ('unit','sku','kind','cost','grade','supplied_at','expires_at')} for u in inv.get('units',[])]),
        'runtime':runtime,'health':health}
    path=ROOT/'logs'/('v22-progress-source-'+stamp+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(source,ensure_ascii=False,indent=2))
    board=source['responses']['/api/board']['data'];base21=read_json(ROOT/BASE21);base22=read_json(ROOT/BASE22)
    windows={};observed={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def rows(kind,lo,hi=end):
            return [dict(json.loads(raw),_event_id=eid,_ts=at) for eid,at,raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,lo,hi))]
        def kv(key,default=None):
            r=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(r[0]) if r else default
        all_sales={r['unit']:r for r in rows('shop_settled',0) if r.get('unit')}
        for label,lo,hi in [('before_equal_duration',start-duration,start),('v22',start,end),('v21_and_v22',base21['at'],end)]:
            orders={r['order']:r for r in rows('buyer_order',lo,hi)}
            results={r['order']:r for r in rows('market_result',lo,hi)}
            purchases={r['order']:r for r in rows('purchase',lo,hi)}
            quotes=rows('quote_comparison',lo,hi);attempts=rows('attempt',lo,hi)
            segments={}
            for kind in ('food','goods'):
                ids={o for o,r in orders.items() if r.get('kind')==kind};closed=ids&results.keys();paid=closed&purchases.keys()
                qs=[q for q in quotes if q.get('order') in closed]
                segments[kind]={'orders':len(ids),'closed':len(closed),'pending':len(ids-closed),'paid':len(paid),
                    'conversion':len(paid)/len(closed) if closed else None,
                    'seconds':dist([results[o]['_ts']-orders[o]['_ts'] for o in closed]),
                    'quotes':len(qs),'above_cap':sum(q['ask']>q['assessment']['cap'] for q in qs),
                    'above_saved_reference':sum(q['ask']>q['assessment'].get('saved_reference_cap',q['assessment']['cap']) for q in qs),
                    'attempt_reasons':dict(Counter(r.get('kind') for r in attempts if r.get('order') in closed)),
                    'new_goods_6h_mature':sum(hi-purchases[o].get('paid_at',purchases[o]['_ts'])>=21600 for o in paid)}
            sales={u:r for u,r in all_sales.items() if lo<=r.get('paid_at',r['_ts'])<=hi}
            known=[r for r in sales.values() if type(r.get('cost')) in (int,float)]
            refunds={}
            for r in rows('public_broken',lo,hi)+rows('public_refund',lo,hi):
                amount=r.get('refunded',r.get('amount'));unit=r.get('unit')
                if unit in all_sales and type(amount) in (int,float) and amount>=0:
                    refunds[unit]=max(amount,refunds.get(unit,0))
            stockouts=rows('stockout',lo,hi);supply=rows('supply_execution_status',lo,hi);reviews=rows('shop_review_observed',lo,hi)
            distinct_reviews={str(r.get('ledger'))+':'+str(r.get('author')):r for r in reviews}
            completed=[r for r in supply if r.get('status')=='complete']
            mix={}
            for kind in ('food','goods'):
                group=[r for r in known if catalog.get(r['sku'],{}).get('kind')==kind]
                mix[kind]={'sales':len(group),'revenue':sum(r['price'] for r in group),
                    'gross_margin':sum(r['price']-r['cost'] for r in group)}
            windows[label]={'start_kst':iso(lo),'end_kst':iso(hi),'minutes':(hi-lo)/60,
                'buyer':segments,'buyer_purchases_by_arrival':len(purchases),'buyer_objective_skips':len(rows('buyer_objective_skip',lo,hi)),
                'shop':{'sales':len(sales),'revenue':sum(r['price'] for r in sales.values()),
                    'gross_margin':sum(r['price']-r['cost'] for r in known),'missing_costs':len(sales)-len(known),
                    'below_cost':sum(r['price']<r['cost'] for r in known),'refunds_in_window_all_sale_cohorts':sum(refunds.values()),
                    'mix':mix,'stockout_events':len(stockouts),'stockout_distinct_tasks':len({r.get('task') for r in stockouts}),
                    'stockout_reasons':dict(Counter(r.get('reason','legacy') for r in stockouts)),
                    'stockout_top_skus':Counter(r.get('sku') for r in stockouts).most_common(8),
                    'supply_statuses':dict(Counter(r['status'] for r in supply)),
                    'completed_supply_units':sum(r.get('qty',0) for r in completed),
                    'completed_qty_histogram':dict(Counter(str(r.get('qty')) for r in completed)),
                    'restock_blocks':dict(Counter(r.get('reason') for r in rows('restock_blocked',lo,hi))),
                    'new_review_stars':dict(Counter(str(r.get('stars')) for r in distinct_reviews.values())),
                    'new_review_mean':statistics.mean(r['stars'] for r in distinct_reviews.values()) if distinct_reviews else None,
                    'low_review_examples':[{k:r.get(k) for k in ('stars','review','author','ledger')} for r in distinct_reviews.values() if r.get('stars',5)<=3][:10]},
                'execution_seconds':{k:dist([r.get('seconds') for r in rows(k,lo,hi)]) for k in
                    ['shop_fast_execution','buyer_saved_execution','buyer_worker_execution','buyer_quote_latency']}}
        policies=rows('market_research_published',start);compiled=rows('feedback_policy_created',start);choices=rows('feedback_main_choice',start)
        ids={r['id'] for r in compiled if r.get('policy_authority')=='agent_selected'}
        control=kv('market_control',{});demand=kv('shop-demand-policy:v21',{});objective=kv('objective-decision-context',{})
        current_policy=kv('feedback-last-policy',{})
        usage=rows('model_usage',start)
        model_buckets=defaultdict(lambda:{'calls':0,'priced_token_estimate_usd':0.,'input_tokens':0,'output_tokens':0})
        for r in usage:
            u=r.get('usage') or {};rates=cfg.get('prices_usd_per_million',{}).get(r.get('model'));cache=cfg.get('cache_prices_usd_per_million',{}).get(r.get('model'))
            details=u.get('input_tokens_details') or {};cached=details.get('cached_tokens',0);written=details.get('cache_write_tokens',0)
            price=((max(0,u.get('input_tokens',0)-cached-written)*rates[0]+cached*cache[0]+written*cache[1]+u.get('output_tokens',0)*rates[1])/1e6
                   if rates and cache else r.get('estimated_usd',0))
            bucket=model_buckets[r.get('role','unknown')+' / '+r.get('model','unknown')]
            bucket['calls']+=1;bucket['priced_token_estimate_usd']+=price
            bucket['input_tokens']+=u.get('input_tokens',0);bucket['output_tokens']+=u.get('output_tokens',0)
        observed={'current_control':control,'current_demand_summary':demand.get('summary'),
            'demand_highest_rates':sorted([dict(sku=s,**r) for s,r in demand.get('skus',{}).items()],key=lambda r:r['rate_per_hour'],reverse=True)[:12],
            'research_publications':len(policies),'distinct_operating_policies':len({json.dumps(r.get('operating_policy'),sort_keys=True) for r in policies}),
            'research':[{k:r.get(k) for k in ('at','summary','operating_intents','operating_policy','forum_focus','trial_review_approved')} for r in policies],
            'current_policy':{k:current_policy.get(k) for k in ('at','status','policy_authority','patches','reasons')},
            'main_choices':dict(Counter(r['role'] for r in choices if not r.get('preview'))),
            'agent_policy_linked_main_choices':dict(Counter(r['role'] for r in choices if r.get('policy_id') in ids and not r.get('preview'))),
            'roles':kv('analytical-role-status'),'framework':kv('framework-plan'),'quality_holdout':kv('quality-holdout-summary'),
            'buyer_objective':objective.get('buyer'),
            'forum':{'deliveries':[{k:r.get(k) for k in ('post_id','information_kind','status','at')} for r in rows('forum_delivery',start) if r.get('role')=='buyer'],
                'policy_choices':dict(Counter(r.get('focus') for r in rows('buyer_forum_policy_choice',start))),
                'formats':(objective.get('buyer') or {}).get('forum_formats')},
            'shared_model_cost':{'calls':len(usage),'priced_token_estimate_usd':sum(r['priced_token_estimate_usd'] for r in model_buckets.values()),'by_role':dict(model_buckets)},
            'errors':{k:len(rows(k,start)) for k in ('market_control_error','cycle_supervisor_error','cycle_supply_error',
                'buyer_worker_error','analytical_role_error','objective_context_error','strategy_error','forum_error')},
            'recovery_events':{k:len(rows(k,start)) for k in ('shop_transport_recovery','shop_transport_unhealthy','shop_recovery_observation')},
            'latest_recovery_event_kinds':db.execute("SELECT kind,count(*) FROM events WHERE ts>=? AND (kind LIKE '%recover%' OR kind LIKE '%transport%') GROUP BY kind",(start,)).fetchall(),
            'rules':kv('market_rules'),
            'inventory_observation':{'at':inv_at,'cash':inv['balance'],'stock_cost':sum(u['cost'] for u in inv['units']),
                'units':len(inv['units']),'skus':len({u['sku'] for u in inv['units']}),'start':inv.get('start'),'injected':inv.get('injected')}}
    comparisons={}
    for label,baseline in [('since_v21',base21),('since_v22_snapshot',base22)]:
        changes={};hours=(end-baseline['at'])/3600
        for role in ('shops','buyers'):
            old={r['name']:r for r in baseline['board'][role]};groups=[]
            for r in board[role]:
                p=old.get(r['name'])
                if not p:continue
                fields=('book','revenue','deals','all','stock_units','stock_cost','stars_rank','refunds') if role=='shops' else ('saved','deals','forum','posts','all')
                delta={f:r[f]-p[f] for f in fields if type(r.get(f)) in (int,float) and type(p.get(f)) in (int,float)}
                groups.append({'name':r['name'],'current':{f:r.get(f) for f in fields},'delta':delta,
                    'per_hour':{f:v/hours for f,v in delta.items()}})
            changes[role]=groups
        comparisons[label]={'start':iso(baseline['at']),'end':iso(end),'hours':hours,'peers':changes,
            'baseline_ranks':{role:ranks(baseline['board'],role) for role in ('shops','buyers')}}
    current_buyer=next(r for r in board['buyers'] if r['name']=='seochanit')
    old_buyer=next(r for r in base22['board']['buyers'] if r['name']=='seochanit')
    n=current_buyer['deals']-old_buyer['deals'];D=current_buyer['deals']+100;oldD=old_buyer['deals']+100
    implied=(current_buyer['saved']*D-old_buyer['saved']*oldD)/n if n>0 else None
    dilution={'new_official_deals':n,'implied_new_contribution_mean_if_no_old_quality_revisions':implied,
        'rounding_error_bound_if_saved_rounded_to_4_decimals':.00005*(D+oldD)/n if n>0 else None,
        'no_quality_revision_assumption_required':True,
        'maximum_saved_with_only_these_new_deals':(old_buyer['saved']*oldD+n)/D,
        'static_peer_deals_needed':{str(target):{str(q):math.ceil(max(0,(target-current_buyer['saved'])*D/(q-target)))
            if q>target else None for q in (1,.9,.8)} for target in (.7096,.7123,.8093)}}
    oldinv=base22['inventory'];oldcost=sum(u['cost'] for u in oldinv['units']);stock=sum(u['cost'] for u in inv['units'])
    assets={'start_at':iso(base22['at']),'end_inventory_at':iso(inv_at),
        'cash_delta':inv['balance']-oldinv['balance'],'stock_cost_delta':stock-oldcost,
        'cash_plus_stock_cost_delta':inv['balance']+stock-oldinv['balance']-oldcost,
        'initial_units':len(oldinv['units']),'current_units':len(inv['units']),
        'capital_in_stock_fraction':stock/(stock+inv['balance']),
        'capital_scope':'physical cash+stock, not official Book level; seed/injected offsets and timing can differ'}
    acquisitions=[];console=ROOT/'logs'/(runtime['launch_id']+'.txt')
    for line in console.read_text(encoding='utf-8',errors='replace').splitlines():
        m=re.match(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.*?) for (\d+), balance (\d+)',line)
        if m:
            at=datetime.fromtimestamp(start,KST).replace(hour=int(m[1][:2]),minute=int(m[1][3:5]),second=int(m[1][6:]),microsecond=0).timestamp()
            if start<=at<=end:acquisitions.append({'at':at,'qty':int(m[2]),'cost':int(m[4])})
    assets['confirmed_runtime_console_acquisition_units']=sum(r['qty'] for r in acquisitions)
    assets['confirmed_runtime_console_acquisition_cost']=sum(r['cost'] for r in acquisitions)
    result={'source':str(path.relative_to(ROOT)),'board_cutoff_kst':iso(end),'runtime_start_kst':iso(start),
        'ranks':{role:ranks(board,role) for role in ('shops','buyers','players')},'comparisons':comparisons,
        'operations':windows,'assets':assets,'buyer_dilution':dilution,'framework':observed,
        'market_transactions_by_this_script':0,'model_calls_by_this_script':0,
        'limitations':['Equal windows are observational; market conditions, offered order mix and outages differ.',
            'Post-v22 goods cannot have six-hour mature outcomes yet. Hidden-cost scenarios are not exact saved rewards.',
            'Rounded board differences plus revisions of old quality outcomes do not identify a new cohort exactly.',
            'Inventory and KV reads are at their observation times; event windows use the frozen board cutoff.',
            'Token prices are configured estimates, not an API invoice. Cash credits are not real USD.']}
    output=ROOT/'logs'/('v22-progress-analysis-'+stamp+'.json')
    output.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path);print('ANALYSIS',output)
    print(json.dumps({'ranks':result['ranks'],'operations':windows,'assets':assets,'buyer_dilution':dilution},ensure_ascii=True))


if __name__=='__main__':main()
