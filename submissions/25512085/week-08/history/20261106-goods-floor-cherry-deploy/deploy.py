"""Approved single-commit cherry-pick; seller-only restart, no state reads."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = Path('submissions/25512085/week-08')
OUT = REPO / REL / 'history/20261106-goods-floor-cherry-deploy'
BASE = '822ca8fb48c02841b16604039b50cfacfdddd666'
TARGET = 'b2150117dd67ba7be622d56b3c01c8237340913a'
PYTHON = '/home/comalswjd/venv/bin/python'
RUNTIME = [str(REL / 'seller.py')]
GUARDS = [str(REL / n) for n in ('seller_cash_policy.json', 'guarded_market_kit.py', 'run_guarded_market.sh', 'agent.json')] + ['scripts/market_kit.py']
PROTECTED = [str(REL / 'history/20261105-goods-selection-deploy' / n) for n in ('DEPLOYMENT.md', 'manifest.json')]

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO).decode().strip()

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(name, data):
    with (OUT / name).open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')

def hashes(paths):
    return {p: digest(REPO / p) for p in paths}

def verify_paths(manifest):
    assert not git('diff', 'HEAD', '--name-only', '--', *manifest['paths']), 'tracked_target_dirty'
    tracked = set(git('-c', 'core.quotepath=false', 'ls-files', '--', *manifest['paths']).splitlines())
    for p in manifest['paths']:
        if p not in tracked and (REPO / p).exists():
            raise AssertionError('untracked_incoming_conflict_' + p)
    return []

def applied_head():
    report = json.loads((OUT / 'remote-applied.json').read_text())
    head = report['head']
    assert git('rev-parse', 'HEAD') == head
    assert git('rev-parse', head + '^') == BASE
    assert '(cherry picked from commit ' + TARGET + ')' in git('show', '-s', '--format=%B', head)
    return head

def protected_unchanged(before):
    assert hashes(PROTECTED) == before['protected_evidence_sha256'], 'old_evidence_changed'
    assert not git('ls-files', '--', *PROTECTED), 'old_evidence_adopted'

def snapshot():
    data = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc / 'cmdline').read_bytes().decode().split('\0')[:-1]
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            role = next((r for r, name in [('kit', 'guarded_market_kit.py'), ('seller', 'seller.py'), ('buyer', 'buyer.py')]
                         if any(a == name or a.endswith('/' + name) for a in args)), None)
            if args and Path(args[0]).name == 'cloudflared':
                role = 'tunnel'
            if role:
                p = dict(pid=int(proc.name), ppid=int(fields[1]), starttime=fields[19], role=role,
                         cwd=str((proc / 'cwd').resolve()), exe=str((proc / 'exe').resolve()))
                if '--port' in args:
                    p['port'] = int(args[args.index('--port') + 1])
                data.append(p)
        except (OSError, ValueError):
            continue
    kits = [p for p in data if p['role'] == 'kit' and p['cwd'].startswith(str(REPO))]
    assert len(kits) == 1, 'kit_ambiguous'
    kit = kits[0]
    def belongs(pid):
        for _ in range(8):
            if pid == kit['pid']:
                return True
            if pid <= 1:
                return False
            try:
                pid = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
            except OSError:
                return False
        return False
    owned = sorted([p for p in data if p == kit or belongs(p['ppid'])], key=lambda p: p['pid'])
    assert len([p for p in owned if p['role'] == 'seller']) == 1, 'seller_ambiguous'
    assert len([p for p in owned if p['role'] == 'tunnel']) == 1, 'tunnel_ambiguous'
    return owned

def public_check():
    with urllib.request.urlopen('https://market.ourolabs.ai/api/board', timeout=20) as r:
        board = json.load(r)
        status = r.status
    found = []
    def walk(obj, parents=()):
        if isinstance(obj, dict):
            if obj.get('name') == 'mjforge':
                found.append((obj, parents))
            for value in obj.values():
                walk(value, parents + (obj,))
        elif isinstance(obj, list):
            for value in obj:
                walk(value, parents)
    walk(board)
    shops = [(o, p) for o, p in found if isinstance(o.get('url'), str)]
    assert len(shops) == 1, 'shop_ambiguous'
    shop, parents = shops[0]
    url = shop['url']
    assert url.startswith('https://')
    with urllib.request.urlopen(url.rstrip('/') + '/.well-known/agent-card.json', timeout=20) as r:
        card_status = r.status
    # Only public status scalars, never board payloads or runtime secrets.
    statuses = []
    for o in (shop,) + parents[-2:]:
        statuses.append({k: v for k, v in o.items() if k in
                         ('alive', 'seller', 'buyer', 'online', 'status', 'name') and isinstance(v, (str, bool, int, type(None)))})
    booleans = {}
    def bool_fields(obj, path=''):
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = path + '/' + k
                if type(v) is bool:
                    booleans[key] = v
                elif isinstance(v, dict):
                    bool_fields(v, key)
    participant = next((o for o in reversed(parents) if o.get('name') == 'mjforge'), shop)
    bool_fields(participant)
    roles = {}
    def role_walk(obj, path=''):
        if isinstance(obj, dict):
            if obj.get('name') == 'mjforge' and 'alive' in obj:
                roles[path] = obj['alive']
            for k, v in obj.items():
                role_walk(v, path + '/' + str(k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                role_walk(v, path + '/' + str(i))
    role_walk(board)
    assert any(p.startswith('/shops/') and v is True for p, v in roles.items()), 'seller_not_alive'
    assert any(p.startswith('/buyers/') and v is True for p, v in roles.items()), 'buyer_not_alive'
    return dict(board_http=status, registered_url=url, card_http=card_status,
                role_alive=roles)

def inspect():
    assert git('rev-parse', 'HEAD') == BASE
    assert not git('diff', '--cached', '--name-only'), 'existing_staged_changes'
    manifest = json.loads((OUT / 'manifest.json').read_text())
    identical_evidence = verify_paths(manifest)
    assert not git('diff', '--name-only'), 'existing_tracked_changes'
    assert not git('ls-files', '--', *PROTECTED), 'prior_evidence_already_tracked'
    assert hashes(RUNTIME) == manifest['base_runtime'], 'base_runtime_hash'
    guard_hash = hashes(GUARDS)
    for p in GUARDS:
        expected = hashlib.sha256(subprocess.check_output(['git', 'show', BASE + ':' + p], cwd=REPO)).hexdigest()
        assert guard_hash[p] == expected, 'guard_modified'
    kit_code = (REPO / 'scripts/market_kit.py').read_text()
    assert 'def watchdog(' in kit_code and 'every=60' in kit_code and 'if procs[0].poll() is not None:' in kit_code
    before = dict(head=BASE, processes=snapshot(), runtime_sha256=hashes(RUNTIME), guard_sha256=guard_hash,
                  protected_evidence_sha256=hashes(PROTECTED),
                  watchdog_seconds=60, identical_existing_evidence=identical_evidence, public=public_check())
    save('remote-before.json', before)
    print(json.dumps(before))

def apply():
    before = json.loads((OUT / 'remote-before.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    assert git('rev-parse', 'HEAD') == BASE and snapshot() == before['processes']
    verify_paths(manifest)
    assert hashes(RUNTIME) == before['runtime_sha256'] and hashes(GUARDS) == before['guard_sha256']
    protected_unchanged(before)
    bundle = OUT / 'goods.bundle'
    assert digest(bundle) == manifest['bundle_sha256']
    subprocess.run(['git', 'bundle', 'verify', str(bundle)], cwd=REPO, check=True)
    git('fetch', str(bundle), 'HEAD')
    assert git('rev-parse', 'FETCH_HEAD') == TARGET
    assert git('rev-list', '--count', BASE + '..' + TARGET) == '2'
    assert git('rev-parse', TARGET + '^^') == BASE
    assert sorted(git('-c', 'core.quotepath=false', 'diff', '--name-only', TARGET + '^', TARGET).splitlines()) == manifest['paths']
    skipped = git('-c', 'core.quotepath=false', 'diff', '--name-only', BASE, TARGET + '^').splitlines()
    assert skipped == manifest['skipped_evidence_paths']
    assert all(p.startswith(str(REL / 'history/20261105-goods-selection-deploy') + '/') for p in skipped)
    assert all(hashlib.sha256(subprocess.check_output(['git', 'show', TARGET + '^:' + p], cwd=REPO)).hexdigest() == manifest['base_files'][p]
               for p in manifest['base_files']), 'skipped_commit_has_code_change'
    backup = OUT / 'backup'
    if backup.exists():
        assert all(digest(backup / Path(p).name) == before['runtime_sha256'][p] for p in RUNTIME)
        assert json.loads((OUT / 'backup-sha256.json').read_text()) == before['runtime_sha256']
    else:
        backup.mkdir(exist_ok=False)
        for p in RUNTIME:
            shutil.copy2(REPO / p, backup / Path(p).name)
        save('backup-sha256.json', before['runtime_sha256'])
    assert not git('diff', '--name-only') and not git('diff', '--cached', '--name-only')
    # Reuse the original author identity for this invocation only, without
    # changing repository/global configuration. Original source retained by -x.
    author = git('show', '-s', '--format=%an', TARGET)
    email = git('show', '-s', '--format=%ae', TARGET)
    with (OUT / 'cherry-pick-console.log').open('x') as log:
        proc = subprocess.run(['git', '-c', 'user.name=' + author, '-c', 'user.email=' + email,
                               'cherry-pick', '-x', TARGET], cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode:
        if (REPO / '.git/CHERRY_PICK_HEAD').exists():
            subprocess.run(['git', 'cherry-pick', '--abort'], cwd=REPO, check=True)
        save('cherry-pick-failed.json', dict(returncode=proc.returncode, head=git('rev-parse', 'HEAD'),
                                           protected_sha256=hashes(PROTECTED), runtime_sha256=hashes(RUNTIME)))
        assert git('rev-parse', 'HEAD') == BASE and hashes(RUNTIME) == before['runtime_sha256']
        protected_unchanged(before)
        raise RuntimeError('cherry_pick_failed_aborted_no_signal')
    head = git('rev-parse', 'HEAD')
    assert git('rev-parse', head + '^') == BASE
    assert sorted(git('-c', 'core.quotepath=false', 'diff', '--name-only', BASE, head).splitlines()) == manifest['paths']
    actual_files = hashes(manifest['paths'])
    assert actual_files == manifest['target_files'], 'target_blob_mismatch'
    protected_unchanged(before)
    assert hashes(RUNTIME) == manifest['target_runtime']
    save('remote-applied.json', dict(head=head, parent=BASE, source_commit=TARGET,
                                   runtime_sha256=hashes(RUNTIME), target_file_sha256=actual_files,
                                   protected_evidence_sha256=hashes(PROTECTED)))
    print('Cherry-pick complete; original seller remains running pending tests. HEAD=' + head)

def validate():
    manifest = json.loads((OUT / 'manifest.json').read_text())
    head = applied_head()
    assert hashes(RUNTIME) == manifest['target_runtime']
    # Tracked code only in an isolated tree: no production keys/inventory/state are copied.
    archive = OUT / 'validation.tar'
    subprocess.run(['git', 'archive', '--format=tar', '-o', str(archive), head,
                    str(REL), 'scripts'], cwd=REPO, check=True)
    tree = OUT / 'validation-tree'
    tree.mkdir(exist_ok=False)
    with tarfile.open(archive) as tf:
        assert all(not m.name.startswith('/') and '..' not in Path(m.name).parts and not m.issym() and not m.islnk() for m in tf)
        tf.extractall(tree)
    runner = '''import json, os, socket, sys, unittest
from pathlib import Path
live = Path('/home/comalswjd/ai-agent-engineering-101')
tree = Path.cwd().parents[2]
def deny(event, args):
    if event == 'socket.connect' and args[0].family != socket.AF_UNIX:
        raise AssertionError('external network forbidden')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if live in path.parents and tree not in path.parents and path != tree:
            raise AssertionError('production repository reads forbidden')
sys.addaudithook(deny)
suite = unittest.defaultTestLoader.loadTestsFromNames(['test_goods_floor_offline','test_goods_selection_offline','test_warranty_disclosure_offline','test_grade_warranty_offline']) if sys.argv[1] == 'focused' else unittest.defaultTestLoader.discover('.', pattern='test_*_offline.py')
r = unittest.TextTestRunner(verbosity=2).run(suite)
print('DEPLOY_TEST_RESULT=' + json.dumps(dict(tests_run=r.testsRun, failures=len(r.failures), errors=len(r.errors), success=r.wasSuccessful())))
sys.exit(0 if r.wasSuccessful() else 1)
'''
    reports = {}
    for mode in ('focused', 'all'):
        with (OUT / (mode + '-offline.log')).open('x') as log:
            proc = subprocess.run([PYTHON, '-B', '-c', runner, mode], cwd=tree / REL, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        text = (OUT / (mode + '-offline.log')).read_text()
        reports[mode] = json.loads(next(l.split('=', 1)[1] for l in text.splitlines() if l.startswith('DEPLOY_TEST_RESULT=')))
        assert proc.returncode == 0 and reports[mode]['success'], 'tests_failed_' + mode
    assert reports['focused']['tests_run'] == 72
    assert reports['all']['tests_run'] == 384
    save('remote-tests.json', reports)
    print(json.dumps(reports))

def restart():
    before = json.loads((OUT / 'remote-before.json').read_text())
    manifest = json.loads((OUT / 'manifest.json').read_text())
    tests = json.loads((OUT / 'remote-tests.json').read_text())
    assert tests['focused']['tests_run'] == 72 and all(r['success'] for r in tests.values())
    applied_head()
    assert hashes(RUNTIME) == manifest['target_runtime']
    protected_unchanged(before)
    assert hashes(GUARDS) == before['guard_sha256'] and snapshot() == before['processes']
    assert not (OUT / 'signal.json').exists(), 'already_signalled'
    seller = next(p for p in before['processes'] if p['role'] == 'seller')
    assert seller['cwd'] == str(REPO / REL) and seller['exe'] == '/usr/bin/python3.10'
    save('signal.json', dict(pid=seller['pid'], starttime=seller['starttime'], signal='SIGTERM',
                            in_flight_memory_loss_possible=True, utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    os.kill(seller['pid'], signal.SIGTERM)
    end = time.monotonic() + 175
    while time.monotonic() < end:
        time.sleep(3)
        try:
            after = snapshot()
            new = next(p for p in after if p['role'] == 'seller')
            if new['pid'] == seller['pid'] or new['starttime'] == seller['starttime']:
                continue
            assert [p for p in after if p['role'] != 'seller'] == [p for p in before['processes'] if p['role'] != 'seller']
            assert new['cwd'] == seller['cwd'] and new['exe'] == seller['exe'] and new['port'] == seller['port']
            with urllib.request.urlopen(f"http://127.0.0.1:{new['port']}/.well-known/agent-card.json", timeout=5) as r:
                assert r.status == 200
            protected_unchanged(before)
            report = dict(head=git('rev-parse', 'HEAD'), source_commit=TARGET, parent=BASE,
                          protected_evidence_sha256=hashes(PROTECTED), before=before['processes'], after=after,
                          local_card_http=200, public=public_check(), runtime_sha256=hashes(RUNTIME),
                          guards_unchanged=hashes(GUARDS) == before['guard_sha256'], tests=tests,
                          utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            save('remote-after.json', report)
            print(json.dumps(report))
            return
        except (OSError, AssertionError, StopIteration):
            continue
    raise RuntimeError('bounded_restart_failed_no_further_signals')

if __name__ == '__main__':
    {'inspect': inspect, 'apply': apply, 'validate': validate, 'restart': restart}[sys.argv[1]]()
