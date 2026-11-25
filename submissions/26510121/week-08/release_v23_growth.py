"""Verified source-only Shop/Buyer promotion; preserve identity and financial state."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v23-stage-20261112'
FILES=['market_buyer.py','policy.json','r3/control.py','r3/customer_memory.py','r3/cycle.py',
       'r3/economy.py','r3/evolution.py','r3/feedback.py','r3/long_memory.py','r3/market_memory.py',
       'r3/merchant.py','r3/meta.py','r3/objective_context.py','r3/policy_learning.py','r3/prepared.py',
       'r3/prewindow.py','r3/research.py','r3/shop_growth.py','r3/shop_offer.py',
       'tests/test_championship.py','tests/test_v23_policy_learning.py','verify_market_cycle_live.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v23'),row)


def checkpoint():
    tests=STAGE/'logs/v23-full-regression-04.txt';raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=506 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Sources changed after tests began'
    evidence=sorted((ROOT/'logs').glob('v23-market-replay-*.json'))[-1]
    replay=json.loads(evidence.read_text(encoding='utf-8'))
    assert replay['guards_verified'] and replay['market_transactions']==replay['model_calls']==replay['shared_state_writes']==0
    assert replay['candidate']['expected_book_scenario']>0
    assert replay['candidate']['capital_commitment']<=replay['candidate']['spendable']
    for name,value in replay['source_hashes'].items():assert engine.sha(STAGE/name)==value,'Replay source drift'
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert cfg['book_growth_v23_enabled'] and cfg['buyer_frontier_v23_enabled']
    assert cfg['shop_inventory_caps_enabled'] is False and cfg['buyer_joint_objective_enabled']
    assert cfg['daily_api_budget_usd'] is None
    assert all('astra' not in r['model'].lower() for r in cfg['background_llm_roles'].values())
    assert all('astra' not in cfg[k].lower() for k in ('actor_model','critic_model'))
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v23-growth-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v23-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v23-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),'evidence':str(evidence.relative_to(ROOT)),
        'patch':str(path.relative_to(ROOT)),'economic_superiority_proven':False,'astra_excluded':True})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules
    from r3.adaptive import activate_shop_revision,activate_buyer_revision
    from r3.policy_learning import update
    from r3.long_memory import refresh
    assert not market_active(),'Single-runtime drain required'
    source=sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1]
    raw=json.loads(source.read_text(encoding='utf-8'))
    assert 0<=time.time()-datetime.fromisoformat(raw['at']).timestamp()<1800,'Refresh rules'
    text=raw['sources']['https://market.ourolabs.ai/agent.md'];assert text['status']==200
    j=Journal();cfg=policy();rules=j.get('market_rules',{})
    known=[e['at'] for e in j.events('buyer_score_formula_changed',100) if e.get('after')=='margin_share_v36']
    rules['buyer_score_epoch']=rules.get('buyer_score_epoch',min(known) if known else time.time())
    j.put('market_rules',rules);parsed=audit_rules(j,text['text'])
    assert parsed['compatible'] and parsed['buyer_compatible'] and parsed['buyer_saved_formula']=='margin_share_v36'
    activate_shop_revision(j,cfg['shop_strategy_revision']);activate_buyer_revision(j,cfg['buyer_strategy_revision'])
    j.put('framework-last-update',0);j.put('memory-refreshed-at',0);refresh(j)
    packet=update(j,cfg)
    engine.save('v23-growth-initialized',{'rules':parsed,'shop_revision':cfg['shop_strategy_revision'],
        'buyer_revision':cfg['buyer_strategy_revision'],'service_skus':len(packet['service']),
        'qualified_service_skus':sum(r['qualified'] for r in packet['service'].values()),
        'counter_samples':sum(r['n'] for group in packet['counter_completion_by_kind'].values() for r in group.values()),
        'historical_raw_evidence_preserved':True})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
