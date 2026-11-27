"""Build narrowly scoped deployment helpers from the prior reviewed procedure."""
from pathlib import Path

OUT = Path(__file__).resolve().parent
OLD = OUT.parent/'20261106-buyer-ranking-deploy-01'
def save(name, text):
    with (OUT/name).open('x', encoding='utf-8', newline='\n') as f:
        f.write(text)

control=(OLD/'control.py').read_text()
control=control.replace("'d45dfac64c094de791f7c51e72d537399789df16'", "'edfb50e5a36815ff131d3ea30d67389758b1d4c8'")
save('control.py',control)
save('local_validate.py',(OLD/'local_validate.py').read_text())
stage=(OLD/'stage.py').read_text().replace('20261106-buyer-ranking-runtime-01','20261106-gap35-runtime-01')
save('stage.py',stage)
runner=(OLD/'run_isolated.py').read_text().replace("names = [", "names = ['test_buyer_gap_counter_offline',",1)
save('run_isolated.py',runner)
remote=(OLD/'remote.py').read_text().replace('20261106-buyer-ranking-runtime-01','20261106-gap35-runtime-01')
remote=remote.replace("[('focused',126),('all',405)]", "[('focused',137),('all',416)]")
remote=remote.replace(".market_buyer-ranking-62b862c.tmp", ".market_buyer-gap35-abc7e0a.tmp")
remote=remote.replace("    assert sha(backup/'market_buyer.py')", "    with (backup/'test_buyer_retry_offline.py').open('xb') as f: f.write((REPO/M['paths'][2]).read_bytes())\n    assert sha(backup/'market_buyer.py')")
remote=remote.replace("    assert sha(backup/'market_buyer.py')", "    assert sha(backup/'test_buyer_retry_offline.py')==M['before'][M['paths'][2]]\n    assert sha(backup/'market_buyer.py')")
remote=remote.replace("    assert hashes(M['paths'])==M['after']\n    unchanged=", "    assert hashes(M['paths'])==M['after']\n    for p in M['paths']: compile((REPO/p).read_bytes(),p,'exec')\n    unchanged=")
remote=remote.replace("    assert sha(original)==M['before']", "    retry=REPO/M['paths'][2]\n    retry_stage=retry.with_name('.retry-gap35.tmp')\n    with retry_stage.open('xb') as f:\n        f.write((tree/M['paths'][2]).read_bytes()); f.flush(); os.fsync(f.fileno())\n    assert (retry_stage.stat().st_mode & 0o777)==(retry.stat().st_mode & 0o777)\n    assert sha(retry)==M['before'][M['paths'][2]]\n    os.replace(retry_stage,retry)\n    assert sha(original)==M['before']")
remote=remote.replace("==[M['paths'][0]]", "==sorted([M['paths'][0],M['paths'][2]])")
remote=remote.replace('fix(week08): backport buyer expected savings ranking','fix(week08): backport Round3 second counter 35 percent gap')
remote=remote.replace('Only buyer ranking and source regression test; preserve monolithic procurement guard.', 'Tests-from: '+ '1854fc8e953dce5274b3765d2f2bef605192fe48' +'\\nOnly Round3 second counter and regressions; preserve monolithic guard and ranking.')
remote=remote.replace("'git','-c','core.hooksPath=/dev/null'", "'git','-c','user.name='+M['author_name'],'-c','user.email='+M['author_email'],'-c','core.hooksPath=/dev/null'")
remote=remote.replace("'backup/market_buyer.py']", "'backup/market_buyer.py','backup/test_buyer_retry_offline.py']")
save('remote.py',remote)
print('Created exclusive gap35 helpers; expected counts must be verified locally before staging.')
