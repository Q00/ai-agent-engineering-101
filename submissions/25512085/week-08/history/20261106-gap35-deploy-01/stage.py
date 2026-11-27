"""Stage only this operation's patch/manifest/test runner, no runtime synchronization."""
import base64
import io
import json
import subprocess
import tarfile
from control import OUT, GCLOUD, FLAGS, sha, ssh, write

remote='/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08/history/20261106-gap35-runtime-01'
manifest=json.loads((OUT/'manifest.json').read_text())
names=['manifest.json','backport.patch','run_isolated.py','remote.py']
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w') as tf:
    for name in names:
        data=(OUT/name).read_bytes(); m=tarfile.TarInfo(name); m.size=len(data); tf.addfile(m,io.BytesIO(data))
if (OUT/'input.tar').exists():
    assert (OUT/'input.tar').read_bytes()==buf.getvalue()
else:
    write('input.tar',buf.getvalue())
code="""import hashlib,subprocess,json
from pathlib import Path
r=Path('/home/comalswjd/ai-agent-engineering-101')
def git(*a): return subprocess.check_output(['git',*a],cwd=r).decode().strip()
assert git('rev-parse','HEAD')==M['base'] and not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
assert all(hashlib.sha256((r/p).read_bytes()).hexdigest()==h for p,h in M['source_sha256'].items())
Path(REMOTE).mkdir(exist_ok=False)
print(json.dumps(dict(stage='created_evidence_only',path=REMOTE)))
"""
print(ssh('M='+repr(manifest)+'\nREMOTE='+repr(remote)+'\n'+code,'stage-mkdir-v2').decode())
with (OUT/'stage-scp.log').open('xb') as f:
    p=subprocess.run([GCLOUD,'compute','scp',str(OUT/'input.tar'),'comalswjd@market-agent:'+remote+'/input.tar',*FLAGS],stdout=f,stderr=subprocess.STDOUT,timeout=180)
assert p.returncode==0
files={n:sha((OUT/n).read_bytes()) for n in names}
code="""import hashlib,tarfile,json
from pathlib import Path
out=Path(REMOTE)
assert hashlib.sha256((out/'input.tar').read_bytes()).hexdigest()==PACKAGE
with tarfile.open(out/'input.tar') as tf:
    members=tf.getmembers(); assert {m.name for m in members}==set(FILES) and len(members)==len(FILES)
    for m in members:
        assert m.isfile() and not (out/m.name).exists()
        data=tf.extractfile(m).read(); assert hashlib.sha256(data).hexdigest()==FILES[m.name]
        with (out/m.name).open('xb') as f: f.write(data)
print(json.dumps(dict(stage='inputs_verified_only',files=FILES)))
"""
print(ssh('REMOTE='+repr(remote)+'\nPACKAGE='+repr(sha(buf.getvalue()))+'\nFILES='+repr(files)+'\n'+code,'stage-unpack').decode())
write('stage.json',json.dumps(dict(remote=remote,files=files,package_sha256=sha(buf.getvalue())),indent=2).encode())
