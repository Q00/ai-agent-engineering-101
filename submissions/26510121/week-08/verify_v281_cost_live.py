"""Read-only post-release proof; skipped reviews are distinct from new approvals."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy

def main():
    now=time.time();cfg=policy();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    public_path=sorted((ROOT/'logs').glob('v281-live-verification-*.json'))[-1];public=read_json(public_path)
    promotion=sorted((ROOT/'logs').glob('v281-source-promotion-*.json'))[-1];manifest=read_json(promotion)
    mismatches=[r['path'] for r in manifest['files'] if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(k):
            row=db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()
            return json.loads(row[0]) if row else {}
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=?',(k,start)).fetchone()[0]
            for k in ('shop_settled','purchase','model_usage','research_critic_skipped','market_research_published',
                'cycle_supervisor_error','analytical_role_error')}
        heartbeat=kv('cycle-heartbeat');entries=kv('research-reviewed-proposals:v281');ready=kv('research-ready-status')
        epoch={r:kv(r+'_revision_epoch') for r in ('shop','buyer')}
    old=read_json(sorted((ROOT/'logs').glob('v28-initialized-*.json'))[-1])
    preserved=all(epoch[r]==old[r+'_revision_epoch'] for r in epoch)
    public_recent=0<=now-datetime.fromisoformat(public['at_utc']).timestamp()<180
    good=public_recent and public.get('online') and not mismatches and now-heartbeat.get('at',0)<45 and preserved
    result={'at':now,'runtime':runtime,'execution_revision':cfg['execution_revision'],'public_ok':bool(public_recent and public.get('online')),
        'source_mismatches':mismatches,'strategy_epochs_preserved':preserved,
        'cost_cap_usd':cfg['daily_api_budget_usd'],'active_gate':cfg.get('research_noop_gate_enabled'),
        'heartbeat':heartbeat,'counts_since_restart':counts,'review_cache_entries':len(entries),'research_readiness':ready,
        'live_savings_observed_as_skipped_calls':counts['research_critic_skipped'],
        'actual_invoice_savings_or_economic_superiority_proven':False}
    p=ROOT/'logs'/('v281-execution-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps({k:v for k,v in result.items() if k not in ('heartbeat','runtime')},ensure_ascii=True))
    return good and counts['shop_settled']>0 and counts['purchase']>0

if __name__=='__main__':raise SystemExit(0 if main() else 1)
