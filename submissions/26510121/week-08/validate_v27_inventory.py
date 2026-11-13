"""Isolated inventory-policy staging and frozen read-only market comparisons."""
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v27-stage-20261113'


def stage():
    assert not STAGE.exists(),'Never overwrite an earlier attempt'
    STAGE.mkdir();(STAGE/'logs').mkdir();(STAGE/'.market').mkdir()
    paths=list(ROOT.glob('*.py'))+[ROOT/'policy.json']
    for folder in ('r3','tests'):paths.extend((ROOT/folder).glob('*.py'))
    for source in paths:
        target=STAGE/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    cfg.update(inventory_portfolio_v27_enabled=True,execution_revision='v27-robust-sku-lot-portfolio-1',
        shop_strategy_revision='shop-v27-opportunity-cost-inventory')
    (STAGE/'policy.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('STAGED_SOURCE_FILES',len(paths),'NO_CREDENTIALS_OR_STATE_COPIED')


def hashes():
    return {p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in list((STAGE/'r3').glob('*.py'))+[STAGE/n for n in ('policy.json','restock.py','verify_market_cycle_live.py')]}


class View:
    def __init__(self,db,now):
        self.db,self.now,self.memory,self.records=db,now,{},[];self.md=STAGE/'.market/replay'
        self.files={p.name:json.loads(p.read_text(encoding='utf-8-sig')) for p in (ROOT/'.market').glob('*.json')
            if p.name in ('inventory.json','catalog.json','board.json','market_stock_counts.json','shop-health.json','forum.json','demand.json')}
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


def replay():
    sys.path.insert(0,str(STAGE))
    from r3.state import read_json
    from r3.economy import restock_candidates
    old=read_json(ROOT/'policy.json');new=read_json(STAGE/'policy.json');now=time.time()
    assert old['background_llm_roles']==new['background_llm_roles'] and old['buyer_strategy_revision']==new['buyer_strategy_revision']
    results={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for name,cfg in (('v26',old),('v27',new)):
            j=View(db,now);j.md=STAGE/'.market'/name
            inv=j.snapshot('inventory.json');catalog=j.snapshot('catalog.json');inv=dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
            j.put('market_control',dict(j.get('market_control',{}),pause_restock=False));j.put('merchant_context_v5',{})
            patches=j.get('feedback-last-policy',{}).get('patches',{}).get('shop',{});cfg=dict(cfg,**patches)
            started=time.perf_counter();rows=restock_candidates(inv,catalog,[],j,cfg,now);elapsed=time.perf_counter()-started
            result={'seconds':elapsed,'lines':len(rows),'units':sum(c['qty'] for c in rows),
                'capital':sum(c['capital_commitment'] for c in rows),'delta':{k:sum(c['expected_delta'].get(k,0) for c in rows) for k in ('book','revenue')},
                'goods_units':sum(c['qty'] for c in rows if c['evidence']['kind']=='goods'),
                'allocation':j.get('inventory-portfolio:v27',{}),
                'rows':[{k:r.get(k) for k in ('sku','qty','capital_commitment','expected_delta','allocation_book')} for r in rows]}
            assert len(rows)<=10 and all(1<=r['qty']<=20 and r['expected_delta']['book']>0 for r in rows)
            results[name]=result
    selection=results['v27']['allocation'];assert selection['dominance_guard_passed']
    baseline=selection['alternatives']['baseline']
    assert baseline['units']==results['v26']['units']
    for key in ('book','revenue'):assert abs(baseline[key]-results['v26']['delta'][key])<1e-5,'Baseline changed'
    assert abs(baseline['capital']-results['v26']['capital'])<1e-5
    report={'at':now,'source_hashes':hashes(),'comparisons':results,'models_unchanged':True,'buyer_unchanged':True,
        'model_calls':0,'market_transactions':0,'live_policy_writes':0,
        'limits':['Shared frozen snapshot and engineering stress scenarios, not causal uplift or superiority over competitors.',
            'Unavailable future orders and acquisition draws cannot be replayed as observed profits.']}
    p=ROOT/'logs'/('v27-inventory-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps({name:{k:v for k,v in r.items() if k not in ('rows','allocation')} for name,r in results.items()}))
    print('SELECTION',json.dumps(selection))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',action='store_true');args=parser.parse_args()
    stage() if args.stage else replay()
