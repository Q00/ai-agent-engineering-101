"""Read-only v20 configuration, source and background-demand-context proof."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy
from r3.shop_growth import inventory_limit,target_stock


def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    inv=read_json(ROOT/'.market/inventory.json',{})
    manifest_path=sorted((ROOT/'logs').glob('v20-source-promotion-*.json'))[-1]
    manifest=read_json(manifest_path)
    mismatches=[r['path'] for r in manifest['files']
                if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        kinds=['market_research_published','feedback_main_choice','shop_settled','purchase','supply_execution_status',
               'analytical_role_error','objective_context_error','cycle_supervisor_error','cycle_supply_error',
               'buyer_worker_error','market_research_deferred']
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=? AND ts<=?',(k,start,now)).fetchone()[0] for k in kinds}
        context=kv('objective-decision-context');diagnostic=(context.get('shop') or {}).get('demand_diagnostics',{})
        heartbeat=kv('cycle-heartbeat');operating=kv('feedback-last-policy')
    flags=(cfg.get('shop_inventory_caps_enabled') is False and inventory_limit(cfg) is None
           and target_stock(1000,8,cfg)>20 and target_stock(1000,0,cfg,True)>6)
    fresh_diagnostic=(context.get('at',0)>=start and 0<=now-context.get('at',0)<180
        and diagnostic.get('inventory_unit_caps_enabled') is False and diagnostic.get('total_unit_ceiling') is None)
    errors={k:counts[k] for k in kinds if k.endswith('_error') or k=='market_research_deferred'}
    result={'at_utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
        'execution_revision':cfg.get('execution_revision'),'source_manifest':str(manifest_path.relative_to(ROOT)),
        'source_mismatches':mismatches,'inventory_unit_caps_removed':flags,
        'inventory_ceiling':inventory_limit(cfg),'physical_units':len(inv.get('units',[])),
        'legal_max_units_per_supply_post':20,'current_supply_window':inv.get('restock'),
        'fresh_background_demand_diagnostic':fresh_diagnostic,'diagnostic':diagnostic,
        'counts_since_restart':counts,'errors_since_restart':errors,'heartbeat':heartbeat,
        'operating_policy':{k:operating.get(k) for k in ('status','policy_authority','research_at','patches','reasons')},
        'new_restock_execution_observed':counts['supply_execution_status']>0,
        'model_calls_by_this_verifier':0,'market_transactions_by_this_verifier':0,
        'economic_superiority_proven':False}
    result['verified']=bool(flags and not mismatches and fresh_diagnostic and now-heartbeat.get('at',0)<30 and not any(errors.values()))
    path=ROOT/'logs'/('v20-live-cap-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','inventory_unit_caps_removed','inventory_ceiling','physical_units',
        'fresh_background_demand_diagnostic','counts_since_restart','errors_since_restart')},indent=2))
    return result['verified']


if __name__=='__main__':raise SystemExit(0 if main() else 1)
