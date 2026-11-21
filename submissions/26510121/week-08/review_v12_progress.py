"""Frozen official boards plus matched order cohorts; no trading or policy edits."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import json
import math
import sqlite3
import statistics
from r3.state import ROOT,read_json,atomic_json

BEFORE='logs/autonomous-deploy-before-20261109T085530589037Z.json'
AFTER='logs/peer-market-source-20261109T122827623381Z.json'
LIVE='logs/championship-live-evidence-20261109T122829255934Z.json'
KST=timezone(timedelta(hours=9))

def own(board,key):return next(r for r in board[key] if r.get('name',r.get('player'))=='seochanit')
def rank(board,table,field,row):
    metric='total' if table=='players' else 'all'
    rows=[r for r in board[table] if type(r.get(metric)) in (int,float) and type(r.get(field)) in (int,float)]
    return {'rank':1+sum(r[field]>row[field] for r in rows),'population':len(rows)}
def median(rows):return statistics.median(rows) if rows else None

def main():
    before,after,live=[read_json(ROOT/p) for p in [BEFORE,AFTER,LIVE]]
    a=before['board'];b=after['responses']['/api/board']['data']
    start=before['at'];end=after['responses']['/api/board']['finished_at'];hours=(end-start)/3600
    out={'sources':[BEFORE,AFTER,LIVE],'start':start,'end':end,'hours':hours,'official':{},'peers':{},'component_ranks':{}}
    for table,fields in [('shops',['book','revenue','deals','stock_units','stock_cost','stars_rank','refunds','all']),
                         ('buyers',['saved','deals','orders','posts','forum','all']),('players',['total'])]:
        metric='total' if table=='players' else 'all';p,r=own(a,table),own(b,table)
        out['official'][table]={'before':{k:p.get(k) for k in fields},'after':{k:r.get(k) for k in fields},
            'before_rank':rank(a,table,metric,p),'after_rank':rank(b,table,metric,r)}
        out['component_ranks'][table]={f:{'before':rank(a,table,f,p),'after':rank(b,table,f,r)} for f in fields}
        old={r.get('name',r.get('player')):r for r in a[table]};peers=[]
        for row in b[table]:
            name=row.get('name',row.get('player'));prior=old.get(name,{})
            changes={f:row[f]-prior[f] for f in fields if type(row.get(f)) in (int,float) and type(prior.get(f)) in (int,float)}
            peers.append({'name':name,'current':{f:row.get(f) for f in fields},'delta':changes,
                'book_per_hour':changes.get('book',0)/hours,'deals_per_hour':changes.get('deals',0)/hours})
        out['peers'][table]=sorted(peers,key=lambda r:r['current'].get(metric) or -1,reverse=True)
    names={r['url'].rstrip('/'):r['name'] for r in after['responses']['/api/shops']['data']['shops']}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN')
        query='SELECT id,ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts'
        def events(kind,t0,t1):return [dict(json.loads(d),_ts=t,_id=i) for i,t,d in c.execute(query,(kind,t0,t1))]
        def kv(key):return json.loads(c.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()[0])
        epoch=kv('buyer_revision_epoch');span=end-epoch
        def cohort(t0,t1):
            orders={r['order']:r for r in events('buyer_order',t0,t1)}
            results={r['order']:r for r in events('market_result',t0,t1) if r.get('order') in orders}
            buys={r['order']:r for r in events('purchase',t0,t1) if r.get('order') in orders}
            outcomes=defaultdict(list)
            for r in events('outcome',t0,t1):outcomes[r.get('order')].append(r)
            attempts=[r for r in events('attempt',t0,t1) if r.get('order') in orders]
            quotes=[r for r in events('quote_comparison',t0,t1) if r.get('order') in orders]
            prep=events('buyer_preparation',t0,t1)
            starts=sorted(r['_ts'] for r in orders.values());timings=[]
            for oid,r in orders.items():
                if oid in results:timings.append(results[oid]['_ts']-r['_ts'])
            kinds={}
            for kind in ['food','goods']:
                ids={oid for oid,r in orders.items() if r.get('kind')==kind};closed=ids & results.keys();paid=ids & buys.keys()
                qs=[r for r in quotes if r['order'] in ids];as_=[r for r in attempts if r['order'] in ids]
                bad={r['order'] for r in as_ if r.get('reason')=='receipt_integrity_failure'}
                purchase_rows=[buys[oid] for oid in paid]
                kinds[kind]={'orders':len(ids),'completed':len(closed),'pending':len(ids-closed),
                    'verified':sum(bool(results[oid].get('verified')) for oid in closed),'paid':len(paid),
                    'initial_saved_mean':statistics.mean(r['initial_saved_contribution'] for r in purchase_rows) if paid else None,
                    'mature_paid':sum(any(r.get('matured') for r in outcomes[oid]) and not any(r.get('broken') for r in outcomes[oid]) for oid in paid),
                    'broken_paid':sum(any(r.get('broken') for r in outcomes[oid]) for oid in paid),
                    'receipt_failure_attempts':sum(r.get('reason')=='receipt_integrity_failure' for r in as_),
                    'receipt_failure_orders':len(bad),'receipt_failure_orders_unpaid':len(bad-buys.keys()),
                    'quotes':len(qs),'above_cap':sum(r['ask']>r['assessment']['cap'] for r in qs),
                    'feasible_at_ask':sum(bool(r.get('feasible_at_ask')) for r in qs),
                    'timing_median':median([results[oid]['_ts']-orders[oid]['_ts'] for oid in closed])}
            failures=defaultdict(lambda:{'attempts':0,'orders':set()})
            for r in attempts:
                if r.get('reason')=='receipt_integrity_failure':
                    key=r.get('shop','');failures[key]['attempts']+=1;failures[key]['orders'].add(r['order'])
            details=[{'shop':s,'name':names.get(s.rstrip('/'),s),'attempts':r['attempts'],
                'orders':len(r['orders']),'unpaid_orders':len(r['orders']-buys.keys())} for s,r in failures.items()]
            return {'start':t0,'end':t1,'hours':(t1-t0)/3600,'by_kind':kinds,'paid':len(buys),
                'initial_saved_mean':statistics.mean(r['initial_saved_contribution'] for r in buys.values()) if buys else None,
                'paid_per_hour':len(buys)/((t1-t0)/3600),'median_seconds':median(timings),
                'median_prep':median([r['seconds'] for r in prep]),'median_start_gap':median([b-a for a,b in zip(starts,starts[1:])]),
                'attempt_reasons':dict(Counter(r.get('reason') or r.get('error_type') or r.get('kind') for r in attempts)),
                'receipt_failure_by_shop':sorted(details,key=lambda r:-r['attempts'])[:8]}
        out['cohorts']={'before':cohort(epoch-span,epoch),'v12':cohort(epoch,end)}
        sales=events('shop_settled',start,end)
        owned={json.loads(d).get('unit') for d, in c.execute('SELECT data FROM events WHERE kind=?',('shop_settled',))}
        refunds=[r for r in events('public_broken',start,end) if r.get('unit') in owned and (r.get('refunded') or 0)>0]
        margin=sum(r['price']-r['cost'] for r in sales if type(r.get('cost')) in (int,float))
        out['shop_local']={'sales':len(sales),'versions':dict(Counter(r.get('strategy_version') for r in sales)),
            'gross_revenue':sum(r['price'] for r in sales),'gross_margin':margin,
            'below_cost':sum(r['price']<r['cost'] for r in sales if type(r.get('cost')) in (int,float)),
            'refund_amount':sum(r['refunded'] for r in refunds),'refund_count':len(refunds),
            'top_margin_skus':Counter({sku:sum(r['price']-r['cost'] for r in sales if r['sku']==sku) for sku in {r['sku'] for r in sales}}).most_common(5)}
        out['adaptive']={k:{f:kv('adaptive:'+k).get(f) for f in ['version','patch','status','since','cooldown_until']} for k in ['shop','buyer']}
        out['sql']={'events':query,'scope':'Exclusive window start; inclusive frozen official board retrieval end; matched own order IDs'}
    out['cash']={'official_inferred':own(b,'shops')['profit']+5000000,
        'inventory_cost':own(b,'shops')['stock_cost'],'model':{k:v for k,v in live['control']['cash_capacity'].items() if k!='reserve_release_schedule'}}
    out['supply']={'units':live['actual_supply_units'],'cost':live['actual_supply_cost']}
    out['rules_unchanged']=read_json(ROOT/'logs/peer-market-source-20261109T074127697069Z.json')['responses']['/agent.md']['data']==after['responses']['/agent.md']['data']
    out['limits']=['Start board is a cached snapshot; local event cutoffs and published board may straddle individual transactions.',
        'All Shop adaptive versions are included, rather than only the latest version.',
        'Before and after cohorts have equal duration but different orders, peers and outages; observed changes are not causal estimates.',
        'Receipt integrity is a grouped guard reason; signatures, final price, SKU or risk cap cannot be distinguished from this log alone.',
        'New v12 goods are under six hours old; initial Saved is not final quality-adjusted reward.']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/f'v12-progress-analysis-{stamp}.json';atomic_json(path,out)
    print('SOURCE',path)
    print(json.dumps({k:v for k,v in out.items() if k not in ['peers','sql']},ensure_ascii=True))

if __name__=='__main__':main()
