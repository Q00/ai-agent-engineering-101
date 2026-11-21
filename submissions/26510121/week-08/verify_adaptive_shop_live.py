"""Read-only verification of deployed v21 rates and protection periods."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy
from r3.demand_policy import KEY,REVISION


def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    source=sorted((ROOT/'logs').glob('v21-source-promotion-*.json'))[-1];manifest=read_json(source)
    mismatch=[r['path'] for r in manifest['files'] if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        p=kv(KEY);merchant=kv('merchant_context_v5');roles=kv('analytical-role-status');heartbeat=kv('cycle-heartbeat')
        kinds=['shop_demand_calibrated','analytical_role_error','merchant_context_error','cycle_supervisor_error',
               'objective_context_error','market_research_deferred','shop_settled','purchase']
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=? AND ts<=?',(k,start,now)).fetchone()[0] for k in kinds}
        ids={json.loads(raw).get('id') for raw, in db.execute(
            'SELECT data FROM events WHERE kind=? AND ts>=? AND ts<=?',('shop_demand_calibrated',now-180,now))}
    fresh=(p.get('revision')==REVISION and p.get('at',0)>=start and 0<=now-p.get('at',0)<180 and now<p.get('expires_at',0))
    consumed=merchant.get('demand_policy_id') in ids and not merchant.get('demand_policy_id')=='uncalibrated-neutral'
    factors=[r['posterior_multiplier'] for r in p.get('skus',{}).values() if r['posterior_multiplier'] is not None]
    hours=[r['coverage_hours'] for r in p.get('skus',{}).values()]
    errors={k:v for k,v in counts.items() if k.endswith('_error') or k=='market_research_deferred'}
    result={'at_utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,'source_manifest':str(source.relative_to(ROOT)),
        'source_mismatches':mismatch,'adaptive_shop_enabled':cfg.get('shop_adaptive_demand_enabled'),
        'inventory_unit_caps_disabled':cfg.get('shop_inventory_caps_enabled') is False,
        'fresh_calibration':fresh,'merchant_consumed_calibration':consumed,'observer':roles.get('shop_demand_observer'),
        'calibration_id':p.get('id'),'merchant_calibration_id':merchant.get('demand_policy_id'),
        'summary':p.get('summary'),'multiplier_range':[min(factors),max(factors)] if factors else None,
        'coverage_hours_range':[min(hours),max(hours)] if hours else None,
        'counts_since_restart':counts,'errors_since_restart':errors,'heartbeat':heartbeat,
        'economic_superiority_proven':False}
    result['verified']=bool(cfg.get('shop_adaptive_demand_enabled') and fresh and consumed and not mismatch and not any(errors.values()))
    path=ROOT/'logs'/('v21-adaptive-live-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','fresh_calibration','merchant_consumed_calibration',
        'summary','multiplier_range','coverage_hours_range','errors_since_restart')}))


if __name__=='__main__':raise SystemExit(0 if main() else 1)
