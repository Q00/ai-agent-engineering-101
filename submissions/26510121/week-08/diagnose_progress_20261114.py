"""Frozen official score changes and bounded read-only execution diagnosis."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
import json
import re
import sqlite3
import time
from r3.state import ROOT,read_json

BASE='logs/autonomous-deploy-before-20261113T070936759881Z.json'
SOURCE='logs/peer-market-source-20261114T033752254761Z.json'
KST=timezone(timedelta(hours=9))
def iso(at):return datetime.fromtimestamp(at,KST).isoformat()


def main(baseline_path=BASE,source_path=SOURCE,console_logs=None):
    BASE,SOURCE=baseline_path,source_path
    source=read_json(ROOT/SOURCE);baseline=read_json(ROOT/BASE)
    before,after=baseline['board'],source['responses']['/api/board']['data']
    lo,hi=baseline['at'],source['responses']['/api/board']['finished_at'];now=time.time();duration=(hi-lo)/3600
    inv=read_json(ROOT/'.market/inventory.json');catalog=read_json(ROOT/'.market/catalog.json')
    peers={};own={};targets={};queries={}
    from r3.meta import eligible_rows,percentile
    scoring_proxy={}
    for board_name,board in [('before',before),('after',after)]:
        cohort=eligible_rows(board,'shop','seochanit');me=next(r for r in cohort if r['name']=='seochanit')
        components={k:{'percentile_proxy':percentile([r[k] for r in cohort if type(r.get(k)) in (int,float)],me[k]),
            'rank':1+sum(r.get(k,float('-inf'))>me[k] for r in cohort),'value':me[k]} for k in ('book','revenue','stars_rank')}
        scoring_proxy[board_name]={'cohort_n':len(cohort),'components':components,
            'all_proxy':sum(w*components[k]['percentile_proxy'] for k,w in [('book',.5),('revenue',.3),('stars_rank',.2)])}
    for role,metrics in [('shops',('book','revenue','deals','stars_rank','stock_units','stock_cost','all')),
                         ('buyers',('saved','surplus','deals','forum','posts','all'))]:
        old={r['name']:r for r in before[role]};new={r['name']:r for r in after[role]}
        own[role]={'before':{k:old['seochanit'].get(k) for k in metrics},'after':{k:new['seochanit'].get(k) for k in metrics},
            'rank_before':1+sum(r.get('all',0)>old['seochanit']['all'] for r in old.values()),
            'rank_after':1+sum(r.get('all',0)>new['seochanit']['all'] for r in new.values())}
        names={r['name'] for r in sorted(new.values(),key=lambda r:r.get('all',0),reverse=True)[:5]}|{'seochanit'}
        peers[role]=[]
        for name in sorted(names):
            if name not in old:continue
            changes={k:{'before':old[name][k],'after':new[name][k],'delta':new[name][k]-old[name][k],
                'per_hour':(new[name][k]-old[name][k])/duration} for k in metrics
                if type(old[name].get(k)) in (int,float) and type(new[name].get(k)) in (int,float)}
            peers[role].append({'name':name,'changes':changes})
        targets[role]={}
        for k in metrics:
            if k in ('all','stock_units','stock_cost'):continue
            me=new['seochanit'].get(k)
            if type(me) not in (int,float):continue
            eligible=[r for r in new.values() if type(r.get(k)) in (int,float)]
            ahead=sorted((r for r in eligible if r[k]>me),key=lambda r:r[k])
            targets[role][k]={'own':me,'raw_rank':1+len(ahead),'registered_numeric_population':len(eligible),
                'next':[{'name':r['name'],'value':r[k],'gap':r[k]-me} for r in ahead[:2]]}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind,start=lo,stop=hi):
            sql='SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid'
            queries['events']=sql
            return [dict(json.loads(raw),_ts=ts,_id=eid) for eid,ts,raw in db.execute(sql,(kind,start,stop))]
        def kv(k):
            r=db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else {}
        sales=events('shop_settled');sale_units={r.get('unit'):r for r in sales if r.get('unit')}
        all_own={r.get('unit'):r for r in events('shop_settled',lo-172800) if r.get('unit')}
        refund_rows=events('public_refund')+events('public_broken');refunds={}
        for r in refund_rows:
            if r.get('unit') not in all_own:continue
            amount=r.get('refunded',r.get('amount'))
            if type(amount) in (int,float) and amount>=0:refunds[r['unit']]=max(refunds.get(r['unit'],0),amount)
        known=[r for r in sale_units.values() if type(r.get('cost')) in (int,float)]
        mix={}
        for kind in ('food','goods'):
            rows=[r for r in known if catalog.get(r.get('sku'),{}).get('kind')==kind]
            mix[kind]={'sales':len(rows),'revenue':sum(r['price'] for r in rows),'cost':sum(r['cost'] for r in rows),
                'gross_margin':sum(r['price']-r['cost'] for r in rows),'grades':dict(Counter(r.get('grade') for r in rows))}
        stocks={}
        for r in events('stockout'):stocks[r.get('task')]=r
        paid_tasks={r.get('task') for r in sales};stocks={k:r for k,r in stocks.items() if k not in paid_tasks}
        states=events('supply_execution_status');complete=[r for r in states if r.get('status')=='complete']
        controls=events('market_control');research=events('market_research_published');plans=events('inventory_portfolio_prepared')
        errors={kind:len(events(kind)) for kind in ('cycle_supply_error','cycle_supervisor_error','buyer_worker_error',
            'analytical_role_error','market_control_error','objective_context_error','market_research_deferred')}
        orders={r['order']:r for r in events('buyer_order')};results={r['order']:r for r in events('market_result')}
        purchases={r['order']:r for r in events('purchase')};segments={}
        for kind in ('food','goods'):
            closed={oid for oid,r in orders.items() if r.get('kind')==kind and oid in results}
            paid=closed&purchases.keys()
            saved=[purchases[o]['initial_saved_contribution'] for o in paid
                if type(purchases[o].get('initial_saved_contribution')) in (int,float)]
            segments[kind]={'closed':len(closed),'paid':len(paid),'conversion':len(paid)/len(closed) if closed else None,
                'confirmed_results':sum(results[o].get('verified') is True for o in closed),
                'known_initial_saved_n':len(saved),'initial_saved_mean':sum(saved)/len(saved) if saved else None}
        snapshot={'at':now,'rules':kv('market_rules'),'control':kv('market_control'),
            'feed_caught_up_at':kv('public_feed_caught_up_at'),'feed_seq':kv('public_ledger_seq'),
            'analytical_roles':kv('analytical-role-status'),'research':kv('research-last-result'),
            'inventory_portfolio':kv('inventory-portfolio:v27'),'policy':kv('feedback-last-policy'),
            'reputation_summary':kv('shop-reputation:v24').get('summary')}
        reviews={}
        for r in events('shop_review_observed'):
            reviews[(r.get('author'),r.get('ledger'))]=r
        linked_reviews=defaultdict(list);sales_by_ledger={r['_id'].partition(':')[2]:r for r in all_own.values()}
        review_reasons=Counter()
        for r in reviews.values():
            sale=sales_by_ledger.get(str(r.get('ledger')),{});kind=catalog.get(sale.get('sku'),{}).get('kind','unknown')
            linked_reviews[kind+':'+str(sale.get('grade'))].append(r)
            if r.get('stars',5)<=3:
                text=str(r.get('review','')).lower()
                reason='shelf_life' if any(w in text for w in ('shelf','expired','freshness','last long','use_within','유통','기한')) else 'failure_or_warranty' if any(w in text for w in ('broke','broken','warranty','refund','고장','보증')) else 'unclassified'
                review_reasons[reason]+=1
        quality={key:{'n':len(rows),'mean':sum(r['stars'] for r in rows)/len(rows),
            'low':sum(r['stars']<=3 for r in rows)} for key,rows in linked_reviews.items()}
        shelves=[r for r in stocks.values() if r.get('reason')=='shelf_life'];shelf_support=Counter()
        for r in shelves:
            maxhours=catalog.get(r.get('sku'),{}).get('shelf_hours');required=r.get('required_use_hours')
            if type(maxhours) not in (int,float) or type(required) not in (int,float):key='unknown'
            else:key='within_catalog_max' if required<=maxhours else 'above_catalog_max'
            shelf_support[key]+=1
        shop={'settlements':len(sale_units),'cost_known_sales':len(known),'settlement_gross_revenue':sum(r['price'] for r in sale_units.values()),
            'known_gross_margin':sum(r['price']-r['cost'] for r in known),'observed_refunds_all_sale_cohorts':sum(refunds.values()),
            'mix':mix,'unpaid_stockout_tasks':len(stocks),'stockout_reasons':dict(Counter(r.get('reason') for r in stocks.values())),
            'supply_statuses':dict(Counter(r.get('status') for r in states)),
            'successful_supply_requested_units':sum(r.get('qty',0) for r in complete),
            'supply_windows':[{'opens':w,'lines':sum(r.get('window')==w for r in complete),
                'units':sum(r.get('qty',0) for r in complete if r.get('window')==w)} for w in sorted({r['window'] for r in complete})],
            'control_samples':len(controls),'control_states':dict(Counter(r.get('status') for r in controls)),
            'control_reasons':dict(Counter(reason for r in controls for reason in r.get('reasons',[]))),
            'inventory_decision_count':len(plans),'inventory_selections':dict(Counter(r.get('selection') for r in plans)),
            'research_publications':len(research),'review_count':len(reviews),'review_stars':dict(Counter(str(r.get('stars')) for r in reviews.values())),
            'review_quality_by_sold_grade':quality,'low_review_reasons':dict(review_reasons),'shelf_stockout_feasibility':dict(shelf_support)}
    # Independently parse successful supply lines. Handle midnight across logs.
    acquired=[]
    logs=console_logs or ['logs/auto-join-20261113T071046765879Z.txt','logs/auto-join-20261113T073206342541Z.txt',
          'logs/auto-join-20261114T033048623982Z.txt']
    for name in logs:
        day=datetime.strptime(name.split('auto-join-')[1][:8],'%Y%m%d').date();previous=None
        for line in (ROOT/name).read_text(encoding='utf-8',errors='replace').splitlines():
            stamp=re.match(r'^(\d\d):(\d\d):(\d\d) ',line)
            if not stamp:continue
            seconds=int(stamp[1])*3600+int(stamp[2])*60+int(stamp[3])
            if previous is not None and seconds<previous-43200:day+=timedelta(days=1)
            previous=seconds;at=datetime.combine(day,datetime.min.time(),tzinfo=KST).timestamp()+seconds
            m=re.search(r'\[supply\] (\d+) x .* for (\d+), balance (\d+)',line)
            if m and lo<=at<hi:acquired.append({'at':at,'qty':int(m[1]),'cost':int(m[2]),'source':name})
    oldinv=baseline['inventory'];unitcost=sum(u['cost'] for u in inv['units']);oldcost=sum(u['cost'] for u in oldinv['units'])
    stockmix={k:{'units':sum(u.get('kind')==k for u in inv['units']),
        'cost':sum(u['cost'] for u in inv['units'] if u.get('kind')==k)} for k in ('food','goods')}
    b=own['shops'];bookdelta=b['after']['book']-b['before']['book'];revdelta=b['after']['revenue']-b['before']['revenue']
    bridge={'official_book_delta':bookdelta,'official_net_revenue_delta':revdelta,
        'recorded_gross_margin':shop['known_gross_margin'],'observed_refunds':shop['observed_refunds_all_sale_cohorts'],
        'unattributed_book_deductions':shop['known_gross_margin']-shop['observed_refunds_all_sale_cohorts']-bookdelta,
        'gross_minus_official_net_revenue':shop['settlement_gross_revenue']-revdelta,
        'limitations':'Residual includes unrecorded refunds, expiry, storage, fees, timestamp/coverage mismatch; not classified wholly as waste.'}
    inventory={'at':iso((ROOT/'.market/inventory.json').stat().st_mtime),'balance':inv['balance'],'cost':unitcost,
        'book_from_inventory':inv['balance']-inv.get('start',5000000)-inv.get('injected',0)+unitcost,
        'before_units':len(oldinv['units']),'units':len(inv['units']),'skus':len({u['sku'] for u in inv['units']}),'mix':stockmix,
        'before_stock_cost':oldcost,'console_acquired_units':sum(r['qty'] for r in acquired),
        'console_acquired_cost':sum(r['cost'] for r in acquired),
        'unexplained_net_unit_reduction':len(oldinv['units'])+sum(r['qty'] for r in acquired)-len(sale_units)-len(inv['units']),
        'limitation':'Private snapshot after board cutoff; unit residual is a reconciliation signal, not confirmed expiry.'}
    report={'window':{'start':iso(lo),'end':iso(hi),'hours':duration},'sources':[BASE,SOURCE,*logs],
        'queries':queries,'own':own,'peers':peers,'scoring_proxy':scoring_proxy,'next_thresholds':targets,'shop':shop,'buyer':segments,
        'bridge':bridge,'inventory':inventory,'current_observation':snapshot,'errors':errors,
        'limitations':['Comparison may include deployment, downtime and settlement boundary effects; not a controlled treatment comparison.',
            'Control/error sample counts are not outage durations or causal lost sales.',
            'Rank thresholds use published numeric rows, not exact unpublished scoring eligibility/tie rules.',
            'Buy-side initial Saved is not mature quality; no future payoff is inferred from predictions.']}
    path=ROOT/'logs'/('progress-diagnosis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({k:report[k] for k in ('window','own','bridge','inventory','buyer','errors')},ensure_ascii=True))
    print('SHOP',json.dumps(shop,ensure_ascii=True))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--baseline',default=BASE);p.add_argument('--source',default=SOURCE)
    p.add_argument('--console-log',action='append');args=p.parse_args()
    main(args.baseline,args.source,args.console_log)
