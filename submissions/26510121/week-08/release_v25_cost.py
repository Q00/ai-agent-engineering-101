"""Verified cheaper background roles, preserving all trading strategies and evidence."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v25-stage-20261113'
FILES=['policy.json','r3/llm_roles.py','tests/test_llm_roles.py','tests/test_uncapped_stock.py',
       'tests/test_v25_cost_routing.py','verify_market_cycle_live.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v25'),row)


def checkpoint():
    tests=sorted((STAGE/'logs').glob('v25-full-regression-*.txt'))[-1];raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=525 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES)
    evidence={}
    for kind in ('v25-cost-replay','v25-live-model-smoke'):
        p=sorted((ROOT/'logs').glob(kind+'-*.json'))[-1];row=json.loads(p.read_text(encoding='utf-8'))
        for name,value in row['source_hashes'].items():assert engine.sha(STAGE/name)==value,'Source drift'
        assert row['market_transactions']==0
        if kind=='v25-cost-replay':
            assert row['supply_actions_identical'] and row['shop_contracts_identical'] and row['unchanged_critic']
            assert all(v['ratio']<.5 for v in row['cost_windows'].values())
        else:
            assert row['schema_valid'] and row['calls']==4 and row['published_only_in_isolation'] and row['live_policy_writes']==0
        evidence[kind]=str(p.relative_to(ROOT))
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8'));old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8'))
    allowed={'execution_revision','llm_cost_v25_enabled','background_llm_roles'}
    assert {k:v for k,v in cfg.items() if k not in allowed}=={k:v for k,v in old.items() if k not in allowed}
    assert cfg['background_llm_roles']['market_research_critic']==old['background_llm_roles']['market_research_critic']
    assert cfg['daily_api_budget_usd'] is None
    assert not any('astra' in r['model'] for r in cfg['background_llm_roles'].values())
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v25-cost-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v25-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v25-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),'evidence':evidence,
        'patch':str(path.relative_to(ROOT)),'economic_superiority_proven':False,'astra_excluded':True,
        'saved_billing_ratio_guaranteed':False,'existing_trading_strategies_preserved':True})


def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    assert not market_active()
    j=Journal();cfg=policy()
    assert j.get('shop_strategy_revision')==cfg['shop_strategy_revision']
    assert j.get('buyer_strategy_revision')==cfg['buyer_strategy_revision']
    engine.save('v25-cost-initialized',{'shop_revision':j.get('shop_strategy_revision'),
        'buyer_revision':j.get('buyer_strategy_revision'),'shop_epoch':j.get('shop_revision_epoch'),
        'buyer_epoch':j.get('buyer_revision_epoch'),'models':cfg['background_llm_roles'],
        'strategy_or_financial_state_reset':False,'daily_usd_cap':cfg['daily_api_budget_usd']})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
