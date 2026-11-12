"""Read-only v18 readout from a frozen official board and append-only events.

No model calls, policy changes, registrations or transactions. Each metric uses
an explicit cutoff; current inventory/KV are identified as later observations.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import sqlite3
import statistics
import sys
import time

from r3.state import ROOT,read_json
from review_evening_progress_20261110 import board_view,peer_changes
from autonomous_status import ReadJournal
from r3.economy import cash_capacity
from r3.adaptive import effective
from r3.shop_growth import inventory_limit
from r3.state import policy

BASELINE='logs/autonomous-deploy-before-20261112T033001945939Z.json'
START=1794454214.8553605
KST=timezone(timedelta(hours=9))


def iso(at):return datetime.fromtimestamp(at,KST).isoformat()


def distribution(values):
    values=sorted(values)
    return {'n':len(values),'median':statistics.median(values) if values else None,
            'p95':values[max(0,math.ceil(.95*len(values))-1)] if values else None}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    source_path=sys.argv[1];proof_path=sys.argv[2]
    source=read_json(ROOT/source_path);proof=read_json(ROOT/proof_path);base=read_json(ROOT/BASELINE)
    board=source['responses']['/api/board']['data'];end=source['responses']['/api/board']['finished_at']
    duration=end-START;lo=START-duration
    old,next_board=board_view(base['board']),board_view(board)
    config=policy();inventory=read_json(ROOT/'.market/inventory.json',{})
    inv_at=(ROOT/'.market/inventory.json').stat().st_mtime
    runtime=read_json(ROOT/'.market/runtime.json',{})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,end)
        def rows(kind,a=lo,b=end):
            return [dict(json.loads(raw),_ts=at,_event_id=eid) for eid,at,raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,a,b))]
        sales=rows('shop_settled');orders=rows('buyer_order');results=rows('market_result');purchases=rows('purchase')
        quotes=rows('quote_comparison');attempts=rows('attempt');windows={}
        for label,a,b in [('before_equal_duration',lo,START),('v18',START,end)]:
            paid={r['unit']:r for r in sales if a<=r.get('paid_at',r['_ts'])<=b}
            timestamped={r['unit']:r for r in sales if a<=r['_ts']<=b}
            own_orders={r['order']:r for r in orders if a<=r['_ts']<=b}
            finals={r['order']:r for r in results if r.get('order') in own_orders and r['_ts']<=b}
            bought={r['order']:r for r in purchases if r['_ts']<=b}
            segments={}
            for kind in ('food','goods'):
                ids={o for o,r in own_orders.items() if r.get('kind')==kind};closed=ids&finals.keys()
                successful=closed&bought.keys();segment_quotes=[r for r in quotes if r.get('order') in closed and r['_ts']<=b]
                quality=[]
                for oid in successful:
                    value=bought[oid].get('initial_saved_contribution')
                    if type(value) in (int,float):quality.append(value)
                segments[kind]={'orders':len(ids),'closed':len(closed),'pending':len(ids-closed),
                    'paid':len(successful),'conversion':len(successful)/len(closed) if closed else None,
                    'order_duration_seconds':distribution([finals[o]['_ts']-own_orders[o]['_ts'] for o in closed]),
                    'quotes':len(segment_quotes),'above_cap':sum(r['ask']>r['assessment']['cap'] for r in segment_quotes),
                    'initial_saved_paid_mean':sum(quality)/len(quality) if quality else None,
                    'attempt_reasons':dict(Counter(r.get('kind') for r in attempts if r.get('order') in closed))}
            units={r['unit'] for r in paid.values()}
            refunds={r['unit']:r for r in rows('public_broken',a,b) if r.get('unit') in units and (r.get('refunded') or 0)>0}
            controls=rows('market_control',a,b)
            windows[label]={'start_kst':iso(a),'end_kst':iso(b),'minutes':(b-a)/60,
                'shop':{'paid_timestamp_sales':len(paid),'journal_arrival_sales':len(timestamped),
                    'revenue':sum(r['price'] for r in paid.values()),'gross_margin':sum(r['price']-r['cost'] for r in paid.values()),
                    'below_cost':sum(r['price']<r['cost'] for r in paid.values()),
                    'observed_same_cohort_refunds':sum(r['refunded'] for r in refunds.values()),
                    'stock_refusals':dict(Counter(r.get('reason','legacy_unclassified') for r in rows('stockout',a,b))),
                    'restock_blocks':dict(Counter(r.get('reason') for r in rows('restock_blocked',a,b)))},
                'buyer':segments,'control_sample_reasons':dict(Counter(reason for r in controls for reason in r.get('reasons',[]))),
                'execution_seconds':{kind:distribution([r['seconds'] for r in rows(kind,a,b) if type(r.get('seconds')) in (int,float)])
                    for kind in ['shop_fast_execution','buyer_saved_execution','buyer_worker_execution','buyer_quote_latency']}}
        policies=rows('market_research_published',START)
        compiled=rows('feedback_policy_created',START)
        selected={r['id']:r for r in compiled if r.get('policy_authority')=='agent_selected'}
        choices=rows('feedback_main_choice',START)
        linked=[r for r in choices if r.get('policy_id') in selected and not r.get('preview')]
        model_usage=rows('model_usage',START)
        contextual=rows('research_context_prepared',START)
        all_purchases={r['order']:r for r in purchases if r['_ts']>=START}
        new_outcomes={}
        for r in rows('outcome',START):
            if r.get('order') in all_purchases and r.get('canonical'):new_outcomes[r['order']]=r
        goods=[p for p in all_purchases.values() if p.get('kind')=='goods']
        forum_deliveries=[r for r in rows('forum_delivery',START) if r.get('role')=='buyer' and r.get('status') in (200,201)]
        forum_latest={r['post_id']:r for r in rows('forum_objective_observation',START)}
        compiled_now=j.get('feedback-last-policy',{});capacity=cash_capacity(inventory,j,config,end)
        cfg,strategy=effective(j,'shop',base=config)
        extra={'policies':[{'at_kst':iso(r['_ts']),**{k:r.get(k) for k in ['operating_policy','operating_intents','forum_focus','summary','objective_context_id']}} for r in policies],
            'main_linked_policy_choices':dict(Counter(r['role'] for r in linked)),
            'main_all_choices':dict(Counter(r['role'] for r in choices if not r.get('preview'))),
            'current_policy':{k:compiled_now.get(k) for k in ['id','at','status','policy_authority','patches','reasons']},
            'current_objective_context':j.get('objective-decision-context',{}),
            'framework':j.get('framework-plan',{}),'roles':j.get('analytical-role-status',{}),
            'quality_holdout':j.get('quality-holdout-summary',{}),
            'research_request_bytes':[{'role':r['role'],'before':r['source_bytes'],'after':r['fact_bytes']} for r in contextual],
            'model_usage':{'calls':len(model_usage),'input_tokens':sum(r.get('usage',{}).get('input_tokens',0) for r in model_usage),
                'output_tokens':sum(r.get('usage',{}).get('output_tokens',0) for r in model_usage),
                'estimated_usd':sum(r.get('estimated_usd',0) for r in model_usage)},
            'new_goods_quality':{'purchased':len(goods),'age_at_least_6h':sum(end-p.get('paid_at',p['_ts'])>=21600 for p in goods),
                'canonical_matured':sum(bool(new_outcomes.get(p['order'],{}).get('matured')) for p in goods)},
            'forum':{'delivered_posts':len(forum_deliveries),'post_ids':[r.get('post_id') for r in forum_deliveries],
                'kinds':dict(Counter(r.get('information_kind') for r in forum_deliveries)),
                'observed_distinct_posts':len(forum_latest),'latest_observations':list(forum_latest.values())},
            'supply_plans_used':rows('shop_prepared_plan_used',START),
            'supply_statuses':dict(Counter(r['status'] for r in rows('supply_execution_status',START))),
            'prepared_plan':j.snapshot('shop-plan-v15.json',{}),
            'errors':{kind:len(rows(kind,START)) for kind in ['objective_context_error','analytical_role_error',
                'cycle_supervisor_error','cycle_supply_error','buyer_worker_error','market_research_deferred',
                'forum_error','market_control_error','strategy_error','feed_probe_error']}}
    console=ROOT/'logs'/(runtime['launch_id']+'.txt');actual_supplies=[];inventory_supplies=[]
    for line in console.read_text(encoding='utf-8',errors='replace').splitlines():
        m=re.match(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.*?) for (\d+), balance (\d+)',line)
        if m:
            at=datetime.fromisoformat('2026-11-12T'+m[1]).replace(tzinfo=KST).timestamp()
            row={'at':at,'qty':int(m[2]),'name':m[3],'cost':int(m[4])}
            if START<=at<=end:actual_supplies.append(row)
            if START<=at<=inv_at:inventory_supplies.append(row)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        inv_sales={r['unit']:r for at,data in db.execute(
            'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=?',('shop_settled',START,inv_at))
            if START<=(r:=json.loads(data)).get('paid_at',at)<=inv_at}
    still_held={u['unit'] for u in inventory.get('units',[])}
    expired=[u for u in base['inventory']['units'] if u.get('expires_at') and u['expires_at']<=inv_at
             and u['unit'] not in inv_sales and u['unit'] not in still_held]
    inventory_identity={'at_kst':iso(inv_at),'initial_units':len(base['inventory']['units']),
        'acquired_units':sum(r['qty'] for r in inventory_supplies),'sold_units':len(inv_sales),
        'expired_initial_units':len(expired),'current_units':len(inventory.get('units',[]))}
    inventory_identity['unit_residual']=inventory_identity['initial_units']+inventory_identity['acquired_units']-len(inv_sales)-len(expired)-inventory_identity['current_units']
    inventory_identity['actual_acquisition_cost']=sum(r['cost'] for r in inventory_supplies)
    inventory_identity['cash_delta']=inventory.get('balance',0)-base['inventory']['balance']
    inventory_identity['inventory_cost_delta']=sum(u['cost'] for u in inventory.get('units',[]))-sum(u['cost'] for u in base['inventory']['units'])
    inventory_identity['cash_plus_stock_cost_delta']=inventory_identity['cash_delta']+inventory_identity['inventory_cost_delta']
    before_shop=old['shops']['own'];after_shop=next_board['shops']['own']
    before_buyer=old['buyers']['own'];after_buyer=next_board['buyers']['own']
    delta=lambda before,after:{k:after[k]-before[k] for k in before.keys()&after.keys()
        if type(before[k]) in (int,float) and type(after[k]) in (int,float)}
    result={'source':source_path,'proof':proof_path,'baseline':BASELINE,'runtime':runtime,
        'cutoff_kst':iso(end),'baseline_at_kst':iso(base['at']),'runtime_start_kst':iso(START),
        'current':next_board,'before':old,'official_deltas':{'shop':delta(before_shop,after_shop),'buyer':delta(before_buyer,after_buyer),
            'total':next_board['players']['own']['total']-old['players']['own']['total']},
        'matched_peer_changes':peer_changes(base['board'],board,(end-base['at'])/3600),
        'windows':windows,'framework':extra,
        'inventory':{'at_kst':iso(inv_at),'units':len(inventory.get('units',[])),
            'skus':len({u['sku'] for u in inventory.get('units',[])}),'by_kind':dict(Counter(u['kind'] for u in inventory.get('units',[]))),
            'cost':sum(u.get('cost',0) for u in inventory.get('units',[])),'balance':inventory.get('balance'),
            'capacity':capacity,'effective_ceiling':inventory_limit(cfg),'restock':inventory.get('restock'),
            'baseline_units':len(base['inventory']['units']),'baseline_cost':sum(u['cost'] for u in base['inventory']['units']),
            'baseline_balance':base['inventory']['balance']},
        'actual_supplies':actual_supplies,'inventory_reconciliation':inventory_identity,
        'connection':{k:proof.get(k) for k in ['at_utc','online','listed','public_statuses','source_mismatches','independent_recovery_guard_active']},
        'rules_sha256':hashlib.sha256(source['responses']['/agent.md']['data'].encode()).hexdigest(),
        'reconciliation':{'official_shop_deals_delta':after_shop['deals']-before_shop['deals'],
            'paid_timestamp_sales':windows['v18']['shop']['paid_timestamp_sales'],
            'journal_arrival_sales':windows['v18']['shop']['journal_arrival_sales'],
            'official_shop_revenue_delta':after_shop['revenue']-before_shop['revenue'],
            'journal_revenue':windows['v18']['shop']['revenue'],
            'official_buyer_deals_delta':after_buyer['deals']-before_buyer['deals'],
            'new_order_cohort_paid':sum(r['paid'] for r in windows['v18']['buyer'].values()),
            'journal_purchases':len(all_purchases)},
        'limitations':['Short, partial post-deployment period; no randomized causal v18 lift or first-place proof.',
            'Official baseline is a cached board captured 13 seconds before runtime start; endpoint accounting can lag local callbacks.',
            'Journal arrival time and paid_at are reported separately; late-arriving older events are not automatically v18 sales.',
            'Equal-duration previous window has different supply phase, downtime and category mix.',
            'Current inventory and KV observation timestamps differ from the frozen board cutoff.',
            'Gross margin precedes storage and future refunds; observed window refunds may concern older cohorts.',
            'New goods require at least six-hour outcome follow-up; forum format scores are sparse, sampled and age-dependent.']}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/('v18-progress-review-'+stamp+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('ANALYSIS',path)
    print(json.dumps({k:result[k] for k in ['cutoff_kst','current','official_deltas','windows','reconciliation']},ensure_ascii=False))
    print('INVENTORY',json.dumps(result['inventory']))
    print('FRAMEWORK',json.dumps({k:extra[k] for k in ['main_linked_policy_choices','errors','model_usage','forum','new_goods_quality','supply_statuses']}))


if __name__=='__main__':main()
