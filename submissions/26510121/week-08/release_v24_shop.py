"""Verified source-only release; never replace identity or financial state."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v24-stage-20261112'
FILES=['seller_r3.py','verify_market_cycle_live.py','r3/cycle.py','r3/economy.py','r3/feedback.py',
    'r3/limits.py','r3/objective_context.py','r3/portfolio.py','r3/quality.py','r3/reputation.py',
    'r3/research.py','r3/shop_growth.py','tests/test_agent_policy_recovery.py','tests/test_autonomous_market.py',
    'tests/test_championship.py','tests/test_customer_memory.py','tests/test_forum_supply.py',
    'tests/test_market_contract.py','tests/test_objective_decisions.py','tests/test_objective_feedback.py',
    'tests/test_settlement.py','tests/test_shop_velocity.py','tests/test_strategy_v4.py',
    'tests/test_v24_capital_reputation.py','tests/test_adaptive.py','tests/test_all_objective.py',
    'tests/test_foundation.py','policy.json']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v24'),row)


def checkpoint():
    tests=sorted((STAGE/'logs').glob('v24-full-regression-*.txt'))[-1];raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=521 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Sources changed after tests began'
    evidence={}
    for kind in ('v24-capital-replay','v24-reputation-replay'):
        path=sorted((ROOT/'logs').glob(kind+'-*.json'))[-1]
        row=json.loads(path.read_text(encoding='utf-8'))
        assert row['guards_verified'] and row['market_transactions']==row['model_calls']==row['shared_state_writes']==0
        for name,value in row['source_hashes'].items():assert engine.sha(STAGE/name)==value,'Replay source drift'
        evidence[kind]=str(path.relative_to(ROOT))
        if kind=='v24-capital-replay':
            assert row['candidate']['expected_book_scenario']>0
            assert row['candidate']['capital_commitment']<=row['candidate']['spendable']
            assert row['candidate']['cash_floor']==0
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'))
    assert cfg['shop_internal_capital_caps_enabled'] is False and cfg['shop_inventory_caps_enabled'] is False
    assert cfg['shop_reputation_v24_enabled'] and cfg['shop_new_goods_warranty_hours']==6
    assert cfg['daily_api_budget_usd'] is None
    assert all('astra' not in r['model'].lower() for r in cfg['background_llm_roles'].values())
    assert cfg['background_llm_roles']==old['background_llm_roles']
    assert all(cfg[k]==old[k] for k in old if k.startswith('buyer_')),'Preserve Buyer release'
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v24-shop-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v24-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v24-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),'evidence':evidence,
        'patch':str(path.relative_to(ROOT)),'economic_superiority_proven':False,'astra_excluded':True})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules,update as control_update
    from r3.adaptive import activate_shop_revision
    from r3.reputation import update
    assert not market_active(),'Drain required'
    raw=json.loads(sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1].read_text(encoding='utf-8'))
    assert 0<=time.time()-datetime.fromisoformat(raw['at']).timestamp()<1800,'Refresh rules'
    source=raw['sources']['https://market.ourolabs.ai/agent.md'];assert source['status']==200
    j=Journal();cfg=policy();buyer_before=j.get('adaptive:buyer');buyer_epoch=j.get('buyer_revision_epoch')
    parsed=audit_rules(j,source['text'])
    assert parsed['compatible'] and parsed['buyer_compatible'] and parsed['buyer_saved_formula']=='margin_share_v36'
    activate_shop_revision(j,cfg['shop_strategy_revision'])
    assert j.get('adaptive:buyer')==buyer_before and j.get('buyer_revision_epoch')==buyer_epoch
    j.put('framework-last-update',0)
    packet=update(j,cfg);control=control_update(j,force=True)
    engine.save('v24-shop-initialized',{'rules':parsed,'shop_revision':cfg['shop_strategy_revision'],
        'buyer_revision':cfg['buyer_strategy_revision'],'buyer_evidence_preserved':True,
        'reputation_summary':packet['summary'],'cash_capacity':control.get('cash_capacity'),
        'historical_raw_evidence_preserved':True})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
