"""Narrow buyer backport orchestration; exclusive evidence outputs only."""
import base64
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
OLD = OUT.parent/'20261106-procurement-guard-deploy'
REPO = OUT.parents[4]
FLAGS = ['--project=studious-plate-458511-a1', '--zone=us-central1-a', '--quiet']
GCLOUD = shutil.which('gcloud.cmd')
def sha(data): return hashlib.sha256(data).hexdigest()
def write(name, data):
    with (OUT/name).open('xb') as f: f.write(data)
def ssh(code, label):
    encoded = base64.b64encode(zlib.compress(code.encode())).decode()
    cmd = "/home/comalswjd/venv/bin/python -B -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('"+encoded+"')))\""
    assert len(cmd)<7500, 'command_too_large'
    with (OUT/(label+'-stderr.log')).open('xb') as err:
        result = subprocess.run([GCLOUD,'compute','ssh','comalswjd@market-agent',*FLAGS,'--command='+cmd],stdout=subprocess.PIPE,stderr=err,timeout=600)
    write(label+'-stdout.log',result.stdout)
    assert result.returncode == 0, label+'_failed'
    return result.stdout

def inspect():
    code = (OLD/'inspect_remote.py').read_text().replace('dab7f5d109d9cac9eb16875aa510dd6db715827a','594e1f9bd4de1662c46e4816fabe5a096ab1233d')
    code = code.replace("('seller_cash_policy.json', 'agent.json', 'GRADE_WARRANTY_POLICY.md', 'POLICY.md')", "('seller_cash_policy.json', 'run_guarded_market.sh')")
    raw = ssh(code,'inspect').decode().splitlines()
    report = json.loads(next(x[7:] for x in raw if x.startswith('REPORT=')))
    archive = base64.b64decode(next(x[8:] for x in raw if x.startswith('ARCHIVE=')))
    assert sha(archive)==report['archive_sha256']
    write('before.json',json.dumps(report,indent=2).encode())
    write('tracked-source.tar',archive)
    for label in ('baseline','sandbox'):
        tree=OUT/'evidence'/label
        tree.mkdir(parents=True,exist_ok=False)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
            assert all(m.isfile() and m.name in report['source_sha256'] for m in tf)
            tf.extractall(tree)
    print(json.dumps(dict(head=report['head'],files=len(report['source_sha256']),processes=report['processes']),indent=2))

if __name__=='__main__':
    if sys.argv[1]=='inspect': inspect()
    elif sys.argv[1]=='remote':
        mode=sys.argv[2]
        code=(OUT/'remote.py').read_text()
        suffix=('-'+sys.argv[3]) if len(sys.argv)>3 else ''
        result=ssh('MODE='+repr(mode)+'\n'+code,mode+suffix)
        if mode=='export':
            dest=OUT/('remote-export'+suffix); dest.mkdir(exist_ok=False)
            files=json.loads(result)
            for name,data in files.items():
                assert not Path(name).is_absolute() and '..' not in Path(name).parts
                p=dest/name; p.parent.mkdir(parents=True,exist_ok=True)
                with p.open('xb') as f: f.write(base64.b64decode(data))
            print('Exported '+str(len(files))+' allowlisted job evidence files')
        else:
            print(result.decode())
