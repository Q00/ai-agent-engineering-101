import json,collections,statistics as st,math
D=json.load(open('ledger_dump.json')); now=D['now']
cat=json.load(open('cat.json'))
def uniq(rows):
    seen={}; 
    for r in rows: seen[r['seq']]=r
    return list(seen.values())
H=[r for r in uniq(D['hour']) if r['ts']>=now-3600]
S=uniq(D['samples'])
# coverage of samples within (now-25h, now-1h)
wins=collections.defaultdict(list)
for r in D['samples']: pass
# compute windows: group consecutive seqs
S=sorted(S,key=lambda r:r['seq']); groups=[]; 
for r in S:
    if groups and r['seq']-groups[-1][-1]['seq']<=2: groups[-1].append(r)
    else: groups.append([r])
cov=sum(g[-1]['ts']-g[0]['ts'] for g in groups if g[0]['ts']>now-86400)
S24=[r for r in S if now-86400<r['ts']<now-3600]
scale=(86400-3600)/cov
print('hour rows',len(H),'sample groups',len(groups),'coverage s',round(cov),'scale',round(scale,1))
kinds=collections.Counter(r['kind'] for r in H); print('hour kinds',kinds)
hs=[r['data'] for r in H if r['kind']=='settle']; ss=[r['data'] for r in S24 if r['kind']=='settle']
print('settles/h',len(hs),'est 24h',round(len(hs)+len(ss)*scale))
json.dump({'hs':hs,'ss':ss,'scale':scale},open('settles.json','w'),ensure_ascii=False)
# expire/storage/broken kinds
for k in ('expire','broken','storage','result'):
    x=[r for r in H if r['kind']==k][:1]; print(k,json.dumps(x,ensure_ascii=False)[:400])
