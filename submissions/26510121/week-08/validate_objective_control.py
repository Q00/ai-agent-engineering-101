"""Frozen real-evidence validation; optional paid research publishes in isolation.

Never registers, trades, votes or changes production policies. Paid calls reuse
the authorized credential in memory and record costs in the production journal.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v18-stage-20261111'
sys.path.insert(0,str(STAGE))


def save(kind,row):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    return path


def replay():
    from r3.state import Journal, atomic_json, read_json, policy
    from r3 import research, feedback
    from r3.objective_context import curate
    now=time.time()
    names=['inventory.json','catalog.json','board.json','market_stock_counts.json','market-control.json',
           'shop-health.json','forum.json','operating-policy-v16.json','shop-plan-v15.json']
    files={n:read_json(ROOT/'.market'/n,{}) for n in names}
    kv_names=['market_rules','public_feed_caught_up_at','public_ledger_seq','board_checked_at','market_control',
        'merchant_context_v5','merchant_stock_timeline','cycle-market-analytics','cycle-meta-state',
        'research-last-result','framework-plan','framework-arena','research-buyer-candidates','adaptive:shop',
        'feedback-last-policy','quality-holdout-summary','autonomy-v14:context','quality_risk_v4']
    kinds=['public_settle','public_broken','shop_settled','stockout','buyer_order','market_result','purchase',
        'outcome','customer_visit','shop_opportunity','customer_outcome','forum_observation','forum_delivery',
        'forum_objective_observation','shop_prepared_plan_used','supply_execution_status','restock_blocked',
        'buyer_contract_result','attempt','quote_comparison','feedback_main_choice','feedback_market_observation',
        'shop_review_observed','framework_order','shop_quote']
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');kv={}
        for key in kv_names:
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            if row:kv[key]=row[0]
        events={kind:db.execute('SELECT id,ts,role,data FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT 20000',
                               (kind,now)).fetchall() for kind in kinds}
    source=save('v18-frozen-evidence',{'cutoff':now,'files':files,'kv':kv,'events':events,
        'source_db':'.market/r3.sqlite3','read_only':True,'market_transactions':0})
    journal=Journal(STAGE/'.market'/('replay-'+str(int(now))))
    for name,value in files.items():atomic_json(journal.md/name,value)
    with journal.connect() as db:
        db.executemany('INSERT OR REPLACE INTO kv VALUES(?,?)',kv.items())
        for kind,rows in events.items():
            db.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',[(eid,kind,at,role,raw) for eid,at,role,raw in rows])
    cfg=policy();requests=[]
    class Fake:
        def post(self,url,**kw):
            body=kw['json'];requests.append(body)
            evidence=json.loads(body['input'])['valid_evidence_ids']
            value={'summary':'Synthetic contract handoff only; not an economic claim.',
                'hypotheses':['Compare supplied numerical alternatives.'],'evidence_ids':evidence[:3],
                'skills':['stock_coverage','buyer_price_gap'],'skill_steps':['measure','diagnose','validate_facts'],
                'buyer_probe_percent':8,'buyer_gap_power':2,'buyer_diverse_quotes':False,'shop_service_level':.9,
                'shop_plan':'diversified','forum_focus':'acceptable_alternatives',
                'operating_actions':['stock_coverage','buyer_price_gap','buyer_information'],
                'operating_policy':{'cover_cycles':3,'initial_batch_limit':6,'frontier_min_orders':8,'recheck_seconds':60},
                'approve_trial':False}
            return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':1,'output_tokens':1},
                'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}]})
    journal.put('research-last-attempt',{})
    # Ensure captured analytics refer to this frozen evidence generation. This
    # is an isolated replay, never a freshness assertion about live execution.
    journal.put('board_checked_at',now);journal.put('public_feed_caught_up_at',now)
    research.analytics(journal,now)
    started=time.perf_counter();original_clock=time.time
    try:
        time.time=lambda:now
        result=research.update(journal,cfg,now,client=Fake(),key='synthetic-validation')
        packet=feedback.update(journal,cfg,now,force=True)
    finally:
        time.time=original_clock
    assert result and len(requests)==2
    facts=json.loads(requests[0]['input'])['facts']
    assert 'objective_decisions' in facts and 'alternatives' in facts['objective_decisions']['shop']
    assert facts['objective_decisions']['shop']['execution_permitted'] is False
    assert all(len(r['input'].encode())<32000 for r in requests)
    assert result['trial_review_approved'] is False
    assert packet['patches']['buyer']['buyer_forum_focus']=='acceptable_alternatives'
    # Frozen real post-start evidence qualifies the existing numeric coverage gate.
    assert packet['patches']['shop']['shop_demand_horizon_cycles'] in (1.5,3)
    path=save('v18-objective-replay',{'source':str(source.relative_to(ROOT)), 'facts':facts,
        'isolated_journal':str(journal.md.relative_to(ROOT)), 'source_cutoff':now,
        'seconds':time.perf_counter()-started,'calls':2,'synthetic_model':True,'market_transactions':0,
        'research_packet':result,'compiled_patches':packet['patches'],
        'request_bytes':[len(r['input'].encode()) for r in requests],
        'context_metrics':journal.events('research_context_prepared',2),
        'shop_alternatives':facts['objective_decisions']['shop']['alternatives'],
        'main_policy_handoff_verified':True,'economic_superiority_proven':False})
    print('SUMMARY',json.dumps({'requests':len(requests),'bytes':[len(r['input'].encode()) for r in requests],
        'seconds':time.perf_counter()-started,'compiled_patches':packet['patches']}))
    return path


def smoke():
    from r3.state import Journal,policy,read_json
    from r3.model import api_key
    from r3.research import call,publish
    source=sorted((ROOT/'logs').glob('v18-objective-replay-*.json'))[-1]
    data=read_json(source);facts=data['facts'];evidence=facts['evidence_ids'];cfg=policy()
    spending=Journal(ROOT/'.market')
    class DiagnosticClient:
        def post(self,url,**kwargs):
            response=httpx.post(url,**kwargs)
            meta={'status':response.status_code}
            if response.status_code==200:
                result=response.json();meta['completion_status']=result.get('status')
                meta['output_types']=[o.get('type') for o in result.get('output',[])]
                meta['message_phases']=[o.get('phase') for o in result.get('output',[]) if o.get('type')=='message']
                raw=''.join(p.get('text','') for o in result.get('output',[]) if o.get('type')=='message'
                    for p in o.get('content',[]) if p.get('type')=='output_text')
                meta['text_chars']=len(raw)
                try:
                    value=json.loads(raw)
                    meta['field_types']={k:type(v).__name__ for k,v in value.items()} if isinstance(value,dict) else type(value).__name__
                except json.JSONDecodeError as exc:
                    meta['json_error']={'reason':exc.msg,'position':exc.pos}
                    rest=raw;objects=[]
                    for _ in range(4):
                        try:
                            value,stop=json.JSONDecoder().raw_decode(rest.lstrip())
                            objects.append({'type':type(value).__name__,
                                'fields':sorted(value) if isinstance(value,dict) else None,
                                'fingerprint':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()})
                            rest=rest.lstrip()[stop:]
                            if not rest.strip():break
                        except json.JSONDecodeError:break
                    meta['complete_json_objects']=objects
                    meta['unparsed_tail_chars']=len(rest)
            save('v18-model-wire-diagnostic',meta)
            return response
    analyst=call(spending,cfg,facts,evidence,'analyst',key=api_key(root=ROOT),client=DiagnosticClient())
    critic=call(spending,cfg,facts,evidence,'critic',proposal=analyst,key=api_key(root=ROOT),client=DiagnosticClient())
    journal=Journal(STAGE/'.market'/('smoke-'+str(int(time.time()))))
    rules=next(e.partition(':')[2] for e in evidence if e.startswith('rules:'))
    journal.put('market_rules',{'sha256':rules,'compatible':True})
    result=publish(journal,cfg,facts,analyst,critic,rules)
    assert result
    save('v18-live-model-smoke',{'source':str(source.relative_to(ROOT)),'at_utc':datetime.now(timezone.utc).isoformat(),
        'calls':2,'schema_valid':True,'market_transactions':0,'published_only_in_isolation':True,
        'analyst':analyst,'critic':critic,'selected':result,'economic_superiority_proven':False})
    print('SELECTED',json.dumps({'policy':result['operating_policy'],'focus':result['forum_focus'],
                               'intents':result['operating_intents']}))


def latency():
    import importlib.util
    import shutil
    # Reuse the already audited fixed replay cases, with v18 sources and its
    # own isolated journal. No live credentials or accounts are copied.
    source=sorted((ROOT/'.market/v17-stage-20261111/.market').glob('parity-source-*.json'))[-1]
    shutil.copyfile(source,STAGE/'.market'/source.name)
    spec=importlib.util.spec_from_file_location('v18_fixed_latency',ROOT/'validate_agent_control.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.STAGE=STAGE
    module.save=lambda kind,value:save(kind.replace('v17','v18'),value)
    assert module.benchmark()


if __name__=='__main__':
    if '--live-models' in sys.argv:smoke()
    elif '--latency' in sys.argv:latency()
    else:replay()
