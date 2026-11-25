"""v19 paid model/contract validation on isolated facts, never market actions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v19-stage-20261112'
sys.path.insert(0,str(STAGE))


def save(kind,row):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2))
    print('SOURCE',path,flush=True)
    return path


def latency():
    old=sorted((ROOT/'.market/v17-stage-20261111/.market').glob('parity-source-*.json'))[-1]
    shutil.copyfile(old,STAGE/'.market'/old.name)
    spec=importlib.util.spec_from_file_location('v19_fixed_latency',ROOT/'validate_agent_control.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.STAGE=STAGE;module.save=lambda kind,value:save(kind.replace('v17','v19'),value)
    assert module.benchmark()


def smoke(capability_only=False):
    from r3.state import Journal,policy,read_json
    from r3.model import api_key
    from r3 import research,capability
    from r3.llm_roles import call_json
    from r3.objective_context import _bounded
    cfg=policy();key=api_key(root=ROOT);assert key,'Previously authorized key unavailable'
    source=sorted((ROOT/'logs').glob('v18-objective-replay-*.json'))[-1]
    captured=read_json(source);facts=captured['facts'];facts.pop('observed_at',None)
    evidence=facts['evidence_ids'];rules=next(e.partition(':')[2] for e in evidence if e.startswith('rules:'))
    isolated=Journal(STAGE/'.market'/('model-smoke-'+str(int(time.time()))))
    isolated.put('market_rules',{'sha256':rules,'compatible':True,'buyer_compatible':True})
    production=Journal(ROOT/'.market')
    class Accounted:
        # Paid calls share the real usage ledger, but all policy publication is isolated.
        def hold_budget(self,*a):return production.hold_budget(*a)
        def close_budget(self,*a):return production.close_budget(*a)
        def add(self,kind,value,*a,**kw):
            if kind in ('model_usage','background_model_failed'):
                production.add(kind,dict(value,validation_only=True,validation_revision='v19'))
            return isolated.add(kind,value,*a,**kw)
    paid=Accounted()
    def research_work():
        a=research.call(paid,cfg,facts,evidence,'analyst',key=key)
        b=research.call(paid,cfg,facts,evidence,'critic',proposal=a,key=key)
        selected=research.publish(isolated,cfg,facts,a,b,rules)
        assert selected
        return {'analyst':a,'critic':b,'selected':selected}
    def capability_work():
        # Fresh read-only aggregate evidence; no inventory/supply mutation.
        now=time.time();f=capability.source(production,cfg,now)
        payload={'facts':_bounded(f),'valid_evidence_ids':f['evidence_ids'],
                 'current_plan':None,'trusted_tools':capability.TOOLS}
        triage=call_json(paid,cfg,'capability_triage',capability.TRIAGE,
            capability.INSTRUCTIONS+'Assess the absent analytic plan and choose the most useful focus.',payload,key=key)
        save('v19-capability-wire-diagnostic',{'role':'triage','valid':capability.valid(triage,f['evidence_ids']),
            'proposal':triage,'allowed_evidence':f['evidence_ids']})
        assert capability.valid(triage,f['evidence_ids'])
        payload['triage']=triage
        design=call_json(paid,cfg,'capability_designer',capability.DESIGN,
            capability.INSTRUCTIONS+'For this isolated evaluation, design a shallow read-only plan of 1-4 tools.',payload,key=key)
        save('v19-capability-wire-diagnostic',{'role':'designer','valid':capability.valid(design,f['evidence_ids'],True),
            'proposal':design,'allowed_evidence':f['evidence_ids']})
        assert capability.valid(design,f['evidence_ids'],True)
        values=capability.compute(design,f,production)
        assert values
        return {'triage':triage,'design':design,'calculated_tools':list(values),'calculation_bytes':len(json.dumps(values).encode())}
    started=time.monotonic()
    try:
        if capability_only:
            capability_result=capability_work()
            save('v19-capability-only-smoke',{'schema_valid':True,'capability':capability_result,
                'usage':isolated.events('model_usage',10),'market_transactions':0})
            return
        with ThreadPoolExecutor(max_workers=2) as pool:
            r=pool.submit(research_work);c=pool.submit(capability_work)
            research_result=r.result();capability_result=c.result()
        usage=isolated.events('model_usage',10)
        assert len(usage)==4
        save('v19-live-model-smoke',{'schema_valid':True,'calls':4,
            'seconds':time.monotonic()-started,'published_only_in_isolation':True,'market_transactions':0,
            'research_source':str(source.relative_to(ROOT)),'research_source_cutoff':captured['source_cutoff'],
            'research_source_is_historical_replay':True,'research':research_result,'capability':capability_result,
            'usage':usage,'economic_superiority_proven':False})
        print('VALIDATED_ROLES',json.dumps({r['role']:r['model'] for r in usage}),flush=True)
    except Exception as exc:
        save('v19-model-validation-failure',{'error_type':type(exc).__name__,
            'reason':str(exc) if type(exc).__name__=='ModelError' else 'see console evidence',
            'usage':isolated.events('model_usage',10),'seconds':time.monotonic()-started,
            'market_transactions':0,'policy_promoted':False})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--latency',action='store_true');p.add_argument('--capability-only',action='store_true');a=p.parse_args()
    latency() if a.latency else smoke(a.capability_only)
