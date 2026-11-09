"""Read-only evidence of the deployed execution strategy, timings and payments."""
from collections import Counter
from datetime import datetime,timezone
import json
import sqlite3
import statistics
import time
from autonomous_status import ReadJournal
from r3.state import ROOT,policy,read_json,atomic_json

if __name__=='__main__':
    now=time.time();cfg=policy()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,now)
        revision=j.get('buyer_strategy_revision');epoch=j.get('buyer_revision_epoch');version=j.get('adaptive:buyer',{}).get('version')
        def fresh(kind):return [r for r in j.events(kind,2000) if epoch<=r['_ts']<=now]
        orders={r['order']:r for r in fresh('buyer_order') if r.get('strategy_version')==version}
        results={r['order']:r for r in fresh('market_result') if r.get('order') in orders}
        purchases={r['order']:r for r in fresh('purchase') if r.get('order') in orders}
        preparation={r['order']:r for r in fresh('buyer_preparation') if r.get('order') in orders}
        timings=[]
        for oid,r in orders.items():
            if oid in results:
                timings.append({'order':oid,'kind':r.get('kind'),'verified':bool(results[oid].get('verified')),
                    'seconds_to_result':results[oid]['_ts']-r['_ts'],
                    'preparation_seconds':preparation.get(oid,{}).get('seconds')})
        baseline=read_json(ROOT/'logs/buyer-v12-profile-20261109T080816901040Z.json',{})
        old=[r for r in baseline.get('timings',[]) if r['total_seconds']>=0]
        starts=sorted(r['_ts'] for r in orders.values())
        race=fresh('buyer_quote_race')
        result={'at':now,'revision':revision,'expected_revision':cfg['buyer_strategy_revision'],'version':version,
            'epoch':epoch,'elapsed_seconds':now-epoch,'runtime':read_json(ROOT/'.market/runtime.json',{}),
            'health':read_json(ROOT/'.market/shop-health.json',{}),'shop_strategy_revision':j.get('shop_strategy_revision'),
            'policy':{k:v for k,v in cfg.items() if k.startswith('buyer_')},
            'orders':dict(Counter(r.get('kind') for r in orders.values())),
            'completed':len(results),'verified':sum(bool(r.get('verified')) for r in results.values()),
            'paid':dict(Counter(r.get('kind') for r in purchases.values())),
            'initial_saved_mean':statistics.mean(r['initial_saved_contribution'] for r in purchases.values()) if purchases else None,
            'purchase_evidence':[{k:r.get(k) for k in ['order','sku','kind','price','value','grade','warranty_hours','ledger','strategy_version','initial_saved_contribution']} for r in purchases.values()],
            'preparation_median_seconds':statistics.median(r['seconds'] for r in preparation.values()) if preparation else None,
            'completed_median_seconds':statistics.median(r['seconds_to_result'] for r in timings) if timings else None,
            'median_order_start_gap_seconds':statistics.median(b-a for a,b in zip(starts,starts[1:])) if len(starts)>1 else None,
            'before_profile_completed_median_seconds':statistics.median(r['total_seconds'] for r in old) if old else None,
            'before_profile_prechoice_median_seconds':statistics.median(r['before_first_choice_seconds'] for r in old if r['before_first_choice_seconds'] is not None) if old else None,
            'quote_races':len(race),'strong_offer_races':sum(r.get('strong_offer',False) for r in race),
            'attempts':dict(Counter(r.get('reason') or r.get('error_type') or r.get('kind') for r in fresh('attempt'))),
            'timings':timings,'errors':{k:len(fresh(k)) for k in ['market_control_error','strategy_error','forum_error']},
            'limits':['Early observed order durations have different peer/order mixes; not causal speedup estimates.',
                      'Initial Saved is not final quality-adjusted Saved; goods need six-hour maturity.']}
    path=ROOT/'logs'/('buyer-v12-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    atomic_json(path,result);print('SOURCE',path)
    print(json.dumps({k:v for k,v in result.items() if k not in ['timings','purchase_evidence','policy']},ensure_ascii=True))
