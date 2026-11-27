"""Verify source attribution and construct only the exact ranking delta."""
import ast
import difflib
import json
import subprocess
from pathlib import Path
from control import OUT, OLD, REPO, sha, write

REL='submissions/25512085/week-08/'
SOURCE=subprocess.check_output(['git','rev-parse','62b862c'],cwd=REPO).decode().strip()
def blob(ref,name): return subprocess.check_output(['git','show',ref+':'+REL+name],cwd=REPO)
before=json.loads((OUT/'before.json').read_text())
name='market_buyer.py'
base=(OUT/'evidence/baseline'/REL/name).read_bytes()
old=blob(SOURCE+'^',name)
new=blob(SOURCE,name)
def funcs(data): return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(data).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
assert funcs(base)['pick_round3_shop_and_sku']==funcs(old)['pick_round3_shop_and_sku'], 'remote_selection_drift'
a=old.decode().splitlines(); b=new.decode().splitlines()
raw=base.splitlines(keepends=True)
delta=[]
for tag,i,j,k,l in reversed(list(difflib.SequenceMatcher(a=a,b=b,autojunk=False).get_opcodes())):
    if tag=='equal': continue
    needle=a[i:j]
    assert needle, 'insertion_needs_context'
    hits=[n for n in range(len(raw)-len(needle)+1) if [s.decode().rstrip('\r\n') for s in raw[n:n+len(needle)]]==needle]
    assert len(hits)==1, (tag,i,hits)
    n=hits[0]; raw[n:n+len(needle)]=[(s+'\n').encode() for s in b[k:l]]
    delta.append(dict(source_old=[i,j],source_new=[k,l]))
target=b''.join(raw)
bf=funcs(base); af=funcs(target)
assert af['pick_round3_shop_and_sku']==funcs(new)['pick_round3_shop_and_sku']
assert {k:v for k,v in bf.items() if k!='pick_round3_shop_and_sku'}=={k:v for k,v in af.items() if k!='pick_round3_shop_and_sku'}
test='test_buyer_savings_ranking_offline.py'
assert REL+test not in before['source_sha256']
testdata=blob(SOURCE,test)
for n,data in [(name,target),(test,testdata)]:
    compile(data,n,'exec')
    (OUT/'evidence/sandbox'/REL/n).write_bytes(data)
    write('target-'+n,data)
patch=b''
for n,olddata,newdata in [(name,base,target),(test,b'',testdata)]:
    patch+=b''.join(difflib.diff_bytes(difflib.unified_diff,olddata.splitlines(keepends=True),newdata.splitlines(keepends=True),fromfile=('a/'+REL+n).encode() if olddata else b'/dev/null',tofile=('b/'+REL+n).encode()))
write('backport.patch',patch)
manifest=dict(source=SOURCE,base=before['head'],paths=[REL+name,REL+test],before={REL+name:sha(base),REL+test:None},after={REL+name:sha(target),REL+test:sha(testdata)},source_sha256=before['source_sha256'],patch_sha256=sha(patch),semantic_checks=dict(original_function_matches=True,new_function_matches=True,other_functions_unchanged=True),delta=delta)
write('manifest.json',json.dumps(manifest,indent=2).encode())
runner=(OLD/'run_isolated.py').read_text()
start=runner.index('    names = ['); end=runner.index('    suite =',start)
runner=runner[:start]+"    names = ['test_buyer_savings_ranking_offline','test_buyer_price_offline','test_buyer_retry_offline','test_buyer_failure_reason_offline','test_buyer_stockout_cooldown_offline','test_goods_selection_offline','test_procurement_guard_offline']\n"+runner[end:]
write('run_isolated.py',runner.encode())
print(json.dumps(manifest,indent=2))
