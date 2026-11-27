"""Unique synthetic snapshots; no operational files are copied or read."""
import hashlib
import json
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
    tree = OUT/'evidence'/label/'submissions/25512085/week-08'
    tree.mkdir(parents=True, exist_ok=False)
    names = subprocess.check_output(['git', 'ls-files', '--', str(BASE/'*.py')], cwd=REPO, text=True).splitlines()
    sources = {REPO/name for name in names if (REPO/name).parent == BASE}
    if label == 'baseline':
        sources.discard(BASE/'test_buyer_integer_wire_offline.py')
    # Include newly authored tests, not arbitrary untracked operational scripts.
    if label != 'baseline':
        sources.add(BASE/'test_buyer_integer_wire_offline.py')
    for source in sorted(sources):
        shutil.copyfile(source, tree/source.name)
    for name in ('seller_cash_policy.json', 'run_guarded_market.sh'):
        shutil.copyfile(BASE/name, tree/name)
    scripts = tree.parents[2]/'scripts'
    scripts.mkdir()
    shutil.copyfile(REPO/'scripts/market_kit.py', scripts/'market_kit.py')
    with (OUT/(label+'-sha256.json')).open('x', encoding='utf-8') as f:
        json.dump({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tree.iterdir()}, f, indent=2, sort_keys=True)
    runner = OUT/'run_isolated.py'
    if not runner.exists():
        with runner.open('x', encoding='utf-8') as f:
            f.write((BASE/'history/20261106-forum-cohort-deploy-01/run_isolated.py').read_text(encoding='utf-8'))
    home = OUT/'evidence'/('home-'+label)
    home.mkdir(exist_ok=False)
    env = {k:v for k,v in os.environ.items() if k in ('SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT')}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE='1')
    with (OUT/(label+'.log')).open('x', encoding='utf-8') as log:
        p = subprocess.run([sys.executable, '-B', str(runner), 'all', label], env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=240)
    print(next((line for line in (OUT/(label+'.log')).read_text(encoding='utf-8').splitlines()
                if line.startswith('BACKPORT_TEST_RESULT=')), 'No result; inspect new log'))
    sys.exit(p.returncode)
