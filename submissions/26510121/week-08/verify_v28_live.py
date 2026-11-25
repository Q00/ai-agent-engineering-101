"""Read-only evidence of v28 processes, scheduled observations and paid execution."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy

def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json',{})
    start=runtime.get('started_at',now)
    promotion=sorted((ROOT/'logs').glob('v28-source-promotion-*.json'))[-1]
    manifest=read_json(promotion)
    bad=[r['path'] for r in manifest['files'] if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    public_path=sorted((ROOT/'logs').glob('v28-live-verification-*.json'))[-1]
    public=read_json(public_path)
    public_at=datetime.fromisoformat(public['at_utc']).timestamp()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(k):
            row=db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()
            return json.loads(row[0]) if row else {}
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=?',(k,start)).fetchone()[0]
            for k in ('reversal_policy_prepared','shop_settled','purchase','buyer_saved_execution',
                'shop_fast_execution','buyer_economic_skip_v28','buyer_economic_routes_paused',
                'market_research_published','market_research_deferred','cycle_supervisor_error','analytical_role_error')}
        heart=kv('cycle-heartbeat');terminal=kv('reversal-policy:v28');ready=kv('research-ready-status')
        epochs={r:kv(r+'_strategy_revision') for r in ('buyer','shop')}
        roles=kv('analytical-role-status');research=kv('research-last-result')
        control=kv('market_control')
        supply={k:[dict(json.loads(data),at=ts) for ts,data in db.execute(
            'SELECT ts,data FROM events WHERE kind=? AND ts>=? ORDER BY ts DESC LIMIT 3',(k,start))]
            for k in ('restock_blocked','restock_decision','shop_prepared_plan_used','supply_execution_status','cycle_supply_error')}
    public_ok=0<=now-public_at<180 and public.get('online') and all(s==200 for s in public['public_statuses'])
    correct_runtime=bool(runtime.get('pid')) and start>promotion.stat().st_mtime and not bad
    live_loop=0<=now-heart.get('at',0)<45 and counts['reversal_policy_prepared']>0
    trades=counts['shop_settled']>0 and counts['purchase']>0
    row={'at':now,'execution_revision':cfg['execution_revision'],'runtime':runtime,'source_mismatches':bad,
        'promotion':promotion.name,'public_verification':public_path.name,'public_ok':bool(public_ok),
        'new_runtime_after_promotion':correct_runtime,'scheduler_live':live_loop,'both_roles_paid_since_restart':trades,
        'counts_since_restart':counts,'heartbeat':heart,'terminal_policy':terminal,'research_readiness':ready,
        'research_at':research.get('at'),'revision_epochs':epochs,'analytical_roles':roles,
        'control':{k:control.get(k) for k in ('at','status','pause_restock','reasons')},'supply':supply,
        'new_llm_roles':0,'economic_superiority_or_first_place_proven':False}
    path=ROOT/'logs'/('v28-execution-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({k:row[k] for k in ('public_ok','new_runtime_after_promotion','scheduler_live','both_roles_paid_since_restart','counts_since_restart','research_readiness')},ensure_ascii=True))
    return public_ok and correct_runtime and live_loop and trades

if __name__=='__main__':raise SystemExit(0 if main() else 1)
