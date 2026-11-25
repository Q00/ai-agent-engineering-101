"""Paired public GETs to identify a stale board after tunnel rotation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import time
import httpx
from r3.state import ROOT


def get(url):
    r=httpx.get(url,timeout=10,trust_env=False,headers={'Cache-Control':'no-cache'})
    data=r.json() if r.status_code==200 else {}
    rows={role:next((x for x in data.get(role,[]) if x.get('name')=='seochanit'),{}) for role in ('shops','buyers')}
    return {'url':url,'status':r.status_code,
        'headers':{k:r.headers.get(k) for k in ('date','age','cf-cache-status','cache-control')},
        'own':{role:{k:row.get(k) for k in ('alive','deals','all','book','saved','card','url')}
               for role,row in rows.items()}}


if __name__=='__main__':
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    base='https://market.ourolabs.ai'
    urls=[base+'/api/board',base+'/api/board?v22_probe='+str(time.time_ns()),base+'/api/shops']
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(get,urls))
    path=ROOT/'logs'/('v22-board-freshness-'+stamp+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(rows,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps(rows,ensure_ascii=True))
