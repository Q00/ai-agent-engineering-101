"""Read-only frozen quote replay of v22; no payments, HTTP or model requests."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v22-stage-20261112'
sys.path.insert(0,str(STAGE))
from r3.state import read_json
from r3.control import audit_rules
from r3.buyer_costs import update as cost_update
from r3.buyer_scoring import MARGIN,joint_actions
from r3.market_memory import buyer_assessment,buyer_price_actions
from r3.model import Chooser


class Frozen:
    def __init__(self,db,now):
        self.db,self.now,self.memory,self.records=db,now,{},[]
        self.md=ROOT/'.market/v22-readonly-replay'
        self.files={p.name:read_json(p) for p in (ROOT/'.market').glob('*.json')}
    @contextmanager
    def connect(self):yield self.db
    def get(self,key,default=None):
        if key in self.memory:return self.memory[key]
        row=self.db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,key,value):self.memory[key]=value
    def snapshot(self,name,default=None):return self.files.get(name,default)
    def events(self,kind,limit=500):
        return [dict(json.loads(d),_event_id=i,_ts=t,_role=r) for i,t,r,d in self.db.execute(
            'SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',
            (kind,self.now,limit))]
    def add(self,kind,row,**kwargs):self.records.append((kind,row))


def main():
    now=time.time();cfg=read_json(STAGE/'policy.json')
    source=sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1]
    raw=read_json(source)['sources']['https://market.ourolabs.ai/agent.md']
    assert raw.get('status')==200
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=Frozen(db,now)
        rules=audit_rules(j,raw['text'],now)
        assert rules['buyer_compatible'] and rules['buyer_saved_formula']==MARGIN
        packet=cost_update(j,cfg,now)
        orders={r['order']:r for r in j.events('buyer_order',1000)}
        catalog=j.snapshot('catalog.json',{});rows=[];times=[]
        chooser=Chooser(j,cfg)
        # Forbid an accidental paid path in this verification process.
        chooser._call=lambda *a,**k:(_ for _ in ()).throw(AssertionError('no paid model calls'))
        for quote in j.events('quote_comparison',100):
            order=orders.get(quote['order']);p=catalog.get(quote['sku']);old=quote.get('assessment',{})
            if not order or not p:continue
            terms={'grade':old.get('required_grade'),'warranty_hours':old.get('required_warranty',0)}
            started=time.perf_counter()
            assessment=buyer_assessment(p,order,terms,j,cfg,shop=quote['shop'])
            assert assessment['cap']<=order['budget']
            actions=buyer_price_actions(quote['ask'],assessment['cap'],p,quote['ask'],.92,0,3,
                failure_stress=assessment['failure'],covered_failure=assessment['covered_failure'],
                saved_state=assessment['saved_state'])
            for action in actions:action['execution_seconds']=3 if action['id']=='accept' else 6
            selected=chooser.choose('buyer',joint_actions(actions),{'buyer_execution':True,'operation':'price'})
            times.append(time.perf_counter()-started)
            rows.append({'sku':quote['sku'],'kind':p['kind'],'quote_at':quote['_ts'],'ask':quote['ask'],
                'previous_cap':old.get('cap'),'budget_quality_cap':assessment['cap'],
                'saved_reference_only':assessment['saved_reference_cap'],
                'saved_target_is_hard_cap':assessment['saved_target_is_hard_cap'],
                'cost_source':assessment['cost_scenario']['expected_source'],
                'cost_samples':assessment['cost_scenario']['samples'],
                'selected':selected['id'],'expected_delta':selected['expected_delta'],
                'component_progress':selected.get('objective_component_progress'),
                'quote_reconstruction':'stored grade/warranty; no claim to reconstruct unrecorded peer terms'})
            if len(rows)>=12:break
        assert rows and all(not r['saved_target_is_hard_cap'] for r in rows)
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'rule_source':str(source.relative_to(ROOT)),
            'rules':rules,'cost_model':packet,'rows':rows,'numeric_seconds':{'n':len(times),
                'median':statistics.median(times),'max':max(times)},
            'model_calls':0,'market_transactions':0,'shared_state_writes':0,
            'all_saved_caps_are_reference_only':True,'economic_superiority_proven':False}
    path=ROOT/'logs'/('v22-buyer-quote-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({'quotes':len(rows),'cost_cohorts':{k:r['n'] for k,r in packet['groups'].items()},
        'numeric_seconds':result['numeric_seconds'],'choices':[r['selected'] for r in rows]}))


if __name__=='__main__':main()
