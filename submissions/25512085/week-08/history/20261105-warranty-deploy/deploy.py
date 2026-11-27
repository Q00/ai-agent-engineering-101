"""Bounded one-commit deployment; never reads credentials or runtime state."""
import datetime
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import urllib.request
import unittest

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = Path('submissions/25512085/week-08')
OUT = REPO / REL / 'history/20261105-warranty-deploy'
BASE = '115d07f82589b9f70691d7d72be8ace42db5bda7'
TARGET = '5c5298a9ce2f3d420e2fd166acbbd3848a026452'
PYTHON = '/home/comalswjd/venv/bin/python'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO).decode().strip()

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def processes():
    result = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc / 'cmdline').read_bytes().decode().split('\0')[:-1]
            cwd = str((proc / 'cwd').resolve())
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            role = None
            if any(a.endswith('/guarded_market_kit.py') or a == 'guarded_market_kit.py' for a in args):
                role = 'kit'
            elif any(a.endswith('/seller.py') or a == 'seller.py' for a in args):
                role = 'seller'
            elif args and Path(args[0]).name == 'cloudflared':
                role = 'tunnel'
            elif any(a.endswith('/buyer.py') or a == 'buyer.py' for a in args):
                role = 'buyer'
            if role:
                item = dict(pid=int(proc.name), ppid=int(fields[1]), starttime=fields[19],
                            role=role, cwd=cwd, exe=str((proc / 'exe').resolve()))
                for i, arg in enumerate(args):
                    if arg == '--port' and i + 1 < len(args):
                        item['port'] = int(args[i+1])
                result.append(item)
        except (OSError, ValueError, ProcessLookupError):
            continue
    return result

def snapshot():
    data = processes()
    kits = [p for p in data if p['role'] == 'kit' and p['cwd'].startswith(str(REPO))]
    assert len(kits) == 1, 'kit_identity_ambiguous'
    kit = kits[0]
    # Inspect intermediate shells internally without printing their arguments.
    def belongs(pid):
        for _ in range(8):
            if pid == kit['pid']:
                return True
            if pid <= 1:
                return False
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                pid = int(fields[1])
            except OSError:
                return False
        return False
    owned = [p for p in data if p == kit or belongs(p['ppid'])]
    assert len([p for p in owned if p['role'] == 'seller']) == 1, 'seller_identity_ambiguous'
    return owned

def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + '\n')

def public_check():
    with urllib.request.urlopen('https://market.ourolabs.ai/api/board', timeout=20) as r:
        board = json.load(r)
        status = r.status
    matches = []
    ancestors = []
    def walk(obj, parents=()):
        if isinstance(obj, dict):
            if obj.get('name') == 'mjforge':
                matches.append(obj)
                ancestors.extend(parents[-2:])
            for value in obj.values():
                walk(value, parents + (obj,))
        elif isinstance(obj, list):
            for value in obj:
                walk(value, parents)
    walk(board)
    shops = [m for m in matches if isinstance(m.get('url'), str)]
    assert len(shops) == 1, 'public_shop_ambiguous'
    shop = shops[0]
    alive_values = [o['alive'] for o in [shop] + ancestors if type(o.get('alive')) is bool]
    url = shop['url']
    assert url.startswith('https://'), 'public_url_invalid'
    with urllib.request.urlopen(url.rstrip('/') + '/.well-known/agent-card.json', timeout=20) as r:
        card_status = r.status
    return dict(board_http=status, name='mjforge', alive=alive_values[-1] if alive_values else None,
                ancestor_fields=[sorted(o.keys()) for o in ancestors],
                public_shop_fields=sorted(shop.keys()),
                public_status={k: shop[k] for k in ['status', 'online', 'last_seen', 'last_seen_at', 'is_alive'] if k in shop},
                registered_url=url, card_http=card_status)

def apply():
    assert git('rev-parse', 'HEAD') == BASE, 'head_changed'
    bundle = OUT / 'warranty.bundle'
    assert digest(bundle) == 'b588ea31764e8a50003c5e974f35fd53b2511964c7ae2d38f2e9025bc22e270e'
    subprocess.run(['git', 'bundle', 'verify', str(bundle)], cwd=REPO, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    git('fetch', str(bundle), 'HEAD')
    assert git('rev-parse', 'FETCH_HEAD') == TARGET
    assert git('rev-parse', TARGET + '^') == BASE
    paths = git('diff-tree', '--no-commit-id', '--name-only', '-r', TARGET).splitlines()
    assert len(paths) == 9 and all(p.startswith(str(REL) + '/') for p in paths)
    assert not git('status', '--porcelain', '--', *paths), 'target_conflict'
    baseline = json.loads((OUT / 'remote-before.json').read_text())
    assert snapshot() == baseline['processes'], 'process_changed_before_apply'
    backup = OUT / 'backup'
    backup.mkdir(exist_ok=False)
    hashes = {}
    for p in paths + [str(REL / 'agent.json'), str(REL / 'run_guarded_market.sh')]:
        source = REPO / p
        if source.exists():
            dest = backup / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            hashes[p] = digest(source)
    assert hashes[str(REL / 'seller.py')] == baseline['seller_sha256']
    save('backup-sha256.json', hashes)
    git('merge', '--ff-only', TARGET)
    validate()

def validate():
    assert git('rev-parse', 'HEAD') == TARGET
    expected = hashlib.sha256(subprocess.check_output(
        ['git', 'show', TARGET + ':' + str(REL / 'seller.py')], cwd=REPO)).hexdigest()
    assert digest(REPO / REL / 'seller.py') == expected == '79b0f54bb024718e0ac44d41b878b2456fa029d04cdd50c7cc7e9a9e5d189dac'
    # Imported test fixtures redirect key/state writes to unique temporary dirs;
    # their socket.connect patch blocks all external I/O. No actual keys are read.
    os.chdir(REPO / REL)
    sys.path.insert(0, str(REPO / REL))
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        'test_warranty_disclosure_offline', 'test_grade_warranty_offline'])
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    report = dict(head=git('rev-parse', 'HEAD'), seller_sha256=expected,
                  tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  success=result.wasSuccessful(), bundle_sha256=digest(OUT / 'warranty.bundle'),
                  public_before=public_check())
    save('remote-applied.json', report)
    print(json.dumps(report))
    assert result.wasSuccessful() and result.testsRun == 27, 'offline_tests_failed'

def restart():
    before = json.loads((OUT / 'remote-before.json').read_text())['processes']
    applied = json.loads((OUT / 'remote-applied.json').read_text())
    assert applied['success'] and applied['tests_run'] == 27
    assert git('rev-parse', 'HEAD') == TARGET
    assert digest(REPO / REL / 'seller.py') == applied['seller_sha256']
    assert snapshot() == before, 'process_changed_before_signal'
    seller = next(p for p in before if p['role'] == 'seller')
    assert seller['cwd'] == str(REPO / REL) and seller['exe'] == '/usr/bin/python3.10'
    # Exactly one SIGTERM; no shell/kit/buyer/tunnel signals. In-flight memory is lost.
    os.kill(seller['pid'], signal.SIGTERM)
    save('signal.json', dict(pid=seller['pid'], starttime=seller['starttime'], signal='SIGTERM',
                            time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            in_flight_memory_loss_possible=True))
    end = time.monotonic() + 175
    after = None
    while time.monotonic() < end:
        time.sleep(3)
        try:
            current = snapshot()
            new = next(p for p in current if p['role'] == 'seller')
            if new['pid'] == seller['pid'] or new['starttime'] == seller['starttime']:
                continue
            assert [p for p in current if p['role'] != 'seller'] == [p for p in before if p['role'] != 'seller'], 'non_seller_changed'
            with urllib.request.urlopen(f"http://127.0.0.1:{new['port']}/.well-known/agent-card.json", timeout=5) as r:
                assert r.status == 200
            after = current
            break
        except (AssertionError, OSError, StopIteration):
            continue
    assert after is not None, 'bounded_restart_failed_use_documented_rollback'
    report = dict(head=git('rev-parse', 'HEAD'), before=before, after=after,
                  local_card_http=200, public_after=public_check(),
                  seller_sha256=digest(REPO / REL / 'seller.py'),
                  preserved_roles=['kit', 'tunnel'], buyer='in_process_kit_unchanged',
                  finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save('remote-after.json', report)
    print(json.dumps(report))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == 'inspect':
        before = snapshot()
        report = dict(head=git('rev-parse', 'HEAD'), processes=before,
                      seller_sha256=digest(REPO / REL / 'seller.py'),
                      code_sha256={str(p): digest(REPO / p) for p in
                          [REL / 'guarded_market_kit.py', Path('scripts/market_kit.py')]})
        save('remote-before.json', report)
        print(json.dumps(report))
    elif sys.argv[1] == 'apply':
        apply()
    elif sys.argv[1] == 'restart':
        restart()
    elif sys.argv[1] == 'validate':
        validate()
    elif sys.argv[1] == 'public':
        print(json.dumps(public_check()))

if __name__ == '__main__':
    main()
