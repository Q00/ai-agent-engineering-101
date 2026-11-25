import json,datetime,re,collections,pathlib
pre='meta/evidence/astra-1110-021612';pack=json.load(open(pre+'.json'));now=datetime.datetime.fromisoformat(pack['time']);cutoff=now.timestamp()
ws={r['opens_at']:r for l in open('.market/measure/supply_windows.jsonl') if (r:=json.loads(l)).get('closes_at',cutoff+1)<=cutoff and r.get('ts',cutoff+1)<=cutoff};wins=sorted(ws)[-2:]
counts=[collections.Counter(),collections.Counter()];examples=[[],[]];cfg=json.load(open('pricing.json'))['restock'];listed=set(cfg['priority_skus'])|set(cfg['high_price_proven_skus'])
for name in ['logs/market-kit.log.1','logs/market-kit.log']:
 for ln,line in enumerate(open(name,errors='replace'),1):
  m=re.match(r'(\d\d:\d\d:\d\d).*\[restock\] guard: (G\w+) (\d+) -> (\d+) \(([^)]+)\)',line)
  if not m:continue
  ts=datetime.datetime.combine(now.date(),datetime.time.fromisoformat(m[1]),now.tzinfo).timestamp()
  if ts>cutoff:ts-=86400
  for j,op in enumerate(wins):
   if op-120<=ts<=ws[op]['closes_at']:
    reason=m[5];counts[j]['all_guard_lines']+=1
    if m[2] in listed:
     for term in reason.split(', '):counts[j][term]+=1
     if len(examples[j])<6: examples[j].append(dict(ref=f'{name}:{ln}',excerpt=line.strip()))
with open(pre+'-guards.jsonl','w') as f:
 for j,op in enumerate(wins):f.write(json.dumps(dict(window=[op,ws[op]['closes_at']],scope='open-120s through close; repeated simulations count as lines, not independent supply failures',counts=dict(counts[j]),examples=examples[j],stock_at_open={s:ws[op].get('stock_at_open',{}).get(s) for s in ['G75f11c19','G0667d440','G2c023a8c']}),ensure_ascii=False)+'\n')
print([dict(x) for x in counts])
