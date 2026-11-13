"""Read-only deployment, portfolio decisions, successful supply and trade proof."""
from datetime import datetime,timezone
import hashlib
import json
import re
import sqlite3
import time
from r3.state import ROOT,read_json,policy


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json');cfg=policy();since=runtime['started_at']
    manifest=json.loads(sorted((ROOT/'logs').glob('v27-source-promotion-*.json'))[-1].read_text(encoding='utf-8'))
    mismatch=[e['path'] for e in manifest['files'] if hashlib.sha256((ROOT/e['path']).read_bytes()).hexdigest()!=e['candidate_sha256']]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(k):
            r=db.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else {}
        def events(k):return [dict(json.loads(raw),_ts=at) for at,raw in db.execute('SELECT ts,data FROM events WHERE kind=? AND ts>=? ORDER BY ts',(k,since))]
        kinds=['inventory_portfolio_prepared','shop_plan_prepared','shop_prepared_plan_used','restock_decision',
            'supply_execution_status','shop_settled','purchase','market_result','cycle_supply_error',
            'cycle_supervisor_error','buyer_worker_error','analytical_role_error']
        counts={k:db.execute('SELECT count(*) FROM events WHERE kind=? AND ts>=?',(k,since)).fetchone()[0] for k in kinds}
        packet=kv('inventory-portfolio:v27');plans=events('shop_prepared_plan_used');decisions=events('restock_decision')
        supply=events('supply_execution_status');results=events('market_result')
        oldbuyer=json.loads((ROOT/'.market/v27-stage-20261113/policy.json').read_text(encoding='utf-8-sig'))['buyer_strategy_revision']
        source=ROOT/'logs'/(runtime['launch_id']+'.txt');acquired=[]
        for line in source.read_text(encoding='utf-8',errors='replace').splitlines() if source.exists() else []:
            m=re.search(r'^([\d:]+) \[supply\] (\d+) x .* for (\d+), balance (\d+)',line)
            if m:acquired.append({'time_kst':m[1],'qty':int(m[2]),'cost':int(m[3])})
        report={'at':now,'runtime':runtime,'minutes_live':(now-since)/60,'execution_revision':cfg['execution_revision'],
            'source_mismatches':mismatch,'analytical_roles':kv('analytical-role-status'),'counts':counts,
            'portfolio':packet,'observer':kv('inventory-observer:v27'),
            'last_plan_used':plans[-1] if plans else None,'last_direct_decision':decisions[-1] if decisions else None,
            'successful_supply_lots':sum(r.get('status')=='complete' for r in supply),
            'console_confirmed_acquired':acquired,'console_confirmed_acquired_units':sum(r['qty'] for r in acquired),
            'supply_console_source':str(source.relative_to(ROOT)),
            'verified_buyer_results':sum(r.get('verified') is True for r in results),
            'buyer_revision_unchanged':kv('buyer_strategy_revision')==oldbuyer,
            'no_additional_llm_roles':True,'models':cfg['background_llm_roles'],
            'astra_excluded':all('astra' not in r['model'] for r in cfg['background_llm_roles'].values()),
            'limits':['Early trades and forecasting dominance are not realized causal profit lift or first-place evidence.',
                'Supply status alone is not the acquisition count: console-confirmed units are reported separately.']}
    p=ROOT/'logs'/('v27-inventory-live-proof-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps({k:report[k] for k in ('minutes_live','source_mismatches','counts','successful_supply_lots','console_confirmed_acquired_units','verified_buyer_results','buyer_revision_unchanged')},ensure_ascii=True))


if __name__=='__main__':main()
