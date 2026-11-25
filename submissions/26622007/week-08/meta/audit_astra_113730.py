"""Fixed-cutoff read-only review; run with the repository venv."""
import collections, datetime as dt, hashlib, json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import market_model, profit_cohorts
ROOT=Path(__file__).resolve().parents[1]
PACK='meta/evidence/astra-1107-113730.json'
p=json.loads(Path(PACK).read_text()); end=dt.datetime.strptime(p['time'],'%Y-%m-%dT%H:%M:%S%z').timestamp()
start=dt.datetime.fromisoformat('2026-11-06T23:00:16+09:00').timestamp()
def emit(kind,**kw): print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False))
rows=[]; refs={}
for i,l in enumerate(open('.market/ledger_rows.jsonl'),1):
 r=json.loads(l)
 if r.get('ts',0)<=end: rows.append(r);refs[r['seq']]=f'.market/ledger_rows.jsonl:{i}'
rows=list({r['seq']:r for r in rows}.values()); sales=[r for r in rows if r.get('k')=='settle']
mine=json.load(open('.market/loss_state.json'))['mine']
mem=json.load(open('memory/seller_memory_r3.json'))['negotiations']
costs={n['unit']:n['cost'] for n in mem if n.get('result')=='deal' and n.get('unit') and isinstance(n.get('cost'),(int,float))}
refunds={s['seq']:s['amount'] for b,s in market_model.link_broken(rows) if str(s['seq']) in mine and 0<=b['ts']-s['ts']<=(s.get('warranty') or 0)*3600}
own=[s for s in sales if str(s['seq']) in mine]
def window(lo,hi):
 ss=[s for s in own if lo<=s['ts']<hi]; mature=[s for s in ss if s['ts']<=end-21600];known=[s for s in mature if mine[str(s['seq'])].get('unit') in costs]
 return dict(hours=round((hi-lo)/3600,3),paid=len(ss),revenue=sum(s['amount'] for s in ss),mature=len(mature),missing_cost=len(mature)-len(known),mature_net=sum(s['amount']-costs[mine[str(s['seq'])]['unit']]-refunds.get(s['seq'],0) for s in known),refunds=sum(refunds.get(s['seq'],0) for s in mature),buyer_paid=sum(s.get('buyer','').lower()=='nocha12' and lo<=s['ts']<hi for s in sales),market_paid=sum(lo<=s['ts']<hi for s in sales),sources=[refs[s['seq']] for s in ss[:1]+ss[-1:]])
emit('method',start=start,end=end,notes='전후 각 30분, 마감 이후 제외. 숙성은 6시간; 원가 누락은 별도. 여러 변경과 시장 이동이 겹쳐 인과효과는 아님.',window=window(start,end))
for path in p['files']:
 if path.startswith('logs/') and Path(path).is_file():
  b=Path(path).read_bytes();emit('log_read',path=path,bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
git=subprocess.check_output(['git','log','--reverse','--since=2026-11-06T23:00:16+09:00','--format=%h%x09%ct%x09%s','--','.'],text=True)
for l in git.splitlines():
 sha,t,title=l.split('\t',2);t=int(t)
 if t>end:continue
 files=subprocess.check_output(['git','show','--format=','--name-only',sha,'--','.'],text=True).splitlines()
 body=subprocess.check_output(['git','show','-s','--format=%B',sha],text=True).strip()
 emit('commit',commit=sha,time=t,title=title,message=body,files=files,before=window(t-1800,t),after=window(t,min(t+1800,end)))
shops=json.load(open('.market/shops.json')); cat=json.load(open('.market/catalog.json'))
for rival in p['board']['rivals']['top']:
 urls={s['url'] for s in shops if s.get('name')==rival['name']}
 urls.update(r['shop'] for r in rows if r.get('shop') and rival['name'].lower() in (r.get('seller') or '').lower())
 ss=[s for s in sales if s.get('shop') in urls and start<=s['ts']<=end]
 emit('rival',name=rival['name'],urls=sorted(urls),paid=len(ss),buyers=len({s['buyer'] for s in ss}),top_buyers=collections.Counter(s['buyer'] for s in ss).most_common(3),revenue=sum(s['amount'] for s in ss),dear_revenue=sum(s['amount'] for s in ss if cat.get(s['sku'],{}).get('list_price',0)>=600000),top=[dict(sku=k,paid=n,buyers=len({s['buyer'] for s in ss if s['sku']==k}),min_price=min(s['amount'] for s in ss if s['sku']==k),max_price=max(s['amount'] for s in ss if s['sku']==k),sources=[refs[s['seq']] for s in ss if s['sku']==k][:2]) for k,n in collections.Counter(s['sku'] for s in ss).most_common(4)],margin='원가 비공개: 실현 마진 추정 안 함')
for i,l in enumerate(open('meta/changes.jsonl'),1):
 r=json.loads(l)
 if dt.datetime.strptime(r['time'],'%Y-%m-%dT%H:%M:%S%z').timestamp()>=start:
  emit('cycle',ref=f'meta/changes.jsonl:{i}',cycle=r['cycle'],status=r['status'],summary=r.get('answer',{}).get('change_summary'),strategy=r.get('answer',{}).get('strategy_check'),problems=r.get('problems'))
for path in sorted(Path('tools').glob('*/manifest.json')):
 name=path.parent.name;calls=[r for r in profit_cohorts.read_rows('.market/tool_calls.jsonl') if r.get('tool')==name and start<=r.get('ts',0)<=end]
 emit('tool',name=name,calls=len(calls),failed=sum(not r.get('ok') for r in calls),selftest='selftest' in json.loads(path.read_text()))
forum=json.load(open('.market/forum.json'))
emit('forum',demand=[{k:x.get(k) for k in ('id','author','text','score')} for x in forum if '구합니다' in x.get('text','')],top=sorted(forum,key=lambda x:-(x.get('score') or 0))[:3])
for name in ['3h','12h']:
 g=p['board']['race']['growth'][name]; h=g['hours'];emit('projection',window=name,hours=h,me_12h=g['me']/h*12,leader_12h=g['leader']/h*12,above_12h=g['above']/h*12,gap_leader_12h=.132+(g['leader']-g['me'])/h*12,gap_above_12h=.006+(g['above']-g['me'])/h*12)
# Snapshots identify actual review boundaries independently of restored file mtimes.
for path in sorted(Path('meta/evidence').glob('astra-*.json')):
 try:
  a=json.loads(path.read_text())
  if a.get('time'):emit('pack_time',path=str(path),time=a['time'],mtime=path.stat().st_mtime)
 except (ValueError,AttributeError):pass
