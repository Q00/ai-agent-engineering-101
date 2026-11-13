"""Context-only correction: preserve both trading policies and maturity epochs."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v271-stage-20261113'
FILES=['policy.json','r3/inventory_portfolio.py','r3/objective_context.py','r3/research.py',
    'tests/test_v271_context.py','verify_market_cycle_live.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v271'),row)


def checkpoint():
    tests=sorted((STAGE/'logs').glob('v271-full-regression-*.txt'))[-1];raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig');match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=577 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES)
    p=sorted((ROOT/'logs').glob('v271-context-replay-*.json'))[-1];row=json.loads(p.read_text(encoding='utf-8'))
    assert row['model_calls']==row['market_transactions']==row['live_policy_writes']==0 and row['economic_function_bodies_unchanged']
    for name,value in row['source_hashes'].items():assert engine.sha(STAGE/name)==value
    assert row['captured'][0]['diagnostics']['fact_bytes']<=23000
    old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'));cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert {k:v for k,v in old.items() if k!='execution_revision'}=={k:v for k,v in cfg.items() if k!='execution_revision'}
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name;entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    dest=ROOT/'logs'/('v271-context-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    dest.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v271-*.txt'):
        target=ROOT/'logs'/('stage-'+source.name)
        if not target.exists():shutil.copyfile(source,target)
    engine.save('v271-release-candidate',{'status':'verified_not_promoted','files':entries,'full_tests_passed':int(match[1]),
        'evidence':str(p.relative_to(ROOT)),'patch':str(dest.relative_to(ROOT)),'trading_algorithm_unchanged':True})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules
    assert not market_active()
    raw=json.loads(sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1].read_text(encoding='utf-8'))
    assert 0<=time.time()-datetime.fromisoformat(raw['at']).timestamp()<1800
    source=raw['sources']['https://market.ourolabs.ai/agent.md'];assert source['status']==200
    j=Journal();cfg=policy();parsed=audit_rules(j,source['text']);assert parsed['compatible'] and parsed['buyer_compatible']
    assert all(j.get(role+'_strategy_revision')==cfg[role+'_strategy_revision'] for role in ('shop','buyer'))
    engine.save('v271-context-initialized',{'rules':parsed,'execution_revision':cfg['execution_revision'],
        'shop_revision_epoch':j.get('shop_revision_epoch'),'buyer_revision_epoch':j.get('buyer_revision_epoch'),
        'strategy_and_financial_state_reset':False,'new_model_roles':0})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
