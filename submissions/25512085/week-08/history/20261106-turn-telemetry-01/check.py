"""Exclusive synthetic snapshots; never read operational data or credentials."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
REPO = BASE.parents[2]

if __name__ == '__main__':
    label = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'all'
    tree = OUT/'evidence'/label/'submissions/25512085/week-08'
    tree.mkdir(parents=True, exist_ok=False)
    names = subprocess.check_output(['git', 'ls-files', '--', str(BASE/'*.py')], cwd=REPO, text=True).splitlines()
    for name in names:
        source = REPO/name
        if source.parent != BASE or (label.startswith('baseline') and source.name == 'test_buyer_turn_telemetry_offline.py'):
            continue
        shutil.copyfile(source, tree/source.name)
    shutil.copyfile(BASE/'seller_cash_policy.json', tree/'seller_cash_policy.json')
    shutil.copyfile(BASE/'run_guarded_market.sh', tree/'run_guarded_market.sh')
    if label.startswith('baseline'):
        original = subprocess.check_output(['git','show','85b39b5^:submissions/25512085/week-08/market_buyer.py'], cwd=REPO)
        with (tree/'market_buyer.py').open('wb') as f:
            f.write(original)
    scripts = OUT/'evidence'/label/'scripts'
    scripts.mkdir()
    shutil.copyfile(REPO/'scripts/market_kit.py', scripts/'market_kit.py')
    new_test = BASE/'test_buyer_turn_telemetry_offline.py'
    if not label.startswith('baseline') and new_test.exists() and not (tree/new_test.name).exists():
        shutil.copyfile(new_test, tree/new_test.name)
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tree.iterdir() if p.is_file()}
    with (OUT/(label+'-source-sha256.json')).open('x', encoding='utf-8') as f:
        json.dump(hashes, f, indent=2, sort_keys=True)
    runner = BASE/'history/20261106-forum-cohort-deploy-01/run_isolated.py'
    target = OUT/'run_isolated.py'
    if not target.exists():
        with target.open('x', encoding='utf-8') as f:
            f.write(runner.read_text(encoding='utf-8'))
    home = OUT/'evidence'/('home-'+label)
    home.mkdir(exist_ok=False)
    env = {k:v for k,v in os.environ.items() if k in ('SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT')}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE='1')
    with (OUT/(label+'.log')).open('x', encoding='utf-8') as log:
        p = subprocess.run([sys.executable, '-B', str(target), mode, label], env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=240)
    lines = (OUT/(label+'.log')).read_text(encoding='utf-8').splitlines()
    print(next((x for x in lines if x.startswith('BACKPORT_TEST_RESULT=')), 'No result; inspect new log'))
    sys.exit(p.returncode)
