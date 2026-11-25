"""Reuse the audited source-only promoter for the v21 Shop model release."""
import argparse
from datetime import datetime,timezone
import difflib
import json
from pathlib import Path
import re
import shutil
import release_role_models as engine

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v21-stage-20261112'
FILES=['r3/demand_policy.py','r3/merchant.py','r3/economy.py','r3/shop_growth.py','r3/control.py',
       'r3/adapter.py','r3/cycle.py','r3/prewindow.py','r3/research.py','r3/objective_context.py',
       'policy.json','verify_market_cycle_live.py','tests/test_adaptive_demand.py',
       'tests/test_market_cycle.py','tests/test_objective_feedback.py','tests/test_all_objective.py']
original_save=engine.save
engine.ROOT=ROOT;engine.STAGE=STAGE;engine.FILES=FILES
engine.save=lambda kind,row:original_save(kind.replace('v19','v21'),row)


def checkpoint():
    tests=STAGE/'logs/v21-full-regression-02.txt';raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=428 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES)
    evidence=sorted((ROOT/'logs').glob('v21-shop-replay-*.json'))[-1]
    replay=json.loads(evidence.read_text(encoding='utf-8'))
    assert replay['schema_valid'] and replay['cash_and_legal_lot_guards_verified']
    assert replay['market_transactions']==0 and replay['model_calls']==0
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert cfg['shop_adaptive_demand_enabled'] and cfg['shop_inventory_caps_enabled'] is False
    assert all(p['model']!='gpt-6-astra' for p in cfg['background_llm_roles'].values())
    entries=[];patch=[]
    for name in FILES:
        before,after=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':engine.sha(before),'candidate_sha256':engine.sha(after)})
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8-sig').splitlines(True) if before.exists() else [],
            after.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v21-adaptive-shop-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v21-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    engine.save('v21-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':engine.sha(tests),
        'evidence':str(evidence.relative_to(ROOT)),'patch':str(path.relative_to(ROOT)),
        'economic_superiority_proven':False,'cash_and_legal_supply_guards_preserved':True})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);a=p.parse_args()
    engine.promote(a.promote) if a.promote else checkpoint()
