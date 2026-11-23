import json,copy
from sim import evaluate,b
base=evaluate(b)
def mod(shop=None,buyer=None):
    bb=copy.deepcopy(b)
    for s in bb['shops']:
        if s['name']=='sohyunkim11' and shop: s.update(shop(s))
    for x in bb['buyers']:
        if x['name']=='sohyunkim11' and buyer: x.update(buyer(x))
    return evaluate(bb)
fmt=lambda r:f"shopALL {r[0]:.3f} buyerALL {r[1]:.3f} total {r[2]:.3f} student_pct {r[3]:.3f}"
print('BASE',fmt(base))
SR={20:4.15,30:4.2,50:4.28}
print('--- seller: +N deals, avg price 22,000, profit/deal p')
for tot in (20,30,50):
    n=tot-6
    for p in (500,1700,3000):
        r=mod(shop=lambda s:{'deals':tot,'revenue':s['revenue']+n*22000,'book':s['book']+n*p,'stars_rank':SR[tot]})
        print(f' deals {tot} (+{n}) profit/deal {p}: rev {111658+n*22000:,} book {8057+n*p:,} ->',fmt(r))
print('--- buyer: +N deals, contribution r per deal')
for n in (10,20,50,100):
    for rr in (0.20,0.27,0.30):
        def bf(x,n=n,rr=rr):
            s=x['saved']*(x['deals']+100)+n*rr; d=x['deals']+n
            return {'saved':s/(d+100),'deals':d}
        r=mod(buyer=bf)
        sv=(0.029*123+n*rr)/(123+n)
        print(f' +{n} r={rr}: saved {sv:.4f} ->',fmt(r))
print('--- combined: shop 20 deals profit 1700 + buyer +20 r .27')
def bf(x): s=x['saved']*(x['deals']+100)+20*.27; d=x['deals']+20; return {'saved':s/(d+100),'deals':d}
print(fmt(mod(shop=lambda s:{'deals':20,'revenue':s['revenue']+14*22000,'book':s['book']+14*1700,'stars_rank':4.15},buyer=bf)))
print('--- buyer posts +50 (forum unchanged)')
print(fmt(mod(buyer=lambda x:{'posts':51})))
print('=== thresholds')
from sim import sbuy,sshops
B=sorted([x for x in b['buyers'] if x['deals']>0 and x['name'] in sbuy],key=lambda x:x['saved'])
print('student buyers',len(B),'saved',[round(x['saved'],3) for x in B][:12],'deals',sorted(x['deals'] for x in B)[:12],'posts',sorted(x['posts'] for x in B)[:15])
S=sorted([s for s in b['shops'] if (s['deals']>0 or s.get('stock_units',0)>0) and s['name'] in sshops],key=lambda s:s['revenue'])
print('student shops',len(S),[(s['name'][:10],s['revenue'],s['book']) for s in S[:10]])
for deals,rev,book,sr in ((100,2_000_000,150_000,4.3),(200,4_000_000,300_000,4.3),(300,6_000_000,900_000,4.35)):
    print(deals,rev,book,fmt(mod(shop=lambda s:{'deals':deals,'revenue':rev,'book':book,'stars_rank':sr})))
for n,rr in ((200,.27),(400,.27)):
    def bf(x,n=n,rr=rr): s=x['saved']*(x['deals']+100)+n*rr; d=x['deals']+n; return {'saved':s/(d+100),'deals':d}
    print('buyer +',n,fmt(mod(buyer=bf)))
