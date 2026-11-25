"""Public board versus real shop discovery probes; no registrations or trades."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import time
import httpx
from r3.state import ROOT,read_json


def get(url):
    started=time.monotonic()
    try:
        response=httpx.get(url,timeout=8,trust_env=False)
        return {'url':url,'status':response.status_code,'seconds':time.monotonic()-started,
                'data':response.json() if response.status_code==200 else {},
                'server':response.headers.get('server')}
    except Exception as exc:return {'url':url,'error':type(exc).__name__,'seconds':time.monotonic()-started}


def diagnose():
    result=get('https://market.ourolabs.ai/api/board')
    board=result.get('data',{})
    registry=get('https://market.ourolabs.ai/api/shops')
    selected=[]
    rows=sorted(board.get('shops',[]),key=lambda r:r.get('all',-1),reverse=True)
    for row in [r for r in rows if r.get('name')=='seochanit']+rows:
        url=row.get('card',{}).get('url','').rstrip('/')
        if not url or any(s['url']==url for s in selected):continue
        selected.append({'name':row['name'],'url':url,'reported_alive':row.get('alive')})
        if len(selected)>=6:break
    with ThreadPoolExecutor(max_workers=6) as pool:
        probes=list(pool.map(lambda row:dict(row,probe=get(row['url']+'/.well-known/agent-card.json')),selected))
    for row in probes:
        data=row['probe'].get('data',{})
        row['probe']['data']={k:data.get(k) for k in ['name','version']}
    body=registry.pop('data',{})
    registry['shop_count']=len(body.get('shops',[])) if isinstance(body,dict) else None
    registry['own']=[{k:s.get(k) for k in ['name','alive','url']} for s in body.get('shops',[]) if s.get('name')=='seochanit']
    result.pop('data',None)
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'market':result,'registry':registry,
        'board_status':{key:dict(Counter(str(r.get('alive','missing')) for r in board.get(key,[]))) for key in ['shops','buyers']},
        'probes':probes,'local_health':read_json(ROOT/'.market/shop-health.json',{}),
        'limitations':['External probes originate from this PC, not the market server.',
            'Board alive values may be cached separately from discovery registry results.']}


if __name__=='__main__':
    result=diagnose();path=ROOT/'logs'/('market-connectivity-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path);print(json.dumps(result,ensure_ascii=False))
