"""Cache-aware read-only official capture with a ledger/server-time receipt."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import time
import httpx
from r3.state import ROOT

def main():
    def get(path):
        start=time.time()
        response=httpx.get('https://market.ourolabs.ai'+path,timeout=20,trust_env=False,
            headers={'Cache-Control':'no-cache'})
        response.raise_for_status()
        return path,{'started_at':start,'finished_at':time.time(),'status':response.status_code,
            'headers':{k:response.headers.get(k) for k in ('date','age','cache-control','cf-cache-status')},
            'data':response.text if path.endswith('.md') else response.json()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=dict(pool.map(get,['/api/board','/api/shops','/agent.md','/api/ledger?after=-1']))
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'responses':rows}
    path=ROOT/'logs'/('v28-progress-source-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    board=rows['/api/board']['data'];tail=rows['/api/ledger?after=-1']['data']
    print('SOURCE',path)
    print(json.dumps({'board_headers':rows['/api/board']['headers'],
        'ledger_tail_seq':tail[-1].get('seq') if tail else None,'ledger_tail_at':tail[-1].get('ts') if tail else None,
        'board_fields':list(board),'own':{role:next(({k:r.get(k) for k in ('name','book','all','deals','revenue','saved','forum','stars_rank','posts')}
            for r in board.get(role,[]) if r.get('name')=='seochanit'),{}) for role in ('shops','buyers')}},ensure_ascii=True))

if __name__=='__main__':main()
