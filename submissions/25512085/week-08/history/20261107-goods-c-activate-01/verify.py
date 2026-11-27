"""Validate saved sanitized evidence and helper syntax, without rerunning activation."""
import hashlib
import json
from pathlib import Path
import subprocess

here=Path(__file__).resolve().parent
repo=here.parents[4]
helpers=sorted(here.glob('*.py'))
for p in helpers: compile(p.read_bytes(),str(p),'exec')
activation=json.loads((here/'activate-stdout.json').read_text())
observation=json.loads((here/'observe-stdout.json').read_text())
manifest=json.loads((here.parent/'20261107-goods-c-review-04/manifest.json').read_text())
tests=json.loads((here.parent/'20261107-goods-c-review-04/remote-evidence/remote-tests.json').read_text())
assert activation['success'] and activation['manual_spawns']==0
assert activation['signals']==[{'pid':299169,'starttime':'7735542','signal':'TERM'}]
assert activation['seller_sha256']==manifest['after']['submissions/25512085/week-08/seller.py']
assert activation['remote_commit']==observation['head']
for role in ('seller','kit','tunnel'):
    assert activation[role]['pid']==observation['processes'][role]['pid']
    assert activation[role]['starttime']==observation['processes'][role]['starttime']
expected=dict(manifest['source_sha256']); expected.update(manifest['after'])
assert all(h==expected[p] for p,h in observation['source_sha256'].items())
assert observation['fixture_sha256']==manifest['after']['submissions/25512085/week-08/test_goods_terms_pilot_offline.py']
assert all(observation['health'][key]==200 for key in ('local_card_http','board_http','public_card_http'))
assert observation['new_C0_effect_verified'] is False
for key in ('sandbox-focused','sandbox-all'):
    assert tests['reports'][key]['success']
result=subprocess.run(['git','diff','--check','HEAD','--',str(here.relative_to(repo))],cwd=repo,capture_output=True)
assert result.returncode==0
report=dict(syntax_files=len(helpers),in_memory_compile=True,evidence_consistent=True,diff_check_exit=result.returncode,
            revision06_tests_previously_run_not_repeated=tests['reports'],
            helper_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in helpers})
with (here/'verification.json').open('x') as f: json.dump(report,f,indent=2)
print(json.dumps(report))
