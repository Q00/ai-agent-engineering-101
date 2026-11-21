"""Promote the verified cycle runtime while preserving keys, state and history."""
import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import os

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'.market/v17-stage-20261111'
FILES=['r3/discovery.py', 'r3/recovery.py', 'r3/adapter.py', 'r3/transport.py', 'r3/cycle.py', 'r3/research.py', 'r3/feedback.py', 'r3/shop_growth.py', 'r3/economy.py', 'r3/contracts.py', 'shop_recovery_guard.py', 'policy.json', 'tests/test_agent_policy_recovery.py', 'tests/test_market_cycle.py', 'verify_market_cycle_live.py']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def write(kind,row):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,indent=2));print('SOURCE',path)
    return path


def checkpoint():
    tests=STAGE/'logs/v17-full-regression-02.txt'
    raw=tests.read_bytes();text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    passed=re.search(r'(\d+) passed',text)
    assert passed and int(passed[1])>=374 and not re.search(r'\d+ failed|\d+ errors',text),'Full regression failed'
    assert all((STAGE/name).stat().st_mtime<=tests.stat().st_ctime for name in FILES),'Source changed after tests started'
    evidence={}
    for kind in ['v17-parity-summary','v17-preparation-benchmark','v17-live-model-smoke']:
        path=sorted((ROOT/'logs').glob(kind+'-*.json'))[-1];evidence[kind]=(path,json.loads(path.read_text(encoding='utf-8')))
    assert evidence['v17-parity-summary'][1]['all_outputs_identical']
    bench=evidence['v17-preparation-benchmark'][1]
    assert bench['restock_same_output'] and bench['buyer_routes_identical'] and bench['prepared_plan_used']
    assert bench['restock_prepared_seconds']<.8*bench['restock_baseline_seconds']
    assert bench['buyer_routes_prepared_seconds']<.8*bench['buyer_routes_baseline_seconds']
    assert evidence['v17-live-model-smoke'][1]['schema_valid'] and evidence['v17-live-model-smoke'][1]['calls']==2
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    assert cfg['daily_api_budget_usd'] is None and cfg['research_budget_mode']=='observe'
    entries=[];patch=[]
    for name in FILES:
        before=ROOT/name;after=STAGE/name
        entries.append({'path':name,'baseline_sha256':sha(before),'candidate_sha256':sha(after)})
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8-sig').splitlines(True) if before.exists() else [],
            after.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    patch_path=ROOT/'logs'/('v17-market-cycle-'+stamp+'.patch');patch_path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v17-*.txt'):
        target=ROOT/'logs'/('stage-'+source.name)
        if not target.exists():shutil.copyfile(source,target)
    return write('v17-release-candidate',{'at':datetime.now(timezone.utc).isoformat(),'status':'verified_not_promoted',
        'full_tests_passed':int(passed[1]),'files':entries,'patch':str(patch_path.relative_to(ROOT)),
        'evidence':{k:str(p.relative_to(ROOT)) for k,(p,v) in evidence.items()},
        'cost_cap_usd':None,'budget_mode':'observe','economic_superiority_or_first_place_proven':False,
        'new_rule_promotion_authority':'existing mature evaluators; reviewed bounded trials only'})


def promote(path):
    from r3.lifecycle import market_active
    if market_active():raise RuntimeError('Safely drain the existing runtime before replacing sources')
    path=path.resolve();assert path.is_relative_to((ROOT/'logs').resolve())
    row=json.loads(path.read_text(encoding='utf-8'));assert row['status']=='verified_not_promoted'
    assert [r['path'] for r in row['files']]==FILES
    for entry in row['files']:
        assert sha(ROOT/entry['path'])==entry['baseline_sha256'],'Baseline changed'
        assert sha(STAGE/entry['path'])==entry['candidate_sha256'],'Candidate changed'
    backup=ROOT/'.market'/('v17-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir()
    for entry in row['files']:
        old=ROOT/entry['path']
        if old.is_file():
            dest=backup/entry['path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(old,dest)
    try:
        for entry in row['files']:
            dest=ROOT/entry['path'];assert dest.resolve().is_relative_to(ROOT.resolve())
            dest.parent.mkdir(parents=True,exist_ok=True)
            temp=dest.with_name(dest.name+'.cycle-release.tmp');temp.write_bytes((STAGE/entry['path']).read_bytes());os.replace(temp,dest)
        assert all(sha(ROOT/e['path'])==e['candidate_sha256'] for e in row['files'])
    except Exception:
        for entry in row['files']:
            old=backup/entry['path']
            if old.is_file():shutil.copyfile(old,ROOT/entry['path'])
        write('v17-source-promotion-failure',{'backup':str(backup.relative_to(ROOT)),'prior_files_restored':True})
        raise
    write('v17-source-promotion',{'status':'promoted_runtime_not_started','candidate':str(path.relative_to(ROOT)),
        'backup':str(backup.relative_to(ROOT)),'files':row['files'],'credentials_and_market_state_replaced':False})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--promote',type=Path);args=parser.parse_args()
    promote(args.promote) if args.promote else checkpoint()
