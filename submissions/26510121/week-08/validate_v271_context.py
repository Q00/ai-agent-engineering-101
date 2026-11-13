"""Stage compact stock-policy context, preserving every trading function."""
import argparse
import ast
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v271-stage-20261113'


def stage():
    assert not STAGE.exists()
    STAGE.mkdir();(STAGE/'logs').mkdir();(STAGE/'.market').mkdir()
    paths=list(ROOT.glob('*.py'))+[ROOT/'policy.json']
    for folder in ('r3','tests'):paths.extend((ROOT/folder).glob('*.py'))
    for source in paths:
        target=STAGE/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    cfg['execution_revision']='v27.1-compact-stock-policy-context-1'
    (STAGE/'policy.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('STAGED',len(paths),'SOURCE_FILES_ONLY')


def replay():
    import importlib.util
    sys.path.insert(0,str(STAGE))
    from r3 import research
    from r3.state import read_json
    from r3.objective_context import curate,FACT_BYTES
    spec=importlib.util.spec_from_file_location('isolated_v27_view',ROOT/'validate_v27_inventory.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    now=time.time();cfg=read_json(STAGE/'policy.json');captured=[]
    def bodies(path):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)}
    old,new=bodies(ROOT/'r3/inventory_portfolio.py'),bodies(STAGE/'r3/inventory_portfolio.py')
    for name in ('supported','scale','downside','unit_scenarios','materialize','forecast','dominates','allocate'):
        assert old[name]==new[name],name
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=mod.View(db,now);j.md=STAGE/'.market/context-proof';j.md.mkdir(exist_ok=True)
        j.put('research-last-attempt',{})
        # Capture only the assembled facts; never call a paid provider or publish.
        def capture(journal,config,facts,evidence,role,**kwargs):
            try:value,diagnostics=curate(facts)
            except ValueError as exc:
                trace=exc.__traceback__;row={}
                while trace:
                    if trace.tb_frame.f_code.co_name=='curate':row=trace.tb_frame.f_locals.get('row',{})
                    trace=trace.tb_next
                def sizes(r):return {k:len(json.dumps(v,ensure_ascii=False).encode()) for k,v in r.items()}
                report={'at':now,'top_bytes':sizes(row),'children_bytes':{k:sizes(v) for k,v in row.items() if isinstance(v,dict)}}
                p=ROOT/'logs'/('v271-context-size-failure-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
                p.open('x',encoding='utf-8').write(json.dumps(report,indent=2));print('DIAGNOSTIC',p)
                print(json.dumps(report));raise
            captured.append({'role':role,'diagnostics':diagnostics,
                'inventory_portfolio':value.get('inventory_portfolio'),
                'stock_reference':value.get('objective_decisions',{}).get('shop',{}).get('lot_portfolio_comparison')})
            raise research.ModelError('validation_capture_no_api')
        research.call=capture
        research.update(j,cfg,now)
        assert captured and captured[0]['diagnostics']['fact_bytes']<=FACT_BYTES
    p=ROOT/'logs'/('v271-context-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    files=['policy.json','r3/inventory_portfolio.py','r3/objective_context.py','r3/research.py','tests/test_v271_context.py','verify_market_cycle_live.py']
    report={'at':now,'source_hashes':{n:hashlib.sha256((STAGE/n).read_bytes()).hexdigest() for n in files},
        'captured':captured,'economic_function_bodies_unchanged':True,'model_calls':0,'market_transactions':0,'live_policy_writes':0,
        'capture_exit':'Stopped after facts assembly/compaction, before provider call and publication.'}
    p.open('x',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2));print('SOURCE',p)
    print(json.dumps(captured,ensure_ascii=True))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',action='store_true');a=p.parse_args();stage() if a.stage else replay()
