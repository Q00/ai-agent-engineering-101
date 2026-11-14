"""Read-only public/state proof for the deployed market-cycle runtime."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
import httpx
from r3.state import ROOT,read_json,policy
from r3.lifecycle import lease


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json',{});health=read_json(ROOT/'.market/shop-health.json',{})
    cfg=policy();url=health.get('url','').rstrip('/')
    def get(url):
        try:
            r=httpx.get(url,timeout=8,trust_env=False,headers={'Cache-Control':'no-cache'})
            return {'status':r.status_code,'data':r.json() if r.status_code==200 else {}}
        except Exception as exc:return {'error':type(exc).__name__,'data':{}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(get,[url+'/.well-known/agent-card.json',url+'/.well-known/acp.json',
                                  'https://market.ourolabs.ai/api/shops','https://market.ourolabs.ai/api/board']))
    board=results[3]['data'];own={role:next((r for r in board.get(role,[]) if r.get('name')=='seochanit'),{}) for role in ('shops','buyers')}
    listed=any(r.get('name')=='seochanit' and r.get('url','').rstrip('/')==url for r in results[2]['data'].get('shops',[]))
    online=all(r.get('status')==200 for r in results) and listed and all(r.get('alive') is True for r in own.values()) and (own['shops'].get('card') or {}).get('url','').rstrip('/')==url
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        kinds=['market_research_published','market_research_deferred','research_skill_execution','research_strategy_queued',
               'research_main_feedback','buyer_prepared_published','buyer_prepared_used','shop_plan_prepared','shop_prepared_plan_used',
               'cycle_supervisor_error','cycle_supply_error','buyer_worker_error','shop_settled','purchase','model_usage']
        if cfg.get('objective_feedback_enabled'):
            kinds+=['feedback_policy_created','feedback_main_choice','quality_holdout_evaluation']
        if cfg.get('agent_policy_selection_enabled'):kinds+=['analytical_role_error']
        if cfg.get('service_contract_v26_enabled'):
            kinds+=['shop_service_need','service_policy_prepared','negotiation_path_prepared',
                'buyer_terms_confirmation','buyer_counter_route','buyer_negotiation_step','buyer_route_terminal']
        if cfg.get('inventory_portfolio_v27_enabled'):kinds+=['inventory_portfolio_prepared']
        if cfg.get('reversal_v28_enabled'):kinds+=['reversal_policy_prepared','buyer_economic_skip_v28','buyer_economic_routes_paused']
        if cfg.get('objective_decision_enabled'):
            kinds+=['objective_alternatives_prepared','objective_context_error','research_context_prepared',
                    'buyer_forum_policy_choice','forum_objective_observation']
        counts={kind:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=?',(kind,runtime.get('started_at',now))).fetchone()[0] for kind in kinds}
        models=[json.loads(raw) for raw, in db.execute('SELECT data FROM events WHERE kind=? AND ts>=?',('model_usage',runtime.get('started_at',now)))]
        heartbeat=kv('cycle-heartbeat');meta=kv('cycle-meta-state');last_research=kv('research-last-result')
        stats=kv('cycle-last-preparation')
        operating=kv('feedback-last-policy');quality=kv('quality-holdout-summary')
    try:
        with lease(ROOT/'.market/shop-recovery-guard.lock'):guard_active=False
    except RuntimeError:guard_active=True
    release='v26' if cfg.get('service_contract_v26_enabled') else 'v25' if cfg.get('llm_cost_v25_enabled') else 'v24' if cfg.get('shop_reputation_v24_enabled') else 'v23' if cfg.get('book_growth_v23_enabled') else 'v22' if cfg.get('buyer_patch36_enabled') else 'v21' if cfg.get('shop_adaptive_demand_enabled') else 'v20' if cfg.get('shop_inventory_caps_enabled') is False else 'v19' if cfg.get('capability_llm_enabled') else 'v18' if cfg.get('objective_decision_enabled') else 'v17' if cfg.get('agent_policy_selection_enabled') else 'v16' if cfg.get('objective_feedback_enabled') else 'v15'
    if cfg.get('inventory_portfolio_v27_enabled'):release='v27'
    if cfg.get('execution_revision','').startswith('v27.1-'):release='v271'
    if cfg.get('reversal_v28_enabled'):release='v28'
    promotion=sorted((ROOT/'logs').glob(release+'-source-promotion-*.json'))[-1]
    manifest=read_json(promotion,{})
    mismatches=[r['path'] for r in manifest['files'] if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    report={'at_utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,'url':url,
            'public_statuses':[r.get('status') for r in results],'public_errors':[r.get('error') for r in results],
            'listed':listed,'online':online,'own':{role:{k:r.get(k) for k in ('alive','all','book','saved','deals','forum')} for role,r in own.items()},
            'source_mismatches':mismatches,'counts_since_restart':counts,'cycle_heartbeat':heartbeat,'last_preparation':stats,
            'meta':meta,'last_research':last_research,'model_usage':[{'role':r.get('role'),'model':r.get('model'),'usage':r.get('usage'),'estimated_usd':r.get('estimated_usd')} for r in models],
            'operating_policy':{k:operating.get(k) for k in ('at','id','status','regime','policy_authority','source_times','patches','reasons','execution_feedback')},
            'quality_holdout':quality,
            'objective_context':kv('objective-decision-context') if cfg.get('objective_decision_enabled') else {},
            'analytical_roles':kv('analytical-role-status'),
            'terminal_contract_policy':kv('reversal-policy:v28') if cfg.get('reversal_v28_enabled') else {},
            'research_ready_status':kv('research-ready-status') if cfg.get('reversal_v28_enabled') else {},
            'recovery_request':read_json(ROOT/'.market/recovery-request.json',{}),
            'recovery_response':read_json(ROOT/'.market/recovery-response.json',{}),
            'market_status_verified':all(r.get('status')==200 for r in results[2:]),
            'independent_recovery_guard_active':guard_active,'primary_recovery':health,
            'cost_cap_usd':cfg.get('daily_api_budget_usd'),'budget_mode':cfg.get('research_budget_mode'),
            'daily_usage':read_json(ROOT/'.market/model-usage-daily.json',{}),
            'limits':['Short observations are not causal economic improvement or a first-place guarantee.',
                      'A registry/board snapshot is not sustained availability proof.']}
    path=ROOT/'logs'/(release+'-live-verification-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:v for k,v in report.items() if k not in ('limits','daily_usage','meta','last_research')},ensure_ascii=True,indent=2))
    return online and not mismatches and now-heartbeat.get('at',0)<30 and guard_active


if __name__=='__main__':raise SystemExit(0 if main() else 1)
