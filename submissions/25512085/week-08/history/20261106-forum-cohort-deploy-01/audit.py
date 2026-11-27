import json
from control import OUT, ssh, write
m=json.loads((OUT/'manifest.json').read_text())
code="""import ast,datetime,hashlib,json,subprocess
from pathlib import Path
r=Path('/home/comalswjd/ai-agent-engineering-101')
out=r/'submissions/25512085/week-08/history/20261106-forum-cohort-runtime-01'
def git(*a):return subprocess.check_output(['git',*a],cwd=r).decode().strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert git('rev-parse','HEAD')=='981c63401db534a30575fa4449d725bee04d4cdd'
assert git('rev-parse','HEAD^')==M['base']
assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
assert sorted(git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())==sorted(M['paths'])
assert 'Backport-of: '+M['source'] in git('log','-1','--format=%B')
for p,h in M['after'].items():
    assert sha(r/p)==h
    compile((r/p).read_bytes(),p,'exec')
unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
assert all(sha(r/p)==h for p,h in unchanged.items())
for p,h in M['before'].items():
    if h is not None:assert sha(out/'backup'/Path(p).name)==h
def functions(b):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(b).body if isinstance(n,ast.FunctionDef)}
checks={}
for name,allowed in [('seller_cash_policy.py',{'compact_break_orders','observe_break_stats'}),('forum_writer.py',{'analysis_candidates'})]:
    p='submissions/25512085/week-08/'+name
    old=functions(subprocess.check_output(['git','show',M['base']+':'+p],cwd=r)); new=functions((r/p).read_bytes())
    changed={f for f in old if old[f]!=new[f]}
    assert changed==allowed
    checks[name]=sorted(changed)
result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=git('rev-parse','HEAD'),tracked_staged_clean=True,unchanged_sources=len(unchanged),backup_hashes_verified=True,changed_functions=checks)
with (out/'audit.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
"""
raw=ssh('M='+repr(m)+'\n'+code,'independent-audit')
write('audit.json',raw)
print(raw.decode())
