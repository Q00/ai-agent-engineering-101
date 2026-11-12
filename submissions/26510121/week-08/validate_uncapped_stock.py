"""Frozen, read-only v20 cap and demand counterfactuals; zero market actions."""
from collections import Counter
from datetime import datetime,timezone
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v20-stage-20261112'
sys.path.insert(0,str(STAGE))


def save(kind,row):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2))
    print('SOURCE',path,flush=True)
    return path


class Frozen:
    def __init__(self,files,values,events):self.files,self.values,self.rows=files,values,events
    def get(self,key,default=None):return copy.deepcopy(self.values.get(key,default))
    def put(self,key,value):self.values[key]=copy.deepcopy(value)
    def snapshot(self,name,default=None):return copy.deepcopy(self.files.get(name,default))
    def events(self,kind,limit=500):return copy.deepcopy(self.rows.get(kind,[])[:limit])


def main():
    from r3.state import read_json,policy
    from r3.economy import cash_capacity,restock_candidates,remaining
    from r3.shop_growth import inventory_limit,target_stock,poisson_target
    import r3.shop_growth as growth
    from r3.merchant import context as merchant_context
    from r3.adaptive import effective
    from autonomous_status import ReadJournal
    now=time.time();names=['inventory.json','catalog.json','board.json','operating-policy-v16.json','market_stock_counts.json']
    files={n:read_json(ROOT/'.market'/n,{}) for n in names}
    cfg=policy();base=read_json(ROOT/'policy.json')
    kinds={'public_settle':20000,'public_broken':20000,'shop_settled':10000,
           'customer_visit':2000,'shop_opportunity':10000,'customer_outcome':8000,'buyer_order':500,'forum_observation':500}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');reader=ReadJournal(db,now)
        reader.snapshot=lambda name,default=None:copy.deepcopy(files.get(name,default))
        effective_cfg,_=effective(reader,'shop',base=base)
        keys=['market_control','public_ledger_seq','merchant_context_v5','merchant_stock_timeline',
              'shop_quality_cohorts_v9','quality_risk_v4','board_checked_at','market_rules']
        values={k:reader.get(k) for k in keys if reader.get(k) is not None}
        events={kind:[dict(json.loads(raw),_event_id=eid,_ts=at,_role=role)
            for eid,at,role,raw in db.execute('SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT ?',
                                             (kind,now,limit))] for kind,limit in kinds.items()}
        sixhour={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=? AND ts<=?',(k,now-21600,now)).fetchone()[0]
                 for k in ('public_settle','customer_visit')}
    source={'cutoff':now,'files':files,'values':values,'events':events,'effective_cfg':effective_cfg,
            'six_hour_source_counts':sixhour,'read_only':True,'market_transactions':0}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    archive=ROOT/'logs'/('v20-frozen-demand-'+stamp+'.json.gz')
    raw=json.dumps(source,ensure_ascii=False,separators=(',',':')).encode()
    with archive.open('xb') as f:f.write(gzip.compress(raw))
    j=Frozen(copy.deepcopy(files),copy.deepcopy(values),events)
    # Recompute from captured source with an in-memory journal only. All cases
    # share the same inference generation, guard state, prices and cash.
    j.put('merchant_context_v5',{})
    merchant=merchant_context(j,files['catalog.json'],now)
    values=copy.deepcopy(j.values)
    inv=copy.deepcopy(files['inventory.json']);inv['restock']={'open':True,'opens_at':now-1,'closes_at':now+180}
    values['market_control']=dict(values.get('market_control',{}),pause_restock=False)
    # A hypothetical open/visible scenario is not authorization for live supply.
    capacity=cash_capacity(inv,j,effective_cfg,now)
    result=[];native_marginal=growth.marginal;native_target=growth.target_stock
    def constant_probe(c,q,held=0):
        if c['evidence'].get('exploration'):
            c=dict(c,evidence=dict(c['evidence'],uncapped_inventory=False))
        return native_marginal(c,q,held)
    def longer_horizon(rate,paid,config,public_qualified=False,public_limit=6):
        return poisson_target(max(0,rate)*6,config.get('shop_stock_service_level',.9) if paid>=3 else .8 if public_qualified else .65)
    for name,caps,probe_fix,scale,longer in [
        ('current_v19',True,False,1,False),
        ('remove_caps_only',False,False,1,False),
        ('v20_uncapped_diminishing_probe',False,True,1,False),
        ('hypothetical_rate_x2',False,True,2,False),
        ('hypothetical_6h_target_coverage',False,True,1,True)]:
        cfg=dict(effective_cfg,shop_inventory_caps_enabled=caps)
        if scale!=1:
            cfg['shop_sku_trend_factors']={sku:effective_cfg.get('shop_sku_trend_factors',{}).get(sku,1)*scale for sku in files['catalog.json']}
        shadow=Frozen(copy.deepcopy(files),copy.deepcopy(values),events)
        growth.marginal=native_marginal if probe_fix else constant_probe
        growth.target_stock=longer_horizon if longer else native_target
        started=time.perf_counter()
        try:rows=restock_candidates(inv,files['catalog.json'],[],shadow,cfg,now)
        finally:growth.marginal=native_marginal;growth.target_stock=native_target
        assert all(type(r['qty']) is int and 1<=r['qty']<=20 for r in rows)
        assert sum(r['capital_commitment'] for r in rows)<=capacity['spendable']+1e-6
        result.append({'scenario':name,'not_executed':True,'inventory_limit':inventory_limit(cfg),
            'lines':len(rows),'units':sum(r['qty'] for r in rows),
            'capital_commitment':sum(r['capital_commitment'] for r in rows),
            'book_scenario':sum(r['expected_delta']['book'] for r in rows),
            'revenue_scenario':sum(r['expected_delta']['revenue'] for r in rows),
            'seconds':time.perf_counter()-started,
            'rows':[{'sku':r['sku'],'qty':r['qty'],'arrival_rate_scenario':r['evidence']['arrival_rate_scenario'],
                'probe':r['evidence']['exploration'],'held':sum(u['sku']==r['sku'] for u in inv.get('units',[])),
                'book':r['expected_delta']['book']} for r in rows]})
    targets=[]
    counts=Counter(u['sku'] for u in inv.get('units',[]) if remaining(u,now)>0)
    for sku,n in counts.items():
        m=merchant.get('skus',{}).get(sku,{})
        paid=m.get('positive_paid_while_available',0);trend=effective_cfg.get('shop_sku_trend_factors',{}).get(sku,1)
        rate=(m.get('paid_arrival_rate_scenario',0) if paid>=3 else m.get('arrival_rate_scenario',0))*trend
        public=sku in effective_cfg.get('shop_public_demand_skus',[])
        settings=dict(public_qualified=public,public_limit=effective_cfg.get('shop_public_demand_caps',{}).get(sku,6))
        targets.append({'sku':sku,'held':n,'own_positive_paid':paid,'rate_scenario':rate,
            'exposure_hours':m.get('available_hours_observed'),
            'public_external_samples':m.get('public',{}).get('samples',0),
            'competition_sellers_plus_us':m.get('competition_sellers_plus_us'),
            'target_capped':target_stock(rate,paid,dict(effective_cfg,shop_inventory_caps_enabled=True),**settings),
            'target_uncapped':target_stock(rate,paid,dict(effective_cfg,shop_inventory_caps_enabled=False),**settings)})
    report={'cutoff':now,'source_archive':str(archive.relative_to(ROOT)),'source_archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'source_raw_bytes':len(raw),'source_compressed_bytes':archive.stat().st_size,'six_hour_source_counts':sixhour,
        'own_stock_units':sum(counts.values()),'own_stock_skus':len(counts),'spendable':capacity['spendable'],
        'effective_operating_values':{k:effective_cfg.get(k) for k in ('shop_inventory_ceiling','shop_demand_horizon_cycles','shop_public_demand_caps')},
        'scenarios':result,'held_sku_targets':targets,
        'capped_low_targets':sum(r['target_capped']<=2 for r in targets),
        'uncapped_low_targets':sum(r['target_uncapped']<=2 for r in targets),
        'changed_held_sku_targets':sum(r['target_capped']!=r['target_uncapped'] for r in targets),
        'market_transactions':0,'model_calls':0,'economic_superiority_proven':False,
        'notes':['Same frozen facts. Hypothetical open/visible supply; live guards not changed.',
                 'Rate x2 and 6h coverage are sensitivity scenarios, not validated demand or promotions.',
                 'Book forecasts retain cost, storage, refund and expiry assumptions; not realized profit.']}
    save('v20-demand-counterfactual',report)
    print('SUMMARY',json.dumps({k:report[k] for k in ('own_stock_units','own_stock_skus','capped_low_targets','uncapped_low_targets','changed_held_sku_targets')}))
    print('SCENARIOS',json.dumps([{k:r[k] for k in ('scenario','lines','units','capital_commitment','book_scenario','seconds')} for r in result]))


def context_contract():
    from r3.state import read_json
    from r3.objective_context import curate
    from r3.shop_growth import demand_diagnostics
    source=sorted((ROOT/'logs').glob('v20-frozen-demand-*.json.gz'))[-1]
    frozen=json.loads(gzip.decompress(source.read_bytes()))
    historical=sorted((ROOT/'logs').glob('v18-objective-replay-*.json'))[-1]
    facts=read_json(historical)['facts']
    cfg=dict(frozen['effective_cfg'],shop_inventory_caps_enabled=False)
    diagnostic=demand_diagnostics(frozen['files']['inventory.json'],frozen['values'].get('merchant_context_v5',{}),cfg)
    facts['objective_decisions']['shop']['demand_diagnostics']=diagnostic
    facts['objective_decisions']['shop']['inventory_ceiling']=None
    facts['parameters']['shop_inventory_caps_enabled']=False
    compact,metrics=curate(facts)
    assert metrics['fact_bytes']<=23000 and compact['parameters']['shop_inventory_caps_enabled'] is False
    assert compact['objective_decisions']['shop']['demand_diagnostics']['total_unit_ceiling'] is None
    save('v20-context-contract',{'schema_valid':True,'metrics':metrics,'source':str(source.relative_to(ROOT)),
        'historical_envelope':str(historical.relative_to(ROOT)),
        'scope':'Bounded schema/size contract only; mixed-date envelope is not a live market diagnosis.',
        'diagnostic':diagnostic,'model_calls':0,'market_transactions':0})


if __name__=='__main__':
    context_contract() if '--context' in sys.argv else main()
