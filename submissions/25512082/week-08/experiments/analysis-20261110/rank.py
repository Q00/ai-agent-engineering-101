import json,sys
b=json.load(open('api_board'))
def pct(v,xs):  # fraction strictly below + half ties
    n=len(xs); lo=sum(x<v for x in xs); eq=sum(x==v for x in xs)
    return (lo+0.5*(eq-1))/(n-1) if n>1 else 0
def shop_all(s,S,mode):
    f=lambda k:[(x.get(k) or 0) for x in S]
    st='stars_rank' if mode else 'stars'
    return 0.5*pct(s['book'],f('book'))+0.3*pct(s['revenue'],f('revenue'))+0.2*pct(s.get(st) or 0,f(st))
S=[s for s in b['shops'] if s['deals']>0 or s.get('stock_units',0)>0]
err=[0,0]
for s in S:
    for m in (0,1): err[m]+=abs(shop_all(s,S,m)-s['all'])
print('shop formula err stars, stars_rank',[round(e/len(S),4) for e in err])
B=[x for x in b['buyers'] if x['deals']>0]
def buyer_all(x,B):
    f=lambda k:[(y.get(k) or 0) for y in B]
    return 0.5*pct(x['saved'],f('saved'))+0.3*(x.get('forum') or 0)+0.1*pct(x['deals'],f('deals'))+0.1*pct(x['posts'],f('posts'))
print('buyer err',round(sum(abs(buyer_all(x,B)-x['all']) for x in B)/len(B),4))
me=[s for s in S if s['name']=='sohyunkim11'][0]; mb=[x for x in B if x['name']=='sohyunkim11'][0]
print('me shop',me['all'],round(shop_all(me,S,0),3),round(shop_all(me,S,1),3),'buyer',mb['all'],round(buyer_all(mb,B),3))
print('stars_rank dist',sorted(round(s.get('stars_rank') or 0,2) for s in S))
