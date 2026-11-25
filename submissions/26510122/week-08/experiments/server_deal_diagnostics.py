"""Read-only, maturation-aware deal diagnostics; no strategy or market mutations."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import re
import statistics
import sys
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from inventory_observation import timestamp
from market_memory import events
from supply_reconciliation import pending_quantity


def read(path):
    rows=[]
    if not path.exists():return rows
    for line in path.read_text(errors='replace').splitlines():
        try:rows.append(json.loads(line))
        except ValueError:pass
    return rows


def summary(values):
    values=sorted(values)
    return {'n':len(values),'median':round(statistics.median(values),2) if values else None,
            'p90':round(values[min(len(values)-1,int(len(values)*.9))],2) if values else None}


def diagnose(root, now, pages=0):
    seller=root/'state/seller-runtime';buyer=root/'state/buyer-runtime'
    starts={role:json.loads((root/'state'/f'{role}-runtime'/'running.json').read_text())['started_at'] for role in ('seller','buyer')}
    since=now-3600
    result={'at':now,'since':since,'maturation_seconds':600,'window_minutes':round((now-since)/60,2)}
    allrows=events(seller/'logs/shop-events.jsonl');rows=[e for e in allrows if since<=(timestamp(e.get('at')) or 0)<=now]
    paid={e['ledger']:e for e in allrows if e['event']=='settled' and e.get('ledger')}
    receipts={e['task_id']:e for e in allrows if e['event']=='receipt_signed' and e.get('task_id')}
    pairs=defaultdict(set)
    for task,e in receipts.items():pairs[(e.get('unit'),e.get('price'))].add(task)
    exact_paid={e['task_id'] for e in allrows if e['event']=='buyer_link' and e.get('source')=='market_verified_result'}
    for e in paid.values():
        tasks=pairs[(e.get('unit'),e.get('price'))]
        if len(tasks)==1:exact_paid.update(tasks)
    initial={e['task_id']:e for e in rows if e['event']=='request_seen' and e.get('task_id')}
    cohort={t for t,e in initial.items() if (timestamp(e.get('at')) or now)>0 and timestamp(e['at'])<=now-600}
    grouped=defaultdict(list)
    for e in allrows:
        if e.get('task_id') in cohort and (timestamp(e.get('at')) or 0)<=now:grouped[e['task_id']].append(e)
    stages=Counter();quote_latency=[];signed_grade=Counter();paid_grade=Counter();below_cost_offers=0
    for task in cohort:
        es=grouped[task];kinds={e['event'] for e in es}
        for kind in ('no_stock','requirements_unmet','opening','offer','receipt_signed','cancelled','rejected','stale_inventory'):
            stages[kind]+=int(kind in kinds)
        if 'opening' in kinds:
            opening=next(e for e in es if e['event']=='opening')
            quote_latency.append(max(0,timestamp(opening['at'])-timestamp(initial[task]['at'])))
            if 'offer' not in kinds:stages['quote_without_offer']+=1
            offers=[e.get('price',0) for e in es if e['event']=='offer']
            if offers and type(opening.get('cost')) in (int,float) and max(offers)<opening['cost']:below_cost_offers+=1
        if 'offer' in kinds and 'receipt_signed' not in kinds:stages['offers_without_receipt']+=1
        if 'receipt_signed' in kinds:
            rec=receipts[task];grade=rec.get('grade','unknown');signed_grade[grade]+=1
            if task in exact_paid:stages['receipt_exact_paid']+=1;paid_grade[grade]+=1
            else:stages['receipt_payment_unconfirmed']+=1
    result['seller']={'events':dict(Counter(e['event'] for e in rows)),
        'mature_request_cohort':{'requests':len(cohort),**dict(stages)},'opening_latency_seconds':summary(quote_latency),
        'receipt_grade_counts':dict(signed_grade),'exact_paid_grade_counts':dict(paid_grade),
        'negotiations_all_offers_below_cost':below_cost_offers,
        'settled_units':len({e['ledger'] for e in rows if e['event']=='settled' and e.get('ledger')}),
        'sold_by_sku':dict(Counter(e.get('sku') for e in rows if e['event']=='settled'))}
    inv=json.loads((seller/'.market/inventory.json').read_text());portfolio=json.loads((seller/'state/inventory-portfolio.json').read_text())
    result['seller']['inventory']={'cash':inv['balance'],'units':len(inv['units']),'variety':len({u['sku'] for u in inv['units']}),
                                  'cost':sum(u['cost'] for u in inv['units'])}
    result['seller']['pending']=[{'sku':i['sku'],'requested':i.get('qty',1),'observed':len(i.get('observed_units',[])),
         'unresolved':pending_quantity(i),'at':i['at'],'reserved_cost':pending_quantity(i)*i['cost_ceiling']} for i in portfolio['intents'] if pending_quantity(i)]
    reserved=sum(i['cost_ceiling']*i.get('qty',1) for i in portfolio['intents'] if i['at']>now-86400 and i.get('kind')!='replenishment')
    result['seller']['novel_exploration_gross_reserved_24h']=reserved
    brows=read(buyer/'logs/buyer-events.jsonl');recent=[e for e in brows if since<=(timestamp(e.get('at')) or 0)<=now]
    orders={e.get('order'):e for e in recent if e.get('event')=='buyer_release_loaded' and e.get('order') and timestamp(e['at'])<=now-600}
    order_ids=set(orders)
    selected=[e for e in brows if e.get('order') in order_ids and (timestamp(e.get('at')) or 0)<=now]
    outcomes=defaultdict(set);negotiations=[]
    for e in selected:
        outcomes[e.get('event')].add(e.get('order'))
        if e.get('event')=='negotiation':negotiations.append(e)
    payments={e.get('order'):e for e in selected if e.get('event')=='payment'}
    failed=[e for e in payments.values() if not e.get('paid')]
    pending_orders=order_ids-set(payments)-outcomes['no_candidate']
    failure_classes=Counter()
    for e in failed:
        reason=e.get('reason') or ''
        failure_classes['shelf_life' if 'shelf life left' in reason else 'grade' if 'not paying: grade' in reason else 'certificate' if 'no certificate' in reason else 'other']+=1
    incomplete=[e for e in negotiations if e.get('result')=='no_receipt']
    asking=[]
    for e in incomplete:
        last=next((r.get('text','') for r in reversed(e.get('transcript',[])) if r.get('speaker')=='seller'),'')
        match=re.search(r'(?:asking|counteroffer|current ask)\s*:?\s*(\d+)',last,re.I)
        asking.append((e,int(match.group(1)) if match else None))
    result['buyer']={'events':dict(Counter(e.get('event') for e in recent)),
        'mature_order_cohort':{'orders':len(orders),'no_candidate':len(outcomes['no_candidate']),
                             'selected':len(outcomes['buyer_policy_selection']),
                             'receipt_pending_orders':len({e['order'] for e in negotiations if e.get('result')=='receipt_pending'}),
                             'paid':sum(e.get('paid') is True for e in payments.values()),'payment_refused':len(failed),
                             'no_recorded_payment_or_no_candidate':len(pending_orders)},
        'negotiation_outcomes':dict(Counter(e.get('result') for e in negotiations)),
        'no_receipt_reasons':dict(Counter(e.get('reason') for e in negotiations if e.get('result')!='receipt_pending')),
        'no_receipt_offer_counts':dict(Counter(len(e.get('offers',[])) for e in incomplete)),
        'no_receipt_last_offer_at_cap':sum(bool(e.get('offers')) and e.get('cap',0)>0 and e['offers'][-1]>=e['cap'] for e in incomplete),
        'no_receipt_final_parseable_ask':sum(a is not None for e,a in asking),
        'no_receipt_final_ask_within_cap':sum(a is not None and a<=e.get('cap',0) for e,a in asking),
        'no_receipt_final_ask_above_cap':sum(a is not None and a>e.get('cap',0) for e,a in asking),
        'checkout_failure_classes':dict(failure_classes),
        'repeated_failed_shop_skus':dict(Counter(f"{e.get('shop_name')}:{e.get('sku')}" for e in failed)),
        'seconds_to_payment':summary([timestamp(e['at'])-timestamp(orders[order]['at']) for order,e in payments.items()])}
    if pages:
        def get(path):
            with urllib.request.urlopen('https://market.ourolabs.ai'+path,timeout=20) as response:return json.load(response)
        public=[];cursor=-500
        for _ in range(pages):
            batch=get('/api/ledger?after='+str(cursor))
            if not batch:break
            public.extend(batch);cursor=min(e['seq'] for e in batch)-501
        public=list({e['seq']:e for e in public}.values());aliases={}
        for e in public:
            if e.get('kind')=='result':
                d=e.get('data',{});rec=d.get('receipt') or {}
                if d.get('shop') and rec.get('shop'):aliases[d['shop']]=rec['shop']
        counts=Counter(aliases.get(e['data'].get('shop'),e['data'].get('shop')) for e in public if e.get('kind')=='settle')
        own_results=[e for e in public if e.get('kind')=='result' and (e.get('data',{}).get('receipt') or {}).get('shop')=='outseop-26510122']
        result['market_sample']={'entries':len(public),'oldest_ts':min(e['ts'] for e in public),'latest_ts':max(e['ts'] for e in public),
            'settled_units':sum(counts.values()),'shops_with_settlement':len(counts),'own_settled_units':counts['outseop-26510122'],
            'settlement_rank':1+sum(v>counts['outseop-26510122'] for v in counts.values()),
            'top_shops':counts.most_common(6),'own_submitted_receipts':len(own_results),
            'own_result_failed_checks':dict(Counter(k for e in own_results for k,v in e['data'].get('checks',{}).items() if v is False))}
    result['limitations']=['This is an observational pre-change snapshot, not a causal experiment.',
        'Mature cohorts exclude arrivals in the last 10 minutes; each cohort is matched by task/order.',
        'Exact seller payment links use unique unit/price or market-verified results. Unconfirmed is not necessarily failed.',
        'The ledger sample ranks only shops with settlements in its time window, including house shops; URL rotation may split unknown aliases.',
        'Negotiated receipts are not paid purchases; budget/private order payloads and credentials are not exported.']
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pages',type=int,default=0);args=parser.parse_args()
    result=diagnose(Path(__file__).resolve().parents[1],time.time(),args.pages)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
