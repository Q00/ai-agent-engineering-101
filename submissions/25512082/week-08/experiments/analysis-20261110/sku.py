import json,collections,statistics as st,re
Z=json.load(open('settles.json')); cat=json.load(open('cat.json')); sc=Z['scale']
D=json.load(open('ledger_dump.json')); now=D['now']
def share(d):
    lp=cat.get(d['sku'],{}).get('list_price'); return d['amount']/lp if lp else None
# overall grade price share
for kind in ('food','goods'):
    for g in 'ABC':
        xs=[share(d) for d in Z['hs']+Z['ss'] if d.get('grade')==g and cat.get(d['sku'],{}).get('kind')==kind and share(d)]
        if xs:
            xs.sort(); n=len(xs)
            print(kind,g,n,'p10/25/50/75',[round(xs[int(n*p)],3) for p in (.1,.25,.5,.75)])
# unmet demand from results in last hour
H=[r for r in D['hour'] if r['ts']>=now-3600]
nostock=collections.Counter()
for r in H:
    if r['kind']=='result' and not r['data'].get('verified'):
        m=re.search(r'(G[0-9a-f]{8})',r['data'].get('review') or '')
        if m and re.search(r'no stock|out of stock|not in stock',r['data'].get('review',''),re.I): nostock[m.group(1)]+=1
sup=collections.Counter(); 
for r in H:
    if r['kind']=='supply': sup[r['data']['sku']]+=r['data']['qty']
agg=collections.defaultdict(lambda:{'h':0,'s':0,'buy':set(),'shops':set(),'px':collections.defaultdict(list)})
for d in Z['hs']:
    a=agg[d['sku']]; a['h']+=1; a['buy'].add(d['buyer']); a['shops'].add(d['shop']); a['px'][d.get('grade')].append(share(d))
for d in Z['ss']:
    a=agg[d['sku']]; a['s']+=1; a['buy'].add(d['buyer']); a['px'][d.get('grade')].append(share(d))
rows=[]
for sku,a in agg.items():
    c=cat.get(sku,{})
    rows.append(dict(sku=sku,name=c.get('name','')[:34],kind=c.get('kind'),lp=c.get('list_price'),cat=c.get('category','').split('>')[-1],h1=a['h'],h24=round(a['h']+a['s']*sc),buyers=len(a['buy']),shops1h=len(a['shops']),
      pA=round(st.median(a['px']['A']),2) if a['px']['A'] else None,pB=round(st.median(a['px']['B']),2) if a['px']['B'] else None,pC=round(st.median(a['px']['C']),2) if a['px']['C'] else None,
      sup1h=sup.get(sku,0),nostock1h=nostock.get(sku,0),shelf=c.get('shelf_hours')))
rows.sort(key=lambda r:-r['h24'])
json.dump(rows,open('sku_rows.json','w'),ensure_ascii=False)
print('distinct skus settled',len(rows),'nostock skus',len(nostock), 'top nostock',nostock.most_common(10))
for r in rows[:40]: print(r)
