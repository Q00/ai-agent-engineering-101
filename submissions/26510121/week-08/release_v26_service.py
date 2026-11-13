"""Hash-checked service-contract release; no credentials or financial state replaced."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v26-stage-20261113'
FILES=['policy.json','seller_r3.py','market_buyer.py','r3/service_contracts.py','r3/service_policy.py',
    'r3/negotiation_path.py','r3/cycle.py','r3/policy_learning.py','r3/market_memory.py','r3/buyer_execution.py',
    'r3/quote_market.py','r3/buyer_routes.py','r3/economy.py','r3/shop_growth.py','r3/prewindow.py','r3/research.py',
    'tests/test_uncapped_stock.py','tests/test_strategy_v4.py','tests/test_v26_service_contracts.py','verify_market_cycle_live.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v26'),row)


def checkpoint():
    tests=sorted((STAGE/'logs').glob('v26-full-regression-*.txt'))[-1];raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=551 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Source changed after tests began'
    p=sorted((ROOT/'logs').glob('v26-service-replay-*.json'))[-1];replay=json.loads(p.read_text(encoding='utf-8'))
    for name,value in replay['source_hashes'].items():assert engine.sha(STAGE/name)==value,'Source drift'
    assert replay['market_transactions']==replay['live_policy_writes']==replay['model_calls']==0
    assert replay['models_unchanged'] and replay['legal_lots_and_positive_book_scenarios']
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'));old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'))
    allowed={'execution_revision','shop_strategy_revision','buyer_strategy_revision','service_contract_v26_enabled','buyer_terms_clarify_seconds'}
    assert {k:v for k,v in cfg.items() if k not in allowed}=={k:v for k,v in old.items() if k not in allowed}
    assert cfg['daily_api_budget_usd'] is None and not cfg['shop_inventory_caps_enabled'] and not cfg['shop_internal_capital_caps_enabled']
    assert cfg['background_llm_roles']==old['background_llm_roles']
    assert not any('astra' in r['model'] for r in cfg['background_llm_roles'].values())
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v26-service-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v26-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v26-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),'evidence':str(p.relative_to(ROOT)),
        'patch':str(path.relative_to(ROOT)),'economic_superiority_proven':False,'astra_excluded':True,
        'main_llm_calls_added':0,'existing_models_and_money_guards_preserved':True})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules,update as control_update
    from r3.adaptive import activate_shop_revision,activate_buyer_revision
    from r3 import service_policy,negotiation_path
    assert not market_active(),'Drain required'
    raw=json.loads(sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1].read_text(encoding='utf-8'))
    assert 0<=time.time()-datetime.fromisoformat(raw['at']).timestamp()<1800,'Refresh rules'
    source=raw['sources']['https://market.ourolabs.ai/agent.md'];assert source['status']==200
    j=Journal();cfg=policy();parsed=audit_rules(j,source['text'])
    assert parsed['compatible'] and parsed['buyer_compatible'] and parsed['buyer_saved_formula']=='margin_share_v36'
    derived=negotiation_path.bootstrap(j)
    activate_shop_revision(j,cfg['shop_strategy_revision']);activate_buyer_revision(j,cfg['buyer_strategy_revision'])
    # Epochs scope policy evaluation, never erase ledger, capital or mature quality.
    j.put('framework-last-update',0);j.put(service_policy.KEY,{});j.put(negotiation_path.KEY,{})
    service=service_policy.update(j,cfg);path=negotiation_path.update(j,cfg);control=control_update(j,force=True)
    engine.save('v26-service-initialized',{'rules':parsed,'shop_revision':cfg['shop_strategy_revision'],
        'buyer_revision':cfg['buyer_strategy_revision'],'derived_exact_routes':derived,
        'service_skus':len(service['shop_needs']),'path_holdout':path['summary'],
        'cash_capacity':control.get('cash_capacity'),'financial_state_reset':False,
        'historical_raw_evidence_preserved':True,'new_paid_model_calls':0})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
