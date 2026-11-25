"""Cross-check public ranking counts against paid evidence without rewriting scores."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from market_memory import iter_events

NAME='outseop-26510122'

def paid_summary(path):
    paid={}
    for row in iter_events(path,kinds={'settled'}):
        if type(row.get('ledger')) is int and type(row.get('price')) is int:
            paid[row['ledger']]=row
    return {'settled':len(paid),'revenue':sum(e['price'] for e in paid.values()),
            'latest_ledger':max(paid,default=None)}

def compare(board, seller, buyer, previous=None, *, now=None):
    now=time.time() if now is None else now
    shops=board.get('shops',[])
    buyers=board.get('buyers',[])
    shop=next((r for r in shops if r.get('name')==NAME),{})
    own=next((r for r in buyers if r.get('name')==NAME),{})
    digest=hashlib.sha256(json.dumps(board,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    issues=[]
    if type(shop.get('deals')) is int and (seller['settled']>shop['deals']+5 or shop['deals']==0 and seller['settled']>0):
        issues.append('seller_paid_count_below_local_verified_lower_bound')
    if shop.get('deals')==0 and shop.get('revenue')==0 and seller['revenue']>0:
        issues.append('seller_revenue_below_local_verified_lower_bound')
    if type(own.get('deals')) is int and (buyer['settled']>own['deals']+5 or own['deals']==0 and buyer['settled']>0):
        issues.append('buyer_paid_count_below_local_verified_lower_bound')
    first_seen=now
    if previous and previous.get('board_digest')==digest:
        first_seen=previous.get('same_board_first_seen',previous['at'])
        progressed=any((current.get('latest_ledger') or 0)>(previous.get(role,{}).get('latest_ledger') or 0)
                       for role,current in [('seller',seller),('buyer',buyer)])
        if progressed and now-first_seen>=60:
            issues.append('board_unchanged_while_new_paid_ledgers_arrived')
    return {'at':now,'board_digest':digest,'same_board_first_seen':first_seen,
            'official_seller':{k:shop.get(k) for k in ('deals','revenue','book','all','refunds')},
            'gross_local_minus_official_revenue':seller['revenue']-(shop.get('revenue') or 0),
            'revenue_comparison_note':'Local gross paid amount and official revenue can differ due to refunds and historical coverage; difference alone does not invalidate official ranking.',
            'count_tolerance_for_request_timing':5,
            'official_buyer':{k:own.get(k) for k in ('deals','saved','all')},
            'seller':seller,'buyer':buyer,'issues':issues,
            'official_ranking_reliable':not issues,
            'note':'Local paid counts are evidence/lower bounds, never replacements for official rank or Saved. '
                   'Seller spread, storage, spoilage and refunds are separate. No keys, DB or ledger rows are changed.'}

def snapshot(root=ROOT):
    root=Path(root)
    path=root/'state/buyer-runtime/state/market-consistency.json'
    try:previous=json.loads(path.read_text())
    except (OSError,ValueError):previous=None
    with urllib.request.urlopen('https://market.ourolabs.ai/api/board?round=3',timeout=8) as response:
        board=json.load(response)
    report=compare(board,paid_summary(root/'state/seller-runtime/logs/shop-events.jsonl'),
                   paid_summary(root/'state/buyer-runtime/logs/buyer-events.jsonl'),previous)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+chr(10));temp.replace(path)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args();print(json.dumps(snapshot(args.root),ensure_ascii=False,indent=2))
