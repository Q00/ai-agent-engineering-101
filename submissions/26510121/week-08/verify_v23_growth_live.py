"""Read-only post-deploy execution evidence; no models, registrations or payments."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy
from autonomous_status import ReadJournal


def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    promotion=sorted((ROOT/'logs').glob('v23-source-promotion-*.json'))[-1]
    manifest=read_json(promotion);mismatch=[r['path'] for r in manifest['files'] if
        hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,now)
        def recent(kind,n=500):return [r for r in j.events(kind,n) if r['_ts']>=start]
        roles=j.get('analytical-role-status',{});learning=j.get('policy-learning:v23',{})
        predictions=recent('prediction',400);shop_contract=[];buyer_counter=[]
        for r in predictions:
            for c in r.get('candidates',[]):
                if r.get('role')=='shop' and (c.get('evidence') or {}).get('buyer_saved_contract'):
                    shop_contract.append({'id':c['id'],'warranty':c.get('warranty_hours'),
                        'scenario':c['evidence']['buyer_saved_contract']})
                if r.get('role')=='buyer' and 'shrunk observed' in c.get('success_prior',''):
                    buyer_counter.append({'id':c['id'],'price':c.get('price'),'probability':c.get('completion_probability')})
        purchases=recent('purchase');sales=recent('shop_settled');results={r['order']:r for r in recent('market_result')}
        errors={k:len(recent(k)) for k in ('market_control_error','cycle_supervisor_error','cycle_supply_error',
            'buyer_worker_error','analytical_role_error','objective_context_error','strategy_error')}
        framework=j.get('framework-plan',{});buyer_epoch=j.get('buyer_revision_epoch',0)
        scopes=[r.get('diagnostic_since',0) for r in framework.get('segments',{}).values()]
        role=roles.get('policy_learning_observer',{})
        active_models=[v['model'] for v in cfg['background_llm_roles'].values()]+[cfg['actor_model'],cfg['critic_model']]
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'runtime':runtime,
            'shop_revision':j.get('shop_strategy_revision'),'buyer_revision':j.get('buyer_strategy_revision'),
            'source_mismatches':mismatch,'astra_excluded':not any('astra' in m.lower() for m in active_models),
            'active_models':sorted(set(active_models)),'roles':roles,
            'learning_age_seconds':now-learning.get('at',0),
            'qualified_service_skus':sum(r['qualified'] for r in learning.get('service',{}).values()),
            'counter_samples':sum(r['n'] for g in learning.get('counter_completion_by_kind',{}).values() for r in g.values()),
            'shop_contract_candidate_examples':shop_contract[:6],'buyer_counter_candidate_examples':buyer_counter[:6],
            'post_restart_shop_sales':len(sales),'post_restart_buyer_purchases':len(purchases),
            'verified_buyer_results':sum(results.get(p['order'],{}).get('verified') is True for p in purchases),
            'new_scope_diagnostics':bool(scopes) and all(v>=buyer_epoch for v in scopes),
            'framework_segments':framework.get('segments'),
            'errors':errors,'observed_supply_units':sum(r.get('qty',0) for r in recent('supply_execution_status') if r.get('status')=='complete'),
            'verified':bool(not mismatch and role.get('status')=='completed' and role.get('at',0)>=start and
                shop_contract and predictions and not any(errors.values())),
            'economic_superiority_proven':False,'first_place_proven':False,'market_transactions_by_this_script':0,'model_calls':0}
    path=ROOT/'logs'/('v23-live-growth-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','source_mismatches','astra_excluded','post_restart_shop_sales',
        'post_restart_buyer_purchases','verified_buyer_results','qualified_service_skus','counter_samples','new_scope_diagnostics','errors')}))


if __name__=='__main__':main()
