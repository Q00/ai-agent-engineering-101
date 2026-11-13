"""Price replay, deterministic action parity and isolated paid contracts; no market actions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v25-stage-20261113'
sys.path.insert(0,str(STAGE))
from r3.state import read_json,Journal
from r3.llm_roles import token_cost,call_json
from r3.model import api_key
from r3.economy import restock_candidates,shop_candidates
from r3 import research,capability


def save(kind,row):
    p=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2));print('SOURCE',p,flush=True)


def hashes():
    return {p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in list((STAGE/'r3').glob('*.py'))+[STAGE/'policy.json']}


class View:
    def __init__(self,db,now):
        self.db,self.now,self.memory,self.records=db,now,{},[];self.md=STAGE/'.market/parity'
        self.files={p.name:read_json(p) for p in (ROOT/'.market').glob('*.json')}
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
    now=time.time();old=read_json(ROOT/'policy.json');new=read_json(STAGE/'policy.json')
    runtime=read_json(ROOT/'.market/runtime.json');result={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for label,lo in [('since_v24',runtime['started_at']),('trailing_24h',now-86400)]:
            rows=[dict(json.loads(raw),at=ts) for ts,raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts',('model_usage',lo,now))]
            rows=[r for r in rows if not r.get('validation_only')]
            costs=[]
            for r in rows:
                model=r['model'];role=r.get('role');usage=r.get('usage') or {}
                mapped=new['background_llm_roles'].get(role,{}).get('model',model)
                costs.append({'role':role,'old_model':model,'new_model':mapped,'usage':usage,
                    'old_cost':token_cost(old,model,usage),'new_cost_same_tokens':token_cost(new,mapped,usage)})
            oldcost=sum(r['old_cost'] for r in costs);newcost=sum(r['new_cost_same_tokens'] for r in costs)
            assert oldcost>0 and newcost/oldcost<.5
            result[label]={'calls':len(rows),'hours':(now-lo)/3600,'baseline_estimate_usd':oldcost,
                'candidate_same_tokens_estimate_usd':newcost,'ratio':newcost/oldcost,'rows':costs}
        plans=[];contracts=[];times=[]
        for cfg in (old,new):
            j=View(db,now);inv=j.snapshot('inventory.json');catalog=j.snapshot('catalog.json')
            j.put('market_control',dict(j.get('market_control',{}),pause_restock=False))
            j.put('merchant_context_v5',{})
            cfg=dict(cfg,**j.get('feedback-last-policy',{}).get('patches',{}).get('shop',{}))
            started=time.perf_counter()
            rows=restock_candidates(dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180}),catalog,[],j,cfg,now)
            plans.append([{k:r.get(k) for k in ('sku','qty','capital_commitment','expected_delta','score')} for r in rows])
            cs=[]
            for unit in sorted(inv['units'],key=lambda u:(u.get('kind')!='goods',u.get('unit')))[:8]:
                cs.append([{k:r.get(k) for k in ('id','action','price','warranty_hours','recovery_floor','expected_delta','score')}
                    for r in shop_candidates(unit,inv,catalog,j,cfg,now=now)])
            contracts.append(cs);times.append(time.perf_counter()-started)
        assert plans[0]==plans[1] and contracts[0]==contracts[1]
    assert old['shop_strategy_revision']==new['shop_strategy_revision'] and old['buyer_strategy_revision']==new['buyer_strategy_revision']
    assert old['background_llm_roles']['market_research_critic']==new['background_llm_roles']['market_research_critic']
    assert new['research_interval_seconds']==old['research_interval_seconds'] and new['daily_api_budget_usd'] is None
    save('v25-cost-replay',{'at':now,'cost_windows':result,'source_hashes':hashes(),
        'supply_actions_identical':True,'shop_contracts_identical':True,'unchanged_critic':True,
        'quantity_and_money_guards_unchanged':True,'parity_seconds':times,'plans':plans[1],
        'market_transactions':0,'model_calls':0,'policy_writes':0,
        'limitation':'Fixed-token repricing and one-snapshot numeric parity do not prove future bills or economic lift.'})
    print(json.dumps({k:{a:b for a,b in r.items() if a!='rows'} for k,r in result.items()}))


def smoke():
    cfg=read_json(STAGE/'policy.json');key=api_key(root=ROOT);assert key,'Previously authorized credential unavailable'
    now=time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=View(db,now);rules=j.get('market_rules',{});op=j.get('feedback-last-policy',{})
        market=j.get('cycle-market-analytics',{});evidence=['rules:'+rules['sha256'],'kv:feedback-last-policy','kv:objective-decision-context','snapshot:inventory.json']
        current=j.get('research-last-result',{}).get('operating_policy')
        facts={'observed_at':now,'evidence_ids':evidence,'current_operating_policy':current,
            'market':market,'control':{k:j.get('market_control',{}).get(k) for k in ('status','reasons','leader_gap','suggestions')},
            'operating_feedback':{k:op.get(k) for k in ('at','regime','facts','peer_trends','objective_targets','patches','reasons','constraints')},
            'objective_decisions':j.get('objective-decision-context',{}),
            'quality_holdout':j.get('quality-holdout-summary',{}),
            'parameters':{k:cfg.get(k) for k in ('shop_inventory_caps_enabled','shop_internal_capital_caps_enabled','shop_demand_horizon_cycles','buyer_contract_probe_percent')}}
        f=capability.source(j,cfg,now);calculations=capability.compute({'tools':capability.TOOLS},f,j)
    isolated=Journal(STAGE/'.market'/('model-smoke-'+str(int(now))));isolated.put('market_rules',rules)
    production=Journal(ROOT/'.market')
    class Accounted:
        def hold_budget(self,*a):return production.hold_budget(*a)
        def close_budget(self,*a):return production.close_budget(*a)
        def add(self,kind,row,**kw):
            isolated.add(kind,row,**kw)
            if kind in ('model_usage','background_model_failed'):
                production.add(kind,dict(row,validation_only=True,validation_revision='v25'))
    paid=Accounted()
    def market_work():
        a=research.call(paid,cfg,facts,evidence,'analyst',key=key)
        b=research.call(paid,cfg,facts,evidence,'critic',proposal=a,key=key)
        packet=research.publish(isolated,cfg,facts,a,b,rules['sha256'])
        assert packet
        return {'analyst':a,'critic':b,'packet':packet}
    def capability_work():
        from r3.objective_context import _bounded
        payload={'facts':_bounded(f),'valid_evidence_ids':f['evidence_ids'],'current_plan':None,'trusted_tools':capability.TOOLS}
        a=call_json(paid,cfg,'capability_triage',capability.TRIAGE,capability.INSTRUCTIONS+'Assess the missing plan.',payload,key=key)
        assert capability.valid(a,f['evidence_ids'])
        payload['triage']=a
        b=call_json(paid,cfg,'capability_designer',capability.DESIGN,capability.INSTRUCTIONS+'For this isolated test design a shallow plan.',payload,key=key)
        assert capability.valid(b,f['evidence_ids'],True)
        return {'triage':a,'designer':b,'trusted_calculations_available':list(calculations)}
    started=time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(market_work);b=pool.submit(capability_work)
            market_result=a.result();capability_result=b.result()
        usage=isolated.events('model_usage',10);assert len(usage)==4
        assert {r['role']:r['model'] for r in usage}=={k:v['model'] for k,v in cfg['background_llm_roles'].items()}
        save('v25-live-model-smoke',{'schema_valid':True,'calls':4,'seconds':time.monotonic()-started,
            'source_hashes':hashes(),'facts':facts,'market':market_result,'capability':capability_result,'usage':usage,
            'published_only_in_isolation':True,'market_transactions':0,'live_policy_writes':0})
    except Exception as exc:
        save('v25-live-model-failure',{'error_type':type(exc).__name__,'reason':str(exc) if type(exc).__name__=='ModelError' else 'see console',
            'usage':isolated.events('model_usage',10),'seconds':time.monotonic()-started,'market_transactions':0,'live_policy_writes':0})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--smoke',action='store_true');a=p.parse_args()
    smoke() if a.smoke else replay()
