"""Promote hash-checked, baseline-preserving whole-lot stock optimization."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v27-stage-20261113'
FILES=['policy.json','r3/economy.py','r3/shop_growth.py','r3/inventory_portfolio.py','r3/cycle.py',
    'r3/research.py','r3/objective_context.py','tests/test_market_cycle.py','tests/test_v27_inventory_portfolio.py',
    'verify_market_cycle_live.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v27'),row)


def checkpoint():
    tests=sorted((STAGE/'logs').glob('v27-full-regression-*.txt'))[-1];raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=572 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Source changed after tests began'
    evidence=[]
    for p in sorted((ROOT/'logs').glob('v27-inventory-replay-*.json'))[-2:]:
        row=json.loads(p.read_text(encoding='utf-8'))
        for name,value in row['source_hashes'].items():assert engine.sha(STAGE/name)==value,'Source drift'
        assert row['market_transactions']==row['live_policy_writes']==row['model_calls']==0
        assert row['models_unchanged'] and row['buyer_unchanged']
        allocation=row['comparisons']['v27']['allocation'];assert allocation['dominance_guard_passed']
        chosen=allocation['alternatives'][allocation['selection']];base=allocation['alternatives']['baseline']
        assert chosen['book']>=base['book']-1e-6 and chosen['stress_book']>=base['stress_book']-1e-6
        evidence.append(str(p.relative_to(ROOT)))
    assert len(evidence)==2
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'));old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'))
    allowed={'execution_revision','shop_strategy_revision','inventory_portfolio_v27_enabled'}
    assert {k:v for k,v in cfg.items() if k not in allowed}=={k:v for k,v in old.items() if k not in allowed}
    assert cfg['daily_api_budget_usd'] is None and not cfg['shop_inventory_caps_enabled'] and not cfg['shop_internal_capital_caps_enabled']
    assert cfg['background_llm_roles']==old['background_llm_roles']
    assert not any('astra' in r['model'] for r in cfg['background_llm_roles'].values())
    assert all(engine.sha(STAGE/n)==engine.sha(ROOT/n) for n in ('market_buyer.py','seller_r3.py'))
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v27-inventory-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v27-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v27-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),'evidence':evidence,
        'patch':str(path.relative_to(ROOT)),'economic_superiority_proven':False,'astra_excluded':True,
        'main_llm_calls_added':0,'buyer_and_models_preserved':True,'capital_and_supply_guards_preserved':True})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules,update as control_update
    from r3.adaptive import activate_shop_revision
    assert not market_active(),'Drain required'
    raw=json.loads(sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1].read_text(encoding='utf-8'))
    assert 0<=time.time()-datetime.fromisoformat(raw['at']).timestamp()<1800,'Refresh rules'
    source=raw['sources']['https://market.ourolabs.ai/agent.md'];assert source['status']==200
    j=Journal();cfg=policy();buyer=j.get('adaptive:buyer');buyer_epoch=j.get('buyer_revision_epoch')
    parsed=audit_rules(j,source['text']);assert parsed['compatible'] and parsed['buyer_compatible']
    activate_shop_revision(j,cfg['shop_strategy_revision'])
    assert j.get('adaptive:buyer')==buyer and j.get('buyer_revision_epoch')==buyer_epoch
    j.put('framework-last-update',0);j.put('inventory-portfolio:v27',{});j.put('inventory-observer:v27',{})
    control=control_update(j,force=True)
    engine.save('v27-inventory-initialized',{'rules':parsed,'shop_revision':cfg['shop_strategy_revision'],
        'buyer_revision':cfg['buyer_strategy_revision'],'buyer_policy_and_epoch_preserved':True,
        'cash_capacity':control.get('cash_capacity'),'financial_state_reset':False,
        'historical_raw_evidence_preserved':True,'new_paid_model_calls':0})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
