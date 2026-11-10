"""Read-only audit of live score, context coverage, memory and adaptation."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import json,sqlite3,statistics,time
from r3.state import ROOT,read_json,atomic_json
from autonomous_status import ReadJournal
from r3.meta import eligible_rows,weights,percentile

SOURCES=['logs/peer-market-source-20261109T122827623381Z.json',
         'logs/peer-market-source-20261109T235759979196Z.json',
         'logs/peer-market-source-20261110T042621171414Z.json']
KST=timezone(timedelta(hours=9))


def mean(xs):return statistics.mean(xs) if xs else None
def main():
    sources=[read_json(ROOT/p) for p in SOURCES]
    end=sources[-1]['responses']['/api/board']['finished_at'];start=end-14400
    result={'sources':SOURCES,'cutoff':end,'captured_at':time.time(),'boards':[]}
    for src in sources:
        b=src['responses']['/api/board']['data'];row={'at':src['responses']['/api/board']['finished_at']}
        for role,key,metric,fields in [('shops','name','all',['book','revenue','deals','stars_rank','stock_units']),
                ('buyers','name','all',['saved','deals','forum','posts']),('players','player','total',['total'])]:
            rows=[r for r in b[role] if type(r.get(metric)) in (int,float)]
            own=next(r for r in rows if r[key]=='seochanit')
            row[role]={'rank':1+sum(r[metric]>own[metric] for r in rows),'population':len(rows),
                      **{f:own.get(f) for f in [metric,*fields]},
                      'leader':{f:max(rows,key=lambda r:r[metric]).get(f) for f in [key,metric,*fields]}}
        result['boards'].append(row)
    board=sources[-1]['responses']['/api/board']['data']
    board=dict(board,buyer_metric='saved',buyer_forum_metric='raw',buyer_rank_scope='everyone')
    result['objective_cohorts']={}
    for role,table in [('shop','shops'),('buyer','buyers')]:
        proxy=eligible_rows(board,role,'seochanit');own=next(r for r in proxy if r['name']=='seochanit')
        score=sum(w*(max(0,min(1,own[f])) if role=='buyer' and f=='forum' else
                     percentile([r[f] for r in proxy if type(r.get(f)) in (int,float)],own[f]))
                  for f,w in weights(board,role).items())
        result['objective_cohorts'][role]={'proxy_population':len(proxy),
            'published_scored':sum(type(r.get('all')) in (int,float) for r in board[table]),
            'proxy_score':score,'published_score':own['all'],
            'note':'Empirical midrank proxy is documented as approximate; discrepancy is not proof of rule violation.'}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,end)
        def ev(kind,t0=start,t1=end):return [dict(json.loads(d),_ts=t,_role=r) for t,r,d in c.execute(
            'SELECT ts,role,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts',(kind,t0,t1))]
        result['window']={'start':start,'end':end,'hours':4}
        result['decision_modes_24h']=[{'role':role,'mode':mode,'n':n} for role,mode,n in c.execute(
            "SELECT role,json_extract(data,'$.mode'),count(*) FROM events WHERE kind='decision' AND ts>? AND ts<=? GROUP BY role,json_extract(data,'$.mode')",(end-86400,end))]
        last=c.execute("SELECT max(ts),count(*) FROM events WHERE kind='model_usage'").fetchone()
        result['model_usage']={'latest':last[0],'total':last[1],
            'last_24h':c.execute("SELECT count(*) FROM events WHERE kind='model_usage' AND ts>? AND ts<=?",(end-86400,end)).fetchone()[0]}
        result['event_sizes']=[{'kind':k,'n':n,'json_characters':bs} for k,n,bs in c.execute(
            'SELECT kind,count(*),sum(length(data)) FROM events GROUP BY kind ORDER BY sum(length(data)) DESC LIMIT 10')]
        result['db_bytes']=(ROOT/'.market/r3.sqlite3').stat().st_size
        result['memory_coverage']={}
        for kind,limit,horizon in [('public_settle',20000,21600),('attempt',2000,21600),
            ('shop_quote',10000,None),('customer_visit',1000,21600),('buyer_order',500,21600),('broken_news',500,None)]:
            rows=c.execute('SELECT ts FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC LIMIT ?',(kind,end,limit)).fetchall()
            total=c.execute('SELECT count(*),min(ts),max(ts) FROM events WHERE kind=? AND ts<=?',(kind,end)).fetchone()
            actual=c.execute('SELECT count(*) FROM events WHERE kind=? AND ts>? AND ts<=?',(kind,end-horizon,end)).fetchone()[0] if horizon else None
            result['memory_coverage'][kind]={'limit':limit,'selected':len(rows),'selected_span_hours':(end-min(t[0] for t in rows))/3600 if rows else None,
                'total_retained':total[0],'oldest_retained':total[1],'nominal_window_count':actual,
                'nominal_window_seconds':horizon,
                'selected_window_count':sum(t[0]>end-horizon for t in rows) if horizon else None}
        visits=ev('customer_visit',0);result['customer_identity']={'retained_visits':len(visits),
            'signed_visits':sum(bool(r.get('signer')) for r in visits),
            'confirmed_identities':len(ev('customer_identity',0)),
            'retained_span_hours':(end-min(r['_ts'] for r in visits))/3600 if visits else None}
        orders={r['order']:r for r in ev('buyer_order')};purchases={r['order']:r for r in ev('purchase') if r['order'] in orders}
        results={r['order']:r for r in ev('market_result') if r.get('order') in orders}
        attempts=[r for r in ev('attempt') if r.get('order') in orders]
        quotes=[r for r in ev('quote_comparison') if r.get('order') in orders]
        result['buyer_funnel']={}
        for kind in ('food','goods'):
            ids={oid for oid,r in orders.items() if r.get('kind')==kind};paid=ids&purchases.keys();closed=ids&results.keys()
            qs=[r for r in quotes if r['order'] in ids];ats=[r for r in attempts if r['order'] in ids]
            byroute=defaultdict(lambda:{'orders':set(),'attempts':0,'seconds':0})
            for r in ats:
                key=r['shop'];byroute[key]['orders'].add(r['order']);byroute[key]['attempts']+=1;byroute[key]['seconds']+=r.get('seconds') or 0
            result['buyer_funnel'][kind]={'orders':len(ids),'closed':len(closed),'pending':len(ids-closed),
                'paid':len(paid),'conversion':len(paid)/len(closed) if closed else None,
                'initial_saved_mean':mean([purchases[oid]['initial_saved_contribution'] for oid in paid]),
                'quotes':len(qs),'above_cap':sum(r['ask']>r['assessment']['cap'] for r in qs),
                'feasible_at_ask':sum(bool(r.get('feasible_at_ask')) for r in qs),
                'reasons':dict(Counter(r.get('reason') or r.get('kind') for r in ats)),
                'top_failed_routes':[{'shop':s,'orders':len(r['orders']),'attempts':r['attempts'],'seconds':r['seconds']} for s,r in sorted(byroute.items(),key=lambda pair:-pair[1]['seconds'])[:5]]}
        predictions=j.events('prediction',2000);decisions={r['decision']:r for r in j.events('decision',2200)}
        result['prediction_sample']={}
        for role in ('shop','buyer'):
            rows=[r for r in predictions if r.get('role')==role];chosen=[]
            for p in rows:
                d=decisions.get(p['decision'],{});pick=next((x for x in p.get('candidates',[]) if x['id']==d.get('chosen')),None)
                if pick:chosen.append((p,pick))
            result['prediction_sample'][role]={'predictions':len(rows),'matched_choices':len(chosen),
                'operations':dict(Counter(p.get('context',{}).get('operation','quote') for p in rows)),
                'candidate_count_mean':mean([len(p.get('candidates',[])) for p in rows]),
                'candidate_stars_delta_nonzero':sum(bool(x.get('expected_delta',{}).get('stars_rank')) for p in rows for x in p.get('candidates',[])),
                'shop_memory_identity_status':dict(Counter(p.get('context',{}).get('customer_memory',{}).get('identity_status','absent') for p in rows)),
                'chosen_completion_mean':mean([x['completion_probability'] for p,x in chosen if 'completion_probability' in x]),
                'price_context_missing_shop':sum(p.get('context',{}).get('operation')=='price' and not p.get('context',{}).get('shop') for p in rows)}
        evaluations=ev('strategy_evaluation',end-86400,end)
        result['adaptive_evaluations_24h']={role:{'actions':dict(Counter(r.get('action') for r in evaluations if r['_role']==role)),
            'reasons':dict(Counter(r.get('reason') for r in evaluations if r['_role']==role)),
            'latest':[r for r in evaluations if r['_role']==role][-2:]} for role in ('shop','buyer')}
        result['adaptive_states']={role:j.get('adaptive:'+role) for role in ('shop','buyer')}
        controls=ev('market_control',start-300,end);time_by=Counter();reason_by=Counter();covered=0
        for i,r in enumerate(controls):
            seconds=max(0,min(end,controls[i+1]['_ts'] if i+1<len(controls) else end,r['_ts']+120)-max(start,r['_ts']))
            time_by[r['status']]+=seconds;covered+=seconds
            for reason in r.get('reasons',[]):reason_by[reason]+=seconds
        result['control_observed_intervals']={'covered_seconds':covered,'status_seconds':dict(time_by),
            'reason_seconds':dict(reason_by),'interval_cap_seconds':120,'not_exact_market_downtime':True}
        result['latest_strategy_actions']=ev('strategy_change',end-86400,end)[-5:]
        allp={r['order']:r for r in ev('purchase',end-172800,end) if r.get('kind')=='goods' and r.get('paid_at',r['_ts'])<=end-21600}
        outcomes=ev('outcome',end-172800,end);latest={}
        for r in outcomes:
            if r.get('broken') or r['order'] not in latest:latest[r['order']]=r
        breaks={r['unit']:r for r in ev('public_broken',end-172800,end) if r.get('unit')}
        expected=[p for oid,p in allp.items() if latest.get(oid,{}).get('refund_status')=='expected_by_warranty_rule']
        unresolved=[p for oid,p in allp.items() if oid not in latest]
        result['quality_feedback']={'mature_eligible_goods':len(allp),'expected_refund_outcomes':len(expected),
            'expected_but_publicly_refunded':sum((breaks.get(p.get('unit'),{}).get('refunded') or 0)>=p['price'] for p in expected),
            'missing_local_outcome':len(unresolved),'missing_but_exact_public_break':sum(p.get('unit') in breaks for p in unresolved)}
        profiles=j.get('merchant_context_v5',{});result['merchant_context']={'at':profiles.get('at'),'summary':profiles.get('summary')}
        timeline=j.get('merchant_stock_timeline',[]);result['stock_timeline']={'n':len(timeline),'span_hours':(end-timeline[0]['at'])/3600 if timeline else None}
        result['rules']={k:v for k,v in j.get('market_rules',{}).items() if k in ('at','buyer_metric','buyer_forum_metric','compatible')}
        result['memory_scan_seconds']=time.time()-result['captured_at']
    result['limits']=['Read-only SQLite snapshot; kv records can be newer than the frozen public board. They are labeled current local state.',
        'Customer FIFO retention can delete pre-cutoff visits before this audit; coverage describes currently retained records, not a historical reconstruction.',
        'SQLite length(TEXT) measures characters, not UTF-8 bytes. event_sizes is an as-read all-time character-count profile.',
        'Window-end in-flight orders are pending; failures are attempts and can occur in later paid orders.',
        'Context limits imply missing evidence, not proven causal contribution to rank loss.',
        'Observed control intervals cap carry-forward at 120 seconds; they do not measure exact market downtime.']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/f'agent-system-audit-{stamp}.json';atomic_json(path,result)
    print('SOURCE',path)
    print(json.dumps({k:v for k,v in result.items() if k not in ('event_sizes','latest_strategy_actions','adaptive_states')},ensure_ascii=False))


if __name__=='__main__':main()
