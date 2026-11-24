import json,copy,sys
b=json.load(open('api_board'))
def pct(v,xs):
    n=len(xs); lo=sum(x<v for x in xs); eq=sum(x==v for x in xs)
    return (lo+0.5*(eq-1))/(n-1) if n>1 else 0
def shop_all(s,S):
    f=lambda k:[(x.get(k) or 0) for x in S]
    return 0.5*pct(s['book'],f('book'))+0.3*pct(s['revenue'],f('revenue'))+0.2*pct(s.get('stars_rank') or 0,f('stars_rank'))
def buyer_all(x,B):
    f=lambda k:[(y.get(k) or 0) for y in B]
    return 0.5*pct(x['saved'],f('saved'))+0.3*(x.get('forum') or 0)+0.1*pct(x['deals'],f('deals'))+0.1*pct(x['posts'],f('posts'))
P=[p for p in b['players'] if p.get('student')]
sshops={p['shop']['name'] for p in P if p.get('shop')}; sbuy={p['buyer']['name'] for p in P if p.get('buyer')}
def evaluate(board, me='sohyunkim11'):
    S=[s for s in board['shops'] if (s['deals']>0 or s.get('stock_units',0)>0) and s['name'] in sshops]
    B=[x for x in board['buyers'] if x['deals']>0 and x['name'] in sbuy]
    sa={s['name']:shop_all(s,S) for s in S}; ba={x['name']:buyer_all(x,B) for x in B}
    tot={p['player']:sa.get((p.get('shop') or {}).get('name'),0)+ba.get((p.get('buyer') or {}).get('name'),0) for p in P}
    v=tot[me]; others=[t for k,t in tot.items() if k!=me]
    rank_pct=sum(t<v for t in others)/len(others)
    return sa.get(me),ba.get(me),v,rank_pct
if __name__=='__main__':
    print('check vs board', evaluate(b))
    err=0
    S=[s for s in b['shops'] if (s['deals']>0 or s.get('stock_units',0)>0) and s['name'] in sshops]
    for p in P:
        if p.get('shop'):
            s=[x for x in S if x['name']==p['shop']['name']]
            if s: err=max(err,abs(shop_all(s[0],S)-p['student_shop']))
    print('max student_shop err',round(err,4))
