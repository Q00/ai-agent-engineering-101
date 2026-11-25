"""Isolated frozen-fact latency validation and explicitly requested paid smoke.

Reads production market evidence. Benchmarks never transact. Paid smoke records
usage against the real shared budget but publishes proposals only in isolation.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v17-stage-20261111'
sys.path.insert(0,str(STAGE))


def save(kind,value):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(value,ensure_ascii=False,indent=2))
    print('SOURCE',path);print(json.dumps(value,ensure_ascii=True,indent=2))


def smoke():
    from r3.state import Journal,policy
    from r3.model import api_key
    from r3.research import call,publish
    cfg=policy();now=time.time()
    journal=Journal(STAGE/'.market'/('smoke-'+str(int(now))))
    journal.put('market_rules',{'sha256':'synthetic-smoke','compatible':True})
    evidence=['rules:synthetic-smoke','synthetic:fixed-market']
    facts={'evidence_ids':evidence,'source':'synthetic fixtures, NOT live market observations',
           'market':{'windows':{'20':{'trades':100,'amount':1000000}}},
           'framework':{'issues':['price_frontier_no_overlap'],'mature_paid':9,'required_paid':20},
           'parameters':{'buyer_probe_percent':8,'buyer_gap_power':2,'buyer_diverse_quotes':False,'shop_service_level':.9}}
    facts['operating_feedback']={'source':'synthetic fixed facts, not current market state',
        'regime':'normal','facts':{'stock_units':2,'own_paid_sales_60min':20,'own_gross_margin_60min':1000000,
            'stockouts_60min':10,'restock_block_reasons':{'market_shop_not_discoverable':3},
            'buyer_segments_60min':{'goods':{'closed':100,'paid':6,'conversion':.06}}},
        'constraints':['saved_price_cap_unchanged','fresh_supply_guards_required','full_refund_reserve'],
        'execution_feedback':{'main_choices_by_policy':{},'buyer_closed_results_by_policy':{}}}
    facts['current_operating_policy']={'cover_cycles':1,'initial_batch_limit':6,'frontier_min_orders':8,'recheck_seconds':120}
    spending=Journal(ROOT/'.market')
    analyst=call(spending,cfg,facts,evidence,'analyst',key=api_key(root=ROOT))
    critic=call(spending,cfg,facts,evidence,'critic',proposal=analyst,key=api_key(root=ROOT))
    result=publish(journal,cfg,facts,analyst,critic,'synthetic-smoke')
    save('v17-live-model-smoke',{'at_utc':datetime.now(timezone.utc).isoformat(),'model':cfg['research_model'],
        'calls':2,'schema_valid':bool(result),'independent_critic':True,'published_only_to_isolated_journal':True,
        'market_transactions':0,'protected_production_strategy_writes':0,
        'analyst_summary':analyst['summary'],'critic_summary':critic['summary'],
        'operating_intents':result.get('operating_intents') if result else None,
        'agent_selected_policy':result.get('operating_policy') if result else None,
        'new_economic_rules_promoted':False})
    return bool(result)


def benchmark():
    from r3.state import Journal,atomic_json,policy
    from r3 import adaptive, market_memory, contracts, buyer_quality, long_memory
    from r3.prewindow import prepare
    from r3.prepared import update,get
    from r3.buyer_routes import context
    from r3.buyer_execution import route_context
    import restock
    source=sorted((STAGE/'.market').glob('parity-source-*.json'))[-1]
    data=json.loads(source.read_text(encoding='utf-8'));now=data['cutoff']
    journal=Journal(STAGE/'.market'/('benchmark-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')))
    for name,value in data['files'].items():atomic_json(journal.md/name,value)
    with journal.connect() as db:
        for key,raw in data['kv'].items():db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',(key,raw))
        for kind,rows in data['events'].items():
            db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',[(eid,kind,at,role,raw) for eid,at,role,raw in rows])
    control=dict(journal.get('market_control',{}),at=now,pause_restock=False,capital_fraction=1,status='observe_and_trade')
    journal.put('market_control',control);journal.put('board_checked_at',now)
    board=journal.snapshot('board.json',{})
    for row in board.get('shops',[]):
        if row.get('name')=='seochanit':row['alive']=True
    atomic_json(journal.md/'board.json',board)
    inv=journal.snapshot('inventory.json',{})
    inv['restock']={'open':True,'opens_at':now-1,'closes_at':now+180}
    atomic_json(journal.md/'inventory.json',inv)
    cfg=policy();original_clock=time.time;original_policy=adaptive.policy
    rows={}
    try:
        time.time=lambda:now
        adaptive.policy=lambda:dict(cfg,market_cycle_enabled=False)
        start=time.perf_counter();baseline=restock.plan(journal);rows['restock_baseline_seconds']=time.perf_counter()-start
        adaptive.policy=lambda:cfg
        effective,strategy=adaptive.effective(journal,'shop')
        start=time.perf_counter();packet=prepare(journal,effective,strategy,now);rows['background_plan_seconds']=time.perf_counter()-start
        start=time.perf_counter();candidate=restock.plan(journal);rows['restock_prepared_seconds']=time.perf_counter()-start
        rows.update(restock_same_output=baseline==candidate,restock_lines=len(baseline['buy']),prepared_plan_used=bool(journal.events('shop_prepared_plan_used')))
        order=next((r['order'] for r in data['cases'] if r['role']=='buyer'),{'kind':'goods'})
        if order.get('kind')=='food':journal.add('buyer_order',dict(order),ts=now)
        start=time.perf_counter();before={'routes':context(journal,now),'execution':route_context(journal,now,order)}
        rows['buyer_routes_baseline_seconds']=time.perf_counter()-start
        update(journal,cfg,now)
        start=time.perf_counter();after=get(journal,order,cfg,now);rows['buyer_routes_prepared_seconds']=time.perf_counter()-start
        rows['buyer_routes_identical']=bool(after) and before=={k:after[k] for k in ('routes','execution')}
    finally:
        time.time=original_clock;adaptive.policy=original_policy
    report={'at_utc':datetime.now(timezone.utc).isoformat(),'frozen_source':str(source.relative_to(ROOT)),**rows,
        'market_transactions':0,'model_calls':0,'scope':'One frozen replay; connectivity is a controlled online scenario. No causal economic lift claim.'}
    save('v17-preparation-benchmark',report)
    return rows['restock_same_output'] and rows['buyer_routes_identical'] and rows['prepared_plan_used']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live-models',action='store_true');args=p.parse_args()
    try:
        result=smoke() if args.live_models else benchmark()
    except Exception as exc:
        save('v17-validation-failure',{'mode':'live-models' if args.live_models else 'benchmark',
            'error_type':type(exc).__name__,'reason':str(exc) if type(exc).__name__=='ModelError' else 'see immutable console evidence',
            'market_transactions':0})
        raise SystemExit(1)
    raise SystemExit(0 if result else 1)
