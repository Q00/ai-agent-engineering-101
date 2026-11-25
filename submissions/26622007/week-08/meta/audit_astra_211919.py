"""Read-only, fixed-cutoff review; stdout is the immutable audit for this cycle."""
import collections, datetime as dt, gzip, hashlib, json, math, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_model, profit_cohorts, profit_price
ROOT=Path(__file__).resolve().parents[1]
END=dt.datetime.fromisoformat('2026-11-04T21:19:19+09:00').timestamp()
START=dt.datetime.fromisoformat('2026-11-04T09:19:33+09:00').timestamp()
def emit(kind,**kw): print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False))
def main():
 rows=list({r['seq']:r for r in profit_cohorts.read_rows(ROOT/'.market/ledger_rows.jsonl') if r.get('ts',0)<=END}.values())
 refs={r['seq']:f'.market/ledger_rows.jsonl:{i}' for i,r in enumerate(profit_cohorts.read_rows(ROOT/'.market/ledger_rows.jsonl'),1)}
 mine=profit_cohorts.read_json(ROOT/'.market/loss_state.json',{})['mine']
 mem=profit_cohorts.read_json(ROOT/'memory/seller_memory_r3.json',{})['negotiations']
 costs={r['unit']:r['cost'] for r in mem if r.get('result')=='deal' and r.get('unit') and isinstance(r.get('cost'),(int,float))}
 rawlogs={}; priced={}; lines=set()
 pack=json.loads((ROOT/'meta/evidence/astra-1104-211919.json').read_text())
 for p in pack['files']:
  if not p.startswith('logs/') or not (ROOT/p).is_file(): continue
  data=(ROOT/p).read_bytes()
  emit('log_read',path=p,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
  if 'market-kit' not in p or 'archive-round2' in p: continue
  text=gzip.decompress(data).decode(errors='replace') if p.endswith('.gz') else data.decode(errors='replace')
  for i,l in enumerate(text.splitlines(),1):
   m=re.search(r'seller PRICE (\S+) unit (\S+) cost (\d+)',l)
   if m: priced[m[2]]=(int(m[3]),f'{p}:{i}')
   if re.match(r'\d\d:\d\d:\d\d ',l): lines.add(l)
 # All relevant logs in this comparison are 11/04. Pre-09:00 material is outside the comparison windows.
 logs=[(dt.datetime.fromisoformat('2026-11-04T'+l[:8]+'+09:00').timestamp(),l) for l in lines]
 refunds={s['seq']:s['amount'] for b,s in market_model.link_broken(rows) if str(s['seq']) in mine and 0<=b['ts']-s['ts']<=s.get('warranty',0)*3600}
 own=[r for r in rows if r.get('k')=='settle' and str(r['seq']) in mine]
 def window(lo,hi):
  ss=[r for r in own if lo<=r['ts']<hi]; ll=[l for t,l in logs if lo<=t<hi]
  known=[s for s in ss if mine[str(s['seq'])].get('unit') in costs]
  return dict(paid=len(ss),revenue=sum(s['amount'] for s in ss),known_net=sum(s['amount']-costs[mine[str(s['seq'])]['unit']]-refunds.get(s['seq'],0) for s in known),missing_cost=len(ss)-len(known),refunds=sum(refunds.get(s['seq'],0) for s in ss),market_paid=sum(r.get('k')=='settle' and lo<=r['ts']<hi for r in rows),cut_lines=sum('[restock] guard:' in l and ' -> ' in l for l in ll),no_stock_logs=sum('REJECTED (not in stock)' in l for l in ll),samples=[refs[s['seq']] for s in ss[:1]+ss[-1:]])
 emit('method',start=START,end=END,notes='전후 각 30분; 알려진 원가만 순기여에 포함; 환불은 마감 원장의 주문 연결로 재계산. 짧은 구간은 미숙성·시장수요·동시변경으로 인과효과 아님. kit 중복 행 제거, 품절 로그는 품절 분이 아님.',cycle=window(START,END))
 git=subprocess.check_output(['git','log','--reverse','--since=2026-11-04T09:05:09+09:00','--format=%h%x09%ct%x09%s','--','.'],cwd=ROOT,text=True)
 for l in git.splitlines():
  sha,t,title=l.split('\t',2); t=int(t)
  if t>END: continue
  files=subprocess.check_output(['git','show','--format=','--name-only',sha,'--','.'],cwd=ROOT,text=True).splitlines()
  emit('commit',commit=sha,title=title,files=files,before=window(t-1800,t),after=window(t,min(t+1800,END)))
 emit('refund_reconciliation',old=pack['shop']['losses']['break_refund'],ledger_refunds=len(refunds),ledger_refund_amount=sum(refunds.values()),board_refunds=pack['board']['shops']['mine']['refunds'])
 for sku in ['G1515d951','G6b9db161','G75f11c19']:
  for label,start in [('all',0),('astra_trial',START),('terra_reduction',dt.datetime.fromisoformat('2026-11-04T15:11:00+09:00').timestamp())]:
   ss=[s for s in own if s.get('sku')==sku and s['ts']>=start]; mature=[s for s in ss if END-s['ts']>=21600]
   missing=[s for s in mature if mine[str(s['seq'])].get('unit') not in costs]
   net=sum(s['amount']-costs[mine[str(s['seq'])]['unit']]-refunds.get(s['seq'],0) for s in mature if mine[str(s['seq'])].get('unit') in costs)
   emit('cohort',sku=sku,cohort=label,paid=len(ss),mature=len(mature),known_net=net,missing=len(missing),refunds=sum(s['seq'] in refunds for s in mature),missing_cost_recovery=[dict(seq=s['seq'],unit=mine[str(s['seq'])].get('unit'),price_cost=priced.get(mine[str(s['seq'])].get('unit'))) for s in missing])
 cat=profit_cohorts.read_json(ROOT/'.market/catalog.json',{})
 names={r['shop']:r['seller'] for r in rows if r.get('seller') and r.get('shop')}
 cfg=profit_cohorts.read_json(ROOT/'pricing.json',{})
 for r in pack['board']['rivals']['top']:
  name=r['name']; urls={url for url,n in names.items() if name.lower() in n.lower()}
  ss=[s for s in rows if s.get('k')=='settle' and s.get('shop') in urls and START<=s['ts']<=END]
  emit('rival',name=name,urls=sorted(urls),paid=len(ss),buyers=len({s.get('buyer') for s in ss}),top=[dict(sku=sku,paid=n,prices=[min(s['amount'] for s in ss if s['sku']==sku),max(s['amount'] for s in ss if s['sku']==sku)],buyers=len({s['buyer'] for s in ss if s['sku']==sku})) for sku,n in collections.Counter(s['sku'] for s in ss).most_common(5)],margin='경쟁 원가 미공개')
 for sku in ['G03d0199f','G05277ffe','Gb5d0dde7','G75f11c19']:
  ss=[s for s in rows if s.get('k')=='settle' and s.get('sku')==sku and END-7200<=s['ts']<=END]; lp=cat[sku]['list_price']; cost=lp*.75
  emit('screen',sku=sku,list_price=lp,paid=len(ss),buyers=len({s['buyer'] for s in ss}),price_min=min(s['amount'] for s in ss),price_max=max(s['amount'] for s in ss),worst_cost_no_surge=cost,worst_cost_max_surge=cost*1.5,floor15=profit_price.floor(cost,lp,'goods',.15),floor25=profit_price.floor(cost,lp,'goods',.25),above_floor15=sum(s['amount']>=profit_price.floor(cost,lp,'goods',.15) for s in ss))
 for path in sorted((ROOT/'tools').glob('*/manifest.json')):
  name=path.parent.name; calls=[r for r in profit_cohorts.read_rows(ROOT/'.market/tool_calls.jsonl') if r.get('tool')==name and START<=r.get('ts',0)<=END]
  emit('tool',name=name,calls=len(calls),failed=sum(not r.get('ok') for r in calls),selftest='selftest' in json.loads(path.read_text()))
 for p in ['.market/measure/orders.jsonl','.market/measure/stock.jsonl','.market/measure/supply_windows.jsonl','.market/measure/sales_6h.jsonl','.market/measure/forum_followup.jsonl']:
  rr=[r for r in profit_cohorts.read_rows(ROOT/p) if r.get('ts',0)<=END]; emit('measure',path=p,rows=len(rr),first=min((r['ts'] for r in rr),default=None),last=max((r['ts'] for r in rr),default=None))
 stock=profit_cohorts.read_rows(ROOT/'.market/measure/stock.jsonl'); windows=profit_cohorts.read_rows(ROOT/'.market/measure/supply_windows.jsonl')
 for hour in range(18,21):
  lo=dt.datetime.fromisoformat(f'2026-11-04T{hour}:00:00+09:00').timestamp(); hi=lo+3600
  ww=[w for w in windows if lo<=w['opens_at']<hi]; emit('hour',hour=hour,paid=window(lo,hi)['paid'],windows=len(ww),paid_per_window=window(lo,hi)['paid']/len(ww) if ww else None)
 for sku in ['G75f11c19','G1515d951','G6b9db161']:
  rr=sorted([r for r in stock if r.get('sku')==sku and r['ts']<=END],key=lambda r:r['ts']); zero=sum((rr[i+1]['ts'] if i+1<len(rr) else END)-r['ts'] for i,r in enumerate(rr) if r['qty']==0)
  emit('stockout',sku=sku,observed_from=rr[0]['ts'] if rr else None,minutes=round(zero/60,2),observed_minutes=round((END-rr[0]['ts'])/60,2) if rr else None)
 forum=profit_cohorts.read_json(ROOT/'.market/forum.json',[])
 emit('forum',demand=[{k:p.get(k) for k in ['id','author','role','text','score']} for p in forum if '구합니다' in p.get('text','')][:8],top=sorted(forum,key=lambda p:-(p.get('score') or 0))[:3])
 for p in sorted((ROOT/'meta/evidence').glob('terra-1104-*.json')):
  x=json.loads(p.read_text()); t=dt.datetime.fromisoformat(x['time']).timestamp()
  if START<=t<=END: emit('terra_pack',cycle=p.stem,profit=x['shop']['profit_if_stock_is_worthless'],players=x['board']['objective']['players'],paid=x['board']['shops']['mine']['deals'],log_counts=x.get('since_last_cycle',{}).get('log_counts'),ref=str(p.relative_to(ROOT)))
if __name__=='__main__': main()
