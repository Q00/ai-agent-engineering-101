"""Copy only tracked code, block network/processes and confine all writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
SOURCE = REPO / 'submissions/25512085/week-08'
label, mode = sys.argv[1:3]
tree = OUT / 'evidence' / label
tree.mkdir(parents=True, exist_ok=False)
cwd = tree / 'submissions/25512085/week-08'
cwd.mkdir(parents=True)
# No state, key, env, market data, logs or untracked originals are copied.
tracked = subprocess.check_output(['git', 'ls-files', 'submissions/25512085/week-08', 'scripts'], cwd=REPO, text=True).splitlines()
for rel in tracked:
    src = REPO / rel
    if (src.parent == SOURCE and src.suffix == '.py') or (src.parent == REPO/'scripts' and src.suffix == '.py') or rel.endswith('/seller_cash_policy.json'):
        dst = tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
# New focused tests are explicitly allowed before their first commit.
test = SOURCE / 'test_seller_locks_cache_offline.py'
if test.exists():
    (cwd / test.name).write_bytes(test.read_bytes())
scratch = tree / 'scratch'
scratch.mkdir()
home = scratch / 'home'
home.mkdir()
for key in ('HOME', 'USERPROFILE'):
    os.environ[key] = str(home)
os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
os.environ['MARKET_DIR'] = str(scratch / 'market')
tempfile.tempdir = str(scratch)
original = tempfile.mkdtemp
tempfile.mkdtemp = lambda suffix=None, prefix=None, dir=None: original(suffix=suffix, prefix=prefix, dir=str(scratch))

def audit(event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'socket.connect', 'socket.sendto', 'socket.getaddrinfo'):
        raise AssertionError('network/child process forbidden: ' + event)
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if REPO in path.parents and tree not in path.parents and REPO/'.venv' not in path.parents:
            raise AssertionError('live repository read forbidden: ' + path.name)
        flags = args[2]
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) and tree not in path.parents:
            raise AssertionError('write outside evidence forbidden')
sys.addaudithook(audit)
sys.dont_write_bytecode = True
os.chdir(cwd)
sys.path.insert(0, str(cwd))
import test_visibility_guard_offline as visibility
for name in ('test_bash_stub_preserves_auth_loop_and_other_python_forwarding', 'test_bash_stub_structure_failure_cannot_fall_back_to_vanilla'):
    setattr(visibility.LauncherTests, name, unittest.skip('isolated runner forbids child processes')(getattr(visibility.LauncherTests, name)))
suite = (unittest.defaultTestLoader.loadTestsFromName('test_seller_locks_cache_offline') if mode == 'focused'
         else unittest.defaultTestLoader.discover('.', pattern='test_*_offline.py'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('RESULT=' + json.dumps(dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped))))
sys.exit(not result.wasSuccessful())
