import json, pathlib, subprocess, datetime, collections, hashlib, re
ROOT=pathlib.Path.cwd(); PREFIX=ROOT/'meta/evidence/astra-1110-021612'
def write(suffix, rows):
 p=pathlib.Path(str(PREFIX)+suffix)
 with p.open('w') as f:
  for r in rows: f.write(json.dumps(r,ensure_ascii=False)+'\n')
 return str(p.relative_to(ROOT))
def readrows(p):
 with open(p,errors='replace') as f:
  for i,s in enumerate(f,1):
   try: yield i,json.loads(s)
   except ValueError:pass
pack=json.load(open(str(PREFIX)+'.json')); now=datetime.datetime.fromisoformat(pack['time']).timestamp(); start=datetime.datetime.fromisoformat('2026-11-09T13:30:41+09:00').timestamp()
raw=subprocess.check_output(['git','log','--since=2026-11-09T13:30:41+09:00','--reverse','--format=%H%x1f%ct%x1f%B%x1e','--','.'],text=True)
commits=[]
for entry in raw.split('\x1e'):
 if not entry.strip():continue
 sha,ts,body=entry.strip().split('\x1f',2)
 files=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',sha,'--','.'],text=True).splitlines()
 commits.append(dict(commit=sha,ts=int(ts),body=body.strip(),files=files))
write('-commits.jsonl',commits)
# Use explicit live memories, never import shop_memory with the harness environment.
mine=json.load(open('.market/loss_state.json')).get('mine',{})
mem=json.load(open('memory/seller_memory_r3.json')); costs={n['unit']:n.get('cost') for n in mem.get('negotiations',[]) if n.get('result')=='deal' and n.get('unit')}
rows=[]; seen=set(); aliases={}
for i,r in readrows('.market/ledger_rows.jsonl'):
 if r.get('seller') and r.get('shop'):aliases[r['shop']]=r['seller']
 if r.get('seq') in seen or (r.get('ts') or 0)<start-21600 or (r.get('ts') or 0)>now:continue
 seen.add(r.get('seq'))
 if r.get('k') in ('settle','broken'): rows.append((i,r))
settles=[(i,r) for i,r in rows if r['k']=='settle']; broken={r.get('order'):r for i,r in rows if r['k']=='broken' and r.get('order')}
own=[(i,r) for i,r in settles if str(r['seq']) in mine]
def count(lo,hi):
 rr=[(i,r) for i,r in own if lo<=r['ts']<min(hi,now)]; mat=[(i,r) for i,r in rr if r['ts']<=now-21600]; net=0; missing=0; refunds=0
 for i,r in mat:
  cost=costs.get(mine[str(r['seq'])].get('unit')); w=r.get('warranty'); b=broken.get(r.get('order')); ref=(r.get('amount') or 0) if b and isinstance(w,(int,float)) and 0<=b['ts']-r['ts']<=w*3600 else 0
  refunds+=ref
  if cost is None or (b and not isinstance(w,(int,float))):missing+=1
  else:net+=(r.get('amount') or 0)-cost-ref
 buyers=[r for i,r in settles if lo<=r['ts']<min(hi,now) and str(r.get('buyer','')).lower()=='nocha12']
 return dict(shop_paid=len(rr),shop_revenue=sum(r.get('amount') or 0 for i,r in rr),mature_paid=len(mat),missing_cost_or_warranty=missing,mature_known_net=net,mature_net=None if missing else (net if mat else None),linked_mature_refunds=refunds,buyer_paid=len(buyers),observed_hours=round(max(0,min(hi,now)-lo)/3600,4),sample_refs=['.market/ledger_rows.jsonl:'+str(i) for i,r in rr[:2]])
audit=[]
for c in commits:
 audit.append(dict(commit=c['commit'][:8],before=count(c['ts']-1800,c['ts']),after=count(c['ts'],c['ts']+1800),note='동일 30분 창. 팩 이후는 잘림; 동시 변경·수요·미숙성으로 개별 인과효과 아님. 순기여는 판매원가와 연결환불 차감, 보관비 제외.'))
write('-commit-metrics.jsonl',audit)
write('-period.jsonl',[dict(start=start,end=now,**count(start,now)),dict(board=pack['board'],rules=pack['rules'],alerts=pack['alerts'])])
rivals=[]
for name in ['nocha12','yms979','inextro','25620027','moss-and-oak','seochanit']:
 rr=[r for i,r in settles if r['ts']>=now-43200 and (str(r['seq']) in mine if name=='nocha12' else name.lower() in str(r.get('seller') or aliases.get(r.get('shop'),'')).lower())]
 grouped=collections.defaultdict(list)
 for r in rr:grouped[r.get('sku')].append(r)
 rivals.append(dict(shop=name,paid=len(rr),revenue=sum(r.get('amount') or 0 for r in rr),buyers=len(set(r.get('buyer') for r in rr)),top=[dict(sku=k,paid=len(v),revenue=sum(x.get('amount') or 0 for x in v),buyers=sorted(set(str(x.get('buyer')) for x in v)),price_min=min(x.get('amount') or 0 for x in v),price_max=max(x.get('amount') or 0 for x in v)) for k,v in sorted(grouped.items(),key=lambda kv:-sum(x.get('amount') or 0 for x in kv[1]))[:8]],margin='원가 비공개; 경쟁자 매출을 순익으로 사용하지 않음'))
write('-rivals.jsonl',rivals)
usage=collections.defaultdict(lambda:dict(calls=0,failures=0))
for i,r in readrows('.market/tool_calls.jsonl'):
 if start<=r.get('ts',0)<=now:
  u=usage[r.get('tool','')];u['calls']+=1;u['failures']+=not r.get('ok',False)
write('-tools.jsonl',[dict(tool=p.name,**usage[p.name],lifetime=pack['tools'].get(p.name,{}),available=p.name in pack['shop']['tools_status']['available']) for p in sorted(pathlib.Path('tools').iterdir()) if p.is_dir() and (p/'tool.py').exists()])
dec=[]
for i,r in readrows('.market/decisions.jsonl'):
 if start<=r.get('ts',0)<=now and r.get('agent')=='restock':dec.append(dict(ref=f'.market/decisions.jsonl:{i}',**r))
write('-restock-decisions.jsonl',dec)
posts=json.load(open('.market/forum.json'));posts=posts if isinstance(posts,list) else posts.get('posts',[])
write('-forum.jsonl',[p for p in posts if any(x in p.get('text','') for x in ['구합니다','원합니다','찾습니다','need','want'])])
logs=[]
for name in pack['files']:
 if not name.startswith('logs/'):continue
 p=pathlib.Path(name)
 if not p.is_file(): logs.append(dict(path=name,missing=True));continue
 n=0;hits=collections.Counter();last=[];sha=hashlib.sha256()
 with p.open('rb') as f:
  for n,line in enumerate(f,1):
   sha.update(line)
   s=line.decode('utf-8','replace')
   for term in ['Traceback','no stock','settled','not paying','supply refused','FAILED','stock cost','units per SKU']:
    if term in s:hits[term]+=1
   if 'stock cost' in s or 'units per SKU' in s:last=(last+[(n,s.strip()[:600])])[-4:]
 logs.append(dict(path=name,lines=n,sha256=sha.hexdigest(),hits=dict(hits),last_guard_lines=last))
write('-log-audit.jsonl',logs)
# Preserve full relevant records; originals are untouched.
write('-cycles.jsonl',[dict(source=f'meta/changes.jsonl:{i}',**r) for i,r in readrows('meta/changes.jsonl') if r.get('time','')>='2026-11-09T13:30'])
print(json.dumps(dict(commits=len(commits),period=count(start,now),rivals=[{k:r[k] for k in ('shop','paid','revenue','buyers')} for r in rivals],restock_decisions=len(dec),logs=len(logs)),ensure_ascii=False))
