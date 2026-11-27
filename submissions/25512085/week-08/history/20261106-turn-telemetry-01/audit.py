"""Compare exclusive source snapshots and emit numbers only, preserving inputs."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
before = json.loads((OUT/'baseline-v4-source-sha256.json').read_text())
after = json.loads((OUT/'after-final-source-sha256.json').read_text())
changed = sorted(k for k in before if before[k] != after.get(k))
added = sorted(set(after)-set(before))
assert changed == ['market_buyer.py'], changed
assert added == ['test_buyer_turn_telemetry_offline.py'], added
result = dict(changed=changed, added=added,
              unchanged_files=len(before)-len(changed),
              baseline_tests=485, final_tests=495, telemetry_tests=10,
              failures=0, errors=0, deployed=False)
for label in ('telemetry-final', 'after-final'):
    lines = (OUT/(label+'.log')).read_text().splitlines()
    report = json.loads(next(x.split('=',1)[1] for x in lines if x.startswith('BACKPORT_TEST_RESULT=')))
    assert report['success'] and not report['errors'] and not report['failures']
measure = next(x for x in (OUT/'telemetry-final.log').read_text().splitlines()
               if x.startswith('SYNTHETIC_TELEMETRY_MEASURE='))
result['synthetic_measure'] = json.loads(measure.split('=',1)[1])
with (OUT/'validation.json').open('x', encoding='utf-8') as f:
    json.dump(result, f, indent=2, sort_keys=True)
print(json.dumps(result, sort_keys=True))
