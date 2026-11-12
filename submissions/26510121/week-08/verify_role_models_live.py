"""Read-only v19 model routing and analytic-evidence-to-Main proof."""
from datetime import datetime,timezone
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    cfg=policy();expected={role:p['model'] for role,p in cfg.get('background_llm_roles',{}).items()}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind):
            return [dict(json.loads(raw),at=at) for at,raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,start,now))]
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        usage=[r for r in events('model_usage') if not r.get('validation_only')]
        actual={role:sorted({r['model'] for r in usage if r.get('role')==role}) for role in expected}
        requests=events('background_model_request');research=events('market_research_published')
        compiled=events('feedback_policy_created')
        reviewed={r['id'] for r in compiled if research and r.get('research_at',0)>=research[0]['at']}
        # Compiler receipts carry the research generation even when its chosen
        # numeric parameters agree with the existing baseline.
        research_times={r['at'] for r in research}
        reviewed|={r['id'] for r in compiled if r.get('research_at') in research_times}
        choices=[r for r in events('feedback_main_choice') if r.get('policy_id') in reviewed and not r.get('preview')]
        linked={role:sum(r.get('role')==role for r in choices) for role in ('shop','buyer')}
        errors={kind:len(events(kind)) for kind in ('analytical_role_error','cycle_supervisor_error',
            'cycle_supply_error','buyer_worker_error','background_model_failed','capability_deferred','research_publication_rejected')}
        plan=kv('capability-plan-v19');brief=kv('capability-brief-v19')
        consumers=[r for r in requests if r.get('capability_context_id') and r.get('role') in ('market_research_analyst','market_research_critic')]
        model_ok=bool(expected) and all(actual[k]==[v] for k,v in expected.items())
        no_astra=all(r.get('model')!='gpt-6-astra' for r in usage+requests)
        result={'at_utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
            'expected_models':expected,'actual_models':actual,'all_roles_verified':model_ok,
            'astra_calls_since_restart':sum(r.get('model')=='gpt-6-astra' for r in usage),
            'capability_plan':plan,'capability_brief':brief,'capability_consumers':consumers,
            'research_publications':research,'compiled_research_generations':len(reviewed),
            'matched_main_choices':linked,'main_examples':choices[-6:],
            'errors_since_restart':errors,'usage':usage,'cycle_heartbeat':kv('cycle-heartbeat'),
            'research_last_attempt':kv('research-last-attempt'),
            'current_policy':{k:kv('feedback-last-policy').get(k) for k in ('id','at','research_at','policy_authority','patches','reasons')},
            'economic_superiority_or_first_place_proven':False}
    result['verified']=bool(model_ok and no_astra and plan and consumers and research and all(linked.values()) and not any(errors.values()))
    path=ROOT/'logs'/('v19-role-live-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','all_roles_verified','actual_models',
        'astra_calls_since_restart','matched_main_choices','errors_since_restart')},indent=2))
    return result['verified']


if __name__=='__main__':raise SystemExit(0 if main() else 1)
