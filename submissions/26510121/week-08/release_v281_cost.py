"""Promote only the tested non-trial critic de-duplication gate."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v281-stage-20261115'
FILES=['policy.json','r3/research_gate.py','r3/research.py','tests/test_research_gate.py','verify_market_cycle_live.py']
save=engine.save;engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:save(kind.replace('v19','v281'),row)

def checkpoint():
    from validate_v281_cost import ECONOMIC
    tests=sorted((STAGE/'logs').glob('v281-full-regression-*.txt'))[-1]
    raw=tests.read_bytes();text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig');m=re.search(r'(\d+) passed',text)
    assert m and int(m[1])>=613 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES)
    p=sorted((ROOT/'logs').glob('v281-cost-replay-*.json'))[-1];proof=json.loads(p.read_text(encoding='utf-8'))
    assert all(engine.sha(ROOT/n)==engine.sha(STAGE/n) for n in ECONOMIC)
    assert all(engine.sha(STAGE/n)==digest for n,digest in proof['source_hashes'].items())
    assert proof['model_calls']==proof['market_transactions']==proof['live_policy_writes']==0
    old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'));cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert {k:v for k,v in old.items() if k not in ('execution_revision','research_noop_gate_enabled')}=={
        k:v for k,v in cfg.items() if k not in ('execution_revision','research_noop_gate_enabled')}
    files=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name;files.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    patch_path=ROOT/'logs'/('v281-cost-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    patch_path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v281-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v281-release-candidate',{'status':'verified_not_promoted','files':files,'full_tests_passed':int(m[1]),
        'patch':str(patch_path.relative_to(ROOT)),'evidence':str(p.relative_to(ROOT)),
        'economic_files_unchanged':ECONOMIC,'budget_cap_and_models_unchanged':True,'realized_savings_not_yet_measured':True})

def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    assert not market_active();j=Journal();cfg=policy()
    assert all(j.get(role+'_strategy_revision')==cfg[role+'_strategy_revision'] for role in ('shop','buyer'))
    j.put('research-reviewed-proposals:v281',{})
    engine.save('v281-initialized',{'execution_revision':cfg['execution_revision'],
        'strategy_epochs_preserved':True,'financial_state_reset':False,'model_roles_unchanged':True,
        'cost_cap_usd':cfg['daily_api_budget_usd']})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
