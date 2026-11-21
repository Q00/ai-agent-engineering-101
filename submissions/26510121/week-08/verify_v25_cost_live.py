"""Read-only post-release proof: actual role models, unchanged trading state and costs."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,policy,read_json
from autonomous_status import ReadJournal


def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json');start=runtime['started_at']
    expected={}
    for release in ('v24','v25'):
        manifest=read_json(sorted((ROOT/'logs').glob(release+'-source-promotion-*.json'))[-1])
        expected.update({r['path']:r['candidate_sha256'] for r in manifest['files']})
    mismatches=[name for name,sha in expected.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha]
    init=read_json(sorted((ROOT/'logs').glob('v25-cost-initialized-*.json'))[-1])
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,now)
        def recent(kind,n=2000):return [r for r in j.events(kind,n) if start<=r['_ts']<=now]
        calls=[r for r in recent('model_usage') if not r.get('validation_only')]
        model_map={r['role']:r['model'] for r in calls}
        for r in calls:
            role=r['role']
            if role in cfg['background_llm_roles']:
                assert r['model']==cfg['background_llm_roles'][role]['model']
                assert r['reasoning_effort']==cfg['background_llm_roles'][role]['reasoning']
        unchanged=(j.get('shop_revision_epoch')==init['shop_epoch'] and j.get('buyer_revision_epoch')==init['buyer_epoch'])
        errors={k:len(recent(k)) for k in ('cycle_supervisor_error','cycle_supply_error','buyer_worker_error',
            'analytical_role_error','market_research_deferred','capability_deferred','objective_context_error','strategy_error')}
        publications=recent('market_research_published');purchases=recent('purchase');sales=recent('shop_settled')
        results={r['order']:r for r in recent('market_result')}
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'runtime':runtime,'source_mismatches':mismatches,
            'actual_models':model_map,'usage':[{k:r.get(k) for k in ('role','model','reasoning_effort','seconds','priced_token_estimate_usd','usage')} for r in calls],
            'token_priced_since_restart_usd':sum(r.get('priced_token_estimate_usd') or 0 for r in calls),
            'policy_epochs_preserved':unchanged,'shop_revision':j.get('shop_strategy_revision'),'buyer_revision':j.get('buyer_strategy_revision'),
            'actual_research_publications':len(publications),'latest_research':publications[0] if publications else None,
            'post_restart_shop_sales':len(sales),'post_restart_buyer_purchases':len(purchases),
            'verified_buyer_results':sum(results.get(r['order'],{}).get('verified') is True for r in purchases),
            'errors':errors,'astra_excluded':not any('astra' in r['model'] for r in calls),
            'verified':bool(not mismatches and unchanged and publications and
                model_map.get('market_research_analyst')=='gpt-6-luna' and model_map.get('market_research_critic')=='gpt-6.1-terra'
                and not any(errors.values())),
            'market_transactions':0,'model_calls_by_this_script':0,
            'limitations':['Short-run actual usage is not a proven 24-hour bill reduction or economic superiority.']}
    path=ROOT/'logs'/('v25-live-cost-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','actual_models','token_priced_since_restart_usd',
        'policy_epochs_preserved','actual_research_publications','post_restart_shop_sales','post_restart_buyer_purchases',
        'verified_buyer_results','source_mismatches','errors')},ensure_ascii=True))


if __name__=='__main__':main()
