import collections, datetime as dt, json, re
from pathlib import Path
end=dt.datetime.fromisoformat('2026-11-07T11:37:32+09:00').timestamp()
windows=[json.loads(l) for l in open('.market/measure/supply_windows.jsonl')]
windows=sorted({w['opens_at']:w for w in windows if w.get('closes_at',end+1)<=end and w.get('ts',end+1)<=end}.values(),key=lambda w:w['opens_at'])[-2:]
log=[]
for i,l in enumerate(open('logs/market-kit.log'),1):
 if not re.match(r'\d{2}:\d{2}:\d{2} ',l):continue
 t=dt.datetime.fromisoformat('2026-11-07T'+l[:8]+'+09:00').timestamp()
 if t<=end:log.append((i,t,l.strip()))
for w in windows:
 cuts=[(i,l) for i,t,l in log if w['opens_at']<=t<=w['closes_at'] and '[restock] guard:' in l]
 print(json.dumps({'kind':'window_guards','opens_at':w['opens_at'],'closes_at':w['closes_at'],'stock_cost_cuts':sum('(stock cost)' in l for _,l in cuts),'units_per_sku':sum('units per SKU' in l for _,l in cuts),'cash_kept':sum('cash kept' in l for _,l in cuts),'samples':[{'ref':f'logs/market-kit.log:{i}','excerpt':l} for i,l in cuts if 'units per SKU' in l][:5]},ensure_ascii=False))
prices=[(i,l) for i,t,l in log if t>=dt.datetime.fromisoformat('2026-11-07T06:37:00+09:00').timestamp() and 'G6b9db161' in l and ('[price]' in l or 'seller PRICE' in l)]
print(json.dumps({'kind':'g6_prices','samples':[{'ref':f'logs/market-kit.log:{i}','excerpt':l} for i,l in prices[:10]],'count':len(prices)},ensure_ascii=False))
# Latest decision actual follow-through at cutoff.
for i,l in enumerate(open('.market/decisions.jsonl'),1):
 r=json.loads(l)
 if r.get('agent')=='restock' and end-7200<=r.get('ts',0)<=end:
  print(json.dumps({'kind':'restock_decision','ref':f'.market/decisions.jsonl:{i}','ts':r['ts'],'reason':r.get('reason'),'calls':r.get('calls')},ensure_ascii=False))
