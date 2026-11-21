"""Read-only frozen-market replay; derived state stays in memory, no trades/API."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v26-stage-20261113'
sys.path.insert(0,str(STAGE))
from r3.state import read_json
from r3 import service_policy,negotiation_path
from r3.economy import restock_candidates
from r3.market_memory import buyer_assessment,buyer_price_actions


def hashes():
    return {p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in list((STAGE/'r3').glob('*.py'))+[STAGE/n for n in ('policy.json','seller_r3.py','market_buyer.py','verify_market_cycle_live.py')]}


class View:
    def __init__(self,db,now):
        self.db,self.now,self.memory,self.records=db,now,{},{};self.md=STAGE/'.market/replay'
        self.files={p.name:read_json(p) for p in (ROOT/'.market').glob('*.json')}
    @contextmanager
    def connect(self):yield self.db
    def get(self,k,default=None):
        if k in self.memory:return self.memory[k]
        row=self.db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,k,v):self.memory[k]=v
    def snapshot(self,k,default=None):return self.files.get(k,default)
    def add(self,k,v,**kwargs):
        eid=kwargs.get('eid') or str(len(self.records))
        self.records.setdefault(eid,dict(v,_event_id=eid,_ts=kwargs.get('ts',self.now),_role=kwargs.get('role','system'),_kind=k))
    def add_batch(self,rows,ts=None):
        for k,v,eid,role in rows:self.add(k,v,eid=eid,role=role,ts=self.now if ts is None else ts)
    def events(self,k,limit=500):
        rows={eid:dict(json.loads(raw),_event_id=eid,_ts=ts,_role=role) for eid,ts,role,raw in self.db.execute(
            'SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',(k,self.now,limit))}
        rows.update({eid:r for eid,r in self.records.items() if r['_kind']==k})
        return sorted(rows.values(),key=lambda r:r['_ts'],reverse=True)[:limit]


def main():
    now=time.time();cfg=read_json(STAGE/'policy.json');old=read_json(ROOT/'policy.json');started=time.perf_counter()
    assert cfg['background_llm_roles']==old['background_llm_roles'] and cfg['daily_api_budget_usd'] is None
    summaries={};plans={};elapsed={};actions={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=View(db,now)
        derived=negotiation_path.bootstrap(j,now);sp=service_policy.update(j,cfg,now);path=negotiation_path.update(j,cfg,now)
        summaries={'historical_exact_routes_derived':derived,'qualified_service_skus':len(sp['shop_needs']),
            'path_samples':path['samples'],'path_holdout':path['summary'],'contract_groups':sp['buyer_contracts'],
            'supply_draws':len(sp['draw_fractions'])}
        inv=j.snapshot('inventory.json');catalog=j.snapshot('catalog.json')
        patches=j.get('feedback-last-policy',{}).get('patches',{}).get('shop',{})
        for name,c in (('v25',dict(old,service_contract_v26_enabled=False)),('v26',cfg)):
            view=View(db,now);view.memory.update(j.memory);view.records.update(j.records)
            view.put('market_control',dict(view.get('market_control',{}),pause_restock=False));view.put('merchant_context_v5',{})
            t=time.perf_counter();rows=restock_candidates(dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180}),catalog,[],view,dict(c,**patches),now)
            elapsed[name]=time.perf_counter()-t
            plans[name]=[{k:r.get(k) for k in ('sku','qty','capital_commitment','expected_delta')} for r in rows]
            assert all(1<=r['qty']<=20 and r['expected_delta']['book']>0 for r in rows)
            assert len(rows)<=10
        samples=[]
        for q in j.events('quote_comparison',6000):
            if q.get('sku') in catalog and catalog[q['sku']].get('kind')=='goods' and not q.get('assessment',{}).get('conditional_terms'):
                samples.append(q)
            if len(samples)>=5:break
        t=time.perf_counter()
        for q in samples:
            product=catalog[q['sku']];a=q['assessment'];current=a['saved_state'];order={'budget':a['cap'],'kind':'goods'}
            updated=buyer_assessment(dict(product,sku=q['sku']),order,{'grade':a['required_grade'],'warranty_hours':a['required_warranty']},j,cfg,shop=q['shop'])
            candidates=buyer_price_actions(q['ask'],a['cap'],product,q['ask'],.92,0,4,saved_state=updated['saved_state'],
                failure_stress=updated['failure'],covered_failure=updated['covered_failure'],counter_model=updated['negotiation_model'],
                target_saved=updated['bid_targets'],path_model=updated['negotiation_path_model'])
            assert all(r['price']<=a['cap'] for r in candidates)
            actions[q['sku']]={'ask':q['ask'],'prices':[r['price'] for r in candidates],
                'validated_path':updated['negotiation_path_model'].get('id')}
        elapsed['five_buyer_frontiers']=time.perf_counter()-t
    result={'at':now,'source_hashes':hashes(),'summary':summaries,'plans':plans,'buyer_frontiers':actions,
        'elapsed_seconds':elapsed,'total_seconds':time.perf_counter()-started,'model_calls':0,'market_transactions':0,
        'live_policy_writes':0,'models_unchanged':True,'legal_lots_and_positive_book_scenarios':True,
        'limitation':'Frozen-snapshot mechanics and predictive checks do not prove future conversion, ratings, economic lift or first place.'}
    p=ROOT/'logs'/('v26-service-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',p);print(json.dumps({'summary':summaries,'elapsed_seconds':elapsed,'plans':plans},ensure_ascii=True))


if __name__=='__main__':main()
