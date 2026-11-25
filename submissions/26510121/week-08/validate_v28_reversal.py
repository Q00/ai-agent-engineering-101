"""Isolated v28 staging and read-only live replay; never trades or calls models."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v28-stage-20261114'

class View:
    def __init__(self,db,now):
        self.db,self.now,self.memory,self.records=db,now,{},[];self.md=STAGE/'.market/replay'
        names=('inventory.json','catalog.json','board.json','market_stock_counts.json','shop-health.json','forum.json','demand.json','registry.json',
            'shop-plan-v15.json','market-research-v15.json','quality-model-v16.json')
        self.files={name:json.loads((ROOT/'.market'/name).read_text(encoding='utf-8-sig'))
            for name in names if (ROOT/'.market'/name).is_file()}
    @contextmanager
    def connect(self):yield self.db
    def get(self,k,default=None):
        if k in self.memory:return self.memory[k]
        row=self.db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,k,v):self.memory[k]=v
    def snapshot(self,k,default=None):return self.files.get(k,default)
    def add(self,k,v,**kwargs):self.records.append((k,v))
    def events(self,k,limit=500):
        return [dict(json.loads(raw),_event_id=eid,_ts=ts,_role=role) for eid,ts,role,raw in self.db.execute(
            'SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',(k,self.now,limit))]

def stage():
    assert not STAGE.exists(),'Preserve earlier attempts'
    STAGE.mkdir();(STAGE/'logs').mkdir();(STAGE/'.market').mkdir()
    paths=list(ROOT.glob('*.py'))+[ROOT/'policy.json']
    for folder in ('r3','tests'):paths.extend((ROOT/folder).glob('*.py'))
    for source in paths:
        target=STAGE/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    cfg.update(reversal_v28_enabled=True,market_end_at='2026-11-17T23:59:00+09:00',
        execution_revision='v28-mature-risk-and-contract-progress-1',
        shop_strategy_revision='shop-v28-mature-contract-frontier',
        buyer_strategy_revision='buyer-v28-all-progress-per-second')
    (STAGE/'policy.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('STAGED',len(paths),'source files; no identity, credentials or database copied')

def replay():
    sys.path.insert(0,str(STAGE))
    from r3.state import read_json
    from r3.economy import restock_candidates,buyer_candidates
    from r3.reversal import update
    now=time.time();old=read_json(ROOT/'policy.json');new=read_json(STAGE/'policy.json')
    hashes={p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in list((STAGE/'r3').glob('*.py'))+[STAGE/'market_buyer.py',STAGE/'policy.json']}
    results={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for name,cfg in [('baseline',old),('v28',new)]:
            j=View(db,now);j.md=STAGE/'.market'/('replay-'+name)
            inv=j.snapshot('inventory.json');catalog=j.snapshot('catalog.json')
            inv=dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
            j.put('market_control',dict(j.get('market_control',{}),pause_restock=False));j.put('merchant_context_v5',{})
            patches=j.get('feedback-last-policy',{}).get('patches',{}).get('shop',{})
            cfg=dict(cfg,**patches);started=time.perf_counter()
            rows=restock_candidates(inv,catalog,[],j,cfg,now)
            results[name]={'seconds':time.perf_counter()-started,'lines':len(rows),'units':sum(r['qty'] for r in rows),
                'goods_units':sum(r['qty'] for r in rows if r['evidence']['kind']=='goods'),
                'expected':{k:sum(r['expected_delta'].get(k,0) for r in rows) for k in ('book','revenue')},
                'allocation':j.get('inventory-portfolio:v27',{}),
                'rows':[{k:r.get(k) for k in ('sku','qty','expected_delta','capital_commitment')} for r in rows]}
            assert len(rows)<=10 and all(1<=r['qty']<=20 for r in rows)
            if name=='v28':
                results[name]['observer']=update(j,cfg,now)
                from r3.risk import shop_refund_risk
                results[name]['seller_risk']={g:shop_refund_risk(j,g,6,cfg,now) for g in 'ABC'}
        attempts={k:View(db,now).get(k,{}) for k in ('research-last-attempt','cycle-heartbeat','analytical-role-status')}
        recent_errors=[{'kind':k,'records':View(db,now).events(k,2)} for k in ('market_research_deferred','research_error','cycle_supervisor_error')]
    assert old['background_llm_roles']==new['background_llm_roles']
    assert not any('astra' in r['model'] for r in new['background_llm_roles'].values())
    report={'at':now,'source_hashes':hashes,'comparisons':results,'research_diagnostics':attempts,
        'recent_errors':recent_errors,'model_calls':0,'market_transactions':0,'live_policy_writes':0,
        'models_unchanged':True,'economic_superiority_proven':False,
        'limits':['Frozen shared market facts; mean-risk and upper-risk forecasts are different estimands.',
            'Future fills, prices, acquisition draws and matured quality are not observed counterfactuals.']}
    p=ROOT/'logs'/('v28-reversal-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2))
    print('SOURCE',p)
    print(json.dumps({k:{m:v for m,v in r.items() if m not in ('rows','allocation','observer','seller_risk')} for k,r in results.items()}))

def context():
    sys.path.insert(0,str(STAGE))
    from r3 import research
    from r3.state import read_json
    from r3.objective_context import curate
    now=time.time();cfg=read_json(STAGE/'policy.json');captured=[]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=View(db,now);j.md=STAGE/'.market/context-proof';j.md.mkdir(exist_ok=True)
        j.put('research-last-attempt',{})
        from r3.reversal import update as terminal_update
        terminal_update(j,cfg,now)
        def capture(journal,config,facts,evidence,role,**kwargs):
            value,metrics=curate(facts);captured.append(dict(metrics,role=role))
            raise research.ModelError('validation_capture_no_api')
        research.call=capture
        research.update(j,cfg,now)
        diagnostics={'readiness':j.get('research-ready-status'),
            'market_at':j.get('cycle-market-analytics',{}).get('at'),
            'policy_status':j.get('feedback-last-policy',{}).get('status'),
            'feed_at':j.get('public_feed_caught_up_at')}
        if not captured:
            # Exercise assembly with the SAME recorded market facts but a
            # coherent analysis-generation clock, isolated in RAM. This is a
            # budget test, never proof of live freshness or payment authority.
            market=dict(j.get('cycle-market-analytics',{}),at=now,feed_at=now)
            j.put('cycle-market-analytics',market);j.put('public_feed_caught_up_at',now)
            operating=dict(j.get('feedback-last-policy',{}),at=now,status='active',
                rules_hash=j.get('market_rules',{}).get('sha256'))
            j.put('feedback-last-policy',operating)
            research.update(j,cfg,now)
        assert captured and captured[0]['fact_bytes']<=23000
    files=['r3/research.py','r3/objective_context.py','r3/reversal.py','policy.json']
    report={'at':now,'captured':captured,'live_readiness_diagnostics':diagnostics,
        'source_hashes':{n:hashlib.sha256((STAGE/n).read_bytes()).hexdigest() for n in files},
        'model_calls':0,'market_transactions':0,'live_policy_writes':0,
        'scope':'Context assembly/budget capture; coherent isolated generation if live analytics are stale.'}
    p=ROOT/'logs'/('v28-context-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps({'captured':captured,'live_readiness':diagnostics},ensure_ascii=True))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',action='store_true');p.add_argument('--context',action='store_true');a=p.parse_args()
    stage() if a.stage else context() if a.context else replay()
