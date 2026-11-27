"""Read-only independent final verification; no operational requests."""
import json
from control import OUT, ssh, write

m=json.loads((OUT/'manifest.json').read_text())
code='M='+repr(m)+'\n'+'''import datetime,hashlib,json,subprocess
from pathlib import Path
r=Path('/home/comalswjd/ai-agent-engineering-101')
def git(*a): return subprocess.check_output(['git',*a],cwd=r).decode().strip()
def sha(p): return hashlib.sha256((r/p).read_bytes()).hexdigest()
assert git('rev-parse','HEAD')=='594e1f9bd4de1662c46e4816fabe5a096ab1233d'
assert git('rev-parse','HEAD^')==M['base']
changed=git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()
assert sorted(changed)==sorted(M['paths'])
assert 'Backport-of: '+M['source'] in git('show','-s','--format=%B','HEAD')
assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
assert {p:sha(p) for p in M['paths']}==M['after']
unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
assert {p:sha(p) for p in unchanged}==unchanged
for p in M['paths']: compile((r/p).read_bytes(),p,'exec')
out=r/'submissions/25512085/week-08/history/20261106-gap35-runtime-01'
for p in (M['paths'][0],M['paths'][2]):
    assert hashlib.sha256((out/'backup'/Path(p).name).read_bytes()).hexdigest()==M['before'][p]
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=git('rev-parse','HEAD'),parent=M['base'],changed=changed,tracked_clean=True,source_sha256=M['after'],unchanged_source_count=len(unchanged),memory_compile=True,backups_verified=True,backport_trailer_verified=True)))
'''
raw=ssh(code,'audit')
report=json.loads(raw)
write('audit.json',json.dumps(report,indent=2).encode())
print(json.dumps(report,indent=2))
