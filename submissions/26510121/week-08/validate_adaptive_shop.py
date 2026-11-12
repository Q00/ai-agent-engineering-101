"""No-trade v21 replay on a frozen, inspectable market generation."""
from datetime import datetime,timezone
import copy
import gzip
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v21-stage-20261112'
sys.path.insert(0,str(STAGE))


def main():
    from r3.state import read_json,policy
    from r3.market_memory import market_book
    from r3.demand_policy import fit,KEY,REVISION
    from r3.economy import restock_candidates,cash_capacity
    from r3.objective_context import curate
    from validate_uncapped_stock import Frozen
    source=sorted((ROOT/'logs').glob('v20-frozen-demand-*.json.gz'))[-1]
    data=json.loads(gzip.decompress(source.read_bytes()));now=data['cutoff']
    j=Frozen(data['files'],copy.deepcopy(data['values']),data['events'])
    catalog=j.snapshot('catalog.json');inv=j.snapshot('inventory.json');cfg=policy()
    started=time.perf_counter()
    packet=fit(catalog,market_book(j,catalog,now),j.get('merchant_stock_timeline',[]),j.events('shop_settled',10000),
        j.events('supply_execution_status',10000),j.snapshot('market_stock_counts.json'),inv,cfg,now)
    packet.update(rules_hash=j.get('market_rules')['sha256'],id='isolated-replay-v21')
    assert packet['skus'] and all(r['rate_per_hour']>=0 and 0<r['coverage_hours']<=6 for r in packet['skus'].values())
    calculated_seconds=time.perf_counter()-started
    virtual=dict(inv,restock={'open':True,'opens_at':now-1,'closes_at':now+180})
    rows=[]
    for enabled in (False,True):
        shadow=Frozen(copy.deepcopy(data['files']),copy.deepcopy(data['values']),data['events'])
        shadow.put('market_control',dict(shadow.get('market_control',{}),pause_restock=False))
        shadow.put('merchant_context_v5',{});shadow.put(KEY,packet)
        config=dict(data['effective_cfg'],shop_adaptive_demand_enabled=enabled,shop_inventory_caps_enabled=False)
        started=time.perf_counter();candidates=restock_candidates(virtual,catalog,[],shadow,config,now)
        spendable=cash_capacity(inv,shadow,config,now)['spendable']
        assert all(1<=r['qty']<=20 for r in candidates)
        assert sum(r['capital_commitment'] for r in candidates)<=spendable
        rows.append({'adaptive':enabled,'lines':len(candidates),'units':sum(r['qty'] for r in candidates),
            'capital_commitment':sum(r['capital_commitment'] for r in candidates),
            'book_scenario':sum(r['expected_delta']['book'] for r in candidates),
            'seconds':time.perf_counter()-started,
            'quantities':[{'sku':r['sku'],'qty':r['qty'],'rate':r['evidence']['arrival_rate_scenario'],
                'coverage_hours':r['evidence'].get('adaptive_coverage_hours')} for r in candidates]})
    envelope=read_json(sorted((ROOT/'logs').glob('v18-objective-replay-*.json'))[-1])['facts']
    envelope['parameters']['shop_adaptive_demand_enabled']=True
    envelope['objective_decisions']['shop']['demand_diagnostics']={'calibration_summary':packet['summary']}
    _,metrics=curate(envelope);assert metrics['fact_bytes']<=23000
    factors=sorted(r['posterior_multiplier'] for r in packet['skus'].values() if r['posterior_multiplier'] is not None)
    horizons=sorted(r['coverage_hours'] for r in packet['skus'].values())
    report={'source':str(source.relative_to(ROOT)),'source_cutoff':now,'revision':REVISION,
        'calibration_seconds':calculated_seconds,'summary':packet['summary'],
        'posterior_multiplier_range':[factors[0],factors[-1]],'coverage_hours_range':[horizons[0],horizons[-1]],
        'scenarios':rows,'context_contract':metrics,'schema_valid':True,'cash_and_legal_lot_guards_verified':True,
        'market_transactions':0,'model_calls':0,'economic_superiority_proven':False,
        'note':'Each row uses its own rate assumption; larger forecast Book is not observed uplift. Historical physical availability is a proxy.'}
    path=ROOT/'logs'/('v21-shop-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({'calibration_seconds':calculated_seconds,'multiplier_range':report['posterior_multiplier_range'],
        'coverage_hours_range':report['coverage_hours_range'],'scenarios':rows},ensure_ascii=True))


if __name__=='__main__':main()
