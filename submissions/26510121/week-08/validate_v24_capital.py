"""Read-only common-snapshot capital/contract ablation; no paid or trading calls."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from validate_v24_reputation import ROOT,STAGE,View
from r3.state import read_json
from r3.economy import restock_candidates,cash_capacity
from r3.reputation import update


def main():
    now=time.time();result={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for tag,path in [('current',ROOT/'policy.json'),('candidate',STAGE/'policy.json')]:
            j=View(db,now);cfg=read_json(path);inv=j.snapshot('inventory.json');catalog=j.snapshot('catalog.json')
            control=j.get('market_control',{});j.put('market_control',dict(control,pause_restock=False))
            j.put('merchant_context_v5',{})
            cfg.update(j.get('feedback-last-policy',{}).get('patches',{}).get('shop',{}))
            if cfg.get('shop_reputation_v24_enabled'):update(j,cfg,now)
            virtual=dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
            started=time.perf_counter();rows=restock_candidates(virtual,catalog,[],j,cfg,now)
            elapsed=time.perf_counter()-started;cap=cash_capacity(virtual,j,cfg,now)
            assert len(rows)<=10 and all(0<r['qty']<=20 for r in rows)
            assert sum(r['capital_commitment'] for r in rows)<=cap['spendable']
            assert all(d['book']>0 for r in rows for d in r['evidence']['marginal_units'])
            result[tag]={'lines':len(rows),'units':sum(r['qty'] for r in rows),'planning_seconds':elapsed,
                'spendable':cap['spendable'],'cash_floor':cap['cash_floor'],
                'capital_commitment':sum(r['capital_commitment'] for r in rows),
                'expected_book_scenario':sum(r['expected_delta']['book'] for r in rows),
                'expected_revenue_scenario':sum(r['expected_delta']['revenue'] for r in rows),
                'plan':[{'sku':r['sku'],'qty':r['qty'],'book':r['expected_delta']['book'],
                         'refund_stress':r['evidence']['refund_stress']} for r in rows]}
    result.update(at=datetime.fromtimestamp(now,timezone.utc).isoformat(),guards_verified=True,
        market_transactions=0,model_calls=0,shared_state_writes=0,economic_superiority_proven=False,
        source_hashes={p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in list((STAGE/'r3').glob('*.py'))+[STAGE/'seller_r3.py',STAGE/'policy.json']},
        limitation='Same observations, different candidate and risk assumptions; forecast is not measured lift.')
    path=ROOT/'logs'/('v24-capital-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:{a:b for a,b in result[k].items() if a!='plan'} for k in ('current','candidate')}))


if __name__=='__main__':main()
