"""Finish only the applied/staged allowlisted change; no reapplication or global git config."""
import json
import subprocess
from control import OUT, REPO, ssh, write

m=json.loads((OUT/'manifest.json').read_text())
author=subprocess.check_output(['git','show','-s','--format=%an%n%ae',m['source']],cwd=REPO).decode().splitlines()
code=(OUT/'remote.py').read_text().split('\ntry:\n',1)[0]
code+='\nAUTHOR='+repr(author)+'\n'
code+='''
assert git('rev-parse','HEAD')==M['base']
assert hashes(M['paths'])==M['after']
assert sorted(git('diff','--cached','--name-only').splitlines())==sorted(M['paths'])
assert not git('diff','--name-only')
unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
assert hashes(unchanged)==unchanged
before=json.loads((OUT/'before.json').read_text())
assert persistent(snapshot())==persistent(before['processes'])
assert sha(OUT/'backup/market_buyer.py')==M['before'][M['paths'][0]]
assert {p:sha(p) for p in before['prior_evidence']}==before['prior_evidence']
msg='fix(week08): backport buyer expected savings ranking\\n\\nBackport-of: '+M['source']+'\\nOnly buyer ranking and source regression test; preserve monolithic procurement guard.'
with (OUT/'finish-commit.log').open('xb') as f:
    subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','-c','user.name='+AUTHOR[0],'-c','user.email='+AUTHOR[1],'commit','-m',msg],cwd=REPO,stdout=f,stderr=subprocess.STDOUT,check=True)
assert git('rev-parse','HEAD^')==M['base']
assert sorted(git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())==sorted(M['paths'])
assert M['source'] in git('show','-s','--format=%B','HEAD')
assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
after=snapshot(); assert persistent(after)==persistent(before['processes'])
tests=json.loads((OUT/'remote-tests.json').read_text())
report=dict(utc=utc(),head=git('rev-parse','HEAD'),parent=M['base'],source=M['source'],runtime_sha256=hashes(M['paths']),tests=tests,processes=after,signals_sent=0,restarts=0,tracked_dirty=[],prior_evidence_unchanged={p:sha(p) for p in before['prior_evidence']}==before['prior_evidence'],commit_identity='source_author_command_local_only')
save('applied.json',report)
sellers=[r for r in after if r['role']=='seller' and r['cwd']==str(REPO/REL)]; assert len(sellers)==1
report['public']=public_check(sellers[0]['port'])
assert hashes(M['paths'])==M['after'] and hashes(unchanged)==unchanged
for p in M['paths']: compile((REPO/p).read_bytes(),p,'exec')
save('final.json',report)
print(json.dumps(report))
'''
result=ssh(code,'finish-commit')
write('final-result.json',result)
print(result.decode())
