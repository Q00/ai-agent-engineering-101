"""Capture BEFORE and AFTER market share and local receipt prices, without inventing measurements.

Use dashboard COUNTED deal numbers as arguments for each distinct snapshot;
local seller signed receipts are a different, explicitly labeled measurement.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent
KIT_LOG=ROOT/'logs'/'market-kit.log'
OUT=ROOT/'logs'/'challenge-1'
PATTERN=re.compile(r'\[SELLER SIGNED RECEIPT\]\s+task=(\S+)\s+item=(lamp|chair|bookshelf)\s+price=(\d+)')


def receipts_from_log() -> dict[str, int]:
    receipts={}
    if not KIT_LOG.exists():
        return receipts
    with KIT_LOG.open('r', encoding='utf-8', errors='replace') as file:
        for line in file:
            match=PATTERN.search(line)
            if match:
                receipts[match.group(1)]=int(match.group(3))
    return receipts


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['before','after'],required=True)
    parser.add_argument('--counted-deals',type=int,required=True)
    parser.add_argument('--market-total',type=int,required=True)
    parser.add_argument('--reviews',type=int,required=True)
    parser.add_argument('--avg-stars',type=float,required=True)
    args=parser.parse_args()
    if args.counted_deals<0 or args.market_total<=0 or args.counted_deals>args.market_total or args.reviews<0 or not 1<=args.avg_stars<=5:
        parser.error('Invalid dashboard metrics')
    OUT.mkdir(parents=True,exist_ok=True)
    current=receipts_from_log()
    before_file=OUT/'before.json'
    baseline={}
    if args.phase=='after':
        if not before_file.exists():
            parser.error('No BEFORE snapshot. Capture before replacing/rerunning seller.')
        baseline=json.loads(before_file.read_text(encoding='utf-8'))
        if 'local_signed_task_ids' not in baseline:
            parser.error('Baseline file has no task IDs')
        prices=[p for task,p in current.items() if task not in set(baseline['local_signed_task_ids'])]
    else:
        prices=list(current.values())
    snap={
        'phase':args.phase,
        'observed_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source':'Counted deals/total and ratings: user-observed Agent Market Round-2 dashboard',
        'shop':'chanarun',
        'counted_deals':args.counted_deals,
        'market_total_counted_deals':args.market_total,
        'share_percent_exact_from_counts':round(args.counted_deals/args.market_total*100,5),
        'reviews':args.reviews,
        'avg_stars':args.avg_stars,
        'local_signed_completed_receipt_count_in_phase':len(prices),
        'local_signed_completed_receipt_avg_price_in_phase':round(sum(prices)/len(prices),2) if prices else None,
        'local_signed_receipts_are_not_necessarily_market_counted_deals':True,
    }
    if args.phase=='before':
        snap['local_signed_task_ids']=sorted(current)
    (OUT/f'{args.phase}.json').write_text(json.dumps(snap,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Seller can now read real aggregated reviews locally.
    reputation={
        'shop':'chanarun',
        'review_count':args.reviews,
        'avg_stars':args.avg_stars,
        'source':snap['source'],
        'observed_utc':snap['observed_utc'],
    }
    rep=ROOT/'.market'/'own-review-summary.json'
    rep.parent.mkdir(parents=True,exist_ok=True)
    rep.write_text(json.dumps(reputation,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in snap.items() if k!='local_signed_task_ids'},indent=2))
    if args.phase=='after':
        print('BEFORE market share:',baseline['share_percent_exact_from_counts'],'%')
        print('AFTER  market share:',snap['share_percent_exact_from_counts'],'%')
        print('BEFORE avg local signed receipt:',baseline['local_signed_completed_receipt_avg_price_in_phase'])
        print('AFTER  avg local signed receipt:',snap['local_signed_completed_receipt_avg_price_in_phase'])

if __name__=='__main__':
    main()
