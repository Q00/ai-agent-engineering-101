"""Read-only frozen-board and journal review; no policy or market mutations."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
import sqlite3
import statistics
import sys
from r3.state import ROOT, read_json
from autonomous_status import ReadJournal
from r3.outcomes import latest

KST = timezone(timedelta(hours=9))
BASELINES = ['logs/peer-market-source-20261109T235759979196Z.json',
             'logs/peer-market-source-20261110T042621171414Z.json',
             'logs/autonomous-deploy-before-20261110T071822185481Z.json']


def unpack(path):
    s = read_json(ROOT/path)
    if 'responses' in s:
        return s, s['responses']['/api/board']['data'], s['responses']['/api/board']['finished_at']
    return s, s['board'], s['at']


def board_view(board):
    output = {}
    for role, key, metric, fields in [
        ('shops', 'name', 'all', ['alive','all','book','balance','revenue','deals','stock_units','stock_cost','stars_rank','stars','refunds']),
        ('buyers', 'name', 'all', ['alive','all','saved','surplus','deals','forum','posts','orders','served']),
        ('players', 'player', 'total', ['total','student_shop','student_buyer','student_total'])]:
        rows = board[role]; own = next(r for r in rows if r[key]=='seochanit')
        scored = [r for r in rows if type(r.get(metric)) in (int,float)]
        metrics = ['book','revenue','stars_rank'] if role=='shops' else ['saved','deals','posts','forum'] if role=='buyers' else []
        output[role] = {'own': {f:own.get(f) for f in fields},
            'rank':1+sum(r[metric]>own[metric] for r in scored), 'population':len(scored),
            'components':{f: {'value':own[f], 'rank':1+sum(type(r.get(f)) in (int,float) and r[f]>own[f] for r in rows)} for f in metrics},
            'leader':{f:r for f,r in max(scored,key=lambda r:r[metric]).items() if f in [key,metric,*metrics]}}
    for field in ['student_shop','student_buyer','student_total']:
        rows = [r for r in board['players'] if r.get('student') and type(r.get(field)) in (int,float)]
        own = next(r for r in rows if r['player']=='seochanit')
        output[field]={'score':own[field],'rank':1+sum(r[field]>own[field] for r in rows),'population':len(rows)}
    return output


def peer_changes(before, after, hours):
    output = {}
    for role,key in [('shops','name'),('buyers','name'),('players','player')]:
        old = {r[key]:r for r in before[role]}; scored = [r for r in after[role] if type(r.get('all' if role!='players' else 'total')) in (int,float)]
        selected = {r[key] for r in sorted(scored,key=lambda r:r.get('all',r.get('total',0)),reverse=True)[:5]}|{'seochanit'}
        output[role] = []
        for r in scored:
            if r[key] not in selected or r[key] not in old: continue
            prior=old[r[key]]
            values={f:{'before':prior[f],'after':r[f],'delta':r[f]-prior[f],'per_hour':(r[f]-prior[f])/hours}
                    for f in ['book','revenue','deals','all','saved','forum','posts','total']
                    if type(r.get(f)) in (int,float) and type(prior.get(f)) in (int,float)}
            output[role].append({'name':r[key],'changes':values})
    return output


def main():
    current_path=sys.argv[1]; source,board,end=unpack(current_path)
    runtime=read_json(ROOT/'.market/runtime.json',{}); start=runtime['started_at']
    result={'source':current_path,'cutoff':end,'cutoff_kst':datetime.fromtimestamp(end,KST).isoformat(),
            'current':board_view(board),'comparisons':[],'runtime':runtime}
    for path in BASELINES:
        old_source,old,at=unpack(path);hours=(end-at)/3600
        result['comparisons'].append({'source':path,'start_kst':datetime.fromtimestamp(at,KST).isoformat(),
            'hours':hours,'before':board_view(old),'peers':peer_changes(old,board,hours)})
    current_rules=source['responses']['/agent.md']['data']
    old_rules=unpack(BASELINES[0])[0]['responses']['/agent.md']['data']
    result['rules']={'same_as_morning':current_rules==old_rules,
                     'sha256':hashlib.sha256(current_rules.encode()).hexdigest()}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,end)
        def events(kind,lo,hi=end):
            return [dict(json.loads(d),_ts=t,_event_id=eid) for eid,t,d in c.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,lo,hi))]
        duration=end-start
        def funnel(lo,hi):
            orders={r['order']:r for r in events('buyer_order',lo,hi)}
            finals={r['order']:r for r in events('market_result',lo,hi) if r['order'] in orders}
            purchases={r['order']:r for r in events('purchase',lo,hi)}
            quotes=events('quote_comparison',lo,hi);attempts=events('attempt',lo,hi)
            out={}
            for kind in ['food','goods']:
                ids={oid for oid,r in orders.items() if r.get('kind')==kind}; closed=ids&finals.keys()
                paid=closed&purchases.keys();rows=[r for r in quotes if r.get('order') in closed]
                times=[finals[oid]['_ts']-orders[oid]['_ts'] for oid in closed]
                ps=[purchases[oid] for oid in paid]
                out[kind]={'orders':len(ids),'closed':len(closed),'pending':len(ids-closed),'paid':len(paid),
                    'conversion':len(paid)/len(closed) if closed else None,
                    'seconds_median':statistics.median(times) if times else None,
                    'quotes':len(rows),'above_cap':sum(r['ask']>r['assessment']['cap'] for r in rows),
                    'feasible_at_ask':sum(bool(r.get('feasible_at_ask')) for r in rows),
                    'ask_cap_ratio_median':statistics.median(r['ask']/r['assessment']['cap'] for r in rows if r['assessment']['cap']>0) if rows else None,
                    'initial_saved_mean':statistics.mean(r['initial_saved_contribution'] for r in ps) if ps else None,
                    'attempts':[{'type':typ,'reason':reason,'n':n} for (typ,reason),n in Counter(
                        (r.get('kind'),r.get('reason')) for r in attempts if r.get('order') in closed).most_common(10)]}
            return {'start':lo,'end':hi,'hours':(hi-lo)/3600,'segments':out}
        result['buyer_windows']={'before_equal_duration':funnel(start-duration,start),
                                  'since_framework':funnel(start,end),'latest_4h':funnel(end-14400,end)}
        all_sales=events('shop_settled',0); units={r.get('unit') for r in all_sales}
        def sales_window(lo):
            rows=[r for r in all_sales if r['_ts']>=lo]
            broken={r['unit']:r for r in events('public_broken',lo) if r.get('unit') in units and (r.get('refunded') or 0)>0}
            gross=sum(r['price']-r['cost'] for r in rows);refunds=sum(r['refunded'] for r in broken.values())
            return {'hours':(end-lo)/3600,'sales':len(rows),'revenue':sum(r['price'] for r in rows),
                'gross_margin':gross,'below_cost':sum(r['price']<r['cost'] for r in rows),
                'observed_refund_units':len(broken),'observed_refund_amount':refunds,
                'margin_minus_observed_refunds':gross-refunds,
                'top_margin_skus':[{'sku':sku,'margin':margin} for sku,margin in Counter({sku:sum(r['price']-r['cost'] for r in rows if r['sku']==sku) for sku in {r['sku'] for r in rows}}).most_common(5)]}
        result['shop_windows']={'since_framework':sales_window(start),'since_morning':sales_window(unpack(BASELINES[0])[2])}
        recent_controls=events('market_control',start)
        result['operations']={'control_samples':len(recent_controls),'status_samples':dict(Counter(r.get('status') for r in recent_controls)),
            'reason_samples':dict(Counter(reason for r in recent_controls for reason in r.get('reasons',[]))),
            'warning':'Sample frequencies are not measured uptime or lost revenue.'}
        result['control']={k:j.get('market_control',{}).get(k) for k in ['at','status','reasons','pause_restock','cash_capacity']}
        inv=read_json(ROOT/'.market/inventory.json',{});us=inv.get('units',[])
        result['inventory']={'read_at':datetime.now(timezone.utc).isoformat(),'balance':inv.get('balance'),
            'units':len(us),'skus':len({u['sku'] for u in us}),'grades':dict(Counter(u.get('grade') for u in us)),
            'cost':sum(u.get('cost',0) for u in us),'restock':inv.get('restock')}
        plan=j.get('framework-plan',{});result['framework']={k:plan.get(k) for k in ['at','revision','arena','evaluation','segments','issues','next_actions','shop_rating']}
        feedback=events('buyer_contract_result',start)
        result['contract_feedback']={'count':len(feedback),'reason':dict(Counter(r.get('reason') for r in feedback)),
            'pauses':len(events('buyer_contract_pause',start))}
        latest_outcomes=latest(j,100000)
        purchases=events('purchase',start)
        result['quality_since_framework']={kind:{'purchases':sum(r.get('kind')==kind for r in purchases),
            'age_ge_6h':sum(r.get('kind')==kind and end-r.get('paid_at',r['_ts'])>=21600 for r in purchases),
            'canonical_outcomes':sum(r.get('kind')==kind and bool(latest_outcomes.get(r['order'],{}).get('canonical')) for r in purchases)} for kind in ['food','goods']}
        result['memory']={'contract_groups':len(j.get('memory-contract-facts:v2',{}).get('groups',{})),
            'episodes':c.execute('SELECT COUNT(*) FROM memory_episodes').fetchone()[0],
            'rating_shadow':j.get('shop-rating-shadow',{})}
        reviews=events('shop_review_observed',start)
        result['reviews']={'observations':len(reviews),'known_identity_observations':sum(bool(r.get('identity_known')) for r in reviews),
            'stars_distribution':dict(Counter(r.get('stars') for r in reviews))}
        result['errors_since_framework']={kind:len(events(kind,start)) for kind in ['strategy_error','market_control_error','merchant_context_error','forum_error']}
        result['last_restock_decisions']=events('restock_decision',end-3600)[-2:]
        result['freshness']={k:j.get(k) for k in ['board_checked_at','public_feed_caught_up_at','public_ledger_seq']}
    result['limits']=['Ranks use published scored populations, include house, and use competition ties.',
        'Frozen board metrics and current KV/inventory read times differ; KV values are not reconstructed historical values.',
        'Before/after windows have equal duration but market availability, peers, mix and outcome follow-up differ; no causal lift is identified.',
        'New goods quality has not reached six-hour maturity; historical canonical backfill is not new-strategy quality evidence.',
        'Refund amounts in a window include older sales; margin-minus-refunds is a cash-flow-style reconciliation, not a cohort loss rate.',
        'Snapshot registry inventory excludes offline shops; board stock includes them. Do not infer demand from an empty registry.',
        'Shop rating predictions remain shadow and do not improve price decisions yet.']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/f'evening-progress-review-{stamp}.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('ANALYSIS',path)
    print(json.dumps({k:result[k] for k in ['cutoff_kst','current','buyer_windows','shop_windows','operations','inventory','quality_since_framework','contract_feedback','errors_since_framework']},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
