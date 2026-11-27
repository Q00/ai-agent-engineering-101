"""Explicit source-only C-first candidate actions. No activation/restart action exists."""
import ast
import base64
import difflib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zlib

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
REL = 'submissions/25512085/week-08/'
REMOTE = '/home/comalswjd/ai-agent-engineering-101/' + REL + 'history/20261107-goods-c-only-runtime-01'
EXPECTED_HEAD = '4a5deb685368343abfd1310c572288a924351b01'
EXPECTED_SELLER = '708845c79e7d9fc33ec0218f93312e452df72f0544beaff9484831ada5517227'
EXPECTED_BUYER = '7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23'
FLAGS = ['--project=studious-plate-458511-a1', '--zone=us-central1-a', '--quiet']


def write(name, data):
    with (OUT/name).open('xb') as stream:
        stream.write(data)


def ssh(code, label):
    encoded = base64.b64encode(zlib.compress(code.encode())).decode()
    command = '/home/comalswjd/venv/bin/python -B -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\''+encoded+'\')))"'
    assert len(command) < 7500
    with (OUT/(label+'-stderr.log')).open('xb') as err:
        result = subprocess.run([shutil.which('gcloud.cmd'), 'compute', 'ssh',
                                 'comalswjd@market-agent', *FLAGS, '--command='+command],
                                stdout=subprocess.PIPE, stderr=err, timeout=600)
    write(label+'-stdout.log', result.stdout)
    assert result.returncode == 0, label+'_failed_see_preserved_logs'
    return result.stdout


def inspect():
    # Read-only source allowlist/proc metadata inspection; no old mutation helper runs.
    code = (OUT.parent/'20261106-procurement-guard-deploy/inspect_remote.py').read_text()
    code = code.replace('dab7f5d109d9cac9eb16875aa510dd6db715827a', EXPECTED_HEAD)
    code = code.replace("('seller_cash_policy.json', 'agent.json', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')",
                        "('seller_cash_policy.json', 'run_guarded_market.sh', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')")
    lines = ssh(code, 'inspect').decode().splitlines()
    report = json.loads(next(line[7:] for line in lines if line.startswith('REPORT=')))
    archive = base64.b64decode(next(line[8:] for line in lines if line.startswith('ARCHIVE=')))
    write('before.json', json.dumps(report, indent=2).encode())
    assert report['head'] == EXPECTED_HEAD
    assert report['source_sha256'][REL+'seller.py'] == EXPECTED_SELLER, 'seller_drift_stop'
    assert report['source_sha256'][REL+'market_buyer.py'] == EXPECTED_BUYER, 'buyer_drift_stop'
    assert hashlib.sha256(archive).hexdigest() == report['archive_sha256']
    previous = json.loads((OUT.parent/'20261107-goods-terms-deploy-01/before.json').read_text())
    assert report['source_sha256'] == previous['source_sha256'], 'allowlist_drift_stop'
    write('tracked-source.tar', archive)
    tree = OUT/'evidence/remote-baseline'
    tree.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
        assert all(m.isfile() and m.name in report['source_sha256'] for m in tf)
        tf.extractall(tree)
    print(json.dumps(dict(head=report['head'], files=len(report['source_sha256']),
                          tracked_dirty=report['tracked_dirty'], processes=report['processes'])))


def prepare():
    before = json.loads((OUT/'before.json').read_text())
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    base = subprocess.check_output(['git', 'rev-parse', '92729a6^'], cwd=REPO, text=True).strip()
    seller_path = REL+'seller.py'
    baseline = (OUT/'evidence/remote-baseline'/seller_path).read_bytes()
    local_base = subprocess.check_output(['git', 'show', base+':'+seller_path], cwd=REPO)
    assert baseline == local_base, 'seller_baseline_bytes_drift_stop'
    assert ast.dump(ast.parse(baseline)) == ast.dump(ast.parse(local_base))
    paths = [seller_path, REL+'test_goods_terms_pilot_offline.py']
    patch = b''
    after = {}
    seller_blob = None
    for path in paths:
        blob = subprocess.check_output(['git', 'show', source+':'+path], cwd=REPO)
        assert (REPO/path).read_bytes().replace(b'\r\n', b'\n') == blob, 'worktree_drift'
        old = (OUT/'evidence/remote-baseline'/path).read_bytes() if path in before['source_sha256'] else b''
        write(Path(path).name, blob)
        if path == seller_path:
            seller_blob = blob
        after[path] = hashlib.sha256(blob).hexdigest()
        patch += ''.join(difflib.unified_diff(old.decode().splitlines(True), blob.decode().splitlines(True),
                         fromfile='a/'+path if old else '/dev/null', tofile='b/'+path)).encode()
    # Price logic must be exactly the pre-A0 implementation, not just similar prices.
    def pricing(blob):
        return next(n for n in ast.parse(blob).body if isinstance(n, ast.FunctionDef)
                    and n.name == 'compute_unit_pricing_and_terms')
    assert ast.dump(pricing(baseline)) == ast.dump(pricing(seller_blob)), 'pricing_AST_drift'
    write('backport.patch', patch)
    manifest = dict(base=before['head'], source=source, local_base=base, paths=paths,
                    source_sha256=before['source_sha256'], after=after, remote=REMOTE,
                    pricing_identical_to_pre_A0_AST=True)
    write('manifest.json', json.dumps(manifest, indent=2).encode())
    print(json.dumps(manifest['after']))


def dependencies():
    # Run only the reviewed pure-local AST equivalence function, not its CLI or mutations.
    namespace = dict(OUT=OUT, REPO=REPO, REL=REL, ast=ast, json=json, write=write)
    # It expects this baseline label. Redirect only the AST input path; no source sync.
    code = (OUT.parent/'20261107-goods-terms-deploy-01/control.py').read_text()
    module = ast.parse(code)
    function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'dependencies')
    fragment = ast.get_source_segment(code, function)
    fragment = fragment.replace("OUT/'evidence/baseline'", "OUT/'evidence/remote-baseline'")
    fragment = fragment.replace("runner = (OUT.parent/'20261107-goods-terms-01/run_isolated.py').read_bytes()",
                                "runner = (OUT/'run_isolated.py').read_bytes()")
    # Runner is already authored/verified here; preserve it rather than overwrite it.
    fragment = fragment.replace("write('run_isolated.py', runner)", "assert runner == (OUT/'run_isolated.py').read_bytes()")
    exec(compile(fragment, '<reviewed-local-dependency-AST-check>', 'exec'), namespace)
    namespace['dependencies']()


def stage():
    assert (OUT/'dependency-equivalence.json').exists()
    manifest = json.loads((OUT/'manifest.json').read_text())
    names = ['manifest.json', 'run_isolated.py', 'validate_remote.py', 'backport.patch',
             'dependency-equivalence.json'] + [Path(path).name for path in manifest['paths']]
    with tarfile.open(OUT/'input.tar', 'x') as tf:
        for name in names:
            tf.add(OUT/name, arcname=name)
    # Only the new exclusive history is created. No production destination exists here.
    ssh('from pathlib import Path\nPath('+repr(REMOTE)+').mkdir(exist_ok=False)\nprint("created_exclusive_history")', 'stage-mkdir')
    with (OUT/'stage-scp.log').open('xb') as log:
        result = subprocess.run([shutil.which('gcloud.cmd'), 'compute', 'scp', str(OUT/'input.tar'),
                                 'comalswjd@market-agent:'+REMOTE+'/input.tar', *FLAGS],
                                stdout=log, stderr=subprocess.STDOUT, timeout=180)
    assert result.returncode == 0
    ssh('import tarfile\nfrom pathlib import Path\np=Path('+repr(REMOTE)+')\nwith tarfile.open(p/"input.tar") as tf:\n assert all(m.isfile() and m.name in '+repr(names)+' for m in tf)\n tf.extractall(p)\nprint("source_only_staged_not_deployed")', 'stage-unpack')


def export():
    names = ['pre-validation.json', 'isolated-patch.log', 'remote-tests.json']
    for label, mode in [('baseline', 'all'), ('baseline-pilot', 'focused'), ('sandbox', 'focused'), ('sandbox', 'all')]:
        name = label+'-'+mode
        names.extend([name+'.log', 'result-'+name+'.json'])
    code = 'import base64,json\nfrom pathlib import Path\np=Path('+repr(REMOTE)+')\nprint(json.dumps({n:base64.b64encode((p/n).read_bytes()).decode() for n in '+repr(names)+' if (p/n).exists()}))'
    data = json.loads(ssh(code, 'export'))
    dest = OUT/'remote-evidence'
    dest.mkdir(exist_ok=False)
    for name, blob in data.items():
        assert name in names
        with (dest/name).open('xb') as stream:
            stream.write(base64.b64decode(blob))
    print('Exported '+str(len(data))+' synthetic/sanitized files only.')


if __name__ == '__main__':
    action = sys.argv[1]
    if action in ('inspect', 'prepare', 'dependencies', 'stage', 'export'):
        globals()[action]()
    elif action == 'validate':
        print(ssh((OUT/'validate_remote.py').read_text(), 'validate').decode())
    else:
        raise ValueError('No such action; activation/restart intentionally unavailable')
