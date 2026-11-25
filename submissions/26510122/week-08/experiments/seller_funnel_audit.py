"""Read-only seller funnel audit. Raw events and signed receipts are not purchases.

Run from the submission directory. Public result sampling can explain recorded
checkout failures, but cannot expose another buyer's private rejection reason.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from market_memory import events
from inventory_observation import timestamp
from stock_policy import replenishment_candidates

NAME='outseop-26510122'
BASE='https://market.ourolabs.ai'


def get(path):
    with urllib.request.urlopen(BASE+path,timeout=20) as response:
        return json.load(response)


def audit(rows,now):
    rows=[e for e in rows if (timestamp(e.get('at')) or 0) <= now]
    starts={e['task_id']:e for e in rows if e['event']=='request_seen' and e.get('task_id')}
    settled={e['ledger']:e for e in rows if e['event']=='settled' and e.get('ledger')}
    paid=list(settled.values())
    receipts={e['task_id']:e for e in rows if e['event']=='receipt_signed' and e.get('task_id')}
    costs={e['unit']:e['cost'] for e in receipts.values() if e.get('unit') and e.get('cost') is not None}
    report={}
    for hours in (1,3,6):
        subset=[e for e in rows if now-hours*3600 <= (timestamp(e.get('at')) or 0) <= now]
        counts=Counter(e['event'] for e in subset)
        cohort={t for t,e in starts.items() if now-hours*3600 <= (timestamp(e['at']) or 0) <= now-600}
        bytask=defaultdict(set)
        for e in rows:
            if e.get('task_id') in cohort: bytask[e['event']].add(e['task_id'])
        sales=[e for e in paid if now-hours*3600 <= (timestamp(e['at']) or 0) <= now]
        no_stock=[e for e in subset if e['event']=='no_stock']
        mature=[e for e in receipts.values() if now-hours*3600 <= (timestamp(e['at']) or 0) <= now-600]
        # Reissuing a receipt for the same unit makes latest-receipt task links
        # ambiguous. Keep that uncertainty visible rather than invent conversion.
        receipt_groups=defaultdict(list)
        for e in receipts.values():receipt_groups[(e.get('unit'),e.get('price'))].append(e)
        unique_paid={(e.get('unit'),e.get('price')) for e in paid}
        settled_tasks={e.get('task_id') for e in paid}
        report[str(hours)]={
            'rolling_events':{k:counts[k] for k in ['request_seen','no_stock','requirements_unmet','opening','offer','counter','rejected','receipt_signed','settled','cancelled','negotiation_expired','stale_inventory']},
            'mature_request_cohort':{'requests':len(cohort),**{k:len(bytask[k]) for k in ['no_stock','requirements_unmet','opening','offer','receipt_signed','settled','rejected','cancelled']},'maturation_seconds':600},
            'mature_receipt_cohort':{'receipts':len(mature),
                'settled_task_links':sum(e['task_id'] in settled_tasks for e in mature),
                'unique_unit_price_receipts':sum(len(receipt_groups[(e.get('unit'),e.get('price'))])==1 for e in mature),
                'ambiguous_reissued_same_unit_price':sum(len(receipt_groups[(e.get('unit'),e.get('price'))])>1 for e in mature),
                'unique_receipts_matching_paid_unit_price':sum(len(receipt_groups[(e.get('unit'),e.get('price'))])==1 and (e.get('unit'),e.get('price')) in unique_paid for e in mature),
                'matching_settled_unit_price_upper_bound':sum((e.get('unit'),e.get('price')) in unique_paid for e in mature)},
            'settled_sales':len(sales),'revenue':sum(e.get('price',0) for e in sales),
            'acquisition_spread_before_holding_refunds':sum(e['price']-costs[e['unit']] for e in sales if e.get('unit') in costs),
            'sold_by_sku':dict(Counter(e.get('sku') for e in sales)),
            'no_stock_exact_skus':dict(Counter(s for e in no_stock for s in e.get('requested_skus',[]))),
            'no_stock_broad_requests':dict(Counter(e.get('request','') for e in no_stock if not e.get('requested_skus')))}
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--pages',type=int,default=12)
    args=p.parse_args();now=time.time();rows=events()
    result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'local':audit(rows,now)}
    board=get('/api/board');result['board']={k:[s for s in board.get(k,[]) if s.get('name')==NAME] for k in ('shops','buyers')}
    public=[];cursor=-500
    for _ in range(args.pages):
        batch=get('/api/ledger?after='+str(cursor))
        if not batch:break
        public.extend(batch);cursor=min(e['seq'] for e in batch)-501
    public=list({e['seq']:e for e in public}.values())
    own=[e for e in public if e.get('kind')=='result' and (e.get('data',{}).get('receipt') or {}).get('shop')==NAME]
    result['public_result_sample']={'entries_scanned':len(public),'oldest_ts':min(e['ts'] for e in public),
        'newest_ts':max(e['ts'] for e in public),'own_results':len(own),
        'failed_checks':dict(Counter(k for e in own for k,v in e['data'].get('checks',{}).items() if v is False)),
        'rows':own}
    runtime=Path('state/seller-runtime');inv=json.loads((runtime/'.market/inventory.json').read_text())
    catalog=json.loads((runtime/'.market/catalog.json').read_text())
    state=json.loads((runtime/'state/inventory-portfolio.json').read_text())
    result['inventory']={'balance':inv['balance'],'units':len(inv['units']),
        'stock_cost':sum(u.get('cost',0) for u in inv['units']),
        'new_trial_candidates':[{k:r[k] for k in ('sku','name','target_options','evidence')} for r in replenishment_candidates(catalog,inv,rows,now,trialed={i['sku'] for i in state['intents'] if i.get('second_trial')}) if r['second_trial']]}
    result['limitations']=['Task linkage in old settlement events uses the most recent receipt for a unit, and can be ambiguous after reissued receipts.',
        'Unsigned or unsubmitted receipts are absent from public results; no private buyer rejection reason can be inferred.',
        'Rolling event counts are different populations; mature cohorts match request task IDs with at least 10 minutes elapsed.',
        'Positive acquisition spread excludes storage costs and refunds.']
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('local','public_result_sample')},ensure_ascii=False)[:2500])
    print(json.dumps({'3h':result['local']['3'],'public':{k:v for k,v in result['public_result_sample'].items() if k!='rows'}},ensure_ascii=False))

if __name__=='__main__':main()
