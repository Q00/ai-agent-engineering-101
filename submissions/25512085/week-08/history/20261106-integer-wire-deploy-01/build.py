"""Create a new, narrowly adapted deployment procedure; never run old mutations."""
from pathlib import Path

OUT = Path(__file__).resolve().parent
OLD = OUT.parent/'20261106-turn-telemetry-deploy-01'
def put(name, text):
    with (OUT/name).open('x', encoding='utf-8', newline='\n') as f:
        f.write(text)
def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new)

c = (OLD/'control.py').read_text()
c = c.replace('20261106-turn-telemetry-runtime-01', '20261106-integer-wire-runtime-01')
c = c.replace('981c63401db534a30575fa4449d725bee04d4cdd', 'be6a77c835fbcabf7edb75097be318ba4f84eecb')
c = c.replace('c819c25', 'cc62b97')
start = c.index('def prepare():')
end = c.index('\ndef stage():', start)
c = c[:start] + '''def prepare():
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
''' + c[end:]
a = c.index('    delta=subprocess.check_output', c.index('def stage():'))
b = c.index('    # SCP', a)
c = c[:a]+"    names=['manifest.json','run_isolated.py','remote.py','backport.patch']+[Path(p).name for p in manifest['paths']]\n"+c[b:]
c = c.replace("('sandbox','telemetry')", "('sandbox','focused')")
c = c.replace("'result-sandbox-telemetry.json'", "'result-sandbox-focused.json'").replace("'sandbox-telemetry.log'", "'sandbox-focused.log'")
put('control.py', c)

r = (OLD/'remote.py').read_text().replace('20261106-turn-telemetry-runtime-01','20261106-integer-wire-runtime-01')
r = r.replace("'.turn-telemetry-'", "'.integer-wire-'")
r = replace(r,"assert reports['sandbox-telemetry']['tests_run']==10\n    assert reports['sandbox-all']['tests_run']==reports['baseline-all']['tests_run']+10", "assert reports['sandbox-all']['tests_run']==reports['baseline-all']['tests_run']+4")
r = r.replace("('sandbox','telemetry')", "('sandbox','focused')")
r = replace(r,"    assert git('diff','HEAD','--name-only').splitlines()==[M['paths'][0]]", "    assert sorted(git('diff','HEAD','--name-only').splitlines())==sorted(p for p in M['paths'] if p in M['source_sha256'])")
r = replace(r,"    msg='feat(week08): backport bounded buyer turn telemetry only\\n\\nBackport-of: 85b39b5, 3f92323, 35deeb6; final source '+M['source']+'\\nPreserve monolithic seller, RPC/parser, counter35, savings ranking, guard and cohort.'", "    msg='fix(week08): send Round 3 offers as integer strings only\\n\\nBackport-of: 0baab04049c88a11e759216a7d7f63a139032ba4 (two expressions and fixtures), cc62b97 (strict tests), bfa4e52 (legacy fixture correction); final source '+M['source']+'\\nPreserve legacy/initial inquiries, counter35, limits, cooldown, telemetry, ranking, guard and cohort.'")
r = replace(r,"    original=REPO/M['paths'][0]\n    with (backup/'market_buyer.py').open('xb') as f:\n        f.write(original.read_bytes()); f.flush(); os.fsync(f.fileno())\n    assert sha(backup/'market_buyer.py')==M['source_sha256'][M['paths'][0]]", "    for p in M['paths']:\n        if p not in M['source_sha256']: continue\n        with (backup/Path(p).name).open('xb') as f:\n            f.write((REPO/p).read_bytes()); f.flush(); os.fsync(f.fileno())\n        assert sha(backup/Path(p).name)==M['source_sha256'][p]")
# Verify the candidate is precisely the two byte substitutions, not merely local AST.
r = replace(r,"    baseline=OUT/'evidence/baseline'/M['paths'][0]", "    baseline=OUT/'evidence/baseline'/M['paths'][0]\n    candidate=baseline.read_bytes()\n    for a,b in [(b'f\"I offer {offer} for this certified item.\"',b'str(offer)'),(b'f\"I offer {seller_ask} for this certified item.\"',b'str(seller_ask)')]:\n        assert candidate.count(a)==1\n        candidate=candidate.replace(a,b)\n    assert candidate==(OUT/'evidence/sandbox'/M['paths'][0]).read_bytes()")
put('remote.py', r)
runner=(OUT.parent/'20261106-integer-wire-01/run_isolated.py').read_text()
runner=runner.replace("names = ['test_forum_intel_offline'", "names = ['test_buyer_integer_wire_offline','test_buyer_turn_telemetry_offline','test_forum_intel_offline'")
runner=runner.replace("os.environ['TEMP'] = os.environ['TMP'] = str(scratch)", "os.environ['HOME'] = os.environ['USERPROFILE'] = os.environ['TEMP'] = os.environ['TMP'] = str(scratch)")
runner=runner.replace("if REPO in path.parents", "if any(part in ('.market','.env') or part.endswith(('.pem','.key')) for part in path.parts):\n            raise AssertionError('runtime credentials/state forbidden')\n        if REPO in path.parents")
put('run_isolated.py',runner)
print('Created new helpers; previous helpers were read, not executed.')
