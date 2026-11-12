"""Hash-checked v19 source promotion, preserving account and market state."""
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
STAGE=ROOT/'.market/v19-stage-20261112'
FILES=['r3/llm_roles.py','r3/capability.py','r3/research.py','r3/cycle.py','policy.json',
       'tests/test_llm_roles.py','tests/test_market_cycle.py','verify_market_cycle_live.py']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def save(kind,row):
    path=ROOT/'logs'/(kind+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(row,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    return path


def checkpoint():
    tests=STAGE/'logs/v19-full-regression-04.txt'
    raw=tests.read_bytes();text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    match=re.search(r'(\d+) passed',text)
    assert match and int(match[1])>=407 and not re.search(r'\d+ failed|\d+ errors',text)
    assert all((STAGE/n).stat().st_mtime<=tests.stat().st_ctime for n in FILES),'Source changed after tests began'
    paths={kind:sorted((ROOT/'logs').glob(kind+'-*.json'))[-1]
           for kind in ['v19-live-model-smoke','v19-preparation-benchmark']}
    smoke=json.loads(paths['v19-live-model-smoke'].read_text(encoding='utf-8'))
    bench=json.loads(paths['v19-preparation-benchmark'].read_text(encoding='utf-8'))
    assert smoke['schema_valid'] and smoke['calls']==4 and smoke['published_only_in_isolation']
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    expected=cfg['background_llm_roles']
    assert all(v['model']!='gpt-6-astra' for v in expected.values()),'Astra explicitly excluded by owner'
    assert {r['role']:r['model'] for r in smoke['usage']}=={k:v['model'] for k,v in expected.items()}
    assert all(r['reasoning_effort']==expected[r['role']]['reasoning'] for r in smoke['usage'])
    assert bench['restock_same_output'] and bench['buyer_routes_identical'] and bench['prepared_plan_used']
    assert bench['restock_prepared_seconds']<bench['restock_baseline_seconds']
    assert bench['buyer_routes_prepared_seconds']<bench['buyer_routes_baseline_seconds']
    assert cfg['capability_llm_enabled'] and cfg['daily_api_budget_usd'] is None
    entries=[];patch=[]
    for name in FILES:
        before,after=ROOT/name,STAGE/name
        entries.append({'path':name,'baseline_sha256':sha(before),'candidate_sha256':sha(after)})
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8-sig').splitlines(True) if before.exists() else [],
            after.read_text(encoding='utf-8-sig').splitlines(True),fromfile='a/'+name,tofile='b/'+name))
    path=ROOT/'logs'/('v19-role-models-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.patch')
    path.open('x',encoding='utf-8').write(''.join(patch))
    for source in (STAGE/'logs').glob('v19-*.txt'):
        dest=ROOT/'logs'/('stage-'+source.name)
        if not dest.exists():shutil.copyfile(source,dest)
    return save('v19-release-candidate',{'status':'verified_not_promoted','files':entries,
        'full_tests_passed':int(match[1]),'test_log_sha256':sha(tests),'patch':str(path.relative_to(ROOT)),
        'evidence':{k:str(v.relative_to(ROOT)) for k,v in paths.items()},
        'economic_superiority_or_first_place_proven':False,'credentials_or_market_state_replaced':False})


def promote(path):
    from r3.lifecycle import market_active
    assert not market_active(),'Drain before replacing runtime sources'
    path=path.resolve();assert path.is_relative_to((ROOT/'logs').resolve())
    row=json.loads(path.read_text(encoding='utf-8'))
    assert row['status']=='verified_not_promoted' and [e['path'] for e in row['files']]==FILES
    for e in row['files']:
        assert sha(ROOT/e['path'])==e['baseline_sha256'],'Baseline changed'
        assert sha(STAGE/e['path'])==e['candidate_sha256'],'Candidate changed'
    backup=ROOT/'.market'/('v19-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir()
    for e in row['files']:
        source=ROOT/e['path'];dest=backup/e['path']
        if source.is_file():dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    try:
        for e in row['files']:
            dest=ROOT/e['path'];assert dest.resolve().is_relative_to(ROOT.resolve())
            dest.parent.mkdir(parents=True,exist_ok=True)
            temporary=dest.with_name(dest.name+'.v19-release.tmp')
            temporary.write_bytes((STAGE/e['path']).read_bytes());os.replace(temporary,dest)
        assert all(sha(ROOT/e['path'])==e['candidate_sha256'] for e in row['files'])
    except Exception:
        for e in row['files']:
            dest=ROOT/e['path'];old=backup/e['path']
            assert dest.resolve().is_relative_to(ROOT.resolve())
            if old.is_file():shutil.copyfile(old,dest)
            elif e['baseline_sha256'] is None and dest.is_file():dest.unlink()
        save('v19-source-promotion-failure',{'prior_sources_restored':True,'backup':str(backup.relative_to(ROOT))})
        raise
    save('v19-source-promotion',{'status':'promoted_runtime_not_started','files':row['files'],
        'candidate':str(path.relative_to(ROOT)),'backup':str(backup.relative_to(ROOT)),
        'credentials_and_market_state_replaced':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--promote',type=Path);a=p.parse_args()
    promote(a.promote) if a.promote else checkpoint()
