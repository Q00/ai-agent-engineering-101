"""Attribution, exact byte-context patch, and changed-function AST proof."""
import ast
import difflib
import json
import subprocess
from control import OUT, REPO, sha, write

REL='submissions/25512085/week-08/'
SOURCE=subprocess.check_output(['git','rev-parse','abc7e0a'],cwd=REPO).decode().strip()
FINAL=subprocess.check_output(['git','rev-parse','1854fc8'],cwd=REPO).decode().strip()
def blob(ref,name): return subprocess.check_output(['git','show',ref+':'+REL+name],cwd=REPO)
def funcs(data): return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(data).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
before=json.loads((OUT/'before.json').read_text())
assert before['source_sha256'][REL+'market_buyer.py']=='01b49b84ef6017c724567a4ab2caef836f51f2d8eb5f0c64798725a20c5dd2fc'
paths=['market_buyer.py','test_buyer_gap_counter_offline.py','test_buyer_retry_offline.py']
changes=[]
for name in paths:
    p=OUT/'evidence/baseline'/REL/name
    base=p.read_bytes() if p.exists() else b''
    if name==paths[1]:
        assert REL+name not in before['source_sha256']
        target=blob(FINAL,name)
    else:
        old=blob(SOURCE+'^' if name==paths[0] else '917e689',name)
        new=blob(SOURCE if name==paths[0] else FINAL,name)
        if name==paths[0]:
            assert funcs(base)['_run_attempt']==funcs(old)['_run_attempt'], 'remote_attempt_drift'
            assert funcs(new)['_run_attempt']==funcs(blob(FINAL,name))['_run_attempt']
        else:
            assert base==old, 'retry_baseline_drift'
        a=old.decode().splitlines(); b=new.decode().splitlines(); raw=base.splitlines(keepends=True)
        for tag,i,j,k,l in reversed(list(difflib.SequenceMatcher(a=a,b=b,autojunk=False).get_opcodes())):
            if tag=='equal': continue
            needle=a[i:j]; assert needle
            hits=[n for n in range(len(raw)-len(needle)+1) if [s.decode().rstrip('\r\n') for s in raw[n:n+len(needle)]]==needle]
            assert len(hits)==1,(name,tag,i,hits)
            n=hits[0]; raw[n:n+len(needle)]=[(s+'\n').encode() for s in b[k:l]]
        target=b''.join(raw)
        if name==paths[0]:
            bf=funcs(base); af=funcs(target)
            assert af['_run_attempt']==funcs(new)['_run_attempt']
            assert {k:v for k,v in bf.items() if k!='_run_attempt'}=={k:v for k,v in af.items() if k!='_run_attempt'}
    compile(target,name,'exec')
    (OUT/'evidence/sandbox'/REL/name).write_bytes(target)
    write('target-'+name,target)
    changes.append((name,base,target))
patch=b''
for n,old,new in changes:
    patch+=b''.join(difflib.diff_bytes(difflib.unified_diff,old.splitlines(keepends=True),new.splitlines(keepends=True),fromfile=('a/'+REL+n).encode() if old else b'/dev/null',tofile=('b/'+REL+n).encode()))
write('backport.patch',patch)
author=subprocess.check_output(['git','show','-s','--format=%an%n%ae',SOURCE],cwd=REPO).decode().splitlines()
m=dict(source=SOURCE,tests_source=FINAL,author_name=author[0],author_email=author[1],base=before['head'],paths=[REL+n for n in paths],before={REL+n:sha(old) if old else None for n,old,new in changes},after={REL+n:sha(new) for n,old,new in changes},source_sha256=before['source_sha256'],patch_sha256=sha(patch),semantic_checks=dict(original_function_matches=True,new_function_matches=True,other_functions_unchanged=True))
write('manifest.json',json.dumps(m,indent=2).encode())
print(json.dumps(dict(base=m['base'],paths=m['paths'],after=m['after'],semantic_checks=m['semantic_checks']),indent=2))
