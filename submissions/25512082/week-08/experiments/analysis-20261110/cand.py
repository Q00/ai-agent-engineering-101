import json,math
rows=json.load(open('sku_rows.json')); cat=json.load(open('cat.json'))
surge={s['sku']:s['factor'] for s in json.load(open('api_supply-prices'))['surge']}
GP={'goods':{'A':.70,'B':.20,'C':.10},'food':{'A':.25,'B':.50,'C':.25}}
COST={'A':(.55,.75),'B':(.40,.60),'C':(.25,.45)}
MKT={'goods':{'A':.705,'B':.626,'C':.549},'food':{'A':.712,'B':.604,'C':.54}}
out=[]
for r in rows:
    if r['buyers']<20 or r['h24']<300: continue
    k=r['kind']; lp=r['lp']; f=surge.get(r['sku'],1.0)
    if k=='food' and (r['shelf'] or 0)<720: continue
    if lp>130000: continue
    ev=0; asks={}
    for g,p in GP[k].items():
        lo,hi=COST[g]; c=(lo+hi)/2*f
        med=r.get('p'+g) or MKT[k][g]
        ask=max(med, c*1.05)
        asks[g]=(round(ask*lp), math.ceil(c*1.05*lp))
        ev+=p*((ask-c)*lp)
    worst_cost=math.ceil(.75*f*lp)
    exp_cost=round(sum(p*sum(COST[g])/2 for g,p in GP[k].items())*f*lp)
    storage7=round(exp_cost*0.07) if k=='goods' else 0
    worst_loss = storage7 if k=='goods' else worst_cost   # goods: unsold -> storage only; food: expiry -> full cost
    out.append(dict(sku=r['sku'],name=r['name'][:26],kind=k,shelf=r['shelf'],lp=lp,h1=r['h1'],h24=r['h24'],buyers=r['buyers'],shops1h=r['shops1h'],surge=f,
        pA=r['pA'],pB=r['pB'],pC=r['pC'],exp_cost=exp_cost,worst_cost=worst_cost,ev=round(ev),ev_pct=round(ev/lp,3),worst_loss=worst_loss,asks=asks))
out.sort(key=lambda x:-(x['h24']/max(1,x['shops1h'])))
json.dump(out,open('cands.json','w'),ensure_ascii=False)
for x in out[:25]: print(json.dumps(x,ensure_ascii=False))
