"""Read-only progress of the manifest's live runs (never includes replays)."""
from collections import Counter
import argparse
import json
from pathlib import Path

B = Path(__file__).resolve().parent
ROOT = B.parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--recovery', action='store_true')
FOLDER = B / 'recovery_429' if parser.parse_args().recovery else B
manifest = json.loads((FOLDER / 'manifest.json').read_text())
metrics, events = [], []
for item in manifest['schedule']:
    metric = FOLDER / (item['run_id'] + '.metrics.json')
    if metric.exists():
        metrics.append(json.loads(metric.read_text()))
    log = ROOT / 'logs' / (item['run_id'] + '.jsonl')
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass  # The writer may currently be appending the last line.
requests = [r for r in events if r['event'] == 'http_request']
usage = [r for r in events if r['event'] == 'http_usage']
print(json.dumps({
    'completed': len(metrics), 'passed': sum(m['passed'] for m in metrics),
    'conditions': {c: {'completed': sum(m['condition'] == c for m in metrics),
                       'passed': sum(m['condition'] == c and m['passed'] for m in metrics)}
                   for c in ('baseline', 'homogeneous', 'overconfident')},
    'requests': len(requests),
    'token_caps': sum(bool({'max_tokens', 'max_completion_tokens'} & r['payload'].keys()) for r in requests),
    'finish_reasons': dict(Counter(r['finish_reason'] for r in usage)),
    'max_completion_tokens': max((r['usage'].get('completion_tokens', 0) for r in usage), default=0),
    'responses_over_2200_tokens': sum(r['usage'].get('completion_tokens', 0) > 2200 for r in usage),
    'http_errors': dict(Counter(str(r['status']) for r in events if r['event'] == 'http_http_error')),
    'failed_count': sum(not m['passed'] for m in metrics),
    'latest_failures': [{'run': m['run_id'], 'error': m['error']} for m in metrics if not m['passed']][-3:],
    'last_completed': metrics[-1]['run_id'] if metrics else None,
    'recent_events': [{k: r[k] for k in ('event', 'task_id', 'worker', 'phase') if k in r} for r in events[-2:]],
}, ensure_ascii=False))
