"""Frozen public board plus read-only journal analysis; no model/trade/policy calls."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import hashlib
import json
import math
import re
import sqlite3
import statistics
import time
from r3.state import ROOT,read_json,policy
from autonomous_status import ReadJournal
from r3.economy import cash_capacity
from review_v22_progress import ranks,dist

SOURCE='logs/peer-market-source-20261113T025926940046Z.json'
BASE='logs/autonomous-deploy-before-20261113T021028458193Z.json'
KST=timezone(timedelta(hours=9))
def iso(t):return datetime.fromtimestamp(t,KST).isoformat()


def main():
    source=read_json(ROOT/SOURCE);baseline=read_json(ROOT/BASE)
    board=source['responses']['/api/board']['data'];end=source['responses']['/api/board']['finished_at']
    local_at=time.time();runtime=read_json(ROOT/'.market/runtime.json');start=runtime['started_at'];duration=end-start
    inv=read_json(ROOT/'.market/inventory.json');inv_at=(ROOT/'.market/inventory.json').stat().st_mtime
    catalog=read_json(ROOT/'.market/catalog.json');cfg=policy();health=read_json(ROOT/'.market/shop-health.json')
    before={role:next(r for r in baseline['board'][role] if r.get('name',r.get('player'))=='seochanit') for role in ('shops','buyers','players')}
    after={role:next(r for r in board[role] if r.get('name',r.get('player'))=='seochanit') for role in before}
    window_specs=[('before_equal',start-duration,start),('v24',start,end)]
    windows={};observed={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,local_at)
        def events(kind,lo,hi=end):
            return [dict(json.loads(raw),_event_id=eid,_ts=at) for eid,at,raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid',(kind,lo,hi))]
        own_sales={r['unit']:r for r in events('shop_settled',end-172800) if r.get('unit')}
        for label,lo,hi in window_specs:
            sales=[r for r in own_sales.values() if lo<=r.get('paid_at',r['_ts'])<hi]
            known=[r for r in sales if type(r.get('cost')) in (int,float)]
            mix={}
            for kind in ('food','goods'):
                group=[r for r in known if catalog.get(r.get('sku'),{}).get('kind')==kind]
                mix[kind]={'sales':len(group),'revenue':sum(r['price'] for r in group),
                    'gross_margin':sum(r['price']-r['cost'] for r in group),
                    'grades':dict(Counter(r.get('grade') for r in group)),
                    'warranties':dict(Counter(str(r.get('warranty_hours',0)) for r in group))}
            orders={r['order']:r for r in events('buyer_order',lo,hi)}
            results={r['order']:r for r in events('market_result',lo,hi)}
            purchases={r['order']:r for r in events('purchase',lo,hi)}
            all_purchases={r['order']:r for r in events('purchase',lo-3600,hi)}
            segments={}
            quotes=events('quote_comparison',lo,hi);attempts=events('attempt',lo,hi)
            for kind in ('goods','food'):
                ids={o for o,r in orders.items() if r.get('kind')==kind};closed=ids&results.keys();paid=closed&purchases.keys()
                qs=[q for q in quotes if q.get('order') in closed]
                segments[kind]={'orders':len(ids),'closed':len(closed),'paid':len(paid),'pending':len(ids-closed),
                    'conversion':len(paid)/len(closed) if closed else None,
                    'seconds':dist([results[o]['_ts']-orders[o]['_ts'] for o in closed]),
                    'quotes':len(qs),'above_cap':sum(q['ask']>q['assessment']['cap'] for q in qs),
                    'attempt_reasons':dict(Counter(r.get('kind') for r in attempts if r.get('order') in closed)),
                    'matured_6h':sum(hi-purchases[o].get('paid_at',purchases[o]['_ts'])>=21600 for o in paid)}
            supply=events('supply_execution_status',lo,hi);completed=[r for r in supply if r.get('status')=='complete']
            reviews={str(r.get('ledger'))+':'+str(r.get('author')):r for r in events('shop_review_observed',lo,hi)}
            low=[]
            for r in reviews.values():
                if r.get('stars',5)<=3:
                    sale=next((s for s in own_sales.values() if s['_event_id']=='settlement:'+str(r.get('ledger'))),{})
                    low.append({k:r.get(k) for k in ('stars','author','review','ledger')}|{'sale':{k:sale.get(k) for k in ('sku','grade','price','cost','warranty_hours')}})
            refunds={}
            for r in events('public_broken',lo,hi)+events('public_refund',lo,hi):
                if r.get('unit') in own_sales and type(r.get('refunded',r.get('amount'))) in (int,float):
                    refunds[r['unit']]=max(refunds.get(r['unit'],0),r.get('refunded',r.get('amount')))
            stockouts=events('stockout',lo,hi)
            predictions=events('prediction',lo,hi)
            realized=events('realized_reward',lo,hi)
            windows[label]={'start':iso(lo),'end':iso(hi),'minutes':(hi-lo)/60,
                'shop':{'sales':len(sales),'revenue':sum(r['price'] for r in sales),
                    'gross_margin':sum(r['price']-r['cost'] for r in known),'missing_costs':len(sales)-len(known),
                    'below_cost':sum(r['price']<r['cost'] for r in known),'mix':mix,
                    'paid_minus_refund_gross_margin_all_sale_cohorts':sum(r['price']-r['cost'] for r in known)-sum(refunds.values()),
                    'refunds_during_window_all_sale_cohorts':sum(refunds.values()),
                    'stockouts':dict(Counter(r.get('reason','legacy') for r in stockouts)),
                    'stockout_tasks':len({r.get('task') for r in stockouts}),
                    'supply_statuses':dict(Counter(r['status'] for r in supply)),
                    'completed_supply_units':sum(r.get('qty',0) for r in completed),
                    'completed_supply_lines':len(completed),
                    'supply_by_window':[{'window':w,'lines':len(rs),'qty':sum(r['qty'] for r in rs)} for w,rs in
                        ((w,[r for r in completed if r.get('window')==w]) for w in sorted({r.get('window') for r in completed}))],
                    'restock_blocks':dict(Counter(r.get('reason') for r in events('restock_blocked',lo,hi))),
                    'review_stars':dict(Counter(str(r.get('stars')) for r in reviews.values())),
                    'review_mean':statistics.mean(r['stars'] for r in reviews.values()) if reviews else None,'low_reviews':low},
                'buyer':{'segments':segments,'purchases':len(purchases),'initial_surplus':sum(r.get('value',0)-r['price'] for r in purchases.values()),
                    'verified_results':sum(r.get('verified') is True for r in results.values()),
                    'objective_skips':len(events('buyer_objective_skip',lo,hi)),
                    'skip_stages':dict(Counter(r.get('stage') for r in events('buyer_objective_skip',lo,hi))),
                    'order_failure_reasons':dict(Counter(r.get('kind') for r in attempts)),
                    'forum_deliveries':[{k:r.get(k) for k in ('post_id','information_kind','status')} for r in events('forum_delivery',lo,hi) if r.get('role')=='buyer']},
                'latencies':{kind:dist([r.get('seconds') for r in events(kind,lo,hi)]) for kind in
                    ('shop_fast_execution','buyer_saved_execution','buyer_worker_execution','buyer_quote_latency')},
                'control':{'samples':len(events('market_control',lo,hi)),
                    'statuses':dict(Counter(r.get('status') for r in events('market_control',lo,hi))),
                    'reasons':dict(Counter(reason for r in events('market_control',lo,hi) for reason in r.get('reasons',[])))}}
        usage=events('model_usage',start);buckets=defaultdict(lambda:{'calls':0,'estimated_usd':0,'input_tokens':0,'output_tokens':0})
        for r in usage:
            u=r.get('usage') or {};rates=cfg.get('prices_usd_per_million',{}).get(r.get('model'));cache=cfg.get('cache_prices_usd_per_million',{}).get(r.get('model'))
            details=u.get('input_tokens_details') or {};cached=details.get('cached_tokens',0);written=details.get('cache_write_tokens',0)
            cost=((max(0,u.get('input_tokens',0)-cached-written)*rates[0]+cached*cache[0]+written*cache[1]+u.get('output_tokens',0)*rates[1])/1e6
                if rates and cache else r.get('estimated_usd',0))
            b=buckets[r.get('role','?')+' / '+r.get('model','?')];b['calls']+=1;b['estimated_usd']+=cost
            b['input_tokens']+=u.get('input_tokens',0);b['output_tokens']+=u.get('output_tokens',0)
        feedback=j.get('feedback-last-policy',{});research=j.get('research-last-result',{});merchant=j.get('merchant_context_v5',{})
        choices=events('feedback_main_choice',start)
        agent_policy_ids={r['id'] for r in events('feedback_policy_created',start)
            if r.get('policy_authority')=='agent_selected'}
        observed={'local_at':iso(local_at),'rules':j.get('market_rules'),'control':j.get('market_control'),
            'feed_age_seconds':local_at-j.get('public_feed_caught_up_at',0),
            'board_age_seconds':local_at-j.get('board_checked_at',0),
            'analytical_roles':j.get('analytical-role-status'),'cycle_preparation':j.get('cycle-last-preparation'),
            'feedback':{k:feedback.get(k) for k in ('at','expires_at','status','policy_authority','patches','reasons','reviewed_intents')},
            'research':{k:research.get(k) for k in ('at','expires_at','summary','operating_intents','operating_policy','operating_actions_reviewed','economic_superiority_verified')},
            'research_publications':len(events('market_research_published',start)),
            'main_feedback_choices':dict(Counter(r.get('role') for r in choices if not r.get('preview'))),
            'agent_selected_choices':dict(Counter(r.get('role') for r in choices if not r.get('preview') and r.get('policy_id') in agent_policy_ids)),
            'merchant_summary':merchant.get('summary'),
            'top_demand':sorted([dict(sku=k,**{key:v.get(key) for key in ('arrival_rate_scenario','physical_stock','available_stock','positive_paid_while_available','coverage_hours')}) for k,v in merchant.get('skus',{}).items()],key=lambda r:r.get('arrival_rate_scenario') or 0,reverse=True)[:10],
            'model_usage':{'calls':len(usage),'estimated_usd':sum(b['estimated_usd'] for b in buckets.values()),'by_role':dict(buckets),
                'astra_calls':sum('astra' in r.get('model','').lower() for r in usage)},
            'reputation_summary':j.get('shop-reputation:v24',{}).get('summary'),
            'errors':{k:len(events(k,start)) for k in ('market_control_error','cycle_supervisor_error','cycle_supply_error','buyer_worker_error','analytical_role_error','objective_context_error','strategy_error')},
            'cash_capacity':cash_capacity(inv,j,cfg,local_at)}
        local_sales={r['unit']:r for r in events('shop_settled',baseline['at'],inv_at) if r.get('unit')}
    initial=baseline['inventory'];initial_units=initial['units'];current_ids={u['unit'] for u in inv['units']}
    new_sales=[r for r in own_sales.values() if start<=r.get('paid_at',r['_ts'])<end];sold_ids={r['unit'] for r in new_sales}
    acquisitions=[];local_acquisitions=[]
    for line in (ROOT/'logs'/(runtime['launch_id']+'.txt')).read_text(encoding='utf-8',errors='replace').splitlines():
        m=re.match(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.*?) for (\d+), balance (\d+)',line)
        if not m:continue
        at=datetime.fromtimestamp(start,KST).replace(hour=int(m[1][:2]),minute=int(m[1][3:5]),second=int(m[1][6:]),microsecond=0).timestamp()
        entry={'at':at,'qty':int(m[2]),'cost':int(m[4]),'name':m[3]}
        if start<=at<=end:acquisitions.append(entry)
        if baseline['at']<=at<=inv_at:local_acquisitions.append(entry)
    expired=[u for u in initial_units if u.get('expires_at') and u['expires_at']<=end and u['unit'] not in current_ids and u['unit'] not in sold_ids]
    groups={}
    for kind in ('goods','food'):
        us=[u for u in inv['units'] if u.get('kind')==kind]
        groups[kind]={'units':len(us),'skus':len({u['sku'] for u in us}),'cost':sum(u['cost'] for u in us),
            'grades':dict(Counter(u.get('grade') for u in us))}
    cost=sum(u['cost'] for u in inv['units']);oldcost=sum(u['cost'] for u in initial_units)
    assets={'inventory_at':iso(inv_at),'baseline_at':iso(baseline['at']),
        'cash':inv['balance'],'cash_before':initial['balance'],'cash_delta':inv['balance']-initial['balance'],
        'cost':cost,'cost_before':oldcost,'cost_delta':cost-oldcost,'stock_fraction':cost/(cost+inv['balance']),
        'units_before':len(initial_units),'units_now':len(inv['units']),'skus_now':len({u['sku'] for u in inv['units']}),'mix':groups,
        'confirmed_acquisition_units':sum(r['qty'] for r in acquisitions),'confirmed_acquisition_cost':sum(r['cost'] for r in acquisitions),
        'acquisitions':acquisitions,'identified_baseline_expirations':len(expired),'baseline_expiration_cost':sum(u['cost'] for u in expired),
        'flow_residual_mixed_cutoffs':len(initial_units)+sum(r['qty'] for r in acquisitions)-len(new_sales)-len(expired)-len(inv['units']),
        'cash_plus_stock_cost_delta':inv['balance']+cost-initial['balance']-oldcost}
    local_expired=[u for u in initial_units if u.get('expires_at') and u['expires_at']<=inv_at
        and u['unit'] not in current_ids and u['unit'] not in local_sales]
    assets['aligned_local_flow']={'until':iso(inv_at),'acquisition_units':sum(r['qty'] for r in local_acquisitions),
        'sales':len(local_sales),'baseline_expirations_identified':len(local_expired),
        'residual':len(initial_units)+sum(r['qty'] for r in local_acquisitions)-len(local_sales)-len(local_expired)-len(inv['units']),
        'note':'Snapshot refresh/poll offsets remain; residual is not automatically expiry.'}
    assets['frozen_inventory_units']=[{k:u.get(k) for k in ('unit','sku','kind','grade','cost','supplied_at','expires_at')} for u in inv['units']]
    comparisons={};hours=(end-baseline['at'])/3600
    for role,fields in [('shops',('book','revenue','deals','all','stars_rank','stock_units','refunds')),
                        ('buyers',('saved','surplus','deals','posts','forum','all'))]:
        old={r['name']:r for r in baseline['board'][role]};rows=[]
        for r in board[role]:
            if r['name'] not in old:continue
            p=old[r['name']];delta={k:r[k]-p[k] for k in fields if type(r.get(k)) in (int,float) and type(p.get(k)) in (int,float)}
            rows.append({'name':r['name'],'delta':delta,'per_hour':{k:v/hours for k,v in delta.items()},'current':{k:r.get(k) for k in fields}})
        comparisons[role]=rows
    old_buyer,new_buyer=before['buyers'],after['buyers'];added=new_buyer['deals']-old_buyer['deals'];D=new_buyer['deals']+100;oldD=old_buyer['deals']+100
    dilution={'new_board_deals':added,'observed_saved_delta':new_buyer['saved']-old_buyer['saved'],
        'implied_new_mean_if_no_old_quality_revisions':(new_buyer['saved']*D-old_buyer['saved']*oldD)/added if added>0 else None,
        'rounding_error_bound':.00005*(D+oldD)/added if added>0 else None,
        'next_saved_targets':[{'name':r['name'],'saved':r['saved'],
            'perfect_deals_if_peer_static':math.ceil((r['saved']-new_buyer['saved'])*D/(1-r['saved']))}
            for r in sorted(board['buyers'],key=lambda r:r.get('saved',-2)) if type(r.get('saved')) in (int,float) and new_buyer['saved']<r['saved']<1][:3]}
    output={'source':SOURCE,'baseline_source':BASE,'public_cutoff':iso(end),'local_cutoff':iso(local_at),'runtime':runtime,
        'ranks':{r:ranks(board,r) for r in ('shops','buyers','players')},
        'baseline_ranks':{r:ranks(baseline['board'],r) for r in ('shops','buyers','players')},
        'own_before':before,'own_after':after,'peer_comparison_hours':hours,'peer_changes':comparisons,
        'windows':windows,'assets':assets,'buyer_dilution':dilution,'observations':observed,'health':health,
        'zero_market_transactions':True,'zero_model_calls':True,'zero_policy_writes':True,
        'limitations':['Equal windows are observational; order mix/supply windows and competitor behavior differ.',
            'New goods have no six-hour mature outcomes. Gross margin is not net Book growth.',
            'Public board, local inventory and KV have separate cutoffs; inventory flow residual is not automatically expiry.',
            'Rounded Saved plus old quality revisions cannot identify exact new-cohort reward.',
            'Configured API dollar estimates are not invoices or virtual market credits.']}
    path=ROOT/'logs'/('v24-progress-analysis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(output,ensure_ascii=False,indent=2))
    print('ANALYSIS',path)
    print(json.dumps({k:output[k] for k in ('public_cutoff','ranks','windows','assets','buyer_dilution')},ensure_ascii=True))


if __name__=='__main__':main()
