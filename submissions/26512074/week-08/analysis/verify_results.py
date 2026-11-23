"""Verify committed, public-field evidence without market access or secrets."""
import csv,json
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parent.parent
def read(name):return json.loads((ROOT/'logs'/name).read_text(encoding='utf-8'))
result=read('round3-results.json');board=read('round3-final-board.json');refusals=read('round3-rejections.json')
with (ROOT/'logs/round3-settlements.csv').open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
counts=Counter(r['role'] for r in rows)
assert counts['seller']==result['seller_local_unique_settlements']
assert counts['buyer']==result['buyer_local']['settle']['unique_orders']
assert len({(r['role'],r['order']or r['unit']or r['seq']) for r in rows})==len(rows)
tail=[r for r in rows if r['role']=='seller' and result['deadline']-7200<=float(r['ts'])<=result['deadline']]
assert len(tail)==result['final_two_hours']['seller_settlements']
assert sum(int(r['amount']) for r in tail)==result['final_two_hours']['seller_revenue']
assert board['at']<result['deadline'] and not board['exact_deadline_snapshot']
log=(ROOT/'logs/market-kit.log').read_text(encoding='utf-8').splitlines()
for category in refusals['categories']:
 for example in category['examples']:assert log[example['line']-1]==example['text']
buyer=next(r for r in board['board']['buyers'] if r['name']=='totato2203-r3')
seller=next(r for r in board['board']['shops'] if r['name']=='totato2203-r3')
assert buyer['deals']==result['official_buyer_deals']
assert seller['deals']==result['official_seller_deals']
print(json.dumps(dict(status='PASS',settlement_rows=len(rows),unique=True,official_counts_match_snapshot=True,log_examples_match_lines=True,exact_deadline_claim=False)))
