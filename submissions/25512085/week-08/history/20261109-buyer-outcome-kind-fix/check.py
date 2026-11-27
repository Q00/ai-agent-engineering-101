"""Unique isolated source trees; no production state, credentials, or network."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import uuid

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
REPO = BASE.parents[2]


def child(tree, pattern):
    scratch = tree.parent / ('scratch-' + tree.name)
    scratch.mkdir()
    home = tree.parent / ('home-' + tree.name)
    allowed = (tree, scratch, home)
    os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
    tempfile.tempdir = str(scratch)
    original = tempfile.mkdtemp
    tempfile.mkdtemp = lambda suffix=None, prefix=None, dir=None: original(
        suffix=suffix, prefix=prefix, dir=str(scratch))

    def audit(event, args):
        if event in ('socket.connect', 'socket.sendto', 'socket.getaddrinfo'):
            raise AssertionError('external network forbidden')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            isolated = any(root == path or root in path.parents for root in allowed)
            if REPO in path.parents and not isolated and REPO / '.venv' not in path.parents:
                raise AssertionError('original repository/runtime read forbidden: ' + path.name)
            flags = args[2]
            if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                if not isolated:
                    raise AssertionError('write outside isolated tree forbidden')
    sys.addaudithook(audit)
    cwd = tree / 'submissions/25512085/week-08'
    os.chdir(cwd)
    sys.path.insert(0, str(cwd))
    sys.argv = ['unittest', 'discover', '-p', pattern, '-v']
    runpy.run_module('unittest', run_name='__main__')


if __name__ == '__main__':
    if sys.argv[1] == '--child':
        child(Path(sys.argv[2]), sys.argv[3])
    else:
        label = sys.argv[1]
        pattern = sys.argv[2] if len(sys.argv) > 2 else 'test_*_offline.py'
        unique = label + '-' + uuid.uuid4().hex[:12]
        tree = OUT / 'evidence' / unique
        cwd = tree / 'submissions/25512085/week-08'
        cwd.mkdir(parents=True, exist_ok=False)
        names = subprocess.check_output(['git', 'ls-files', '--', str(BASE / '*.py')], cwd=REPO, text=True).splitlines()
        sources = {REPO / name for name in names if (REPO / name).parent == BASE}
        sources.update(BASE / name for name in ('buyer_outcomes.py', 'test_buyer_outcomes_offline.py',
                                               'test_buyer_outcomes_p2_offline.py',
                                               'test_buyer_outcomes_kind_offline.py'))
        for source in sources:
            if label == 'baseline-repro' and source.name == 'buyer_outcomes.py':
                # Historical CODE only, never historical journal/state; preserve failure evidence.
                (cwd / source.name).write_bytes(subprocess.check_output(
                    ['git', 'show', 'd8e540e:' + source.relative_to(REPO).as_posix()], cwd=REPO))
            else:
                shutil.copyfile(source, cwd / source.name)
        for name in ('seller_cash_policy.json', 'run_guarded_market.sh'):
            shutil.copyfile(BASE / name, cwd / name)
        scripts = tree / 'scripts'
        scripts.mkdir()
        shutil.copyfile(REPO / 'scripts/market_kit.py', scripts / 'market_kit.py')
        home = tree.parent / ('home-' + tree.name)
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if k in ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT')}
        env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE='1')
        log = OUT / (unique + '.log')
        with log.open('x', encoding='utf-8') as output:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                                     '--child', str(tree), pattern], env=env,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=240)
        print(json.dumps({'log': str(log.relative_to(REPO)), 'exit_code': result.returncode}))
        sys.exit(result.returncode)
