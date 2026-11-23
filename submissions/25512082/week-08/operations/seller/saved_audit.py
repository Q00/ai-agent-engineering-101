import json,time,urllib.request
W='/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08/'
cat=json.load(open(W+'.market/catalog.json'))
def g(a):
    for t in range(4):
        try:
            r=json.load(urllib.request.urlopen(f'https://market.ourolabs.ai/api/ledger?after={a}',timeout=30)); return r if isinstance(r,list) else r.get('entries')
        except Exception: time.sleep(2)
    return []
P=[json.loads(l) for l in open(W+'.market/payments.jsonl') if l.strip()]
out=[]
for p in P:
    if not p.get('ledger'): continue
    seq=p['ledger']; e=g(seq-1); x=[y for y in e if y['seq']==seq]
    if not x: out.append({'ledger':seq,'error':'settle not found'}); continue
    d=x[0]['data']; unit=d.get('unit'); sku=d.get('sku') or d.get('item'); lp=cat.get(sku,{}).get('list_price')
    cost=None; sup=None; cur=seq
    for k in range(400):
        cur-=500; e=g(cur)
        for y in e:
            if y['kind']=='supply' and unit in (y['data'].get('units') or []): cost=y['data']['total']/y['data']['qty']; sup=y['seq']
        if cost is not None or not e: break
    # broken/refund for this order?
    out.append({'ledger':seq,'sku':sku,'list':lp,'paid':d.get('amount'),'grade':d.get('grade'),'expires_at':d.get('expires_at'),'unit':unit,'cost':cost,'supply_seq':sup})
    print(json.dumps(out[-1],ensure_ascii=False),flush=True)
json.dump(out,open('data/saved_audit.json','w'),ensure_ascii=False,indent=1)
print('done')
