"""Read-only official rule/mechanism capture and frozen procurement replay.

Public GETs only; all replay journal writes stay in memory. No orders or models.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import sqlite3
import sys
import time
import httpx
from autonomous_status import ReadJournal
from r3.state import ROOT,policy,read_json
from r3.economy import restock_candidates,cash_capacity


def capture():
    def get(path):
        try:
            response=httpx.get('https://market.ourolabs.ai'+path,timeout=15,trust_env=False)
            return path,{'status':response.status_code,'data':response.text if path.endswith('.md') else response.json()}
        except Exception as exc:return path,{'error':type(exc).__name__}
    with ThreadPoolExecutor(max_workers=3) as pool:
        public=dict(pool.map(get,['/agent.md','/api/board','/api/shops','/api/supply-prices']))
    now=time.time()
    files={name:read_json(ROOT/'.market'/name,{}) for name in
           ['inventory.json','catalog.json','board.json','market_stock_counts.json','shop-health.json','demand.json']}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('BEGIN')
        class Frozen(ReadJournal):
            def snapshot(self,name,default=None):return files.get(name,default)
        journal=Frozen(conn,now);cfg=policy()
        inventory=files['inventory.json']
        hypothetical=dict(inventory,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
        started=time.monotonic()
        candidates=restock_candidates(hypothetical,files['catalog.json'],[],journal,cfg,now)
        latency=defaultdict(list)
        epoch=journal.get('objective_epoch',now)
        for row in journal.events('model_usage',10000):
            if row['_ts']>=epoch:latency[row.get('role')].append(row.get('seconds',0))
        elapsed=time.monotonic()-started
        return {'checked_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'public':public,
            'local_files':files,'policy':cfg,
            'baseline':{'NOT_EXECUTED':True,'revision':cfg['version'],
                'cash_capacity':cash_capacity(inventory,journal,cfg,now),
                'lines':candidates,'seconds':elapsed,
                'model_latency':{role:{'n':len(xs),'average':sum(xs)/len(xs),'maximum':max(xs)}
                                 for role,xs in latency.items()}},
            'journal_cutoff':now,'journal_revision':journal.get('objective_revision'),
            'limitations':['Replay is a planning scenario, not a trade or a measured profit.',
                'Endpoints and inventory snapshots are read within seconds, not atomically.']}


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');result=capture()
    path=ROOT/'logs'/('shop-edge-source-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    print(json.dumps({'public_status':{k:v.get('status',v.get('error')) for k,v in result['public'].items()},
        'cash_capacity':result['baseline']['cash_capacity'],'model_latency':result['baseline']['model_latency'],
        'baseline_lines':[{'sku':r['sku'],'qty':r['qty'],'book':r['expected_delta']['book'],
            'capital':r['capital_commitment'],'hold_hours':r['evidence']['expected_holding_hours']}
            for r in result['baseline']['lines']]},ensure_ascii=False))
