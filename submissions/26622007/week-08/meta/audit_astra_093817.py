"""Fixed-cutoff read-only audit; stdout saved once, never rewrites operational logs."""
import collections, datetime as dt, gzip, hashlib, json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc, market_model, strategy_screen
ROOT=Path(__file__).resolve().parents[1]
START=dt.datetime.fromisoformat('2026-11-04T21:19:19+09:00').timestamp()
END=dt.datetime.fromisoformat('2026-11-05T09:38:17+09:00').timestamp()
def emit(kind,**x): print(json.dumps(dict(kind=kind,**x),ensure_ascii=False))
def read(p): return pc.read_json(ROOT/p,{})
def rows(p): return pc.read_rows(ROOT/p)
def main():
 pack=read('meta/evidence/astra-1105-093817.json')
 for p in pack['files']:
  if p.startswith('logs/') and (ROOT/p).is_file():
   b=(ROOT/p).read_bytes(); content=gzip.decompress(b) if p.endswith('.gz') else b
   emit('log_read',path=p,bytes=len(b),lines=len(content.splitlines()),sha256=hashlib.sha256(b).hexdigest())
 rr=list({r['seq']:r for r in rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=END}.values())
 mine=read('.market/loss_state.json')['mine']; mem=read('memory/seller_memory_r3.json')['negotiations']
 costs={r['unit']:r['cost'] for r in mem if r.get('result')=='deal' and r.get('unit') and isinstance(r.get('cost'),(int,float))}
 refs={r['seq']:f'.market/ledger_rows.jsonl:{i}' for i,r in enumerate(rows('.market/ledger_rows.jsonl'),1)}
 own=[r for r in rr if r.get('k')=='settle' and str(r['seq']) in mine]
 refunds={s['seq']:s['amount'] for b,s in market_model.link_broken(rr) if str(s['seq']) in mine and 0<=b['ts']-s['ts']<=s.get('warranty',0)*3600 and (not b.get('order') or b['order']==s.get('order'))}
 def win(lo,hi):
  ss=[r for r in own if lo<=r['ts']<hi]; known=[r for r in ss if mine[str(r['seq'])].get('unit') in costs]
  return dict(paid=len(ss),revenue=sum(r['amount'] for r in ss),net_after_refunds=sum(r['amount']-costs[mine[str(r['seq'])]['unit']]-refunds.get(r['seq'],0) for r in known),missing_cost=len(ss)-len(known),open_warranty=sum(r['ts']>END-21600 for r in ss),market_paid=sum(r.get('k')=='settle' and lo<=r['ts']<hi for r in rr),refs=[refs[r['seq']] for r in ss[:1]+ss[-1:]])
 emit('cycle',start=START,end=END,**win(START,END))
 git=subprocess.check_output(['git','log','--reverse','--since=2026-11-04T21:19:19+09:00','--format=%h%x09%ct%x09%s','--','.'],text=True)
 for l in git.splitlines():
  sha,t,title=l.split('\t',2); t=int(t)
  if t>END:continue
  files=subprocess.check_output(['git','show','--format=','--name-only',sha,'--','.'],text=True).splitlines()
  emit('commit',sha=sha,ts=t,title=title,files=files,before=win(t-1800,t),after=win(t,min(t+1800,END)))
 for i,g in enumerate(rows('meta/grades.jsonl'),1):
  if not g.get('cycle','').startswith('terra-'):continue
  t=dt.datetime.fromisoformat(g['time']).timestamp()
  if t<START:continue
  emit('terra',cycle=g['cycle'],status=g['status'],source=f'meta/grades.jsonl:{i}',check=g.get('strategy_check'),gap=g.get('evidence_gap'),before=win(t-3600,t),after=win(t,min(t+3600,END)))
 for label,start in [('restored',1793795897),('trial',1793809801)]:
  c=pc.summarize(rr,mine,mem,rows('memory/agent/losses.jsonl'),END,start)
  for sku in ['G1515d951','G6b9db161','G75f11c19','Ge62449c9']:emit('cohort',label=label,sku=sku,start=start,data=c['skus'].get(sku))
 for r in own:
  if r.get('order')=='o-e4ddb664917c':emit('trial_sale',sale=r,cost=costs.get(mine[str(r['seq'])].get('unit')),refund=refunds.get(r['seq'],0),mature=END-r['ts']>=21600,ref=refs[r['seq']])
 wins=sorted({r['opens_at']:r for r in rows('.market/measure/supply_windows.jsonl') if START<=r.get('opens_at',0)<=END}.values(),key=lambda r:r['opens_at'])
 incoming={r['unit']:r for r in rows('.market/measure/units_in.jsonl') if r['ts']<=END}
 outgoing={r['unit']:r['ts'] for r in rows('.market/measure/units_out.jsonl') if r['ts']<=END}
 for r in own:
  u=mine[str(r['seq'])].get('unit'); outgoing[u]=min(outgoing.get(u,END+1),r['ts'])
 for sku,target in [('G1515d951',5),('G6b9db161',2),('G75f11c19',2)]:
  pairs=[]
  for a,b in zip(wins,wins[1:]):
   same=[u for u,r in incoming.items() if r['sku']==sku and r.get('supplied_at',r['ts'])<=a['opens_at'] and outgoing.get(u,END+1)>b['opens_at']]
   if len(same)>=target:pairs.append(dict(opens=[a['opens_at'],b['opens_at']],units=same))
  emit('retention',sku=sku,target=target,windows=len(wins),pairs=pairs,meaning='target units survived both opens = at least one additional unit above target-1; actual sale or observed departure, whichever earlier')
 stock=rows('.market/measure/stock.jsonl')
 for hour in range(12):
  lo=END-12*3600+hour*3600; hi=lo+3600; window_n=sum(lo<=r['opens_at']<hi for r in wins)
  shortages={}
  for sku in ['G1515d951','G6b9db161','G75f11c19']:
   ss=sorted([r for r in stock if r['sku']==sku and r['ts']<hi],key=lambda r:r['ts']); zero=0
   for i,r in enumerate(ss):
    end=min(hi,ss[i+1]['ts'] if i+1<len(ss) else hi)
    if r['qty']==0:zero+=max(0,end-max(lo,r['ts']))
   shortages[sku]=round(zero/60,2)
  emit('hour',start=lo,end=hi,windows=window_n,paid_per_window=win(lo,hi)['paid']/window_n if window_n else None,stockout_minutes=shortages,**win(lo,hi))
 names={r['shop']:r['seller'] for r in rr if r.get('seller') and r.get('shop')}
 for rival in pack['board']['rivals']['top']:
  urls={u for u,n in names.items() if rival['name'].lower() in n.lower()}
  ss=[r for r in rr if r.get('k')=='settle' and r.get('shop') in urls and START<=r['ts']<=END]
  emit('rival',name=rival['name'],mapped=bool(urls),paid=len(ss) if urls else None,buyers=len({r.get('buyer') for r in ss}) if urls else None,top=[dict(sku=k,paid=v,min=min(r['amount'] for r in ss if r['sku']==k),max=max(r['amount'] for r in ss if r['sku']==k)) for k,v in collections.Counter(r['sku'] for r in ss).most_common(5)],margin=None)
 for p in sorted((ROOT/'tools').glob('*/manifest.json')):
  calls=[r for r in rows('.market/tool_calls.jsonl') if r.get('tool')==p.parent.name and START<=r.get('ts',0)<=END]
  emit('tool',name=p.parent.name,calls=len(calls),failed=sum(not r.get('ok') for r in calls),selftest=read(str(p.relative_to(ROOT))).get('selftest'))
 for off in [3600,0]:
  emit('screen_hour',end=END-off,data=strategy_screen.screen(rr,read('.market/catalog.json'),read('.market/inventory.json'),read('pricing.json'),END-off,hours=1,sku='Ge62449c9'),surge_note='current snapshot only, not historical surge')
 trades=read('memory/market_memory_r3.json')['trades']; cutoff=1793797078
 def ts(r):return dt.datetime.fromisoformat(r['time']).replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp()
 paid_orders={r['order'] for r in rows('.market/payments.jsonl') if r.get('paid')}
 for label,ss in [('before',[r for r in trades if ts(r)<cutoff][-100:]),('after',[r for r in trades if cutoff<=ts(r)<=END][:100])]:
  paid=[r for r in ss if r.get('order') in paid_orders]; goods=[r for r in paid if (read('.market/catalog.json').get(r.get('item')) or {}).get('kind')=='goods']
  emit('buyer100',label=label,orders=len(ss),paid=len(paid),goods=len(goods),nominal_goods_surplus=sum(r['list_price']-r['price'] for r in goods),above800k=sum((r.get('price') or 0)>800000 for r in paid),note='nominal goods list-price saving, not actual board surplus; food value missing')
 forum=read('.market/forum.json')
 for r in forum if isinstance(forum,list) else []:
  if r.get('ts',0)<=END and ('구합니다' in r.get('text','') or r.get('author')=='nocha12'):emit('forum',post=r)
if __name__=='__main__':main()
