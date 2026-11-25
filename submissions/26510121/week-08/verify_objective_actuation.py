"""Read-only proof of live research -> compiler -> actual Main choices."""
from datetime import datetime, timezone
import json
import sqlite3
import time
from r3.state import ROOT,read_json


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind):
            return [dict(json.loads(raw),at=at) for at,raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,start,now))]
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        research=events('market_research_published');compiled=events('feedback_policy_created')
        reviewed={r['id']:r for r in compiled if r.get('policy_authority')=='agent_selected'
                  and r.get('patches',{}).get('shop',{}).get('shop_demand_horizon_cycles')}
        main=[r for r in events('feedback_main_choice') if r.get('policy_id') in reviewed and not r.get('preview')]
        counts={role:sum(r['role']==role for r in main) for role in ('shop','buyer')}
        models=events('model_usage');contexts=events('research_context_prepared')
        errors={kind:len(events(kind)) for kind in ('objective_context_error','analytical_role_error',
            'cycle_supervisor_error','cycle_supply_error','buyer_worker_error','market_research_deferred')}
        packet=kv('objective-decision-context');policy=kv('feedback-last-policy')
        result={'at_utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
            'research_publications':len(research),'paid_research_roles':sorted({r.get('role') for r in models}),
            'context_preparation':contexts,'errors_since_restart':errors,
            'current_policy':{k:policy.get(k) for k in ('id','at','status','policy_authority','patches','reasons')},
            'research':[{k:r.get(k) for k in ('at','operating_policy','forum_focus','operating_intents','objective_context_id')} for r in research],
            'matched_main_choices':counts,'main_examples':[r for role in ('shop','buyer')
                for r in [x for x in main if x['role']==role][-4:]],
            'objective_context_id':packet.get('id'),'forum_deliveries':events('forum_delivery'),
            'forum_observations':events('forum_objective_observation'),
            'shop_settled':len(events('shop_settled')),'buyer_purchases':len(events('purchase')),
            'shop_prepared_plans_consumed':len(events('shop_prepared_plan_used')),
            'economic_superiority_or_first_place_proven':False}
    result['verified']=bool(research and all(counts.values()) and packet.get('id')==research[-1].get('objective_context_id')
        and {'market_research_analyst','market_research_critic'}<={r.get('role') for r in models}
        and len(contexts)>=2 and all(r['fact_bytes']<=23000 for r in contexts) and not any(errors.values()))
    path=ROOT/'logs'/('v18-actuation-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('verified','research_publications','matched_main_choices',
          'errors_since_restart','shop_settled','buyer_purchases','shop_prepared_plans_consumed')},indent=2))
    return result['verified']


if __name__=='__main__':
    raise SystemExit(0 if main() else 1)
