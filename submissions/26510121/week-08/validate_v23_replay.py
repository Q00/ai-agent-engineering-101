"""Frozen read-only planning ablation; forecasts are not measured market lift."""
from contextlib import contextmanager
from datetime import datetime,timezone
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v23-stage-20261112'
sys.path.insert(0,str(STAGE))
from r3.state import read_json
from r3.economy import restock_candidates,cash_capacity,shop_candidates
from r3.policy_learning import update as learn
from r3.shop_growth import target_stock
from r3.market_memory import buyer_assessment,buyer_price_actions


class View:
    def __init__(self,db,now,tag,files):
        self.db,self.now,self.memory,self.records=db,now,{},[]
        self.md=ROOT/'.market'/('v23-replay-'+tag);self.files=copy.deepcopy(files)
    @contextmanager
    def connect(self):yield self.db
    def get(self,key,default=None):
        if key in self.memory:return self.memory[key]
        row=self.db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,key,value):self.memory[key]=value
    def snapshot(self,name,default=None):return self.files.get(name,default)
    def add(self,kind,row,**kwargs):self.records.append((kind,row))
    def events(self,kind,limit=500):
        return [dict(json.loads(raw),_event_id=eid,_ts=at,_role=role) for eid,at,role,raw in self.db.execute(
            'SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',
            (kind,self.now,limit))]


def main():
    now=time.time();files={p.name:read_json(p) for p in (ROOT/'.market').glob('*.json')}
    current=read_json(ROOT/'policy.json');candidate=read_json(STAGE/'policy.json')
    inv=files['inventory.json'];catalog=files['catalog.json'];result={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for tag,cfg in [('current',current),('candidate',candidate)]:
            j=View(db,now,tag,files)
            rules=j.get('market_rules',{});known=j.events('buyer_score_formula_changed',10)
            rules=dict(rules,buyer_score_epoch=min((r['at'] for r in known if r.get('after')=='margin_share_v36'),default=now))
            j.put('market_rules',rules);j.put('merchant_context_v5',{})
            # Virtual supply-open comparison only; execution guards stay on in production.
            control=j.get('market_control',{});j.put('market_control',dict(control,pause_restock=False,at=now))
            j.put('board_checked_at',now)
            patch=j.get('feedback-last-policy',{}).get('patches',{}).get('shop',{})
            cfg=dict(cfg,**patch)
            learning=learn(j,cfg,now) if cfg.get('book_growth_v23_enabled') else {}
            virtual=dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
            started=time.perf_counter();rows=restock_candidates(virtual,catalog,[],j,cfg,now);elapsed=time.perf_counter()-started
            cap=cash_capacity(virtual,j,cfg,now)
            assert len(rows)<=10 and all(type(r['qty']) is int and 1<=r['qty']<=20 for r in rows)
            assert sum(r['capital_commitment'] for r in rows)<=cap['spendable']
            assert all(d['book']>0 for r in rows for d in r['evidence']['marginal_units'])
            contracts=[]
            chosen_units=sorted(inv.get('units',[]),key=lambda u:(u.get('kind')!='goods',-u.get('list_price',0)))[:8]
            for unit in chosen_units:
                offers=shop_candidates(unit,inv,catalog,j,cfg,now=now)
                quote=[o for o in offers if o['action']!='hold']
                assert all(o['price']>=o.get('recovery_floor',0) for o in quote)
                best=max(quote,key=lambda o:o['expected_delta']['book']) if quote else None
                contracts.append({'sku':unit['sku'],'grade':unit.get('grade'),'cost':unit['cost'],
                    'candidates':len(quote),'best_expected_book':best['expected_delta']['book'] if best else None,
                    'best_warranty':best['warranty_hours'] if best else None,
                    'saved_scenario':(best['evidence'].get('buyer_saved_contract') if best else None)})
            orders={r['order']:r for r in j.events('buyer_order',500)};buy=[]
            for quote in j.events('quote_comparison',80):
                order=orders.get(quote['order']);p=catalog.get(quote['sku']);old=quote['assessment']
                if not order or not p:continue
                a=buyer_assessment(p,order,{'grade':old.get('required_grade'),'warranty_hours':old.get('required_warranty',0)},j,cfg,shop=quote['shop'])
                acts=buyer_price_actions(quote['ask'],a['cap'],p,quote['ask'],cfg.get('buyer_counter_ratio',.92),0,4,
                    failure_stress=a['failure'],covered_failure=a['covered_failure'],saved_state=a['saved_state'],
                    counter_model=a.get('negotiation_model'),target_saved=cfg.get('buyer_saved_bid_targets'))
                assert all(0<r['price']<=order['budget'] for r in acts)
                assert len({r['id'] for r in acts})==len(acts)
                buy.append({'sku':quote['sku'],'actions':len(acts),'hard_saved_cap':a['saved_target_is_hard_cap'],
                    'counter_prices':[r['price'] for r in acts if r.get('action',r['id'])=='counter']})
                if len(buy)>=8:break
            result[tag]={'planning_seconds':elapsed,'lines':len(rows),'units':sum(r['qty'] for r in rows),
                'capital_commitment':sum(r['capital_commitment'] for r in rows),'spendable':cap['spendable'],
                'expected_book_scenario':sum(r['expected_delta']['book'] for r in rows),
                'expected_revenue_scenario':sum(r['expected_delta']['revenue'] for r in rows),
                'plan':[{'sku':r['sku'],'qty':r['qty'],'book':r['expected_delta']['book'],
                    'pipeline_buffer':r['evidence'].get('reserved_pipeline_buffer')} for r in rows],
                'contracts':contracts,'buyer_actions':buy,
                'learning':{'qualified_service_skus':sum(r['qualified'] for r in learning.get('service',{}).values()),
                    'counter_samples':sum(r['n'] for g in learning.get('counter_completion_by_kind',{}).values() for r in g.values())}}
    result.update(at=datetime.fromtimestamp(now,timezone.utc).isoformat(),market_transactions=0,model_calls=0,
        source_hashes={str(p.relative_to(STAGE)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in list((STAGE/'r3').glob('*.py'))+[STAGE/'market_buyer.py',STAGE/'policy.json']},
        shared_state_writes=0,guards_verified=True,economic_superiority_proven=False,
        limits=['Shadow supply opening is hypothetical; actual execution keeps market/control/identity/cash guards.',
            'Forecasts differ in assumptions and candidate sets; this is not a randomized economic comparison.'])
    path=ROOT/'logs'/('v23-market-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:{f:result[k][f] for f in ('units','capital_commitment','expected_book_scenario','expected_revenue_scenario','planning_seconds','learning')}
        for k in ('current','candidate')}))


if __name__=='__main__':main()
