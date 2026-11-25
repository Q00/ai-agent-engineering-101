"""Frozen public scoreboard and read-only order/settlement diagnostic. No trading."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import statistics
import sqlite3
from r3.state import ROOT, read_json

SOURCE = ROOT/'logs/peer-market-source-20261105T094827924168Z.json'
BASELINE = ROOT/'logs/autonomous-deploy-before-20261105T083003012800Z.json'


def main():
    source, old = read_json(SOURCE), read_json(BASELINE)
    board = source['responses']['/api/board']['data']
    end, start = source['responses']['/api/board']['finished_at'], old['at']
    hours = (end-start)/3600
    cohort = [r for r in board['buyers'] if r.get('deals', 0)>0 and type(r.get('all')) in (int,float)]
    me = next(r for r in cohort if r['name']=='seochanit')
    weights={'surplus':.5,'forum':.3,'deals':.1,'posts':.1}
    components={k:{'value':me[k], 'rank':1+sum((r.get(k) or 0)>me[k] for r in cohort),
                   'reconstructed_percentile':(sum((r.get(k) or 0)<me[k] for r in cohort)+
                       .5*(sum((r.get(k) or 0)==me[k] for r in cohort)-1))/max(1,len(cohort)-1),
                   'weight':w} for k,w in weights.items()}
    def compact(r):return {k:r.get(k) for k in ['name','orders','deals','surplus','forum','posts','all']}
    before={r['name']:r for r in old['board']['buyers']}
    peers={}
    for name in ['seochanit','inextro','psyoung82','totato2203-r3','shaun0927-labbuyer','shaun0927']:
        r=next(x for x in cohort if x['name']==name);a=before.get(name,{})
        delta={k:r[k]-a[k] for k in ['orders','deals','surplus','forum','posts','all'] if k in a}
        peers[name]={'before':compact(a),'after':compact(r),'delta':delta,
                     'surplus_per_hour':delta.get('surplus',0)/hours,
                     'deals_per_hour':delta.get('deals',0)/hours,
                     'net_surplus_increment_per_added_deal':delta.get('surplus',0)/max(1,delta.get('deals',0))}
    catalog=read_json(ROOT/'.market/catalog.json',{})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('BEGIN')
        def kv(k,default=None):
            row=conn.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()
            return json.loads(row[0]) if row else default
        epoch=kv('objective_epoch',start)
        def events(kind,since):
            return [dict(json.loads(d),_ts=t,_event_id=i) for i,t,d in conn.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts',
                (kind,since,end))]
        public=events('public_settle',start)
        broken=events('public_broken',start-21600)
        peer_mix={}
        for name in peers:
            trades=[e for e in public if e.get('buyer')==name]
            peer_mix[name]={}
            for kind in ['goods','food']:
                rows=[e for e in trades if catalog.get(e.get('sku') or e.get('item'),{}).get('kind')==kind]
                peer_mix[name][kind]={'deals':len(rows),'paid':sum(e['amount'] for e in rows),
                    'initial_list_minus_price':sum(catalog[e.get('sku') or e['item']]['list_price']-e['amount'] for e in rows),
                    'skus':Counter(e.get('sku') or e.get('item') for e in rows).most_common(8)}
        periods={}
        for label,since in [('recent',start),('since_buyer_v6',epoch)]:
            orders={e['order']:e for e in events('buyer_order',since)}
            buys={e['order']:e for e in events('purchase',since)}
            results={e['order']:e for e in events('market_result',since)}
            agreements=events('agreement',since);payments=events('payment',since)
            attempts=events('attempt',since);executions=events('buyer_execution',since)
            bykind={}
            for kind in ['goods','food']:
                ids={o for o,e in orders.items() if e.get('kind')==kind}
                purchases=[e for o,e in buys.items() if o in ids]
                gross=sum(e['value']-e['price'] for e in purchases)
                failure=Counter(e.get('reason') or e.get('kind','unknown') for e in attempts if e.get('order') in ids)
                paymentfail=Counter(e.get('reason','unknown') for e in payments if e.get('order') in ids and not e.get('paid'))
                counters=[e for e in events('buyer_counter',since) if e.get('order') in ids]
                bykind[kind]={'orders':len(ids),'budget_total':sum(orders[o]['budget'] for o in ids),
                    'purchases':len(purchases),'purchase_rate':len(purchases)/max(1,len(ids)),
                    'initial_surplus':gross,'initial_surplus_per_purchase':gross/max(1,len(purchases)),
                    'agreements_orders':len({e['order'] for e in agreements if e.get('order') in ids}),
                    'no_purchase_orders':len(ids-set(buys)), 'attempt_failures':dict(failure),
                    'payment_failures':dict(paymentfail),'counter_moves':len(counters),
                    'warranties':dict(Counter(str(e.get('warranty_hours',0)) for e in purchases)),
                    'purchase_grades':dict(Counter(str(e.get('grade')) for e in purchases)),
                    'by_category':Counter(orders[e['order']].get('category','unknown') for e in purchases).most_common(8)}
                elapsed=[results[o]['_ts']-orders[o]['_ts'] for o in ids if o in results]
                bykind[kind]['all_completed_order_seconds']=sum(elapsed)
                bykind[kind]['median_completed_order_seconds']=statistics.median(elapsed) if elapsed else None
            ages=[]
            for oid,p in buys.items():
                if oid in orders:ages.append(p.get('paid_at',p['_ts'])-orders[oid]['_ts'])
            models=[e for e in events('model_usage',since) if e.get('role','').startswith('buyer')]
            periods[label]={'start':since,'end':end,'hours':(end-since)/3600,
                'orders':len(orders),'purchases':len(buys),'results':len(results),
                'results_verified':sum(e.get('verified') is True for e in results.values()),
                'execution_attempts':len(executions),'by_kind':bykind,
                'paid_order_latency_seconds':{'median':statistics.median(ages) if ages else None,'sum':sum(ages)},
                'buyer_model_calls':len(models),'buyer_model_seconds':sum(e.get('seconds',0) for e in models),
                'forum_deliveries':dict(Counter(str(e['status']) for e in events('forum_delivery',since) if e['role']=='buyer')),
                'quote_orders':len({e['order'] for e in events('quote_comparison',since)}),
                'failed_checkout_orders_later_paid':len({e['order'] for e in payments if not e.get('paid')} & set(buys))}
        purchases=events('purchase',epoch)
        exactbreaks={e.get('unit'):e for e in broken}
        faultloss=[]
        for p in purchases:
            e=exactbreaks.get(p.get('unit'))
            if e:
                refund=e.get('refunded',0)
                faultloss.append({'order':p['order'],'sku':p['sku'],'grade':p.get('grade'),
                    'initial_surplus':p['value']-p['price'],'actual_surplus_after_break':-p['price']+refund,
                    'revision_loss':p['value']-refund,'refunded':refund})
        predictions=events('prediction',start)
        decisions={e['decision']:e for e in events('decision',start)}
        lower=[]
        for p in predictions:
            if p.get('role')!='buyer' or p.get('context',{}).get('ask') is not None:continue
            cs=p['candidates'];decision=decisions.get(p['decision'],{})
            chosen=next((c for c in cs if c['id']==decision.get('chosen')),None)
            if not chosen:continue
            best=max(cs,key=lambda c:c.get('score',0))
            if chosen.get('score',0)<.8*best.get('score',0):
                lower.append({'order':p.get('context',{}).get('order',{}).get('order'),
                              'best_numeric_score':best['score'],'chosen_score':chosen['score'],
                              'mode':decision.get('mode')})
        queue=conn.execute('SELECT status,payload FROM forum').fetchall()
        pending=[json.loads(p) for status,p in queue if status=='pending' and json.loads(p).get('as','buyer')=='buyer']
        forum={'pending_buyer':len(pending),'pending_unexpired':sum(p.get('expires_at',end+1)>end for p in pending),
               'recent_types':Counter(e['key'].split(':')[0] for e in events('forum_delivery',epoch) if e['role']=='buyer')}
        result={'sources':{'public':str(SOURCE.relative_to(ROOT)),'baseline':str(BASELINE.relative_to(ROOT)),
                            'journal':'.market/r3.sqlite3 read-only transaction','rules':'https://market.ourolabs.ai/agent.md'},
                'as_of':source['checked_at'],'period_hours':hours,'own':compact(me),'components':components,
                'cohort_n':len(cohort),'reconstructed_all':sum(v['weight']*v['reconstructed_percentile'] for v in components.values()),
                'percentile_is_inferred_from_published_rows':True,'peers':peers,'public_peer_mix':peer_mix,
                'periods':periods,'verified_exact_faults':faultloss,'forum':forum,
                'choice_below_80pct_numeric_score':lower,'quality_model':kv('quality_risk_v4',{}),
                'limits':['Public trade list-minus-price is initial value, not matured reward.',
                    'Net scoreboard growth includes revisions to earlier purchases.',
                    'No-purchase orders include unavailable or genuinely unsuitable offers.',
                    'Missed hypothetical quotes are not assumed purchasable.',
                    'Forum queue is current at analysis time; score and event windows end at the frozen source cutoff.']}
    path=ROOT/'logs'/('buyer-rank-diagnosis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
