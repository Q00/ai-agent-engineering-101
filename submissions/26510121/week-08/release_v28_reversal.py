"""Hash-checked v28 promotion; state and credentials remain in place."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import time
import release_role_models as engine

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v28-stage-20261114'
FILES=['policy.json','market_buyer.py','r3/reversal.py','r3/adapter.py','r3/risk.py','r3/buyer_quality.py',
    'r3/market_memory.py','r3/shop_growth.py','r3/inventory_portfolio.py','r3/economy.py',
    'r3/shop_offer.py','r3/customer_memory.py','r3/quote_market.py','r3/cycle.py','r3/research.py',
    'r3/buyer_forum.py','tests/test_market_cycle.py','tests/test_strategy_v4.py',
    'tests/test_v27_inventory_portfolio.py','tests/test_v28_reversal.py','verify_market_cycle_live.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v28'),row)

def checkpoint():
    tests=sorted((STAGE/'logs').glob('v28-full-regression-*.txt'))[-1]
    raw=tests.read_bytes();text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=601 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Sources changed after test start'
    replay_path=sorted((ROOT/'logs').glob('v28-reversal-replay-*.json'))[-1]
    replay=json.loads(replay_path.read_text(encoding='utf-8'))
    for name,digest in replay['source_hashes'].items():assert engine.sha(STAGE/name)==digest,'Replay source drift'
    assert replay['model_calls']==replay['market_transactions']==replay['live_policy_writes']==0
    context_path=sorted((ROOT/'logs').glob('v28-context-replay-*.json'))[-1]
    context=json.loads(context_path.read_text(encoding='utf-8'))
    assert context['captured'] and context['captured'][0]['fact_bytes']<=23000
    assert context['source_hashes']=={n:engine.sha(STAGE/n) for n in context['source_hashes']}
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'))
    changed={k for k in set(cfg)|set(old) if cfg.get(k)!=old.get(k)}
    assert changed=={'reversal_v28_enabled','market_end_at','execution_revision','shop_strategy_revision','buyer_strategy_revision'}
    assert cfg['background_llm_roles']==old['background_llm_roles'] and cfg['daily_api_budget_usd'] is None
    assert not any('astra' in p['model'] for p in cfg['background_llm_roles'].values())
    assert cfg['shop_inventory_caps_enabled'] is False and cfg['shop_internal_capital_caps_enabled'] is False
    assert all(engine.sha(STAGE/n)==engine.sha(ROOT/n) for n in ('seller_r3.py','r3/checkout.py','run_market.py'))
    entries=[];patch=[]
    for name in FILES:
        a,b=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(a),'candidate_sha256':engine.sha(b)})
        patch.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True) if a.exists() else [],
            b.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    dest=ROOT/'logs'/('v28-reversal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    dest.open('x',encoding='utf-8').write(''.join(patch))
    for p in (STAGE/'logs').glob('v28-*.txt'):
        target=ROOT/'logs'/('stage-'+p.name)
        if not target.exists():shutil.copyfile(p,target)
    engine.save('v28-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),
        'evidence':[str(replay_path.relative_to(ROOT)),str(context_path.relative_to(ROOT))],
        'patch':str(dest.relative_to(ROOT)),'live_economic_superiority_proven':False,
        'new_llm_roles':0,'payment_and_identity_guards_preserved':True})

def initialize():
    from r3.state import Journal,policy
    from r3.lifecycle import market_active
    from r3.control import audit_rules,update as control_update
    from r3.adaptive import activate_shop_revision,activate_buyer_revision
    assert not market_active(),'Drain first'
    raw=json.loads(sorted((ROOT/'logs').glob('patch-rules-source-*.json'))[-1].read_text(encoding='utf-8'))
    assert 0<=time.time()-datetime.fromisoformat(raw['at']).timestamp()<1800
    source=raw['sources']['https://market.ourolabs.ai/agent.md'];assert source['status']==200
    assert 'Nov 17, 23:59 KST' in raw['home_text']
    j=Journal();cfg=policy();parsed=audit_rules(j,source['text'])
    assert parsed['compatible'] and parsed['buyer_compatible'] and parsed['buyer_saved_formula']=='margin_share_v36'
    activate_shop_revision(j,cfg['shop_strategy_revision']);activate_buyer_revision(j,cfg['buyer_strategy_revision'])
    for key,value in [('merchant_context_v5',{}),('inventory-portfolio:v27',{}),('framework-last-update',0),
        ('research-last-attempt',{}),('feedback-last-policy',{})]:j.put(key,value)
    result=control_update(j,force=True)
    engine.save('v28-initialized',{'execution_revision':cfg['execution_revision'],'rules':parsed,
        'shop_revision_epoch':j.get('shop_revision_epoch'),'buyer_revision_epoch':j.get('buyer_revision_epoch'),
        'financial_state_reset':False,'historical_evidence_preserved':True,'market_end_at':cfg['market_end_at'],
        'control_status':result.get('status'),'new_model_roles':0})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);p.add_argument('--initialize',action='store_true');a=p.parse_args()
    if a.promote:engine.promote(a.promote)
    elif a.initialize:initialize()
    else:checkpoint()
