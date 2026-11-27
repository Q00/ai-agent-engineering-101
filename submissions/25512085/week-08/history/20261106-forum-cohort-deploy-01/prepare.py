import ast
import difflib
import json
import subprocess
from control import OUT, REPO, sha, write
REL='submissions/25512085/week-08/'
SOURCE='417786f44d7315160a278f6de9229bdc0b05ca5c'
def source(rev,n):return subprocess.check_output(['git','show',rev+':'+REL+n],cwd=REPO)
def funcs(b):return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(b).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
names=['seller_cash_policy.py','forum_writer.py','test_forum_intel_offline.py','test_forum_quality_evidence_offline.py']
before=json.loads((OUT/'before.json').read_text())
patch=[]; after={}; pre={}; checks={}
for name in names:
    p=REL+name; dest=OUT/'evidence/sandbox'/p
    if name=='test_forum_quality_evidence_offline.py':
        assert not dest.exists(); old=b''; new=source(SOURCE,name)
    else:
        old=(OUT/'evidence/baseline'/p).read_bytes(); baseline=source(SOURCE+'^',name); final=source(SOURCE,name)
        a=baseline.decode().splitlines(keepends=True); b=final.decode().splitlines(keepends=True)
        text=old.decode()
        allowed={'seller_cash_policy.py':{'compact_break_orders','observe_break_stats'},'forum_writer.py':{'analysis_candidates'}}
        if name in allowed:
            of,lf=funcs(old),funcs(baseline)
            for f in allowed[name]:assert of[f]==lf[f], 'function_baseline_mismatch_'+f
        changes=[(i,j,k,l) for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op!='equal']
        for i,j,k,l in reversed(changes):
            # Anchor inserts with two unchanged surrounding lines; exact byte-context only.
            start=max(0,i-1); end=j
            if i==j: end=min(len(a),j+1)
            needle=''.join(a[start:end]); replacement=''.join(a[start:i]+b[k:l]+a[j:end])
            assert text.count(needle)==1, 'nonunique_context_'+name
            text=text.replace(needle,replacement,1)
        new=text.encode()
        if name in allowed:
            nf,ff=funcs(new),funcs(final)
            assert all(nf[f]==ff[f] for f in allowed[name])
            assert all(nf[f]==v for f,v in of.items() if f not in allowed[name])
            checks[name]=dict(changed_functions=sorted(allowed[name]),other_functions_unchanged=True,baseline_ast_match=True,final_ast_match=True)
    compile(new,p,'exec')
    dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(new)
    pre[p]=sha(old) if old else None; after[p]=sha(new)
    patch.extend(difflib.unified_diff(old.decode().splitlines(True),new.decode().splitlines(True),fromfile='a/'+p if old else '/dev/null',tofile='b/'+p))
write('backport.patch',''.join(patch).encode())
author=subprocess.check_output(['git','show','-s','--format=%an%n%ae',SOURCE],cwd=REPO).decode().splitlines()
m=dict(base=before['head'],source=SOURCE,source_sha256=before['source_sha256'],paths=[REL+n for n in names],before=pre,after=after,patch_sha256=sha((OUT/'backport.patch').read_bytes()),author_name=author[0],author_email=author[1])
write('manifest.json',json.dumps(m,indent=2).encode()); write('ast-checks.json',json.dumps(checks,indent=2).encode())
print(json.dumps(checks,indent=2))
