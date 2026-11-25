"""Promote an allowlisted tested tree only after the runtime lease is released."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,difflib,hashlib,json,shutil
from r3.state import ROOT,atomic_json
from r3.lifecycle import market_active

BASE=ROOT/'.market/system-upgrade-20261110'
STAGE=BASE/'repo/submissions/26510121/week-08'
FILES=['r3/outcomes.py','r3/long_memory.py','r3/contracts.py','r3/evolution.py',
    'r3/state.py','r3/adaptive.py','r3/risk.py','r3/meta.py','r3/customer_memory.py','r3/economy.py',
    'r3/adapter.py','r3/model.py','r3/control.py','r3/buyer_execution.py','r3/quote_market.py',
    'market_buyer.py','seller_r3.py','policy.json','verify_system_upgrade.py',
    'tests/test_system_outcomes.py','tests/test_long_framework_memory.py','tests/test_contract_frontier.py',
    'tests/test_evolution_framework.py','tests/test_framework_contract_guards.py']

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main(apply=False):
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    changes=[];patch=[]
    for name in FILES:
        old,new=ROOT/name,STAGE/name
        if not new.is_file():raise RuntimeError('Missing staged source: '+name)
        changes.append({'file':name,'before_sha256':digest(old),'after_sha256':digest(new)})
        a=old.read_text(encoding='utf-8').splitlines(keepends=True) if old.exists() else []
        b=new.read_text(encoding='utf-8').splitlines(keepends=True)
        patch.extend(difflib.unified_diff(a,b,fromfile='a/'+name,tofile='b/'+name))
    if not apply:
        path=ROOT/'logs'/f'system-v13-staged-{stamp}.patch'
        path.write_text(''.join(patch),encoding='utf-8')
        atomic_json(BASE/'manifest.json',{'at':stamp,'files':changes,'patch':str(path)})
        print('STAGED_PATCH',path);return
    manifest=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    if market_active():raise RuntimeError('Existing runtime still active; no source replaced')
    if manifest['files']!=changes:raise RuntimeError('Source changed since staged review; no source replaced')
    backup=BASE/'backup';backup.mkdir(exist_ok=False)
    for name in FILES:
        old,new=ROOT/name,STAGE/name
        if old.exists():
            saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old,saved)
        old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(new,old)
    path=ROOT/'logs'/f'system-v13-promoted-{stamp}.json'
    atomic_json(path,{'at':stamp,'files':changes,'backup':str(backup),'runtime_was_stopped':True})
    print('PROMOTED',path)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
