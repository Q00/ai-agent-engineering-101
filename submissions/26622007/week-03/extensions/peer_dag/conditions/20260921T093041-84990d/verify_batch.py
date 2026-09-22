"""Read-only verification of this recorded batch; save new evidence with exclusive creation."""
from collections import Counter
from datetime import datetime
from itertools import combinations
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parent.parent
sys.path.insert(0, str(ROOT))
from audit import audit
from core import fingerprint
from models import messages, team_roster
from response_formats import response_format


def inspect(metric):
    run_id, condition = metric['run_id'], metric['condition']
    log = ROOT / 'logs' / (run_id + '.jsonl')
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    state = next(row['settings'] for row in rows if row['event'] == 'run_start')
    trace = audit(log)
    errors, requests, intervals, active = [], {}, [], {}
    request_count = response_count = 0
    fixed_fields = ('model', 'temperature', 'max_tokens', 'reasoning', 'provider')
    for row in rows:
        event = row['event']
        if event == 'http_request':
            request_count += 1
            key = (row['task_id'], row['contractor'], row['phase'], row['attempt'])
            if key in requests:
                errors.append({'kind': 'duplicate_request', 'key': key})
            requests[key] = row['payload']
            payload = json.loads(row['payload']['messages'][1]['content'])
            expected_messages = messages(row['contractor'], row['phase'], payload, condition)
            checks = {
                'messages': row['payload']['messages'] == expected_messages,
                'format': row['payload'].get('response_format') == response_format(row['phase'], payload),
                'roster': json.dumps(team_roster(condition), ensure_ascii=False, sort_keys=True)
                          in row['payload']['messages'][0]['content'],
                'transport': all(row['payload'].get(field) == state['transport'].get(field) for field in fixed_fields),
            }
            for name, passed in checks.items():
                if not passed:
                    errors.append({'kind': name, 'key': key})
        elif event == 'http_response':
            response_count += 1
            key = (row['task_id'], row['contractor'], row['phase'], row['attempt'])
            try:
                envelope = json.loads(row['raw_response'])
                content = json.loads(envelope['choices'][0]['message']['content'])
                schema = requests[key]['response_format']['json_schema']['schema']
                Draft202012Validator.check_schema(schema)
                validation = list(Draft202012Validator(schema).iter_errors(content))
                for failure in validation:
                    errors.append({'kind': 'response_schema', 'key': key, 'message': failure.message})
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                errors.append({'kind': 'response_decode', 'key': key, 'message': str(exc)})
        elif event == 'call_start' and row['phase'] in ('execute', 'synthesize'):
            key = (row['task_id'], row['worker'], row['phase'])
            active[key] = row
        elif event == 'call_end' and row['phase'] in ('execute', 'synthesize'):
            key = (row['task_id'], row['worker'], row['phase'])
            start = active.pop(key)
            intervals.append({'task_id': key[0], 'worker': key[1], 'phase': key[2],
                              'start': start['at'], 'end': row['at'],
                              'task_status': trace['task_statuses'].get(key[0])})
    overlaps = []
    for left, right in combinations(intervals, 2):
        start = max(datetime.fromisoformat(left['start']), datetime.fromisoformat(right['start']))
        end = min(datetime.fromisoformat(left['end']), datetime.fromisoformat(right['end']))
        if end > start:
            overlaps.append({'left': left['task_id'], 'right': right['task_id'],
                             'workers': [left['worker'], right['worker']],
                             'seconds': (end-start).total_seconds(),
                             'both_tasks_succeeded': left['task_status'] == right['task_status'] == 'succeeded'})
    if state.get('team_roster') != team_roster(condition):
        errors.append({'kind': 'recorded_roster'})
    awards = [row for row in rows if row['event'] == 'award']
    root_plan = next((row['plan'] for row in awards if row['task_id'] == 'release-review'), None)
    artifact_path = ROOT / 'runs' / run_id / 'artifacts/release-review.json'
    artifact = json.loads(artifact_path.read_text()).get('artifact') if artifact_path.exists() else None
    return {
        'run_id': run_id, 'condition': condition, 'passed': not errors and trace['passed'] and request_count > 0,
        'requests_checked': request_count, 'responses_schema_checked': response_count, 'errors': errors,
        'http_errors': dict(Counter(str(row['status']) for row in rows if row['event'] == 'http_http_error')),
        'transport_errors': sum(row['event'] == 'http_transport_error' for row in rows),
        'proposal_rejections': [{'task_id': row['task_id'], 'worker': row['worker'], 'error': row['error']}
                                for row in rows if row['event'] == 'proposal_rejected'],
        'task_failures': [{'task_id': row['task_id'], 'error': row['outcome']['error']}
                          for row in rows if row['event'] == 'task_end' and row['outcome']['status'] == 'failed'],
        'root_plan_sha': fingerprint(root_plan) if root_plan else None,
        'root_plan': root_plan,
        'award_sha': fingerprint({row['task_id']: row['worker'] for row in awards}),
        'artifact_sha': fingerprint(artifact) if artifact else None,
        'intervals': intervals, 'overlaps': overlaps, 'trace': trace,
    }


def main():
    summary = json.loads((FOLDER / 'summary.json').read_text())
    runs = [inspect(metric) for metric in summary['runs']]
    conditions = {}
    for condition in summary['conditions']:
        subset = [run for run in runs if run['condition'] == condition]
        conditions[condition] = {
            'distinct_root_plans': len({run['root_plan_sha'] for run in subset if run['root_plan_sha']}),
            'distinct_award_maps': len({run['award_sha'] for run in subset}),
            'distinct_full_artifacts': len({run['artifact_sha'] for run in subset if run['artifact_sha']}),
            'runs_with_actual_parallel_execution': sum(bool(run['overlaps']) for run in subset),
            'runs_with_successful_parallel_tasks': sum(any(item['both_tasks_succeeded'] for item in run['overlaps']) for run in subset),
            'runs_with_same_worker_execution_overlap': sum(any(item['workers'][0] == item['workers'][1] for item in run['overlaps']) for run in subset),
            'http_429': sum(run['http_errors'].get('429', 0) for run in subset),
            'requests': sum(run['requests_checked'] for run in subset),
            'responses': sum(run['responses_schema_checked'] for run in subset),
        }
    result = {'passed': all(run['passed'] for run in runs),
              'meaning': 'Request, response schema and trace integrity; separate from task success.',
              'verifier_sha': fingerprint(Path(__file__).read_text()), 'conditions': conditions, 'runs': runs}
    with (FOLDER / 'verification.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'passed': result['passed'], 'conditions': conditions,
                      'errors': {run['run_id']: run['errors'] for run in runs if run['errors']}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
