"""Independent SQL joins and inventory arithmetic for the frozen review."""
from datetime import datetime, timezone, timedelta
import json
import re
import sqlite3
import sys
from r3.state import ROOT,read_json

r=read_json(ROOT/sys.argv[1]);checks={};sql='''
WITH orders AS (
 SELECT json_extract(data,'$.order') AS oid,json_extract(data,'$.kind') AS category,
 ROW_NUMBER() OVER(PARTITION BY json_extract(data,'$.order') ORDER BY ts DESC,rowid DESC) AS rn
 FROM events WHERE kind='buyer_order' AND ts>=? AND ts<=?
), closed AS (
 SELECT DISTINCT json_extract(data,'$.order') AS oid
 FROM events WHERE kind='market_result' AND ts>=? AND ts<=?
), paid AS (
 SELECT DISTINCT json_extract(data,'$.order') AS oid
 FROM events WHERE kind='purchase' AND ts>=? AND ts<=?
)
SELECT o.category,COUNT(*),SUM(p.oid IS NOT NULL)
FROM orders o JOIN closed z ON z.oid=o.oid LEFT JOIN paid p ON p.oid=o.oid
WHERE o.rn=1 GROUP BY o.category
'''
with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
    c.execute('BEGIN')
    for name in ['before_equal_duration','since_framework']:
        w=r['buyer_windows'][name];lo,hi=w['start'],w['end']
        actual={category:{'closed':n,'paid':paid} for category,n,paid in c.execute(sql,(lo,hi,lo,hi,lo,hi))}
        for category,row in actual.items():
            assert row=={k:w['segments'][category][k] for k in ['closed','paid']}
        checks[name]=actual
lo=datetime.fromisoformat(r['comparisons'][2]['start_kst']).timestamp();hi=r['cutoff']
supplies=[]
for line in (ROOT/'logs/auto-join-20261110T071853692465Z.txt').read_text(encoding='utf-8',errors='replace').splitlines():
    m=re.match(r'^(\d{2}:\d{2}:\d{2}) \[supply\] (\d+) x .* for (\d+), balance (\d+)',line)
    if not m:continue
    at=datetime.fromisoformat('2026-11-10T'+m[1]+'+09:00').timestamp()
    if lo<=at<=hi:supplies.append({'at':at,'qty':int(m[2]),'cost':int(m[3])})
before=r['comparisons'][2]['before']['shops']['own'];after=r['current']['shops']['own']
stock={'baseline_stock':before['stock_units'],'purchased_units':sum(x['qty'] for x in supplies),
       'purchase_calls':len(supplies),'purchase_cost':sum(x['cost'] for x in supplies),
       'official_sales':after['deals']-before['deals'],'current_stock':after['stock_units'],
       'source':'logs/auto-join-20261110T071853692465Z.txt','parser':r'^HH:MM:SS [supply] Q x ... for COST, balance BALANCE'}
stock['residual']=stock['baseline_stock']+stock['purchased_units']-stock['official_sales']-stock['current_stock']
checks['inventory_reconciliation']=stock
checks['method_note']='Stock residual is reported, not forced to zero. Console and board cutoff can straddle a transaction.'
checks['sql']=sql
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
path=ROOT/'logs'/f'evening-progress-validation-{stamp}.json'
path.write_text(json.dumps(checks,indent=2),encoding='utf-8')
print('VALIDATION',path);print(json.dumps(checks))
