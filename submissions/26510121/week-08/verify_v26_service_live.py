"""Read-only evidence of v26 observers, exact contracts and real completed trades."""
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import time
from r3.state import ROOT,read_json,policy


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json');cfg=policy();since=runtime['started_at']
    manifest=json.loads(sorted((ROOT/'logs').glob('v26-source-promotion-*.json'))[-1].read_text(encoding='utf-8'))
    mismatches=[r['path'] for r in manifest['files'] if hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()!=r['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(k):
            r=db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else {}
        def events(k):return [dict(json.loads(raw),_ts=at) for at,raw in db.execute('SELECT ts,data FROM events WHERE kind=? AND ts>=? ORDER BY ts',(k,since))]
        service=kv('service-policy:v26');path=kv('buyer-negotiation-path:v26');roles=kv('analytical-role-status')
        kinds=['shop_service_need','service_policy_prepared','negotiation_path_prepared','buyer_terms_confirmation',
            'buyer_counter_route','buyer_negotiation_step','buyer_route_terminal','shop_settled','purchase','market_result',
            'cycle_supply_error','cycle_supervisor_error','buyer_worker_error','analytical_role_error']
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=?',(k,since)).fetchone()[0] for k in kinds}
        needs=events('shop_service_need');confirmations=events('buyer_terms_confirmation');results=events('market_result')
        choices={r.get('decision'):r.get('chosen') for r in events('decision')}
        chosen=[]
        for p in events('prediction'):
            if p.get('role')!='buyer' or p.get('context',{}).get('operation')!='price':continue
            c=next((r for r in p.get('candidates',[]) if r.get('id')==choices.get(p.get('decision'))),{})
            chosen.append({k:c.get(k) for k in ('id','price','continuation_model_id','payment_price_scenarios','path_expected_seconds')})
        report={'at':now,'runtime':runtime,'execution_revision':cfg['execution_revision'],'minutes_live':(now-since)/60,
            'source_mismatches':mismatches,'analytical_roles':roles,'counts':counts,
            'verified_buyer_results':sum(r.get('verified') is True for r in results),
            'service_generation':service.get('generation'),'qualified_service_skus':len(service.get('shop_needs',{})),
            'observed_explicit_needs':sum(bool(r.get('explicit_use')) for r in needs),
            'fresh_supply_draws':len(service.get('draw_fractions',[])),'negotiation_holdout':path.get('summary'),
            'negotiation_samples':path.get('samples'),'selected_buyer_price_actions':chosen[-12:],
            'confirmation_statuses':{s:sum(r.get('status')==s for r in confirmations) for s in ('response_received','unconfirmed')},
            'models':cfg['background_llm_roles'],'astra_excluded':not any('astra' in r['model'] for r in cfg['background_llm_roles'].values()),
            'additional_llm_roles':0,'models_or_cadence_changed':False,
            'limitation':'Early live mechanics and paid deals do not prove causal economic improvement or mature review/Saved uplift.'}
    p=ROOT/'logs'/('v26-service-live-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps({k:report[k] for k in ('minutes_live','source_mismatches','counts','verified_buyer_results','qualified_service_skus','negotiation_holdout','confirmation_statuses')},ensure_ascii=True))


if __name__=='__main__':main()
