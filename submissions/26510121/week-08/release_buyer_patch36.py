"""Hash-checked v22 Buyer release; account, payment and Shop state are preserved."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v22-stage-20261112'
FILES=['r3/buyer_scoring.py','r3/control.py','r3/market_memory.py','r3/buyer_routes.py',
       'r3/buyer_execution.py','r3/economy.py','r3/contracts.py','r3/prepared.py',
       'r3/outcomes.py','r3/evolution.py','r3/feedback.py','r3/objective_context.py',
       'r3/research.py','r3/progress.py','r3/buyer_costs.py','r3/cycle.py','r3/model.py',
       'market_buyer.py','review.py','policy.json','verify_market_cycle_live.py',
       'tests/test_patch36.py','tests/test_patch35.py','tests/test_buyer_patch34.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v22'),row)


def checkpoint():
    tests=STAGE/'logs/v22-full-regression-04.txt';raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=494 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Source changed after full tests began'
    evidence=sorted((ROOT/'logs').glob('v22-buyer-quote-replay-*.json'))[-1]
    replay=json.loads(evidence.read_text(encoding='utf-8'))
    assert replay['all_saved_caps_are_reference_only'] and replay['rows']
    assert replay['model_calls']==replay['market_transactions']==replay['shared_state_writes']==0
    assert replay['numeric_seconds']['max']<.25
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert cfg['buyer_joint_objective_enabled'] and cfg['buyer_patch36_enabled']
    assert cfg['shop_adaptive_demand_enabled'] and cfg['shop_inventory_caps_enabled'] is False
    assert cfg['daily_api_budget_usd'] is None
    assert all(p['model']!='gpt-6-astra' for p in cfg['background_llm_roles'].values())
    before_cfg=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'))
    allowed={'buyer_joint_objective_enabled','buyer_patch36_enabled','buyer_strategy_revision','execution_revision'}
    assert all(cfg.get(k)==v for k,v in before_cfg.items() if k not in allowed),'Unrelated configuration changed'
    entries=[];patch=[]
    for name in FILES:
        before,after=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(before),'candidate_sha256':engine.sha(after)})
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8-sig').splitlines(True) if before.exists() else [],
            after.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v22-buyer-patch36-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v22-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v22-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),
        'evidence':str(evidence.relative_to(ROOT)),'patch':str(path.relative_to(ROOT)),
        'saved_average_is_not_a_veto':True,'economic_superiority_proven':False})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules
    from r3.buyer_scoring import MARGIN
    from r3.buyer_costs import update
    from r3.adaptive import activate_buyer_revision
    assert not market_active(),'Initialize before restarting the single runtime'
    source=sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1]
    raw=json.loads(source.read_text(encoding='utf-8'))
    age=time.time()-datetime.fromisoformat(raw['at']).timestamp()
    assert 0<=age<1800,'Refresh public rules first'
    rules=raw['sources']['https://market.ourolabs.ai/agent.md']
    assert rules['status']==200
    j=Journal();cfg=policy();before=j.get('framework-arena',{})
    parsed=audit_rules(j,rules['text'])
    assert parsed['buyer_compatible'] and parsed['buyer_saved_formula']==MARGIN
    activate_buyer_revision(j,cfg['buyer_strategy_revision'])
    j.add('buyer_scoring_release',{'revision':cfg['buyer_strategy_revision'],'score_formula':MARGIN,
        'previous_arena':before,'purpose':'fresh joint-objective experiment; raw history retained'},role='buyer')
    j.put('framework-arena',{});j.put('framework-last-update',0)
    packet=update(j,cfg)
    engine.save('v22-buyer-scoring-initialized',{'rules':parsed,'cost_model_id':packet['id'],
        'cost_cohorts':{k:r['n'] for k,r in packet['groups'].items()},'buyer_revision':cfg['buyer_strategy_revision']})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
