"""Source-only VM backport using existing gcloud authentication; exclusive logs."""
import base64
import ast
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
REMOTE = '/home/comalswjd/ai-agent-engineering-101/' + REL + 'history/20261107-goods-terms-runtime-01'
FLAGS = ['--project=studious-plate-458511-a1', '--zone=us-central1-a', '--quiet']

def write(name, data):
    with (OUT/name).open('xb') as f:
        f.write(data)

def ssh(code, label):
    enc = base64.b64encode(zlib.compress(code.encode())).decode()
    cmd = '/home/comalswjd/venv/bin/python -B -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\''+enc+'\')))"'
    assert len(cmd) < 7500
    with (OUT/(label+'-stderr.log')).open('xb') as err:
        p = subprocess.run([shutil.which('gcloud.cmd'), 'compute', 'ssh', 'comalswjd@market-agent', *FLAGS, '--command='+cmd], stdout=subprocess.PIPE, stderr=err, timeout=600)
    write(label+'-stdout.log', p.stdout)
    assert p.returncode == 0, label+'_failed'
    return p.stdout

def inspect():
    code = (OUT.parent/'20261106-procurement-guard-deploy/inspect_remote.py').read_text()
    code = code.replace('dab7f5d109d9cac9eb16875aa510dd6db715827a', '4a5deb685368343abfd1310c572288a924351b01')
    code = code.replace("('seller_cash_policy.json', 'agent.json', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')", "('seller_cash_policy.json', 'run_guarded_market.sh', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')")
    lines = ssh(code, 'inspect').decode().splitlines()
    report = json.loads(next(x[7:] for x in lines if x.startswith('REPORT=')))
    blob = base64.b64decode(next(x[8:] for x in lines if x.startswith('ARCHIVE=')))
    assert hashlib.sha256(blob).hexdigest() == report['archive_sha256']
    assert report['source_sha256'][REL+'market_buyer.py'] == '7e7a8a5905b29b9c583840e4a16ead2ed5a6d5f6698b5cda22c1709c2b60fc23'
    write('before.json', json.dumps(report, indent=2).encode())
    write('tracked-source.tar', blob)
    for label in ('baseline', 'sandbox'):
        tree = OUT/'evidence'/label
        tree.mkdir(parents=True, exist_ok=False)
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            assert all(m.isfile() and m.name in report['source_sha256'] for m in tf)
            tf.extractall(tree)
    print(json.dumps(dict(head=report['head'], files=len(report['source_sha256']), processes=report['processes'])))

def prepare():
    before = json.loads((OUT/'before.json').read_text())
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    assert source.startswith('0a67422')
    base = subprocess.check_output(['git', 'rev-parse', '92729a6^'], cwd=REPO, text=True).strip()
    seller_path = REL+'seller.py'
    remote_seller = (OUT/'evidence/baseline'/seller_path).read_bytes()
    local_base = subprocess.check_output(['git', 'show', base+':'+seller_path], cwd=REPO)
    assert ast.dump(ast.parse(remote_seller)) == ast.dump(ast.parse(local_base)), 'seller_baseline_ast_drift'
    assert remote_seller == local_base, 'seller_baseline_byte_drift'
    # Check the split local facade against every monolithic function referenced by seller.
    local_policy = ast.parse((REPO/REL/'seller_cash_policy.py').read_bytes())
    remote_policy = ast.parse((OUT/'evidence/baseline'/REL/'seller_cash_policy.py').read_bytes())
    attrs = sorted({n.attr for n in ast.walk(ast.parse((REPO/seller_path).read_bytes()))
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'cash_policy'})
    evidence = {'seller_baseline_byte_ast_equal': True, 'cash_policy_references': attrs}
    write('dependency-v2-initial.json', json.dumps(evidence, indent=2).encode())
    # Dependency equivalence is completed separately before any remote staging.
    print(json.dumps(evidence))
    patch = b''
    paths = [seller_path, REL+'test_goods_terms_pilot_offline.py']
    after = {}
    for p in paths:
        candidate = subprocess.check_output(['git', 'show', source+':'+p], cwd=REPO)
        assert (REPO/p).read_bytes().replace(b'\r\n', b'\n') == candidate, 'worktree_drift'
        old = (OUT/'evidence/baseline'/p).read_bytes() if p in before['source_sha256'] else b''
        if p != seller_path:
            assert not (OUT/'evidence/baseline'/p).exists()
        write(Path(p).name, candidate)
        (OUT/'evidence/sandbox'/p).write_bytes(candidate)
        after[p] = hashlib.sha256(candidate).hexdigest()
        patch += ''.join(difflib.unified_diff(old.decode().splitlines(True), candidate.decode().splitlines(True), fromfile='a/'+p if old else '/dev/null', tofile='b/'+p)).encode()
    write('backport.patch', patch)
    manifest = dict(base=before['head'], source=source, local_base=base, paths=paths, source_sha256=before['source_sha256'], after=after, remote=REMOTE)
    write('manifest.json', json.dumps(manifest, indent=2).encode())

def dependencies():
    import copy
    remote = ast.parse((OUT/'evidence/baseline'/REL/'seller_cash_policy.py').read_bytes())
    local = ast.parse((REPO/REL/'seller_cash_policy.py').read_bytes())
    def funcs(tree):
        return {n.name:n for n in tree.body if isinstance(n, ast.FunctionDef)}
    rf, lf = funcs(remote), funcs(local)
    reports = {}
    for name in ('config', 'number'):
        assert ast.dump(rf[name]) == ast.dump(lf[name]), name
        reports[name] = 'identical AST'
    class Unbind(ast.NodeTransformer):
        def visit_Attribute(self, node):
            if isinstance(node.value, ast.Name) and node.value.id == 'policy':
                return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
            return self.generic_visit(node)
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'minimum_contribution':
                assert isinstance(node.args[0], ast.Name) and node.args[0].id == 'policy'
                node.args.pop(0)
            return self.generic_visit(node)
    for module, names in [('_cash_pricing', ('warranty_hours', 'minimum_contribution', 'profit_floor', 'reference_price', 'quote')),
                          ('_cash_risk', ('refund_probability',))]:
        internal = funcs(ast.parse((REPO/REL/(module+'.py')).read_bytes()))
        for name in names:
            fn = copy.deepcopy(internal[name])
            assert fn.args.args.pop(0).arg == 'policy'
            fn = Unbind().visit(fn)
            assert ast.dump(fn) == ast.dump(rf[name]), 'dependency_AST_mismatch_'+name
            if name != 'minimum_contribution':
                wrapper = lf[name]
                call = wrapper.body[-1].value
                assert isinstance(call.func, ast.Attribute) and call.func.value.id == module and call.func.attr == name
                assert ast.unparse(call.args[0]) == '_sys.modules[__name__]'
                assert [ast.unparse(x) for x in call.args[1:]] == [a.arg for a in wrapper.args.args]
                assert ast.dump(wrapper.args) == ast.dump(rf[name].args)
            reports[name] = 'identical AST after removing live facade parameter; wrapper checked'
    for name in ('PRIOR_BREAK',):
        def val(tree):
            return next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
        assert ast.dump(val(remote)) == ast.dump(val(local))
        reports[name] = 'identical AST'
    assert (REPO/REL/'seller_cash_policy.json').read_bytes().replace(b'\r\n', b'\n') == (OUT/'evidence/baseline'/REL/'seller_cash_policy.json').read_bytes()
    reports['configuration'] = 'byte identical after CRLF normalization'
    write('dependency-equivalence.json', json.dumps(reports, indent=2).encode())
    runner = (OUT.parent/'20261107-goods-terms-01/run_isolated.py').read_bytes()
    write('run_isolated.py', runner)
    print(json.dumps(reports))

def stage():
    assert (OUT/'dependency-equivalence.json').exists()
    manifest = json.loads((OUT/'manifest.json').read_text())
    names = ['manifest.json', 'run_isolated.py', 'validate_remote.py', 'backport.patch', 'dependency-equivalence.json'] + [Path(p).name for p in manifest['paths']]
    with tarfile.open(OUT/'input.tar', 'x') as tf:
        for n in names:
            tf.add(OUT/n, arcname=n)
    ssh('from pathlib import Path\nPath('+repr(REMOTE)+').mkdir(exist_ok=False)\nprint("created_exclusive_stage")', 'stage-mkdir')
    with (OUT/'stage-scp.log').open('xb') as log:
        p = subprocess.run([shutil.which('gcloud.cmd'), 'compute', 'scp', str(OUT/'input.tar'), 'comalswjd@market-agent:'+REMOTE+'/input.tar', *FLAGS], stdout=log, stderr=subprocess.STDOUT, timeout=180)
    assert p.returncode == 0
    ssh('import tarfile\nfrom pathlib import Path\np=Path('+repr(REMOTE)+')\nwith tarfile.open(p/"input.tar") as tf:\n assert all(m.isfile() and m.name in '+repr(names)+' for m in tf)\n tf.extractall(p)\nprint("staged_source_only_no_live_changes")', 'stage-unpack')

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'inspect':
        inspect()
    elif mode == 'prepare':
        prepare()
    elif mode == 'dependencies':
        dependencies()
    elif mode == 'stage':
        stage()
    elif mode == 'validate':
        print(ssh((OUT/'validate_remote.py').read_text(), 'validate').decode())
    elif mode == 'observe-hold':
        print(ssh((OUT/'observe_hold.py').read_text(), 'observe-hold').decode())
    elif mode == 'export':
        names = ['pre-validation.json', 'isolated-patch.log', 'remote-tests.json', 'hold-observation.json']
        for label, test_mode in [('baseline', 'all'), ('baseline-pilot', 'focused'), ('sandbox', 'focused'), ('sandbox', 'all')]:
            n = label+'-'+test_mode
            names.extend([n+'.log', 'result-'+n+'.json'])
        code = 'import base64,json\nfrom pathlib import Path\np=Path('+repr(REMOTE)+')\nprint(json.dumps({n:base64.b64encode((p/n).read_bytes()).decode() for n in '+repr(names)+' if (p/n).exists()}))'
        data = json.loads(ssh(code, 'export'))
        dest = OUT/'remote-evidence'
        dest.mkdir(exist_ok=False)
        for n, blob in data.items():
            assert n in names
            with (dest/n).open('xb') as f:
                f.write(base64.b64decode(blob))
        print('Exported '+str(len(data))+' synthetic/sanitized evidence files; no runtime logs/state copied.')
    elif mode == 'read-runtime':
        # Code only. No environment, runtime state, credentials, or raw logs.
        code = "from pathlib import Path\np=Path('/home/comalswjd/ai-agent-engineering-101')\n"
        code += "for n in ['scripts/market_kit.py','submissions/25512085/week-08/run_guarded_market.sh']:\n print('SOURCE_FILE='+n)\n print((p/n).read_text())\n"
        print(ssh(code, 'read-runtime').decode())
