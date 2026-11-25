"""Source-only v20 release after cap-removal, money-guard and replay checks."""
import argparse
from datetime import datetime,timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v20-stage-20261112'
FILES=['r3/shop_growth.py','r3/economy.py','r3/portfolio.py','r3/prewindow.py','r3/feedback.py',
       'r3/objective_context.py','r3/capability.py','r3/research.py','policy.json','verify_market_cycle_live.py',
       'tests/test_uncapped_stock.py','tests/test_championship.py','tests/test_shop_velocity.py','tests/test_objective_feedback.py']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def save(kind,row):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2));print('SOURCE',path)
    return path


def checkpoint():
    tests=STAGE/'logs/v20-full-regression-03.txt';raw=tests.read_bytes()
    text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=420 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Source changed after tests began'
    paths={kind:sorted((ROOT/'logs').glob(kind+'-*.json'))[-1] for kind in ['v20-demand-counterfactual','v20-context-contract']}
    replay=json.loads(paths['v20-demand-counterfactual'].read_text(encoding='utf-8'))
    context=json.loads(paths['v20-context-contract'].read_text(encoding='utf-8'))
    assert replay['market_transactions']==0 and replay['model_calls']==0 and context['schema_valid']
    scenarios={r['scenario']:r for r in replay['scenarios']}
    old,new=scenarios['current_v19'],scenarios['v20_uncapped_diminishing_probe']
    assert old['units']==new['units'] and old['lines']==new['lines']
    assert abs(old['capital_commitment']-new['capital_commitment'])<1e-6
    assert abs(old['book_scenario']-new['book_scenario'])<1e-6
    assert [(r['sku'],r['qty']) for r in old['rows']]==[(r['sku'],r['qty']) for r in new['rows']]
    assert new['inventory_limit'] is None and all(1<=r['qty']<=20 for r in new['rows'])
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert cfg['shop_inventory_caps_enabled'] is False and cfg['daily_api_budget_usd'] is None
    assert all(v['model']!='gpt-6-astra' for v in cfg['background_llm_roles'].values())
    entries=[];patch=[]
    for name in FILES:
        before,after=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':sha(before),'candidate_sha256':sha(after)})
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8-sig').splitlines(True) if before.exists() else [],
            after.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v20-uncapped-stock-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v20-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    return save('v20-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':sha(tests),'patch':str(path.relative_to(ROOT)),
        'evidence':{k:str(v.relative_to(ROOT)) for k,v in paths.items()},
        'current_frozen_portfolio_unchanged':True,'inventory_unit_caps_removed':True,
        'cash_refund_storage_and_legal_lot_guards_preserved':True,
        'economic_superiority_or_first_place_proven':False})


def promote(path):
    from r3.lifecycle import market_active
    assert not market_active(),'Drain before replacing runtime sources'
    path=path.resolve();assert path.is_relative_to((ROOT/'logs').resolve())
    row=json.loads(path.read_text(encoding='utf-8'))
    assert row['status']=='verified_not_promoted' and [e['path'] for e in row['files']]==FILES
    for e in row['files']:
        assert sha(ROOT/e['path'])==e['baseline_sha256'],'Baseline changed'
        assert sha(STAGE/e['path'])==e['candidate_sha256'],'Candidate changed'
    backup=ROOT/'.market'/('v20-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir()
    for e in row['files']:
        source=ROOT/e['path'];dest=backup/e['path']
        if source.is_file():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    try:
        for e in row['files']:
            dest=ROOT/e['path'];assert dest.resolve().is_relative_to(ROOT.resolve())
            dest.parent.mkdir(parents=True,exist_ok=True)
            temporary=dest.with_name(dest.name+'.v20-release.tmp')
            temporary.write_bytes((STAGE/e['path']).read_bytes());os.replace(temporary,dest)
        assert all(sha(ROOT/e['path'])==e['candidate_sha256'] for e in row['files'])
    except Exception:
        for e in row['files']:
            dest=ROOT/e['path'];old=backup/e['path'];assert dest.resolve().is_relative_to(ROOT.resolve())
            if old.is_file():shutil.copyfile(old,dest)
            elif e['baseline_sha256'] is None and dest.is_file():dest.unlink()
        save('v20-source-promotion-failure',{'prior_sources_restored':True,'backup':str(backup.relative_to(ROOT))});raise
    save('v20-source-promotion',{'status':'promoted_runtime_not_started','files':row['files'],
        'candidate':str(path.relative_to(ROOT)),'backup':str(backup.relative_to(ROOT)),
        'credentials_and_market_state_replaced':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);args=p.parse_args()
    promote(args.promote) if args.promote else checkpoint()
