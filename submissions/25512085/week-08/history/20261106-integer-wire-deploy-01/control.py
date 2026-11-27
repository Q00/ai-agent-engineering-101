"""Narrow telemetry deployment; exclusive evidence files, existing gcloud auth only."""
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zlib

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
REL = 'submissions/25512085/week-08/'
REMOTE = '/home/comalswjd/ai-agent-engineering-101/' + REL + 'history/20261106-integer-wire-runtime-01'
FLAGS = ['--project=studious-plate-458511-a1', '--zone=us-central1-a', '--quiet']
def sha(b): return hashlib.sha256(b).hexdigest()
def write(n, b):
    with (OUT/n).open('xb') as f: f.write(b)
def ssh(code, label):
    enc = base64.b64encode(zlib.compress(code.encode())).decode()
    cmd = '/home/comalswjd/venv/bin/python -B -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\''+enc+'\')))"'
    assert len(cmd) < 7500
    with (OUT/(label+'-stderr.log')).open('xb') as err:
        p = subprocess.run([shutil.which('gcloud.cmd'),'compute','ssh','comalswjd@market-agent',*FLAGS,'--command='+cmd],stdout=subprocess.PIPE,stderr=err,timeout=600)
    write(label+'-stdout.log', p.stdout)
    assert p.returncode == 0, label+'_failed'
    return p.stdout

def inspect():
    code = (OUT.parent/'20261106-procurement-guard-deploy/inspect_remote.py').read_text()
    code = code.replace('dab7f5d109d9cac9eb16875aa510dd6db715827a','be6a77c835fbcabf7edb75097be318ba4f84eecb')
    code = code.replace("('seller_cash_policy.json', 'agent.json', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')", "('seller_cash_policy.json', 'run_guarded_market.sh')")
    lines = ssh(code,'inspect').decode().splitlines()
    report = json.loads(next(x[7:] for x in lines if x.startswith('REPORT=')))
    blob = base64.b64decode(next(x[8:] for x in lines if x.startswith('ARCHIVE=')))
    assert sha(blob) == report['archive_sha256']
    write('before.json', json.dumps(report,indent=2).encode())
    write('tracked-source.tar', blob)
    for label in ('baseline','sandbox'):
        tree = OUT/'evidence'/label
        tree.mkdir(parents=True,exist_ok=False)
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            assert all(m.isfile() and m.name in report['source_sha256'] for m in tf)
            tf.extractall(tree)
    baseline = (OUT/'evidence/baseline'/REL/'market_buyer.py').read_bytes()
    localbase = subprocess.check_output(['git','show','cc62b97:'+REL+'market_buyer.py'],cwd=REPO)
    assert baseline == localbase, 'buyer_baseline_byte_mismatch'
    assert ast.dump(ast.parse(baseline)) == ast.dump(ast.parse(localbase))
    print(json.dumps(dict(head=report['head'],files=len(report['source_sha256']),buyer_baseline_sha256=sha(baseline),byte_and_ast_equal=True,processes=report['processes'])))

def prepare():
    import difflib
    before=json.loads((OUT/'before.json').read_text())
    assert before['source_sha256'][REL+'market_buyer.py']=='1e71f1e53ebfb1f651f79335c0250db1b93416e56a0638b1f26c0cc588c72889'
    names=['market_buyer.py','test_buyer_integer_wire_offline.py','test_buyer_price_offline.py','test_buyer_retry_offline.py','test_buyer_turn_telemetry_offline.py']
    paths=[REL+n for n in names]
    assert paths[1] not in before['source_sha256']
    source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    assert source=='bfa4e52a507bfd338336ac8e6fb210cf40f99bea'
    after={}; delta=''
    for p in paths:
        dest=OUT/'evidence/sandbox'/p
        old=dest.read_bytes() if dest.exists() else b''
        blob=(REPO/p).read_bytes()
        assert blob==subprocess.check_output(['git','show',source+':'+p],cwd=REPO)
        if p==paths[0]:
            candidate=old
            for a,b in [(b'f"I offer {offer} for this certified item."',b'str(offer)'),(b'f"I offer {seller_ask} for this certified item."',b'str(seller_ask)')]:
                assert candidate.count(a)==1
                candidate=candidate.replace(a,b)
            assert ast.dump(ast.parse(candidate))==ast.dump(ast.parse(blob))
            assert candidate==blob
        elif old:
            baseline=subprocess.check_output(['git','show','cc62b97:'+p],cwd=REPO)
            assert old==baseline, 'test_baseline_drift_'+p
        write(Path(p).name,blob); after[p]=sha(blob)
        delta+=''.join(difflib.unified_diff(old.decode().splitlines(True),blob.decode().splitlines(True),fromfile='a/'+p if old else '/dev/null',tofile='b/'+p))
        dest.write_bytes(blob)
    write('backport.patch',delta.encode())
    author=subprocess.check_output(['git','show','-s','--format=%an%n%ae','0baab04'],cwd=REPO,text=True).splitlines()
    manifest=dict(base=before['head'],source=source,paths=paths,source_sha256=before['source_sha256'],after=after,author_name=author[0],author_email=author[1],remote=REMOTE,attribution={'cc62b97':'initial strict tests; red evidence','0baab04':'two production expressions; three fixture updates; strict tests','bfa4e52':'legacy strict fixture correction'})
    write('manifest.json',json.dumps(manifest,indent=2).encode())
    print(json.dumps(dict(source=source,after=after)))

def stage():
    manifest=json.loads((OUT/'manifest.json').read_text())
    names=['manifest.json','run_isolated.py','remote.py','backport.patch']+[Path(p).name for p in manifest['paths']]
    # SCP transports source-only allowlist; no operational inputs or broad sync.
    with tarfile.open(OUT/'input.tar','x') as tf:
        for n in names: tf.add(OUT/n,arcname=n)
    ssh("from pathlib import Path\nPath("+repr(REMOTE)+").mkdir(exist_ok=False)\nprint('created_exclusive_stage')",'stage-mkdir')
    with (OUT/'stage-scp.log').open('xb') as log:
        p=subprocess.run([shutil.which('gcloud.cmd'),'compute','scp',str(OUT/'input.tar'),'comalswjd@market-agent:'+REMOTE+'/input.tar',*FLAGS],stdout=log,stderr=subprocess.STDOUT,timeout=180)
    assert p.returncode == 0
    ssh("import tarfile\nfrom pathlib import Path\np=Path("+repr(REMOTE)+")\nwith tarfile.open(p/'input.tar') as tf:\n assert all(m.isfile() and m.name in "+repr(names)+" for m in tf)\n tf.extractall(p)\nprint('staged_source_only')",'stage-unpack')

def local():
    home=OUT/'evidence/local-home-v2'; home.mkdir(exist_ok=False)
    env={k:v for k,v in os.environ.items() if k in ('SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT')}
    env.update(HOME=str(home),USERPROFILE=str(home),PYTHONDONTWRITEBYTECODE='1')
    reports={}
    for label,mode in [('sandbox','focused'),('baseline','all'),('sandbox','all')]:
        name='local-v2-'+label+'-'+mode
        with (OUT/(name+'.log')).open('xb') as log:
            p=subprocess.run([sys.executable,'-B',str(OUT/'run_isolated.py'),mode,label],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
        result=next((x.split('=',1)[1] for x in (OUT/(name+'.log')).read_text().splitlines() if x.startswith('BACKPORT_TEST_RESULT=')),None)
        reports[name]=json.loads(result) if result else dict(success=False)
        write(name+'.json',json.dumps(reports[name],indent=2).encode())
        print(json.dumps(reports[name]))
        assert p.returncode==0 and reports[name]['success'], name+'_failed'

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='inspect': inspect()
    elif mode=='prepare': prepare()
    elif mode=='stage': stage()
    elif mode=='local': local()
    elif mode=='export':
        names=['before.json','remote-tests.json','result-baseline-all.json','result-sandbox-focused.json','result-sandbox-all.json','isolated-patch.log','baseline-all.log','sandbox-focused.log','sandbox-all.log','pre-atomic.json','atomic-applied.json','commit.log','applied.json','observation.json','failure-validate.json','failure-apply.json','failure-observe.json']
        code='import base64,json\nfrom pathlib import Path\np=Path('+repr(REMOTE)+')\nprint(json.dumps({n:base64.b64encode((p/n).read_bytes()).decode() for n in '+repr(names)+' if (p/n).exists()}))'
        data=json.loads(ssh(code,'export'))
        dest=OUT/'remote-evidence'; dest.mkdir(exist_ok=False)
        for n,b in data.items():
            assert n in names
            with (dest/n).open('xb') as f: f.write(base64.b64decode(b))
        print('Exported '+str(len(data))+' allowlisted synthetic test / sanitized deployment evidence files; no runtime log download.')
    elif mode=='remote':
        action=sys.argv[2]
        code='MODE='+repr(action)+'\n'+(OUT/'remote.py').read_text()
        print(ssh(code,action).decode())
