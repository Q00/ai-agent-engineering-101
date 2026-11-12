"""Inspect actual post-restart Buyer actions without HTTP, LLM or trades."""
from datetime import datetime,timezone
import json
import sqlite3
import time
from r3.state import ROOT,read_json
from autonomous_status import ReadJournal


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json',{})
    start=runtime.get('started_at',now)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,now)
        rules=j.get('market_rules',{});roles=j.get('analytical-role-status',{})
        predictions=[r for r in j.events('prediction',500) if r['_ts']>=start and r.get('role')=='buyer']
        quotes=[r for r in j.events('quote_comparison',200) if r['_ts']>=start]
        buys=[r for r in j.events('purchase',100) if r['_ts']>=start]
        results={r['order']:r for r in j.events('market_result',100) if r['_ts']>=start}
        decisions=[r for r in j.events('buyer_saved_execution',200) if r['_ts']>=start]
        cost=j.get('buyer-cost-scenarios:v22',{})
        selected={r.get('decision'):r.get('chosen') for r in j.events('decision',500) if r['_ts']>=start}
        tradeoffs=[]
        for prediction in predictions:
            chosen=next((c for c in prediction.get('candidates',[]) if c['id']==selected.get(prediction.get('decision'))),{})
            if chosen.get('id')=='accept' and chosen.get('expected_delta',{}).get('saved',0)<0:
                tradeoffs.append({'order':prediction.get('context',{}).get('order',{}).get('order'),
                    'operation':prediction.get('context',{}).get('operation'),'price':chosen.get('price'),
                    'expected_delta':chosen['expected_delta'],'objective_utility':chosen.get('objective_utility')})
        formula_ok=rules.get('buyer_saved_formula')=='margin_share_v36'
        new_predictions=[r for r in predictions if r.get('context',{}).get('buyer_score_formula')=='margin_share_v36']
        quote_rows=[{'order':r['order'],'sku':r['sku'],'ask':r['ask'],'cap':r['assessment']['cap'],
            'saved_reference_cap':r['assessment'].get('saved_reference_cap'),
            'saved_target_is_hard_cap':r['assessment'].get('saved_target_is_hard_cap'),
            'cost_source':(r['assessment'].get('cost_scenario') or {}).get('expected_source')}
            for r in quotes[:12]]
        errors={kind:len([r for r in j.events(kind,50) if r['_ts']>=start]) for kind in
            ['analytical_role_error','buyer_worker_error','cycle_supervisor_error','objective_context_error','strategy_error']}
        cost_role=roles.get('buyer_cost_observer',{})
        research=j.get('research-last-result',{})
        models=[{'role':r.get('role'),'model':r.get('model')} for r in j.events('model_usage',20) if r['_ts']>=start]
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'runtime':runtime,
            'buyer_revision':j.get('buyer_strategy_revision'),'formula_ok':formula_ok,
            'cost_observer':cost_role,'cost_packet_age_seconds':now-cost.get('at',0),
            'research':{k:research.get(k) for k in ('at','trial_review_approved','operating_intents')},
            'active_background_models_since_restart':models,
            'cost_cohorts':{k:r['n'] for k,r in cost.get('groups',{}).items()},
            'post_restart_buyer_predictions':len(predictions),'new_formula_predictions':len(new_predictions),
            'post_restart_quotes':len(quotes),'quotes':quote_rows,
            'accepted_below_saved_average_for_joint_objective':tradeoffs,
            'post_restart_purchases':len(buys),'purchases':[{k:r.get(k) for k in
                ('order','sku','price','paid_at','score_formula','initial_saved_contribution','initial_saved_lower_scenario')}
                for r in buys[:8]],'numeric_decision_seconds':[r.get('seconds') for r in decisions[:12]],
            'purchases_with_verified_result':sum(results.get(r['order'],{}).get('verified') is True for r in buys),
            'errors':errors,'verified':bool(formula_ok and new_predictions and quotes and
                all(r['saved_target_is_hard_cap'] is False for r in quote_rows) and
                cost_role.get('status')=='completed' and cost_role.get('at',0)>=start and not any(errors.values())),
            'economic_superiority_proven':False,'market_transactions_by_this_script':0,'model_calls':0}
    path=ROOT/'logs'/('v22-buyer-live-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','buyer_revision','formula_ok','cost_observer',
        'post_restart_buyer_predictions','new_formula_predictions','post_restart_quotes','post_restart_purchases','errors')}))


if __name__=='__main__':main()
