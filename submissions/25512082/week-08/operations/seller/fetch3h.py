import json,sys,time,urllib.request
def g(a):
    for t in range(4):
        try:
            r=json.load(urllib.request.urlopen(f'https://market.ourolabs.ai/api/ledger?after={a}',timeout=30)); return r if isinstance(r,list) else r.get('entries')
        except Exception: time.sleep(2)
    return []
since=time.time()-float(sys.argv[1])*3600; out=open(sys.argv[2],'w')
top=g(-1)[-1]['seq']; lo,hi=top-400000,top
while hi-lo>500:
    m=(lo+hi)//2; e=g(m)
    if e and e[0]['ts']<since: lo=m
    else: hi=m
cur=lo; n=0
while True:
    e=g(cur)
    if not e: break
    for x in e:
        if x['kind'] in('settle','supply','expire','broken','result'):
            if x['kind']=='result':
                d=x['data']; x={'seq':x['seq'],'ts':x['ts'],'kind':'result','data':{'shop':d.get('shop'),'buyer':d.get('buyer'),'stars':d.get('stars'),'verified':d.get('verified'),'settled':(d.get('checks') or {}).get('settled')}}
            out.write(json.dumps(x,ensure_ascii=False)+'\n'); n+=1
    cur=e[-1]['seq']
    if len(e)<500: break
print('rows',n,'last',cur)
