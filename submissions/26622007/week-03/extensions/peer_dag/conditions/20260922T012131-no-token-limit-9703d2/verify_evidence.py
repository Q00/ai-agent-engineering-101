"""Recompute metrics from saved evidence; no network calls or log edits."""
from collections import Counter
import json
from pathlib import Path
import re
import sys

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
sys.path.insert(0, str(ROOT))
from suite_study import inspect_run, group_metrics
from recover_429 import selected_failure

primary = json.loads((B / 'summary.json').read_text())
recovery = json.loads((B / 'recovery_429/summary.json').read_text())
assert len(primary['runs']) == 45
assert [r['run_id'] for r in primary['runs']] == [r['run_id'] for r in primary['manifest']['schedule']]
selected = [r['run_id'] for r in primary['runs'] if selected_failure(r)]
assert [r['source_run_id'] for r in recovery['runs']] == selected
assert set(r['run_id'] for r in primary['runs']).isdisjoint(r['run_id'] for r in recovery['runs'])
counts = {}
for name, folder, summary in [('primary', B, primary), ('recovery', B / 'recovery_429', recovery)]:
    requests = responses = 0
    finishes = Counter()
    for metric in summary['runs']:
        attempt = json.loads((folder / (metric['run_id'] + '.attempt.json')).read_text())
        assert inspect_run(attempt) == metric, metric['run_id']
        assert metric['request_checks_passed'], metric['run_id']
        assert not metric['verification_errors'], metric['run_id']
        for row in map(json.loads, (ROOT / 'logs' / (metric['run_id'] + '.jsonl')).read_text().splitlines()):
            if row['event'] == 'http_request':
                requests += 1
                payload = row['payload']
                assert not {'max_tokens', 'max_completion_tokens'} & payload.keys()
                assert payload['response_format']['type'] == 'json_schema'
                assert payload['response_format']['json_schema']['strict'] is True
                assert payload['provider']['only'] == ['fireworks']
            elif row['event'] == 'http_response':
                responses += 1
                finishes[json.loads(row['raw_response'])['choices'][0]['finish_reason']] += 1
    assert json.loads(json.dumps(group_metrics(summary['runs']))) == summary['total']
    counts[name] = {'runs': len(summary['runs']), 'requests': requests, 'responses': responses,
                    'finish_reasons': dict(finishes), 'metrics_recomputed': True,
                    'no_request_token_limits': True, 'strict_response_format': True}
broken = []
files = [B / 'FINAL_REPORT.md', B / 'QUALITY_REVIEW.md', B / 'recovery_429/REPORT.md',
         B / 'recovery_429/FACT_KEY_COLLISION.md',
         B / 'recovery_429/QUALITY_REVIEW.md', *B.glob('documents/*.md'), *B.glob('recovery_429/documents/*.md')]
for path in files:
    for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if '://' in target or target.startswith('#'):
            continue
        destination = (path.parent / target.split('#')[0]).resolve()
        if not destination.exists():
            broken.append({'source': str(path), 'target': target})
assert not broken, broken
result = {'passed': True, 'counts': counts, 'recovery_matches_original_429_failures': True,
          'checked_markdown_files': len(files), 'broken_links': broken}
with (B / 'evidence_verification.json').open('x') as stream:
    json.dump(result, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps(result, ensure_ascii=False))
